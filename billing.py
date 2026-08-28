"""
billing.py
----------
Calcul des décomptes de charges MENSUELS (page /decompte), suivant le modèle
RCP (regroupement dans le cadre de la consommation propre) — le même que ce
que proposent les prestataires du marché comme Climkit : la consommation de
chaque zone est scindée en une part solaire autoconsommée et une part
achetée au réseau, facturées à deux prix différents.

## Ce que mesure quoi (établi empiriquement, pas supposé)

Chaque zone a un bloc Loxone "Moniteur de flux d'énergie" (EFM) alimenté par
deux compteurs, plus un compteur plus ancien de périmètre différent :

  - `<Zone> Grid (total)` — énergie ACHETÉE AU RÉSEAU par la zone.
    Vérifié : sa valeur instantanée est identique à la sortie `Gpwr` de
    l'EFM de la zone (99-100 % des échantillons), et n'est jamais négative
    — c'est de l'import pur, pas un compteur bidirectionnel.

  - `<Zone> Solaire (total)` — énergie solaire AUTOCONSOMMÉE par la zone.
    Vérifié de deux façons : (a) sa valeur instantanée est identique à la
    sortie `Ppwr` de l'EFM de la zone, et (b) son cumul est identique, à
    0,01 kWh près sur les incréments, à la sortie `selfConsumption` du même
    bloc EFM. C'est donc Loxone lui-même qui qualifie cette série
    d'autoconsommation. Enfin, la somme des six zones (28,78 kWh sur la
    fenêtre de données brutes) égale la sortie `selfConsumption` du bloc EFM
    du BÂTIMENT (28,79 kWh) : les zones se répartissent bien l'intégralité
    de l'autoconsommation de l'immeuble.

  => **Consommation facturable d'une zone = Grid + Solaire.**

  - `Appartement N / Commerce / Rez jardin (total)` — compteur plus ancien
    (UUID de génération antérieure aux compteurs EFM, posés en octobre 2025),
    d'un PÉRIMÈTRE DIFFÉRENT : en août 2026 il enregistre 6,3 kWh/jour sur
    App 1 quand le seul compteur Grid en enregistre 7,0. Ce n'est donc PAS
    une seconde mesure de la même chose, et l'écart avec Grid+Solaire n'est
    pas une anomalie de compteur (confirmé par l'installateur). Il est gardé
    en information de contrôle, jamais utilisé pour facturer.

## Les deux taux, à ne pas confondre

  - **Taux d'autoproduction** = solaire autoconsommé / consommation totale.
    "Quelle part de ce que je consomme vient du soleil ?" Monte en été
    (1 % en février 2026, 23 % en juillet). C'est l'indicateur qui parle à
    un propriétaire, donc celui mis en avant.

  - **Taux d'autoconsommation** = solaire autoconsommé / production totale.
    "Quelle part de ce que je produis est consommée sur place plutôt que
    réinjectée ?" BAISSE en été (84 % en février 2026, 29 % en juillet) —
    mathématiquement correct (on produit beaucoup plus que ce qu'on peut
    absorber) mais contre-intuitif : à afficher uniquement avec son
    explication, et jamais seul.

## Deux choix de calcul

1. **Découpage mensuel calendaire, en heure locale Europe/Zurich** — bornes
   à minuit LOCAL, pas UTC comme le reste du dashboard
   (`db.query_daily_last`) : sur une facture, un mois commence à 00:00 chez
   le propriétaire. Le mensuel remplace un découpage bimestriel essayé
   d'abord : il isole le mois d'installation des compteurs (octobre 2025,
   incomplet) au lieu de perdre tout un bimestre.

2. **Consommation = relevé de fin − relevé de début** (`db.query_value_at`),
   jamais une somme de deltas journaliers : c'est la logique d'un relevé de
   compteur physique à deux dates, insensible aux trous de collecte au
   milieu de la période, et sans double comptage à cheval sur deux mois.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import db

TIMEZONE = "Europe/Zurich"

MONTH_LABELS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

# Une baisse d'un compteur cumulatif signale un reset / un remplacement de
# compteur, JAMAIS une consommation négative. Seuil en kWh : les micro-baisses
# de l'ordre de 1e-3 sont un simple écart d'arrondi entre le poller live
# (pleine précision) et l'historique Statistics importé (arrondi par Loxone),
# elles ne doivent pas déclencher d'alerte.
RUPTURE_MIN_DROP_KWH = 0.5

# Séries à écarter quand on cherche le compteur de contrôle d'une zone : le
# chauffage et l'eau chaude sont des compteurs distincts (périmètres
# séparés), et "Grid"/"Solaire"/"Sol" sont les compteurs EFM de facturation.
CONTROLE_EXCLUDE_RE = re.compile(r"(?i)(chauffage|\bgrid\b|\bsol\b|solaire|eau[ _-]?chaude)")

# Repli pour identifier un compteur solaire mal classé automatiquement :
# "Communs Sol" est rangé en "énergie consommée" par classification.py, faute
# de mot-clé reconnu ("Sol" abrégé). On ne corrige pas la règle globale
# (risque de capturer "chauffage au sol"), on fait le repli ici, localement.
SOLAR_LABEL_RE = re.compile(r"(?i)(\bsol\b|solaire|solar|\bpv\b)")

BATIMENT_PRODUCTION_RE = re.compile(r"(?i)^production\b")
BATIMENT_RESEAU_RE = re.compile(r"(?i)^r[ée]seau\b")


# --------------------------------------------------------------------------
# Périodes mensuelles
# --------------------------------------------------------------------------

def period_key(year: int, month: int) -> str:
    """Clé stable d'un mois, ex: "2026-05". `month` est 1-12."""
    return f"{year}-{month:02d}"


