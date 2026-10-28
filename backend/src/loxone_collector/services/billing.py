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
from datetime import date, datetime
from zoneinfo import ZoneInfo

from loxone_collector import db
from loxone_collector.services import repartition

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
#
# "chaleur", "cenergie" et "nrmachine" viennent de MS-PPE-Sequoia, dont les
# compteurs thermiques sont nommés "compteur chaleur kWh" (et non
# "chauffage") et rangés par classification.py en "energie_consommee", faute
# de type "chaleur". Sans ces exclusions, le compteur de contrôle d'un lot
# était le compteur de CHALEUR -- "APP 35 compteur chaleur kWh [50]" passe
# avant "Appartement 35" dans l'ordre alphabétique de _pick (majuscules
# avant minuscules) -- soit des kWh thermiques affichés dans une colonne
# électrique. "CEnergieApp35"/"CNrMachineApp35" sont, eux, la part de
# buanderie commune du lot (catégorie Loxone "Lessive"), déjà comprise dans
# le compteur des communs : les additionner double-compterait.
#
# "c[ch]aleur" et non "chaleur" : un point de l'App 34 est réellement nommé
# "APP 34 compteur Ccaleur volume [53]" dans Loxone Config (coquille côté
# installateur). Sans la tolérance, ce compteur de volume de chauffage
# devenait le compteur de contrôle électrique de l'App 34.
CONTROLE_EXCLUDE_RE = re.compile(
    r"(?i)(chauffage|c[ch]aleur|\bgrid\b|\bsol\b|solaire|eau[ _-]?chaude|cenergie|nrmachine)"
)

# Repli pour identifier un compteur solaire mal classé automatiquement :
# "Communs Sol" est rangé en "énergie consommée" par classification.py, faute
# de mot-clé reconnu ("Sol" abrégé). On ne corrige pas la règle globale
# (risque de capturer "chauffage au sol"), on fait le repli ici, localement.
SOLAR_LABEL_RE = re.compile(r"(?i)(\bsol\b|solaire|solar|\bpv\b)")

# « Compteur PV (total) » et « Compteur Intro » : convention de La Calbasse
# (CLAUDE.md, 2026-10-26). Le libellé exact pour le PV : Horizon a des
# « Compteur PV PPE / garages / Production » qui ne sont pas la production.
BATIMENT_PRODUCTION_RE = re.compile(r"(?i)^(production\b|compteur pv \(total\)$)")
BATIMENT_RESEAU_RE = re.compile(r"(?i)^(r[ée]seau|compteur intro)\b")
# PAC de La Calbasse : compteur de bâtiment (sans zone), hors lots et hors
# Communs, avec ses parts réseau / solaire. Elle devient une ligne du
# décompte, comme les Communs, sinon ses kWh ne sont facturés nulle part et
# le bilan ne boucle pas. « PAC déjà mesuré » de Sequoia, compris dans les
# Communs, n'est pas concerné.
PAC_BATIMENT_RE = re.compile(r"(?i)^pac( r[ée]seau| solaire)? \(total\)$")
ZONE_PAC = "PAC"
# Réserves affichées sous le décompte (page et classeur) quand une zone
# repose sur un compteur dont la place dans l'installation reste à
# confirmer. La PAC de La Calbasse serait un sous-compteur du Commun
# (utilisateur, 2026-10-27), alors que les données disent le contraire
# (octobre 2026 : PAC 1 328 kWh, Commun 171 kWh ; bilan à +2,6 % avec la
# PAC comptée à part). Elle reste une ligne à part, sous réserve.
RESERVES_ZONES = {
    ZONE_PAC: (
        "PAC : sa consommation est une ligne à part, en plus des Communs. "
        "Ces chiffres sont sous réserve de l'installation physique : si le "
        "compteur de la PAC est un sous-compteur des Communs, ses kWh sont "
        "déjà dans ceux des Communs et le total les compte deux fois."
    ),
}
# À Sequoia, « Production Solaire » s'arrête le 2026-09-09. Le compteur
# qui avance ensuite s'appelle exactement « Solaire (total) » (depuis le
# 2026-09-24, index différent : ce n'est pas la suite du précédent).
# « Solaire & Batterie » (Arlopi) ne doit pas prendre sa place.
# Sur une fenêtre passée, production_couvrante choisit celui qui a des
# heures dans la fenêtre — sinon le bilan n'a aucune production et le
# décompte reste sur les séries Grid/Solaire.
SOLAIRE_BATIMENT_RE = re.compile(r"(?i)^solaire \(total\)$")
# Précision des parts réseau/solaire recalculées : la même que
# scripts/export_appartement.py, pour que réseau + solaire = consommation
# à l'affichage près.
KWH_DECIMALES = 2


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