def parse_period_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", key or "")
    if not m:
        raise ValueError(f"clé de période invalide: {key!r} (attendu ex: 2026-05)")
    return int(m.group(1)), int(m.group(2))


def period_bounds(year: int, month: int, tz_name: str = TIMEZONE) -> tuple[int, int]:
    """Bornes epoch [début, fin[ d'un mois, calées sur minuit LOCAL. `fin`
    est la borne exclusive : minuit local du 1er du mois suivant."""
    tz = ZoneInfo(tz_name)
    start = datetime(year, month, 1, tzinfo=tz)
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(end_year, end_month, 1, tzinfo=tz)
    return int(start.timestamp()), int(end.timestamp())


def period_label(year: int, month: int) -> str:
    return f"{MONTH_LABELS[month - 1]} {year}"


def period_label_short(year: int, month: int) -> str:
    """Libellé compact pour les axes de graphs, ex: "mai 26"."""
    return f"{MONTH_LABELS[month - 1][:4].lower()}. {year % 100:02d}"


def periods_covering(first_ts: int, last_ts: int, tz_name: str = TIMEZONE) -> list[dict]:
    """Tous les mois qui recouvrent [first_ts, last_ts], du plus ancien au
    plus récent."""
    tz = ZoneInfo(tz_name)
    first = datetime.fromtimestamp(first_ts, tz)
    last = datetime.fromtimestamp(last_ts, tz)
    out = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        start, end = period_bounds(year, month, tz_name)
        out.append({
            "key": period_key(year, month),
            "label": period_label(year, month),
            "label_court": period_label_short(year, month),
            "year": year,
            "month": month,
            "start": start,
            "end": end,
        })
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


# --------------------------------------------------------------------------
# Résolution des séries : quelle série alimente quelle colonne
# --------------------------------------------------------------------------

def _totals(series: list[dict]) -> list[dict]:
    return [s for s in series if s.get("state_name") == "total"]


def _pick(candidates: list[dict]) -> dict | None:
    """Choix déterministe quand plusieurs séries pourraient convenir : la
    première par ordre alphabétique de libellé. Les candidats écartés sont
    remontés à part (voir resolve_zones) pour que la page puisse signaler
    l'ambiguïté au lieu de la masquer."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda s: s.get("label", ""))[0]


def _src(s: dict | None) -> dict | None:
    if not s:
        return None
    return {"series_id": s["series_id"], "label": s.get("label", ""), "unit": s.get("unit", "")}


def resolve_zones(series: list[dict]) -> list[dict]:
    """Associe à chaque zone (champ `apartment` de series_meta) les séries
    qui l'intéressent : les deux compteurs EFM de facturation (réseau,
    solaire) et le compteur de contrôle (périmètre différent, informatif).

    Retourne aussi, pour chaque colonne, les candidats écartés (`ambigus`) :
    la page les affiche pour que la correspondance série -> colonne soit
    vérifiable à l'œil, et corrigeable via /admin, plutôt que devinée dans
    le dos de l'utilisateur.
    """
    zones: dict[str, list[dict]] = {}
    for s in _totals(series):
        apt = (s.get("apartment") or "").strip()
        if not apt:
            continue
        zones.setdefault(apt, []).append(s)

    out = []
    for apt in sorted(zones, key=_zone_sort_key):
        items = zones[apt]
        reseau_c = [s for s in items if s.get("resource_type") == "energie_reseau"]
        solaire_c = [s for s in items if s.get("resource_type") == "energie_solaire"]
        if not solaire_c:
            solaire_c = [
                s for s in items
                if s.get("resource_type") != "energie_reseau"
                and SOLAR_LABEL_RE.search(s.get("label", ""))
            ]
        solaire_ids = {s["series_id"] for s in solaire_c}
        controle_c = [
            s for s in items
            if s.get("resource_type") == "energie_consommee"
            and s["series_id"] not in solaire_ids
            and not CONTROLE_EXCLUDE_RE.search(s.get("label", ""))
        ]

        reseau, solaire, controle = _pick(reseau_c), _pick(solaire_c), _pick(controle_c)
        out.append({
            "zone": apt,
            "label": _zone_label(apt),
            "sources": {
                "reseau": _src(reseau),
                "solaire": _src(solaire),
                "controle": _src(controle),
            },
            "ambigus": {
                "reseau": [s["label"] for s in reseau_c if reseau and s["series_id"] != reseau["series_id"]],
                "solaire": [s["label"] for s in solaire_c if solaire and s["series_id"] != solaire["series_id"]],
                "controle": [s["label"] for s in controle_c if controle and s["series_id"] != controle["series_id"]],
            },
        })
    return out


def resolve_batiment(series: list[dict]) -> dict:
    """Compteurs d'immeuble. `production` est la production photovoltaïque
    totale. `reseau_import`/`reseau_export` sont conservés à titre indicatif
    mais NE servent pas au décompte : ce compteur (même UUID de contrôle que
    `Production` et `Batterie`) est posé au point de raccordement de
    l'onduleur, pas sur l'alimentation des zones — mesuré sur la fenêtre de
    données brutes, il enregistre 3,35 kWh d'import quand la somme des
    compteurs Grid des zones en enregistre 85,8. Les deux ne sont donc pas
    comparables, et l'injection réelle se déduit de
    `production - autoconsommation`."""
    globals_ = [s for s in series if not (s.get("apartment") or "").strip()]
    prod = _pick([s for s in _totals(globals_) if BATIMENT_PRODUCTION_RE.search(s.get("label", ""))])
    imp = _pick([s for s in _totals(globals_) if BATIMENT_RESEAU_RE.search(s.get("label", ""))])
    exp = _pick([
        s for s in globals_
        if s.get("state_name") == "totalNeg" and BATIMENT_RESEAU_RE.search(s.get("label", ""))
    ])
    return {"production": _src(prod), "reseau_import": _src(imp), "reseau_export": _src(exp)}


def _zone_sort_key(apt: str):
    m = re.search(r"\d+", apt)
    return (0, int(m.group()), apt) if m else (1, 0, apt)


# Les identifiants de zone sont normalisés en majuscules sans espace par
# classification.extract_apartment ("Rez Jardin" -> "REZJARDIN") : on refait
# ici le chemin inverse pour l'affichage, qui est lu par un propriétaire.
ZONE_LABEL_OVERRIDES = {"REZJARDIN": "Rez Jardin", "COMMUN": "Communs"}


def _zone_label(apt: str) -> str:
    if apt in ZONE_LABEL_OVERRIDES:
        return ZONE_LABEL_OVERRIDES[apt]
    m = re.fullmatch(r"(?i)APP(\d+)", apt)
    if m:
        return f"App {int(m.group(1))}"
    return apt.capitalize() if apt.isupper() else apt


# --------------------------------------------------------------------------
# Relevés et consommation d'une période
# --------------------------------------------------------------------------

def _reading_delta(conn, series_id: str, start_ts: int, end_ts: int,
                    now_ts: int) -> dict:
    """Consommation d'une série cumulative sur [start_ts, end_ts[ : relevé de
    fin - relevé de début, plus tout ce qui permet de juger sa fiabilité.

    `now_ts` sert uniquement à la détection des trous de collecte : sur un
    mois EN COURS, la borne de fin est dans le futur, et comparer le dernier
    relevé à cette borne signalerait à tort des semaines de données
    manquantes."""
    start = db.query_value_at(conn, series_id, start_ts)
    end = db.query_value_at(conn, series_id, end_ts - 1)

    res = {
        "kwh": None,
        "releve_debut": None,
        "releve_fin": None,
        "releve_debut_ts": None,
        "releve_fin_ts": None,
        "alertes": [],
    }
    if start is None:
        res["alertes"].append("pas de relevé avant le début de la période")
    else:
        res["releve_debut_ts"], res["releve_debut"] = start
    if end is None:
        res["alertes"].append("pas de relevé dans la période")
    else:
        res["releve_fin_ts"], res["releve_fin"] = end

    if start is None or end is None:
        return res

    # Un relevé de début trop antérieur à la borne signale un trou de
    # collecte : la consommation calculée déborde alors sur le mois
    # précédent et est surestimée.
    if start_ts - start[0] > 2 * 86400:
        days = (start_ts - start[0]) // 86400
        res["alertes"].append(f"relevé de début vieux de {days} j (trou de collecte)")
    ref_end = min(end_ts - 1, now_ts)
    if ref_end - end[0] > 2 * 86400:
        days = (ref_end - end[0]) // 86400
        res["alertes"].append(f"relevé de fin vieux de {days} j (trou de collecte)")

    res["kwh"] = end[1] - start[1]

    ruptures = _detect_ruptures(conn, series_id, start_ts, end_ts)
    if ruptures:
        res["ruptures"] = ruptures
        res["alertes"].append(
            f"{len(ruptures)} rupture(s) de compteur dans la période -> consommation non calculable"
        )
        res["kwh"] = None
    elif res["kwh"] < 0:
        res["alertes"].append("relevé de fin inférieur au relevé de début (compteur remis à zéro)")
        res["kwh"] = None
    return res


def _detect_ruptures(conn, series_id: str, start_ts: int, end_ts: int) -> list[dict]:
    """Jours où le compteur cumulatif a BAISSÉ de plus de
    RUPTURE_MIN_DROP_KWH : reset, remplacement de compteur, ou dépassement
    de capacité. Un décompte calculé à cheval sur un tel jour est faux, donc
    on préfère ne rien afficher plutôt qu'un chiffre plausible mais faux."""
    rows = db.query_daily_last(conn, series_id, start_ts, end_ts - 1)
    out = []
    for i in range(1, len(rows)):
        prev_value = rows[i - 1][1]
        day_ts, value = rows[i]
        if prev_value - value > RUPTURE_MIN_DROP_KWH:
            out.append({"date_ts": day_ts, "avant": prev_value, "apres": value})
    return out