def parse_jour(value: str, tz_name: str = TIMEZONE) -> int:
    """« 2026-01-15 » → minuit local de ce jour (epoch)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError(f"date invalide: {value!r} (attendu ex: 2026-01-15)")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"date invalide: {value!r} (attendu ex: 2026-01-15)") from None
    return int(datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz_name)).timestamp())


def _jour_court(ts: int, tz_name: str = TIMEZONE) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%d.%m")


def periodes_plage(start_ts: int, end_ts: int, tz_name: str = TIMEZONE) -> list[dict]:
    """Mois calendaires de [start_ts, end_ts[, le premier et le dernier
    rognés aux bornes (départ d'un locataire le 15 : « Octobre 2026
    (01.10 → 15.10) »). Chaque morceau garde la clé de son mois et se
    calcule comme un mois entier, avec son propre verdict de répartition."""
    out = []
    for p in periods_covering(start_ts, end_ts - 1, tz_name):
        start, end = max(p["start"], start_ts), min(p["end"], end_ts)
        if start >= end:
            continue
        if (start, end) != (p["start"], p["end"]):
            p = dict(
                p, start=start, end=end, partiel=True,
                label=f"{p['label']} ({_jour_court(start, tz_name)} → {_jour_court(end - 1, tz_name)})",
            )
        out.append(p)
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
        if not apt and PAC_BATIMENT_RE.match(s.get("label", "")):
            apt = ZONE_PAC
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
    totals = _totals(globals_)
    prod = _pick([s for s in totals if SOLAIRE_BATIMENT_RE.search(s.get("label", ""))])
    if not prod:
        prod = _pick([s for s in totals if BATIMENT_PRODUCTION_RE.search(s.get("label", ""))])
    imp = _pick([s for s in _totals(globals_) if BATIMENT_RESEAU_RE.search(s.get("label", ""))])
    exp = _pick([
        s for s in globals_
        if s.get("state_name") == "totalNeg" and BATIMENT_RESEAU_RE.search(s.get("label", ""))
    ])
    return {"production": _src(prod), "reseau_import": _src(imp), "reseau_export": _src(exp)}


def production_couvrante(conn, series: list[dict], start_ts: int, end_ts: int,
                         statistics: bool = False) -> dict | None:
    """Compteur de production qui a des relevés horaires sur [start, end).

    resolve_batiment préfère « Solaire (total) », le compteur vivant. Sur
    Sequoia ce compteur n'a pas d'historique avant le 2026-09-24 : un
    décompte d'août n'y trouve aucune heure, le bilan se déclare
    incalculable, et les parts restent les séries fausses du Miniserver.
    Ici on garde le candidat qui couvre la fenêtre. À égalité d'heures,
    « Solaire (total) » l'emporte.
    """
    globals_ = [s for s in series if not (s.get("apartment") or "").strip()]
    candidats = [
        s for s in _totals(globals_)
        if SOLAIRE_BATIMENT_RE.search(s.get("label", ""))
        or BATIMENT_PRODUCTION_RE.search(s.get("label", ""))
    ]
    if not candidats:
        return None
    scored = []
    for s in candidats:
        if statistics:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ts / 3600) FROM statistic_samples "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (s["series_id"], start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM readings_hourly WHERE series_id = ? AND ts >= ? AND ts < ?",
                (s["series_id"], start_ts, end_ts),
            ).fetchone()
        n = row[0] if row else 0
        live = 1 if SOLAIRE_BATIMENT_RE.search(s.get("label", "")) else 0
        scored.append((n, live, s))
    n, _, best = max(scored)
    if n == 0:
        return None
    return _src(best)


def productions_sur_fenetre(conn, series: list[dict], start_ts: int, end_ts: int,
                            statistics: bool = False) -> list[dict]:
    """Compteurs de production qui ont des relevés sur [start, end), du plus
    ancien au plus récent.

    À Sequoia, « Production Solaire » s'arrête le 9 septembre 2026 et
    « Solaire (total) » commence le 10 : aucun des deux ne couvre septembre
    seul. Les deux restent, dans l'ordre, pour que les heures se relaient
    au lieu d'abandonner le début du mois.
    """
    globals_ = [s for s in series if not (s.get("apartment") or "").strip()]
    candidats = [
        s for s in _totals(globals_)
        if SOLAIRE_BATIMENT_RE.search(s.get("label", ""))
        or BATIMENT_PRODUCTION_RE.search(s.get("label", ""))
    ]
    scored = []
    for s in candidats:
        if statistics:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM statistic_samples "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (s["series_id"], start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM readings_hourly "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (s["series_id"], start_ts, end_ts),
            ).fetchone()
        n = row[0] if row else 0
        if n:
            scored.append((row[1], s))
    scored.sort(key=lambda item: item[0])
    return [_src(s) for _, s in scored]


def _zone_sort_key(apt: str):
    m = re.search(r"\d+", apt)
    return (0, int(m.group()), apt) if m else (1, 0, apt)


# Les identifiants de zone sont normalisés en majuscules sans espace par
# classification.extract_apartment ("Rez Jardin" -> "REZJARDIN") : on refait
# ici le chemin inverse pour l'affichage, qui est lu par un propriétaire.
ZONE_LABEL_OVERRIDES = {"REZJARDIN": "Rez Jardin", "COMMUN": "Communs",
                        "SALLESCOMMUNES": "Salles Communes",
                        "WALLBOX": "Wallbox", ZONE_PAC: "PAC"}


def _zone_label(apt: str) -> str:
    if apt in ZONE_LABEL_OVERRIDES:
        return ZONE_LABEL_OVERRIDES[apt]
    m = re.fullmatch(r"(?i)APP(\d+)", apt)
    if m:
        return f"App {int(m.group(1))}"
    m = re.fullmatch(r"(?i)BUREAU(\d+)", apt)
    if m:
        return f"Bureau {int(m.group(1))}"
    return apt.capitalize() if apt.isupper() else apt


# --------------------------------------------------------------------------
# Relevés et consommation d'une période
# --------------------------------------------------------------------------

RUPTURE_MIN_DROP_EAU_M3 = 0.05


def min_drop_for_resource_type(resource_type: str | None) -> float:
    """Seuil de détection de rupture adapté à l'échelle du type de
    ressource : un compteur d'eau (m^3) a des incréments bien plus petits
    qu'un compteur d'énergie (kWh) -- reprend la distinction déjà encodée
    par poste de facturation dans scripts/export_appartement.py::RULES.
    Utilisé par la route générique /api/series/<id>/range (dashboard),
    qui sert aussi bien des séries eau que chauffage/énergie."""
    if resource_type and resource_type.startswith("eau_"):
        return RUPTURE_MIN_DROP_EAU_M3
    return RUPTURE_MIN_DROP_KWH


def reading_delta(conn, series_id: str, start_ts: int, end_ts: int,
                   now_ts: int, min_drop: float = RUPTURE_MIN_DROP_KWH,
                   statistics: bool = False) -> dict:
    """Consommation d'une série cumulative sur [start_ts, end_ts[ : relevé de
    fin - relevé de début, plus tout ce qui permet de juger sa fiabilité.

    `now_ts` sert uniquement à la détection des trous de collecte : sur un
    mois EN COURS, la borne de fin est dans le futur, et comparer le dernier
    relevé à cette borne signalerait à tort des semaines de données
    manquantes.

    `min_drop` est le seuil de détection d'une rupture de compteur, dans
    l'unité de la série : la valeur par défaut est calibrée pour des kWh, un
    compteur d'eau en m^3 demande un seuil plus fin (voir
    scripts/export_appartement.py).

    `statistics=True` : l'index vient des Statistics du Miniserver
    (`statistic_samples`), pas du collecteur. C'est le mode des décomptes.
    """
    if statistics:
        start = db.query_statistic_at(conn, series_id, start_ts)
        end = db.query_statistic_at(conn, series_id, end_ts - 1)
    else:
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

    ruptures = _detect_ruptures(conn, series_id, start_ts, end_ts, min_drop, statistics)
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


# scripts/export_appartement.py appelle encore ce nom.
_reading_delta = reading_delta


def delta_compteurs_successifs(conn, sources: list[dict], start_ts: int, end_ts: int,
                               now_ts: int, statistics: bool = False) -> dict:
    """Consommation quand plusieurs compteurs se relaient sur la période.

    Chaque compteur est lu jusqu'au premier relevé du suivant : les index
    ne sont pas comparables d'un compteur à l'autre (changement de
    « Production Solaire » vers « Solaire » à Sequoia), on additionne les
    écarts. Un seul compteur reste un relevé de fin moins relevé de début.
    """
    if len(sources) <= 1:
        src = sources[0] if sources else None
        if not src:
            return _absent()
        return reading_delta(conn, src["series_id"], start_ts, end_ts, now_ts,
                             statistics=statistics)

    bornes = []
    for src in sources:
        if statistics:
            row = conn.execute(
                "SELECT MIN(ts) FROM statistic_samples "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (src["series_id"], start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MIN(ts) FROM readings_hourly "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (src["series_id"], start_ts, end_ts),
            ).fetchone()
        if row and row[0] is not None:
            bornes.append((int(row[0]), src))
    bornes.sort(key=lambda item: item[0])
    if len(bornes) <= 1:
        src = bornes[0][1] if bornes else (sources[0] if sources else None)
        if not src:
            return _absent()
        return reading_delta(conn, src["series_id"], start_ts, end_ts, now_ts,
                             statistics=statistics)

    total = 0.0
    got = False
    alertes = []
    debut_ts = debut = fin_ts = fin = None
    for i, (first, src) in enumerate(bornes):
        seg_start = start_ts if i == 0 else first
        seg_end = bornes[i + 1][0] if i + 1 < len(bornes) else end_ts
        if seg_start >= seg_end:
            continue
        d = reading_delta(conn, src["series_id"], seg_start, seg_end, now_ts,
                          statistics=statistics)
        if i == 0:
            debut_ts, debut = d["releve_debut_ts"], d["releve_debut"]
        if d["releve_fin_ts"] is not None:
            fin_ts, fin = d["releve_fin_ts"], d["releve_fin"]
        if d["kwh"] is not None:
            total += d["kwh"]
            got = True
        alertes.extend(d["alertes"])
    return {
        "kwh": total if got else None,
        "releve_debut": debut,
        "releve_fin": fin,
        "releve_debut_ts": debut_ts,
        "releve_fin_ts": fin_ts,
        "alertes": alertes,
    }


def _detect_ruptures(conn, series_id: str, start_ts: int, end_ts: int,
                     min_drop: float = RUPTURE_MIN_DROP_KWH,
                     statistics: bool = False) -> list[dict]:
    """Jours où le compteur cumulatif a BAISSÉ de plus de `min_drop` (par
    défaut RUPTURE_MIN_DROP_KWH) : reset, remplacement de compteur, ou
    dépassement de capacité. Un décompte calculé à cheval sur un tel jour est
    faux, donc on préfère ne rien afficher plutôt qu'un chiffre plausible
    mais faux."""
    if statistics:
        rows = conn.execute(
            """
            SELECT CAST(ts / 86400 AS INTEGER) * 86400 AS day, MAX(value)
              FROM statistic_samples
             WHERE series_id = ? AND ts BETWEEN ? AND ?
             GROUP BY day ORDER BY day
            """,
            (series_id, start_ts, end_ts - 1),
        ).fetchall()
    else:
        rows = db.query_daily_last(conn, series_id, start_ts, end_ts - 1)
    out = []
    for i in range(1, len(rows)):
        prev_value = rows[i - 1][1]
        day_ts, value = rows[i]
        if prev_value - value > min_drop:
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
                      tz_name: str = TIMEZONE,
                      fenetre: tuple[int, int] | None = None) -> dict:
    """Décompte complet : pour chaque zone et chaque mois, la part réseau et
    la part solaire autoconsommée, le taux d'autoproduction, les montants,
    et les alertes de fiabilité. Puis la synthèse d'immeuble.

    La source des parts réseau/solaire vient de repartition.evaluer_site :
    séries du Miniserver quand elles sont les seules utilisables (Arlopi),
    répartition recalculée sur le compteur du lot quand elles sont
    incohérentes (Sequoia). Le total facturé reste le relevé de compteur.

    `fenetre` : bornes sur lesquelles juger le bilan du bâtiment et choisir
    les compteurs de production. Par défaut, celles de `periods`. Un export
    passe toute la plage des données, pour qu'un mois isolé soit jugé comme
    sur la page (un bilan sur un seul mois peut boucler par hasard).
    """

    zones = resolve_zones(series)
    batiment_src = resolve_batiment(series)
    if periods:
        fenetre_debut, fenetre_fin = fenetre or (periods[0]["start"], periods[-1]["end"])
        actives = productions_sur_fenetre(
            conn, series, fenetre_debut, fenetre_fin, statistics=True,
        )
        if actives:
            batiment_src["productions"] = actives
        couvrant = production_couvrante(
            conn, series, fenetre_debut, fenetre_fin, statistics=True,
        )
        if couvrant:
            batiment_src["production"] = couvrant

    # Un verdict par mois : à Sequoia, le bloc « Répartition Solaire » est
    # faux jusqu'au 09.09.2026 et juste ensuite. Jugé sur tout l'historique,
    # les mois d'avant la correction condamnaient aussi les mois suivants.
    # La comparabilité des compteurs de bâtiment, elle, reste jugée sur
    # toute la fenêtre (voir repartition.evaluer_site, `bilan_reference`).
    evaluations = {}
    if periods:
        bilan_fenetre = repartition.evaluer_site(
            conn, zones, batiment_src, fenetre_debut, fenetre_fin, statistics=True,
        )["bilan"]
        evaluations = {
            p["key"]: _evaluer_mois(conn, series, zones, p, bilan_fenetre) for p in periods
        }

    for z in zones:
        z["periodes"] = {}
        for p in periods:
            evaluation = evaluations[p["key"]]
            if evaluation["source_recommandee"] == "calculee":
                z["periodes"][p["key"]] = _zone_period_calculee(
                    conn, z, p, tarifs, now_ts, tz_name, evaluation,
                )
            else:
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
        "repartition": {k: _repartition_payload(ev) for k, ev in evaluations.items()},
        "reserves": [RESERVES_ZONES[z["zone"]] for z in zones if z["zone"] in RESERVES_ZONES],
    }


def cumul_periodes(payload: dict, cle: str, label: str, start: int, end: int) -> dict:
    """Décompte de plusieurs mois (`payload`, calculé sur `periodes_plage`)
    ramené à une seule période `cle` : les kWh de chaque mois additionnés.
    Un mois sans relevé rend le total de la zone inconnu, jamais un total
    silencieusement incomplet. Le détail par mois reste dans
    `payload["detail"]`."""
    periods = payload["periodes"]
    labels = [p["label"] for p in periods]

    zones = []
    for z in payload["zones"]:
        entries = [z["periodes"][p["key"]] for p in periods]
        zones.append({**z, "periodes": {cle: _cumul_zone(entries, labels)}})

    bat_src = payload["batiment"]["sources"]
    bat_mois = [payload["batiment"]["periodes"][p["key"]] for p in periods]
    reps = [payload["repartition"].get(p["key"]) or _repartition_payload(None) for p in periods]
    sources = {r["source"] for r in reps}
    return {
        **payload,
        "periodes": [{"key": cle, "label": label, "start": start, "end": end}],
        "zones": zones,
        "batiment": {"sources": bat_src, "periodes": {cle: _cumul_batiment(bat_mois, labels)}},
        "repartition": {cle: {
            "source": sources.pop() if len(sources) == 1 else "mixte",
            "confiance_verifiee": all(r["confiance_verifiee"] for r in reps),
            "raisons": [f"{lbl} : {r_}" for lbl, r in zip(labels, reps) for r_ in r["raisons"]],
        }},
        "detail": {
            "periodes": periods,
            "zones": payload["zones"],
            "repartition": payload["repartition"],
        },
    }


def _somme_ou_rien(values: list) -> float | None:
    return None if any(v is None for v in values) else sum(values)


def _cumul_lecture(blocks: list[dict], labels: list[str]) -> dict:
    return {
        "kwh": _somme_ou_rien([b["kwh"] for b in blocks]),
        "releve_debut": blocks[0].get("releve_debut"),
        "releve_debut_ts": blocks[0].get("releve_debut_ts"),
        "releve_fin": blocks[-1].get("releve_fin"),
        "releve_fin_ts": blocks[-1].get("releve_fin_ts"),
        "alertes": [f"{lbl} : {a}" for lbl, b in zip(labels, blocks) for a in b.get("alertes") or []],
    }


def _cumul_zone(entries: list[dict], labels: list[str]) -> dict:
    reseau = _cumul_lecture([e["reseau"] for e in entries], labels)
    solaire = _cumul_lecture([e["solaire"] for e in entries], labels)
    total = _somme_ou_rien([e["total"] for e in entries])
    tarif = entries[0]["tarif"]
    return {
        "reseau": reseau,
        "solaire": solaire,
        "controle": _cumul_lecture([e["controle"] for e in entries], labels),
        "total": total,
        "taux_autoproduction": taux(solaire["kwh"], total),
        "en_cours": any(e["en_cours"] for e in entries),
        "facturable": all(e["facturable"] for e in entries),
        "tarif": tarif,
        "tarifs_differents": any(e["tarif"] != tarif for e in entries),
        "montants": montants(reseau["kwh"], solaire["kwh"], tarif),
        "alertes": [f"{lbl} : {a}" for lbl, e in zip(labels, entries) for a in e["alertes"]],
    }


def _cumul_batiment(mois: list[dict], labels: list[str]) -> dict:
    def somme_connue(key):
        vals = [m[key] for m in mois if m[key] is not None]
        return sum(vals) if vals else None

    production = _cumul_lecture([m["production"] for m in mois], labels)
    autoconso = somme_connue("autoconsommation")
    conso = somme_connue("consommation_totale")
    prod = production["kwh"]
    return {
        "en_cours": any(m["en_cours"] for m in mois),
        "production": production,
        "autoconsommation": autoconso,
        "achat_reseau": somme_connue("achat_reseau"),
        "consommation_totale": conso,
        "injection": (prod - autoconso) if (prod is not None and autoconso is not None) else None,
        "taux_autoproduction": taux(autoconso, conso),
        "taux_autoconsommation": taux(autoconso, prod),
        "zones_incompletes": any(m["zones_incompletes"] for m in mois),
    }


def _evaluer_mois(conn, series: list[dict], zones: list[dict], p: dict,
                  bilan_fenetre: dict) -> dict:
    """`repartition.evaluer_site` sur le seul mois `p`, avec le compteur de
    production qui couvre ce mois-là. `bilan_fenetre` décide si le bilan
    du bâtiment est exploitable."""

    batiment = resolve_batiment(series)
    actives = productions_sur_fenetre(conn, series, p["start"], p["end"], statistics=True)
    if actives:
        batiment["productions"] = actives
    couvrant = production_couvrante(conn, series, p["start"], p["end"], statistics=True)
    if couvrant:
        batiment["production"] = couvrant
    return repartition.evaluer_site(
        conn, zones, batiment, p["start"], p["end"], statistics=True,
        bilan_reference=bilan_fenetre,
    )


def repartition_du_mois(payload: dict, period_key: str) -> dict:
    """Source des parts réseau/solaire du mois (`_repartition_payload`)."""
    return (payload.get("repartition") or {}).get(period_key) or _repartition_payload(None)


# Wallbox de Sequoia : le compteur de facturation (« Wallbox Energizähler »)
# démarre le 04.03.2026 à 20 h, à l'index 0. Avant, il n'y a rien à
# facturer : 0 kWh, pas un relevé manquant (règle reprise de l'ancien
# scripts/export_appartement.py).
WALLBOX_LABEL_RE = re.compile(r"(?i)wallbox\s+energiz")
WALLBOX_VALID_FROM = int(datetime(2026, 3, 4, 20, 0, tzinfo=ZoneInfo(TIMEZONE)).timestamp())


def _lecture_zone(conn, src: dict, p: dict, now_ts: int) -> dict:
    """Relevé d'un compteur de zone sur la période `p`."""
    start = p["start"]
    if WALLBOX_LABEL_RE.search(src.get("label") or ""):
        if p["end"] <= WALLBOX_VALID_FROM:
            return {
                "kwh": 0.0, "releve_debut": None, "releve_fin": None,
                "releve_debut_ts": None, "releve_fin_ts": None,
                "alertes": [],
            }
        start = max(start, WALLBOX_VALID_FROM)
    return reading_delta(conn, src["series_id"], start, p["end"], now_ts, statistics=True)


def _zone_period(conn, zone: dict, p: dict, tarifs: list[dict], now_ts: int,
                  tz_name: str) -> dict:
    src = zone["sources"]

    def delta(key):
        s = src[key]
        return _lecture_zone(conn, s, p, now_ts) if s else _absent()

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
    prods = [s for s in (src.get("productions") or []) if s]
    if len(prods) > 1:
        production = delta_compteurs_successifs(
            conn, prods, p["start"], p["end"], now_ts, statistics=True,
        )
    elif src.get("production"):
        production = reading_delta(
            conn, src["production"]["series_id"], p["start"], p["end"], now_ts,
            statistics=True,
        )
    else:
        production = _absent()

    autoconso = 0.0
    achat = 0.0
    conso_totale = 0.0
    complet = True
    conso_connue = False
    for z in zones:
        e = z["periodes"][p["key"]]
        if e["solaire"]["kwh"] is None or e["reseau"]["kwh"] is None:
            complet = False
            continue
        conso_connue = True
        autoconso += e["solaire"]["kwh"]
        achat += e["reseau"]["kwh"]
        conso_totale += e["total"]

    prod_kwh = production["kwh"]
    # Même règle que la consommation : les lots sans relevé sont omis, pas
    # un motif pour vider le mois. L'injection est alors production moins
    # cette autoconsommation connue (les lots manquants gonflent la part
    # injectée ; la page liste déjà ces relevés).
    injection = (prod_kwh - autoconso) if (prod_kwh is not None and conso_connue) else None

    return {
        "en_cours": p["end"] > now_ts,
        "production": production,
        "autoconsommation": autoconso if conso_connue else None,
        "achat_reseau": achat if conso_connue else None,
        "consommation_totale": conso_totale if conso_connue else None,
        "injection": injection,
        # Deux taux volontairement distincts -- voir le docstring du module.
        "taux_autoproduction": taux(autoconso if conso_connue else None,
                                     conso_totale if conso_connue else None),
        "taux_autoconsommation": taux(autoconso if conso_connue else None, prod_kwh),
        "zones_incompletes": not complet,
    }


def _repartition_payload(evaluation: dict | None) -> dict:
    """Ce que la page peut afficher sans relire l'audit : d'où viennent les
    parts réseau/solaire, et pourquoi."""
    if not evaluation:
        return {"source": "loxone", "confiance_verifiee": False, "raisons": []}
    return {
        "source": evaluation["source_recommandee"],
        "confiance_verifiee": evaluation["confiance_verifiee"],
        "raisons": list(evaluation["audit"].get("raisons") or []),
    }


def _lecture_calculee(kwh: float | None) -> dict:
    """Part réseau ou solaire issue de la répartition, pas d'un relevé.
    Pas d'alerte ici : une alerte sur ces blocs ferait croire, sur la page,
    qu'un compteur n'a pas de relevé."""
    return {
        "kwh": kwh, "releve_debut": None, "releve_fin": None,
        "releve_debut_ts": None, "releve_fin_ts": None, "alertes": [],
    }


def _zone_period_calculee(conn, zone: dict, p: dict, tarifs: list[dict],
                          now_ts: int, tz_name: str, evaluation: dict) -> dict:
    """Même forme que `_zone_period`, mais réseau et solaire sont la part
    du relevé du lot. Leur somme est ce relevé, arrondi à KWH_DECIMALES.
    """

    src = zone["sources"]
    controle = _lecture_zone(conn, src["controle"], p, now_ts) if src.get("controle") else _absent()
    total = controle["kwh"]
    en_cours = p["end"] > now_ts
    alertes = []
    if en_cours:
        alertes.append("mois en cours, chiffres non définitifs")
    if not src.get("controle"):
        alertes.append("compteur du lot introuvable : répartition impossible")
        reseau = solaire = _lecture_calculee(None)
    else:
        for a in controle["alertes"]:
            alertes.append(f"compteur : {a}")
        split = repartition.split_zone(
            conn, src["controle"]["series_id"],
            evaluation["fraction"]["fraction"], p["start"], p["end"],
            statistics=True,
        )
        base = split["total"] - split["non_reparti"]
        part = (split["solaire"] / base) if base > 0 else None
        if total is not None and part is not None:
            solaire_kwh = round(total * part, KWH_DECIMALES)
            total = round(total, KWH_DECIMALES)
            reseau_kwh = total - solaire_kwh
        elif total is not None and base <= 0 and total <= 0:
            solaire_kwh, reseau_kwh = 0.0, 0.0
        else:
            solaire_kwh = reseau_kwh = None
            if total is not None and total > 0:
                alertes.append("part solaire incalculable sur cette période")
        reseau = _lecture_calculee(reseau_kwh)
        solaire = _lecture_calculee(solaire_kwh)

    r, s = reseau["kwh"], solaire["kwh"]
    parts_ok = r is not None and s is not None
    tarif = tarif_for(tarifs, p["start"], tz_name)
    return {
        "reseau": reseau,
        "solaire": solaire,
        "controle": controle,
        "total": total,
        "taux_autoproduction": taux(s, total) if parts_ok else None,
        "en_cours": en_cours,
        "facturable": bool(parts_ok and not en_cours),
        "tarif": tarif,
        "montants": montants(r, s, tarif),
        "alertes": alertes,
    }


def _absent() -> dict:
    return {
        "kwh": None, "releve_debut": None, "releve_fin": None,
        "releve_debut_ts": None, "releve_fin_ts": None,
        "alertes": ["aucune série trouvée pour cette colonne"],
    }


# --------------------------------------------------------------------------
# Postes hors électricité : chauffage, eau chaude, buanderie
# --------------------------------------------------------------------------

# Chauffage : seule l'énergie thermique (kWh) est décomptée, jamais le
# volume circulé. Sequoia nomme ses compteurs « APP 01 compteur chaleur [5] »
# et « APP 01 compteur chaleur volume [5] » (« Ccaleur » à l'App 34) ;
# Arlopi « Chauffage App 1 », La Calbasse « APP35 Chauffage ».
CHAUFFAGE_RE = re.compile(r"(?i)(chauffage|c[ch]aleur)")
VOLUME_RE = re.compile(r"(?i)\bvolume\b")
# Buanderie de Sequoia (catégorie Loxone « Lessive ») : CEnergieAppXX est
# l'électricité de la buanderie attribuée au lot par badge, CNrMachineAppXX
# son nombre de cycles.
BUANDERIE_KWH_RE = re.compile(r"(?i)cenergie")
BUANDERIE_CYCLES_RE = re.compile(r"(?i)cnrmachine")
# APP100 naît des points « CEnergieApp100 » : ce n'est pas un logement.
ZONES_FANTOMES = {"APP100"}
# Buanderie livrée le 17.07.2026. Avant, CEnergie incrémentait tous les
# lots (~37 kWh) avec 0 cycle.
BUANDERIE_VALID_FROM = int(datetime(2026, 7, 17, tzinfo=ZoneInfo(TIMEZONE)).timestamp())
RUPTURE_MIN_DROP_CYCLES = 0.9

POSTES = ("chauffage", "eau_chaude", "buanderie", "buanderie_cycles")
_POSTE_MIN_DROP = {
    "chauffage": RUPTURE_MIN_DROP_KWH,
    "eau_chaude": RUPTURE_MIN_DROP_EAU_M3,
    "buanderie": RUPTURE_MIN_DROP_KWH,
    "buanderie_cycles": RUPTURE_MIN_DROP_CYCLES,
}


def _role_poste(s: dict) -> str | None:
    label = s.get("label") or ""
    if BUANDERIE_CYCLES_RE.search(label):
        return "buanderie_cycles"
    if BUANDERIE_KWH_RE.search(label):
        return "buanderie"
    if CHAUFFAGE_RE.search(label):
        if VOLUME_RE.search(label):
            return None
        if s.get("resource_type") in ("energie_reseau", "energie_solaire"):
            return None
        return "chauffage"
    if s.get("resource_type") == "eau_chaude":
        return "eau_chaude"
    return None


def resolve_postes(series: list[dict]) -> list[dict]:
    """Compteurs de chauffage (énergie), d'eau chaude et de buanderie de
    chaque zone. Une zone peut avoir plusieurs compteurs d'un même poste
    (deux compteurs de chaleur aux Communs de Sequoia, deux au Commerce
    d'Arlopi) : tous sont gardés. Les compteurs sans zone (« Charge
    Chauffage », « Boiler ecs ») sont des compteurs d'immeuble, hors
    décompte."""
    zones: dict[str, dict[str, list[dict]]] = {}
    for s in _totals(series):
        apt = (s.get("apartment") or "").strip()
        if not apt or apt.upper() in ZONES_FANTOMES:
            continue
        role = _role_poste(s)
        if role is None:
            continue
        zones.setdefault(apt, {r: [] for r in POSTES})[role].append(_src(s))
    out = []
    for apt in sorted(zones, key=_zone_sort_key):
        compteurs = {r: sorted(v, key=lambda c: c["label"]) for r, v in zones[apt].items()}
        out.append({"zone": apt, "label": _zone_label(apt), "compteurs": compteurs})
    return out


def compute_postes(conn, postes: list[dict], p: dict, now_ts: int) -> dict:
    """Relevés du mois `p` pour le chauffage, l'eau chaude et la buanderie.
    Une ligne par compteur pour le chauffage et l'eau chaude, une ligne
    par zone pour la buanderie (énergie et cycles). Même méthode que
    l'électricité : index de fin − index de début, Statistics du
    Miniserver."""
    en_cours = p["end"] > now_ts
    buanderie_ouverte = p["end"] > BUANDERIE_VALID_FROM
    buanderie_start = max(p["start"], BUANDERIE_VALID_FROM)

    def lire(src, role, start):
        return reading_delta(conn, src["series_id"], start, p["end"], now_ts,
                             min_drop=_POSTE_MIN_DROP[role], statistics=True)

    out = {
        "chauffage": [], "eau_chaude": [], "buanderie": [],
        "en_cours": en_cours,
        "buanderie_depuis": buanderie_start if buanderie_ouverte and buanderie_start > p["start"] else None,
        "buanderie_avant_mise_en_service": False,
    }
    for z in postes:
        for role in ("chauffage", "eau_chaude"):
            for src in z["compteurs"][role]:
                out[role].append({
                    "zone": z["zone"], "label": z["label"],
                    "compteur": src["label"], "series_id": src["series_id"],
                    "en_cours": en_cours, **lire(src, role, p["start"]),
                })
        kwh_src = (z["compteurs"]["buanderie"] or [None])[0]
        cycles_src = (z["compteurs"]["buanderie_cycles"] or [None])[0]
        if not (kwh_src or cycles_src):
            continue
        if not buanderie_ouverte:
            out["buanderie_avant_mise_en_service"] = True
            continue
        out["buanderie"].append({
            "zone": z["zone"], "label": z["label"], "en_cours": en_cours,
            "energie": lire(kwh_src, "buanderie", buanderie_start) if kwh_src else _absent(),
            "cycles": lire(cycles_src, "buanderie_cycles", buanderie_start) if cycles_src else _absent(),
        })
    return out




def available_range(conn, zones: list[dict], batiment: dict) -> tuple[int, int] | None:
    """Premier et dernier index Statistics des compteurs du décompte.
    Sert à proposer les mois qui ont réellement des relevés. Le collecteur
    n'entre pas dans cette plage."""
    ids = [s["series_id"] for z in zones for s in z["sources"].values() if s]
    ids += [s["series_id"] for s in batiment.values() if s]
    firsts, lasts = [], []
    for sid in set(ids):
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM statistic_samples WHERE series_id = ?", (sid,)
        ).fetchone()
        if row and row[0] is not None:
            firsts.append(row[0])
            lasts.append(row[1])
    if not firsts:
        return None
    return min(firsts), max(lasts)