# --------------------------------------------------------------------------
# Tarifs
# --------------------------------------------------------------------------

def tarif_for(tarifs: list[dict], start_ts: int, tz_name: str = TIMEZONE) -> dict | None:
    """Tarif applicable à un mois : le plus récent dont la date de prise
    d'effet est antérieure ou égale au DÉBUT de la période. Prendre le début
    (et pas la fin) garantit qu'une hausse de prix décidée en cours de mois
    ne s'applique pas rétroactivement à tout le mois."""
    day = datetime.fromtimestamp(start_ts, ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    applicable = [t for t in tarifs if t["valid_from"] <= day]
    return applicable[-1] if applicable else None


def montants(reseau_kwh: float | None, solaire_kwh: float | None,
              tarif: dict | None) -> dict:
    """Montants d'une ligne de décompte. Retourne des None (et pas des 0)
    quand la consommation ou le tarif manque : un montant à 0 CHF affiché
    faute de données se confond avec un montant à 0 CHF réellement dû."""
    if tarif is None or reseau_kwh is None or solaire_kwh is None:
        return {"ht": None, "tva": None, "ttc": None,
                "detail_reseau": None, "detail_solaire": None}
    detail_reseau = reseau_kwh * tarif["prix_reseau"]
    detail_solaire = solaire_kwh * tarif["prix_solaire"]
    ht = detail_reseau + detail_solaire
    tva = ht * tarif["taux_tva"] / 100.0
    return {
        "ht": ht,
        "tva": tva,
        "ttc": ht + tva,
        "detail_reseau": detail_reseau,
        "detail_solaire": detail_solaire,
    }


def taux(numerateur: float | None, denominateur: float | None) -> float | None:
    """Ratio en %, ou None si incalculable. Un dénominateur nul (aucune
    production en décembre, par exemple) donne None et pas 0 % : "pas de
    production du tout" et "production entièrement réinjectée" ne sont pas
    la même information."""
    if numerateur is None or not denominateur:
        return None
    return numerateur / denominateur * 100.0


# --------------------------------------------------------------------------
# Décompte complet
# --------------------------------------------------------------------------

def compute_decompte(conn, series: list[dict], periods: list[dict],
                      tarifs: list[dict], now_ts: int,
                      tz_name: str = TIMEZONE) -> dict:
    """Décompte complet : pour chaque zone et chaque mois, la part réseau et
    la part solaire autoconsommée, le taux d'autoproduction, les montants,
    et les alertes de fiabilité. Puis la synthèse d'immeuble."""
    zones = resolve_zones(series)
    batiment_src = resolve_batiment(series)

    for z in zones:
        z["periodes"] = {}
        for p in periods:
            z["periodes"][p["key"]] = _zone_period(conn, z, p, tarifs, now_ts, tz_name)

    batiment = {"sources": batiment_src, "periodes": {}}
    for p in periods:
        batiment["periodes"][p["key"]] = _batiment_period(conn, batiment_src, zones, p, now_ts)

    return {
        "timezone": tz_name,
        "periodes": periods,
        "zones": zones,
        "batiment": batiment,
        "tarifs": tarifs,
        "generated_at": now_ts,
    }


def _zone_period(conn, zone: dict, p: dict, tarifs: list[dict], now_ts: int,
                  tz_name: str) -> dict:
    src = zone["sources"]

    def delta(key):
        s = src[key]
        return _reading_delta(conn, s["series_id"], p["start"], p["end"], now_ts) if s else _absent()

    reseau, solaire, controle = delta("reseau"), delta("solaire"), delta("controle")
    r, s = reseau["kwh"], solaire["kwh"]
    total = (r + s) if (r is not None and s is not None) else None

    tarif = tarif_for(tarifs, p["start"], tz_name)
    m = montants(r, s, tarif)
    en_cours = p["end"] > now_ts

    alertes = []
    if en_cours:
        alertes.append("mois en cours, chiffres non définitifs")
    for nom, block in (("réseau", reseau), ("solaire", solaire)):
        for a in block["alertes"]:
            alertes.append(f"{nom} : {a}")

    return {
        "reseau": reseau,
        "solaire": solaire,
        "controle": controle,
        "total": total,
        "taux_autoproduction": taux(s, total),
        "en_cours": en_cours,
        "facturable": bool(total is not None and not en_cours),
        "tarif": tarif,
        "montants": m,
        "alertes": alertes,
    }


def _batiment_period(conn, src: dict, zones: list[dict], p: dict, now_ts: int) -> dict:
    """Synthèse d'immeuble d'un mois.

    L'autoconsommation totale est la SOMME des parts solaires des zones, et
    non une lecture de compteur : c'est la seule définition vérifiée (elle
    égale la sortie `selfConsumption` du bloc EFM du bâtiment). L'injection
    s'en déduit par `production - autoconsommation`, plutôt que de lire le
    `totalNeg` du compteur Réseau, qui est sur un autre périmètre (voir
    resolve_batiment)."""
    production = (
        _reading_delta(conn, src["production"]["series_id"], p["start"], p["end"], now_ts)
        if src["production"] else _absent()
    )

    autoconso = 0.0
    achat = 0.0
    conso_totale = 0.0
    complet = True
    for z in zones:
        e = z["periodes"][p["key"]]
        if e["solaire"]["kwh"] is None or e["reseau"]["kwh"] is None:
            complet = False
            continue
        autoconso += e["solaire"]["kwh"]
        achat += e["reseau"]["kwh"]
        conso_totale += e["total"]

    prod_kwh = production["kwh"]
    injection = (prod_kwh - autoconso) if (prod_kwh is not None and complet) else None

    return {
        "en_cours": p["end"] > now_ts,
        "production": production,
        "autoconsommation": autoconso if complet else None,
        "achat_reseau": achat if complet else None,
        "consommation_totale": conso_totale if complet else None,
        "injection": injection,
        # Deux taux volontairement distincts -- voir le docstring du module.
        "taux_autoproduction": taux(autoconso if complet else None,
                                     conso_totale if complet else None),
        "taux_autoconsommation": taux(autoconso if complet else None, prod_kwh),
        "zones_incompletes": not complet,
    }


def _absent() -> dict:
    return {
        "kwh": None, "releve_debut": None, "releve_fin": None,
        "releve_debut_ts": None, "releve_fin_ts": None,
        "alertes": ["aucune série trouvée pour cette colonne"],
    }


def available_range(conn, zones: list[dict], batiment: dict) -> tuple[int, int] | None:
    """Premier et dernier relevé disponibles, tous compteurs du décompte
    confondus -- sert à proposer par défaut la liste des mois qui ont
    réellement des données. Interroge série par série (index (series_id, ts))
    plutôt qu'un MIN/MAX global sur `readings_hourly`, qui scannerait les
    centaines de milliers de lignes issues du backfill Statistics."""
    ids = [s["series_id"] for z in zones for s in z["sources"].values() if s]
    ids += [s["series_id"] for s in batiment.values() if s]
    firsts, lasts = [], []
    for sid in set(ids):
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM readings_hourly WHERE series_id = ?", (sid,)
        ).fetchone()
        if row and row[0] is not None:
            firsts.append(row[0])
            lasts.append(row[1])
        row = conn.execute(
            "SELECT MAX(ts) FROM readings WHERE series_id = ? AND value IS NOT NULL", (sid,)
        ).fetchone()
        if row and row[0] is not None:
            lasts.append(row[0])
    if not firsts:
        return None
    return min(firsts), max(lasts)
