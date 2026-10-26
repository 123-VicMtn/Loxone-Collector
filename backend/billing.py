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

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

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

BATIMENT_PRODUCTION_RE = re.compile(r"(?i)^production\b")
BATIMENT_RESEAU_RE = re.compile(r"(?i)^r[ée]seau\b")
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
                        "WALLBOX": "Wallbox"}


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
                      tz_name: str = TIMEZONE) -> dict:
    """Décompte complet : pour chaque zone et chaque mois, la part réseau et
    la part solaire autoconsommée, le taux d'autoproduction, les montants,
    et les alertes de fiabilité. Puis la synthèse d'immeuble.

    La source des parts réseau/solaire vient de repartition.evaluer_site :
    séries du Miniserver quand elles sont les seules utilisables (Arlopi),
    répartition recalculée sur le compteur du lot quand elles sont
    incohérentes (Sequoia). Le total facturé reste le relevé de compteur.
    """
    # Import local : repartition importe déjà billing (résolution des séries).
    import repartition

    zones = resolve_zones(series)
    batiment_src = resolve_batiment(series)
    evaluation = None
    if periods:
        fenetre_debut, fenetre_fin = periods[0]["start"], periods[-1]["end"]
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
        evaluation = repartition.evaluer_site(
            conn, zones, batiment_src, periods[0]["start"], periods[-1]["end"],
            statistics=True,
        )

    for z in zones:
        z["periodes"] = {}
        for p in periods:
            if evaluation and evaluation["source_recommandee"] == "calculee":
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
        "repartition": _repartition_payload(evaluation),
    }


def _zone_period(conn, zone: dict, p: dict, tarifs: list[dict], now_ts: int,
                  tz_name: str) -> dict:
    src = zone["sources"]

    def delta(key):
        s = src[key]
        return reading_delta(conn, s["series_id"], p["start"], p["end"], now_ts,
                             statistics=True) if s else _absent()

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
    import repartition

    src = zone["sources"]
    controle = (
        reading_delta(conn, src["controle"]["series_id"], p["start"], p["end"], now_ts,
                      statistics=True)
        if src.get("controle") else _absent()
    )
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


EXPORT_HEADERS = [
    "Mois", "Zone", "Consommation totale (kWh)", "Solaire (kWh)", "Réseau (kWh)", "État",
]
_BUANDERIE_HEADER = "Buanderie (kWh)"


def etat_zone(entry: dict) -> str:
    """Même libellé que le badge de la page : facturable, mois en cours,
    ou données incomplètes."""
    if entry.get("facturable"):
        return "facturable"
    if entry.get("en_cours"):
        return "mois en cours"
    return "données incomplètes"


def _etat_releve(quantite, en_cours: bool) -> str:
    if en_cours:
        return "mois en cours"
    return "facturable" if quantite is not None else "données incomplètes"


def _postes_du_mois(payload: dict, period_key: str) -> dict | None:
    return (payload.get("postes") or {}).get(period_key)


def _buanderie_par_zone(postes: dict | None) -> dict[str, float | None] | None:
    """kWh de buanderie par code de zone, ou None si le mois n'en a pas
    (site sans buanderie, ou mois antérieur à sa mise en service)."""
    if not postes or not postes.get("buanderie"):
        return None
    return {b["zone"]: b["energie"]["kwh"] for b in postes["buanderie"]}


def _mois_label(payload: dict, period_key: str) -> str:
    year, month = parse_period_key(period_key)
    for p in payload.get("periodes") or []:
        if p.get("key") == period_key and p.get("label"):
            return p["label"]
    return period_label(year, month)


def export_lignes(payload: dict, period_key: str) -> list[list]:
    """Tableau électricité du mois choisi : une ligne par zone puis le
    total. Colonne Buanderie quand le mois en a une. Les kWh sont des
    nombres (ou None), pas des textes formatés. Lève KeyError si le mois
    n'est pas dans le décompte."""
    mois_label = _mois_label(payload, period_key)
    buanderie = _buanderie_par_zone(_postes_du_mois(payload, period_key))

    cles = ["total", "solaire", "reseau"] + (["buanderie"] if buanderie is not None else [])
    rows: list[list] = []
    sums = {k: 0.0 for k in cles}
    seen = {k: False for k in cles}
    found = False
    for z in payload.get("zones", []):
        e = z.get("periodes", {}).get(period_key)
        if e is None:
            continue
        found = True
        vals = {"total": e["total"], "solaire": e["solaire"]["kwh"], "reseau": e["reseau"]["kwh"]}
        if buanderie is not None:
            vals["buanderie"] = buanderie.get(z.get("zone"))
        for key in cles:
            if vals[key] is None:
                continue
            sums[key] += vals[key]
            seen[key] = True
        rows.append([mois_label, z.get("label") or z.get("zone"),
                     *[vals[k] for k in cles], etat_zone(e)])
    if not found:
        raise KeyError(period_key)
    rows.append([mois_label, "Total immeuble",
                 *[sums[k] if seen[k] else None for k in cles], ""])
    headers = list(EXPORT_HEADERS)
    if buanderie is not None:
        headers.insert(-1, _BUANDERIE_HEADER)
    return [headers, *rows]


# Tableaux chauffage et eau chaude : (titre, grandeur, unité, décimales).
_POSTE_TABLES = {
    "chauffage": ("Chauffage", "Énergie", "kWh th", 2),
    "eau_chaude": ("Eau chaude", "Volume", "m³", 3),
}
_INDEX_DECIMALES = 3


def _arrondi(value, decimales: int) -> float | None:
    if value is None:
        return None
    return float(f"{float(value):.{decimales}f}")


def _somme_arrondie(values: list, decimales: int) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return float(f"{sum(nums):.{decimales}f}")


def _nom_compteur(label: str) -> str:
    return re.sub(r"\s*\(total\)\s*$", "", label or "").strip()


def lignes_compteurs(postes: dict, role: str) -> tuple[list[dict], float | None]:
    """Lignes du tableau chauffage ou eau chaude (une par compteur), avec
    les valeurs arrondies telles qu'affichées, et leur total."""
    dec = _POSTE_TABLES[role][3]
    lignes = []
    for r in postes.get(role) or []:
        lignes.append({
            "zone": r["label"],
            "compteur": _nom_compteur(r["compteur"]),
            "debut": _arrondi(r.get("releve_debut"), _INDEX_DECIMALES),
            "fin": _arrondi(r.get("releve_fin"), _INDEX_DECIMALES),
            "quantite": _arrondi(r.get("kwh"), dec),
            "etat": _etat_releve(r.get("kwh"), r.get("en_cours", False)),
            "alertes": list(r.get("alertes") or []),
        })
    return lignes, _somme_arrondie([ln["quantite"] for ln in lignes], dec)


def lignes_buanderie(postes: dict) -> tuple[list[dict], dict]:
    """Lignes du tableau buanderie (une par appartement) et leurs totaux."""
    lignes = []
    for b in postes.get("buanderie") or []:
        energie, cycles = b["energie"]["kwh"], b["cycles"]["kwh"]
        complet = energie is not None and cycles is not None
        alertes = [f"Énergie : {a}" for a in b["energie"].get("alertes") or []]
        alertes += [f"Cycles : {a}" for a in b["cycles"].get("alertes") or []]
        lignes.append({
            "zone": b["label"],
            "cycles": _arrondi(cycles, 0),
            "energie": _arrondi(energie, 2),
            "etat": _etat_releve(energie if complet else None, b.get("en_cours", False)),
            "alertes": alertes,
        })
    totaux = {
        "cycles": _somme_arrondie([ln["cycles"] for ln in lignes], 0),
        "energie": _somme_arrondie([ln["energie"] for ln in lignes], 2),
    }
    return lignes, totaux


def _fr(value, decimales: int) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{decimales}f}".replace(".", ",")


def export_csv_lignes(payload: dict, period_key: str) -> list[list[str]]:
    """CSV du mois : une section par tableau (électricité, chauffage, eau
    chaude, buanderie), séparées par une ligne vide. Point-virgule et
    virgule décimale, déjà formatés. Lève KeyError si le mois n'est pas
    dans le décompte."""
    elec = export_lignes(payload, period_key)
    mois_label = _mois_label(payload, period_key)
    out: list[list[str]] = [["Électricité"], elec[0]]
    zones = [[_arrondi(c, 2) if not isinstance(c, str) else c for c in row] for row in elec[1:-1]]
    # Total des valeurs affichées, comme le classeur : sinon la somme des
    # lignes du fichier diffère du total d'un centime.
    total = [mois_label, "Total immeuble"]
    for i in range(2, len(elec[0]) - 1):
        total.append(_somme_arrondie([row[i] for row in zones], 2))
    total.append("")
    for row in [*zones, total]:
        out.append([c if isinstance(c, str) else _fr(c, 2) for c in row])

    postes = _postes_du_mois(payload, period_key)
    if not postes:
        return out
    for role, (titre, grandeur, unite, dec) in _POSTE_TABLES.items():
        lignes, total = lignes_compteurs(postes, role)
        if not lignes:
            continue
        out += [[], [titre], [
            "Mois", "Zone", "Compteur", f"Index début ({unite})", f"Index fin ({unite})",
            f"{grandeur} ({unite})", "État",
        ]]
        for ln in lignes:
            out.append([
                mois_label, ln["zone"], ln["compteur"],
                _fr(ln["debut"], _INDEX_DECIMALES), _fr(ln["fin"], _INDEX_DECIMALES),
                _fr(ln["quantite"], dec), ln["etat"],
            ])
        out.append([mois_label, "Total immeuble", "", "", "", _fr(total, dec), ""])
    lignes, totaux = lignes_buanderie(postes)
    if lignes:
        out += [[], ["Buanderie"], ["Mois", "Zone", "Cycles", "Énergie (kWh)", "État"]]
        for ln in lignes:
            out.append([mois_label, ln["zone"], _fr(ln["cycles"], 0),
                        _fr(ln["energie"], 2), ln["etat"]])
        out.append([mois_label, "Total immeuble", _fr(totaux["cycles"], 0),
                    _fr(totaux["energie"], 2), ""])
    return out


# Mise en page du classeur téléchargé. Même famille visuelle que
# scripts/export_appartement.py : le fichier doit s'ouvrir déjà lisible,
# sans élargir les colonnes ni deviner le site et le mois.
# Colonnes de l'onglet Électricité : (titre, largeur). La colonne
# buanderie n'existe que si le mois en a une, d'où des positions
# calculées et non figées.
_ELEC_COLONNES = {
    "zone": ("Zone", 28),
    "total": ("Consommation totale (kWh)", 22),
    "solaire": ("Solaire (kWh)", 18),
    "reseau": ("Réseau (kWh)", 18),
    "buanderie": (_BUANDERIE_HEADER, 18),
    "chf_reseau": ("Réseau TTC (CHF)", 18),
    "chf_solaire": ("Solaire TTC (CHF)", 18),
    "ttc": ("Total TTC (CHF)", 18),
    "etat": ("État", 24),
}
_ELEC_KWH = ("total", "solaire", "reseau", "buanderie")
_ELEC_CHF = ("chf_reseau", "chf_solaire", "ttc")
_PRIX_RESEAU_ROW = 4
_PRIX_SOLAIRE_ROW = 5
_TVA_ROW = 6
_XLSX_HEADER_ROW = 8
_KWH_FMT = "#,##0.00"
_M3_FMT = "#,##0.000"
_CYCLES_FMT = "#,##0"
_CHF_FMT = "#,##0.00"
_PRIX_FMT = "0.0000"
_TVA_FMT = "0.00"
_INDEX_FMT = "#,##0.000"
_NAVY = "1F4E78"
_THIN = Border(
    left=Side(style="thin", color="D4D4D4"),
    right=Side(style="thin", color="D4D4D4"),
    top=Side(style="thin", color="D4D4D4"),
    bottom=Side(style="thin", color="D4D4D4"),
)
_TOTAL_BORDER = Border(
    left=Side(style="thin", color="D4D4D4"),
    right=Side(style="thin", color="D4D4D4"),
    top=Side(style="medium", color=_NAVY),
    bottom=Side(style="thin", color="D4D4D4"),
)
_FILL_HEADER = PatternFill("solid", fgColor=_NAVY)
_FILL_TOTAL = PatternFill("solid", fgColor="FFF2CC")
_FILL_AMBER = PatternFill("solid", fgColor="FFF8E1")
_FILL_STRIPE = PatternFill("solid", fgColor="F4F7FB")
_FILL_EDIT = PatternFill("solid", fgColor="FFF9C4")
_FONT_TITLE = Font(bold=True, size=16, color=_NAVY)
_FONT_HEADER = Font(bold=True, color="FFFFFF", size=11)
_FONT_SUB = Font(italic=True, size=11, color="525252")
_FONT_NOTE = Font(size=10, color="525252")
_FONT_NOTE_HEAD = Font(bold=True, size=11, color=_NAVY)
_FONT_TOTAL = Font(bold=True, size=11)
_FONT_ETAT = {
    "facturable": Font(color="166534"),
    "mois en cours": Font(color="92400E"),
    "données incomplètes": Font(color="991B1B"),
}
_ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
_ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
_ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_ALIGN_WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _kwh_affiche(value) -> float | None:
    """Deux décimales, comme l'affichage d'un relevé. Une case vide reste
    vide : un zéro affiché serait un relevé à zéro, pas un relevé absent."""
    if value is None:
        return None
    return float(f"{float(value):.2f}")


def _index_affiche(value) -> float | None:
    """Trois décimales : assez pour vérifier index de fin − index de début,
    sans traînée binaire (594.2790000000001)."""
    if value is None:
        return None
    return float(f"{float(value):.3f}")


def _somme_affichee(values: list) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return float(f"{sum(nums):.2f}")


def _jour(ts: int, tz_name: str) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%d.%m.%Y")


def _horodatage(ts: int, tz_name: str) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%d.%m.%Y à %H:%M")


def _zones_du_mois(payload: dict, period_key: str) -> list[tuple]:
    out = []
    for z in payload.get("zones") or []:
        e = (z.get("periodes") or {}).get(period_key)
        if e is None:
            continue
        out.append((z, e))
    return out


def _couverture_mois(payload: dict, period_key: str, entries: list) -> tuple[str, str]:
    """Libellé du mois et phrase de période, en heure suisse. Un mois en
    cours s'arrête au dernier relevé, comme sur la page."""
    tz = payload.get("timezone") or TIMEZONE
    year, month = parse_period_key(period_key)
    label = period_label(year, month)
    start, end = period_bounds(year, month, tz)
    for p in payload.get("periodes") or []:
        if p.get("key") != period_key:
            continue
        label = p.get("label") or label
        start = p.get("start", start)
        end = p.get("end", end)
        break

    bat = ((payload.get("batiment") or {}).get("periodes") or {}).get(period_key) or {}
    generated = payload.get("generated_at")
    en_cours = bool(bat.get("en_cours")) or any(e.get("en_cours") for _, e in entries)
    if generated and end > generated:
        en_cours = True

    if not en_cours:
        return label, f"{_jour(start, tz)} → {_jour(end - 1, tz)} (heure de Suisse)"

    stamps = []
    for _, e in entries:
        for side in ("reseau", "solaire", "controle"):
            ts = (e.get(side) or {}).get("releve_fin_ts")
            if ts:
                stamps.append(ts)
    prod_ts = (bat.get("production") or {}).get("releve_fin_ts")
    if prod_ts:
        stamps.append(prod_ts)
    if stamps:
        return label, (
            f"{_jour(start, tz)} → {_jour(max(stamps), tz)} (dernier relevé, mois en cours)"
        )
    return label, f"{_jour(start, tz)} → {_jour(end - 1, tz)} (mois en cours)"


def _releves_manquants(entries: list) -> list[str]:
    """Pourquoi une ligne n'est pas facturable. Les alertes des colonnes
    d'abord ; à défaut, celles de la zone (répartition impossible, compteur
    muet). Le mois en cours est déjà dit dans le sous-titre."""
    lignes = []
    for z, e in entries:
        if etat_zone(e) == "mois en cours":
            continue
        nom = z.get("label") or z.get("zone") or ""
        cote_lignes = []
        for cote, key in (("Réseau", "reseau"), ("Solaire", "solaire")):
            for detail in (e.get(key) or {}).get("alertes") or []:
                cote_lignes.append(f"{nom} — {cote} : {detail}")
        if cote_lignes:
            lignes.extend(cote_lignes)
            continue
        if etat_zone(e) == "facturable":
            continue
        for detail in e.get("alertes") or []:
            lignes.append(f"{nom} : {detail}")
    return lignes


def _notes_classeur(payload: dict, manquants: list[str],
                    postes: dict | None = None) -> list[tuple[str, str]]:
    notes: list[tuple[str, str]] = [(
        "Chaque zone est scindée en kWh achetés au réseau et kWh solaires "
        "autoconsommés. La consommation totale est leur somme.",
        "note",
    )]
    if _buanderie_par_zone(postes) is not None:
        notes.append((
            "La colonne Buanderie est l'électricité de la buanderie commune "
            "attribuée au lot (CEnergieAppXX). Elle passe par le compteur des "
            "Communs : elle n'est ni dans la consommation totale du lot, ni "
            "dans les montants, ni déduite des Communs. Détail dans l'onglet Buanderie.",
            "note",
        ))
    elif postes and postes.get("buanderie_avant_mise_en_service"):
        notes.append((
            "Buanderie : pas de décompte avant sa mise en service le 17.07.2026.",
            "note",
        ))
    rep = payload.get("repartition") or {}
    if rep.get("source") == "calculee":
        notes.append((
            "Ces parts sont calculées. La consommation vient du compteur du lot, "
            "répartie selon le mix du bâtiment (production moins réinjection). "
            "Les séries Grid et Solaire du Miniserver ne sont pas utilisées.",
            "note",
        ))
    elif rep.get("source") == "loxone" and not rep.get("confiance_verifiee"):
        notes.append((
            "Ces parts sont les mesures du Miniserver. Le bilan du bâtiment "
            "ne permet pas de les revérifier.",
            "note",
        ))
    elif rep.get("source") == "loxone":
        notes.append(("Ces parts sont les mesures du Miniserver.", "note"))
    notes.append((
        "Une case vide n'est pas un zéro : le relevé manque, et le total "
        "ne compte que les relevés présents.",
        "note",
    ))
    notes.append((
        "Le total porte sur toutes les zones. Filtrer le tableau ne change pas ce total.",
        "note",
    ))
    if manquants:
        notes.append(("Relevés incomplets", "head"))
        notes.extend((ligne, "note") for ligne in manquants)
    return notes


def _style_kwh(cell, value) -> None:
    if isinstance(value, float):
        cell.number_format = _KWH_FMT
        cell.alignment = _ALIGN_RIGHT
    else:
        cell.alignment = _ALIGN_RIGHT


def _tarif_du_mois(entries: list) -> dict | None:
    """Un mois n'a qu'un tarif pour le site. On prend celui déjà résolu
    sur les zones (tarif_for au début de la période)."""
    for _, e in entries:
        tarif = e.get("tarif") or None
        if tarif and tarif.get("prix_reseau") is not None and tarif.get("prix_solaire") is not None:
            return tarif
    return None


def _formule_ttc(qte: str, prix: str, tva: str) -> str:
    """Montant TTC, vide si la quantité, le prix ou la TVA manque : un
    0 CHF ne doit pas se confondre avec une ligne sans relevé."""
    return f'=IF(OR({qte}="",{prix}="",{tva}=""),"",{qte}*{prix}*(1+{tva}/100))'


def _formule_somme(col: str, first: int, last: int) -> str:
    return f'=IF(COUNT({col}{first}:{col}{last})=0,"",SUM({col}{first}:{col}{last}))'


def _mise_en_page(ws, ncol: int, last_row: int, header_row: int,
                  site: str, label: str, footer: str) -> None:
    last_col = get_column_letter(ncol)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.horizontalCentered = True
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.5, right=0.5, top=0.75, bottom=0.6, header=0.28, footer=0.28)
    ws.print_title_rows = f"1:{header_row}"
    ws.print_area = f"A1:{last_col}{last_row}"
    ws.freeze_panes = f"A{header_row + 1}"
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 110
    ws.sheet_properties.tabColor = _NAVY
    ws.oddHeader.left.text = site or "Décompte"
    ws.oddHeader.right.text = label
    ws.oddFooter.left.text = footer
    ws.oddFooter.right.text = "Page &P / &N"
    ws.page_setup.oddEven = False


def _ecrire_titre(ws, titre: str, sous: str, ncol: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    cell = ws.cell(1, 1, titre)
    cell.font = _FONT_TITLE
    cell.alignment = _ALIGN_LEFT
    ws.row_dimensions[1].height = 26
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    cell = ws.cell(2, 1, sous)
    cell.font = _FONT_SUB
    cell.alignment = _ALIGN_WRAP
    ws.row_dimensions[2].height = 32


def _prix_jaune(cell, value, fmt: str) -> None:
    cell.value = value
    cell.fill = _FILL_EDIT
    cell.border = _THIN
    cell.font = Font(bold=True)
    cell.number_format = fmt
    cell.alignment = _ALIGN_RIGHT


def _compteurs_statistiques(zone: dict, calculee: bool) -> list[tuple[str, str]]:
    """Compteur physique de l'appartement, celui dont les statistiques
    du Miniserver sont un index. En répartition recalculée c'est le
    compteur du lot. Sinon les compteurs mesurés (réseau et solaire),
    ou le compteur unique du lot quand il n'y a pas de split."""
    sources = zone.get("sources") or {}

    def add(key, out, seen):
        src = sources.get(key) or {}
        sid = src.get("series_id")
        if not sid or sid in seen:
            return
        seen.add(sid)
        out.append((src.get("label") or key, sid))

    out, seen = [], set()
    if calculee or not (sources.get("reseau") or sources.get("solaire")):
        add("controle", out, seen)
    else:
        add("reseau", out, seen)
        add("solaire", out, seen)
    return out


def _points_horaires(conn, series_ids: list[str], start: int, end: int) -> dict[str, list[tuple]]:
    """Points Statistics du Miniserver, un par échantillon."""
    if conn is None or not series_ids:
        return {}
    marks = ",".join("?" * len(series_ids))
    rows = conn.execute(
        f"""
        SELECT series_id, ts, value FROM statistic_samples
        WHERE series_id IN ({marks}) AND ts >= ? AND ts < ?
        ORDER BY series_id, ts
        """,
        (*series_ids, start, end),
    ).fetchall()
    out: dict[str, list[tuple]] = {}
    for sid, ts, value in rows:
        out.setdefault(sid, []).append((ts, value))
    return out


def _bornes_mois(payload: dict, period_key: str) -> tuple[int, int]:
    tz = payload.get("timezone") or TIMEZONE
    year, month = parse_period_key(period_key)
    start, end = period_bounds(year, month, tz)
    for p in payload.get("periodes") or []:
        if p.get("key") == period_key:
            start = p.get("start", start)
            end = p.get("end", end)
            break
    return start, end


def _ecrire_onglet_releves(wb: Workbook, entries: list, payload: dict, period_key: str,
                           site: str, label: str, couverture: str, tz: str, conn) -> None:
    """Une ligne par point horaire des statistiques du Miniserver, pour
    le compteur de chaque appartement."""
    calculee = (payload.get("repartition") or {}).get("source") == "calculee"
    start, end = _bornes_mois(payload, period_key)
    compteurs = []
    for z, _e in entries:
        zone = z.get("label") or z.get("zone") or ""
        for nom, sid in _compteurs_statistiques(z, calculee):
            compteurs.append((zone, nom, sid))
    points = _points_horaires(conn, [sid for _, _, sid in compteurs], start, end)

    ws = wb.create_sheet("Relevés")
    ncol = 4
    sous = (
        f"{label}  ·  {couverture}. Une ligne par point horaire enregistré "
        "dans les statistiques du Miniserver, pour le compteur de chaque appartement."
    )
    _ecrire_titre(ws, f"Relevés de compteurs — {site}" if site else "Relevés de compteurs", sous, ncol)
    ws.row_dimensions[2].height = 36
    headers = ["Zone", "Compteur", "Date", "Index"]
    header_row = 4
    for col, nom in enumerate(headers, start=1):
        cell = ws.cell(header_row, col, nom)
        cell.font = _FONT_HEADER
        cell.fill = _FILL_HEADER
        cell.alignment = _ALIGN_CENTER
        cell.border = _THIN
    ws.row_dimensions[header_row].height = 22

    row = header_row
    for zone, nom, sid in compteurs:
        for ts, value in points.get(sid, []):
            row += 1
            ws.cell(row, 1, zone).alignment = _ALIGN_LEFT
            ws.cell(row, 2, nom).alignment = _ALIGN_LEFT
            date = ws.cell(row, 3, _horodatage(int(ts), tz))
            date.alignment = _ALIGN_CENTER
            index = ws.cell(row, 4, _index_affiche(value))
            index.number_format = _INDEX_FMT
            index.alignment = _ALIGN_RIGHT

    n_points = row - header_row
    if n_points:
        ws.auto_filter.ref = f"A{header_row}:D{row}"
    for col, width in {"A": 22, "B": 36, "C": 22, "D": 16}.items():
        ws.column_dimensions[col].width = width
    _mise_en_page(
        ws, ncol, max(row, header_row), header_row, site, label, "Relevés de compteurs",
    )


def _ecrire_notes(ws, start_row: int, notes: list[tuple[str, str]], ncol: int,
                  largeur_totale: float) -> int:
    """Notes de lecture sous un tableau, une par ligne fusionnée. Retourne
    la dernière ligne écrite."""
    car_par_ligne = max(40, int(largeur_totale * 0.73))
    row = start_row
    for text, kind in notes:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncol)
        cell = ws.cell(row, 1, text)
        cell.font = _FONT_NOTE_HEAD if kind == "head" else _FONT_NOTE
        cell.alignment = _ALIGN_WRAP
        lignes_texte = max(1, (len(text) + car_par_ligne - 1) // car_par_ligne)
        ws.row_dimensions[row].height = 16 * lignes_texte + 4
        row += 1
    return row - 1


def _ecrire_onglet_tableau(wb: Workbook, nom: str, titre: str, sous: str,
                           colonnes: list[tuple[str, int, str | None]],
                           lignes: list[list], total: list | None,
                           notes: list[tuple[str, str]],
                           site: str, label: str, footer: str,
                           prix: list[tuple[str, float | None, str]] | None = None,
                           note_prix: str = "") -> None:
    """Onglet à un tableau : en-tête, une ligne par entrée, ligne de total,
    notes. `colonnes` : (titre, largeur, format) où format est un format
    numérique Excel, None pour du texte, ou "etat" pour la colonne d'état.

    `prix` : cases jaunes modifiables (libellé, valeur, format) écrites en
    B4, B5... au-dessus du tableau. Une cellule de ligne peut alors être
    une fonction `f(row)` qui rend une formule, et une cellule du total une
    fonction `f(first, last)`."""
    ws = wb.create_sheet(nom)
    ncol = len(colonnes)
    _ecrire_titre(ws, titre, sous, ncol)
    ws.row_dimensions[2].height = 36
    header_row = 4
    if prix:
        for i, (libelle, valeur, fmt) in enumerate(prix):
            r = 4 + i
            ws.cell(r, 1, libelle).font = Font(bold=True, color=_NAVY)
            _prix_jaune(ws.cell(r, 2), valeur, fmt)
            ws.row_dimensions[r].height = 20
        last_prix = 4 + len(prix) - 1
        ws.merge_cells(start_row=4, start_column=3, end_row=last_prix, end_column=ncol)
        cell = ws.cell(4, 3, note_prix)
        cell.font = _FONT_SUB
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        header_row = last_prix + 2
    for c, (nom_col, _w, _f) in enumerate(colonnes, start=1):
        cell = ws.cell(header_row, c, nom_col)
        cell.font = _FONT_HEADER
        cell.fill = _FILL_HEADER
        cell.alignment = _ALIGN_CENTER
        cell.border = _THIN
    ws.row_dimensions[header_row].height = 32

    i_etat = next((i for i, (_n, _w, f) in enumerate(colonnes) if f == "etat"), None)
    row = header_row
    for i, valeurs in enumerate(lignes):
        row += 1
        etat = valeurs[i_etat] if i_etat is not None else "facturable"
        fill = _FILL_AMBER if etat != "facturable" else (_FILL_STRIPE if i % 2 else None)
        for c, (value, (_n, _w, fmt)) in enumerate(zip(valeurs, colonnes), start=1):
            formule = callable(value)
            cell = ws.cell(row, c, value(row) if formule else value)
            cell.border = _THIN
            if fill is not None:
                cell.fill = fill
            if fmt == "etat":
                cell.font = _FONT_ETAT.get(value, Font())
                cell.alignment = _ALIGN_CENTER
            elif fmt:
                if formule or isinstance(value, (int, float)):
                    cell.number_format = fmt
                cell.alignment = _ALIGN_RIGHT
            else:
                cell.alignment = _ALIGN_LEFT
        ws.row_dimensions[row].height = 18
    if lignes:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncol)}{row}"

    if total is not None and lignes:
        first_data, last_data = header_row + 1, row
        row += 1
        for c, (value, (_n, _w, fmt)) in enumerate(zip(total, colonnes), start=1):
            formule = callable(value)
            cell = ws.cell(row, c, value(first_data, last_data) if formule else value)
            cell.font = _FONT_TOTAL
            cell.fill = _FILL_TOTAL
            cell.border = _TOTAL_BORDER
            if fmt and fmt != "etat":
                if formule or isinstance(value, (int, float)):
                    cell.number_format = fmt
                cell.alignment = _ALIGN_RIGHT
            else:
                cell.alignment = _ALIGN_LEFT
        ws.row_dimensions[row].height = 22

    for c, (_n, width, _f) in enumerate(colonnes, start=1):
        ws.column_dimensions[get_column_letter(c)].width = width
    last_row = _ecrire_notes(ws, row + 2, notes, ncol, sum(w for _n, w, _f in colonnes))
    _mise_en_page(ws, ncol, max(last_row, header_row), header_row, site, label, footer)


def _notes_manquants(lignes: list[dict], nom) -> list[tuple[str, str]]:
    """Relevés qui empêchent une ligne d'être facturable. Le mois en cours
    est déjà dit dans le sous-titre."""
    detail = []
    for ln in lignes:
        if ln["etat"] != "données incomplètes":
            continue
        for a in ln["alertes"] or ["relevé manquant"]:
            detail.append((f"{nom(ln)} : {a}", "note"))
    return [("Relevés incomplets", "head"), *detail] if detail else []


def _index_identiques(lignes: list[dict]) -> list[tuple[str, str]]:
    """Compteurs de zones différentes aux mêmes index de début et de fin :
    à Sequoia, l'eau chaude de l'App 41 a l'historique exact de celle de
    l'App 35 (un seul compteur lu deux fois dans Loxone Config)."""
    groupes: dict[tuple, list[dict]] = {}
    for ln in lignes:
        if ln["debut"] and ln["fin"] is not None:
            groupes.setdefault((ln["debut"], ln["fin"]), []).append(ln)
    notes = []
    for (debut, fin), group in groupes.items():
        zones = list(dict.fromkeys(ln["zone"] for ln in group))
        if len(zones) < 2:
            continue
        notes.append((
            f"{' et '.join(zones)} : mêmes index en début et en fin de mois "
            f"({_fr(debut, _INDEX_DECIMALES)} → {_fr(fin, _INDEX_DECIMALES)}). "
            "Probablement un seul compteur lu deux fois : à vérifier dans Loxone Config.",
            "note",
        ))
    return [("Points d'attention", "head"), *notes] if notes else []


def _ecrire_onglets_postes(wb: Workbook, postes: dict, site: str, label: str,
                           couverture: str, tva_defaut: float | None = None) -> None:
    """Onglets Chauffage, Eau chaude et Buanderie. Un onglet n'est créé que
    si le site a des compteurs de ce poste.

    Chauffage et eau chaude ont leur prix HT et leur TVA en cases jaunes
    (pas de tarif en base pour ces postes : le prix part vide, la TVA
    reprend celle du tarif électrique s'il y en a un)."""
    explications = {
        "chauffage": "Énergie de chauffage = index de fin − index de début du "
                     "compteur de chaleur. Le volume circulé n'est pas repris.",
        "eau_chaude": "Volume d'eau chaude sanitaire = index de fin − index de "
                      "début. L'eau froide n'est pas reprise.",
    }
    for role, (titre, grandeur, unite, dec) in _POSTE_TABLES.items():
        lignes, total = lignes_compteurs(postes, role)
        if not lignes:
            continue
        qte_fmt = _M3_FMT if unite == "m³" else _KWH_FMT
        colonnes = [
            ("Zone", 22, None),
            ("Compteur", 46, None),
            (f"Index début ({unite})", 18, _INDEX_FMT),
            (f"Index fin ({unite})", 18, _INDEX_FMT),
            (f"{grandeur} ({unite})", 18, qte_fmt),
            ("Montant TTC (CHF)", 18, _CHF_FMT),
            ("État", 22, "etat"),
        ]
        # Prix en B4, TVA en B5 ; quantité en E, montant en F.
        def montant(row):
            return _formule_ttc(f"E{row}", "$B$4", "$B$5")

        def somme_montants(first, last):
            return _formule_somme("F", first, last)

        rows = [[ln["zone"], ln["compteur"], ln["debut"], ln["fin"], ln["quantite"],
                 montant, ln["etat"]]
                for ln in lignes]
        notes = [(explications[role], "note")]
        if len({ln["zone"] for ln in lignes}) < len(lignes):
            notes.append((
                "Une zone peut avoir plusieurs compteurs : une ligne par compteur, "
                "le total les additionne tous.", "note",
            ))
        notes.append((
            "Une case vide n'est pas un zéro : le relevé manque, et le total "
            "ne compte que les relevés présents.", "note",
        ))
        notes += _index_identiques(lignes)
        notes += _notes_manquants(lignes, lambda ln: f"{ln['zone']} — {ln['compteur']}")
        _ecrire_onglet_tableau(
            wb, titre, f"{titre} — {site}" if site else titre,
            f"{label}  ·  {couverture}", colonnes, rows,
            ["Total immeuble", "", None, None, total, somme_montants, ""], notes,
            site, label, f"{titre} · {unite} et CHF",
            prix=[
                (f"Prix {titre.lower()} HT (CHF/{unite})", None, _PRIX_FMT),
                ("TVA (%)", tva_defaut, _TVA_FMT),
            ],
            note_prix=(
                f"Montant TTC = {grandeur.lower()} × prix HT × (1 + TVA). Les cases "
                "jaunes sont modifiables : les montants se recalculent. Une quantité, "
                "un prix ou une TVA vide ne donne pas de montant."
            ),
        )

    lignes, totaux = lignes_buanderie(postes)
    if not lignes:
        return
    colonnes = [
        ("Zone", 22, None),
        ("Cycles", 14, _CYCLES_FMT),
        ("Énergie (kWh)", 18, _KWH_FMT),
        ("État", 22, "etat"),
    ]
    rows = [[ln["zone"], ln["cycles"], ln["energie"], ln["etat"]] for ln in lignes]
    notes = [
        ("Énergie (CEnergieAppXX) et nombre de cycles (CNrMachineAppXX) de la "
         "buanderie commune, attribués à chaque appartement par badge.", "note"),
        ("Cette énergie passe par le compteur des Communs : elle n'est pas dans "
         "la consommation électrique du lot, et n'est pas déduite des Communs.", "note"),
    ]
    if postes.get("buanderie_depuis"):
        notes.append((
            "Décompte à partir du 17.07.2026, mise en service de la buanderie. "
            "Avant, l'énergie s'incrémentait sur tous les lots sans cycle.", "note",
        ))
    notes += _notes_manquants(lignes, lambda ln: ln["zone"])
    _ecrire_onglet_tableau(
        wb, "Buanderie", f"Buanderie — {site}" if site else "Buanderie",
        f"{label}  ·  {couverture}", colonnes, rows,
        ["Total immeuble", totaux["cycles"], totaux["energie"], ""], notes,
        site, label, "Buanderie · kWh et cycles",
    )


def build_decompte_workbook(payload: dict, period_key: str, site: str, conn=None) -> Workbook:
    """Classeur du mois choisi, ouvrable tel quel.

    Onglets : Électricité (kWh, prix et montants, colonne buanderie si le
    mois en a une), puis Chauffage, Eau chaude et Buanderie quand le site
    a ces compteurs (`payload["postes"]`), et enfin les Relevés horaires.

    Les chiffres sont ceux de `export_lignes` (donc de la page), arrondis
    au centième de kWh pour que la somme des lignes égale le total affiché.
    Lève KeyError si le mois n'a aucune zone.
    """
    entries = _zones_du_mois(payload, period_key)
    if not entries:
        raise KeyError(period_key)

    tz = payload.get("timezone") or TIMEZONE
    label, couverture = _couverture_mois(payload, period_key, entries)
    generated = payload.get("generated_at")
    manquants = _releves_manquants(entries)
    postes = _postes_du_mois(payload, period_key)
    buanderie = _buanderie_par_zone(postes)

    lignes = []
    for z, e in entries:
        lignes.append({
            "zone": z.get("label") or z.get("zone") or "",
            "reseau": _kwh_affiche((e.get("reseau") or {}).get("kwh")),
            "solaire": _kwh_affiche((e.get("solaire") or {}).get("kwh")),
            "total": _kwh_affiche(e.get("total")),
            "buanderie": _kwh_affiche((buanderie or {}).get(z.get("zone"))),
            "etat": etat_zone(e),
        })
    totaux = {k: _somme_affichee([ln[k] for ln in lignes]) for k in _ELEC_KWH}
    taux = None
    if totaux["total"]:
        taux = (totaux["solaire"] or 0.0) / totaux["total"]

    cles = ["zone", "total", "solaire", "reseau"]
    if buanderie is not None:
        cles.append("buanderie")
    cles += [*_ELEC_CHF, "etat"]
    col = {k: i for i, k in enumerate(cles, start=1)}
    ncol = len(cles)

    def ref(key: str, row: int) -> str:
        return f"{get_column_letter(col[key])}{row}"

    wb = Workbook()
    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    ws = wb.active
    ws.title = "Électricité"

    sous = label + "  ·  " + couverture
    if generated:
        sous += f"  ·  généré le {_horodatage(int(generated), tz)}"
    _ecrire_titre(
        ws,
        f"Décompte de charges — {site}" if site else "Décompte de charges",
        sous, ncol,
    )
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 8

    tarif = _tarif_du_mois(entries)
    prix_reseau = tarif.get("prix_reseau") if tarif else None
    prix_solaire = tarif.get("prix_solaire") if tarif else None
    taux_tva = tarif.get("taux_tva") if tarif else None
    ws.cell(_PRIX_RESEAU_ROW, 1, "Prix réseau HT (CHF/kWh)").font = Font(bold=True, color=_NAVY)
    ws.cell(_PRIX_SOLAIRE_ROW, 1, "Prix solaire HT (CHF/kWh)").font = Font(bold=True, color=_NAVY)
    ws.cell(_TVA_ROW, 1, "TVA (%)").font = Font(bold=True, color=_NAVY)
    _prix_jaune(ws.cell(_PRIX_RESEAU_ROW, 2), prix_reseau, _PRIX_FMT)
    _prix_jaune(ws.cell(_PRIX_SOLAIRE_ROW, 2), prix_solaire, _PRIX_FMT)
    _prix_jaune(ws.cell(_TVA_ROW, 2), taux_tva, _TVA_FMT)
    for r in (_PRIX_RESEAU_ROW, _PRIX_SOLAIRE_ROW, _TVA_ROW):
        ws.row_dimensions[r].height = 20
    vf = str((tarif or {}).get("valid_from") or "")
    if len(vf) >= 10 and vf[4] == "-" and vf[7] == "-":
        origine = f"Tarif en vigueur depuis le {vf[8:10]}.{vf[5:7]}.{vf[:4]}."
    elif tarif:
        origine = "Tarif enregistré pour ce site."
    else:
        origine = "Aucun tarif enregistré pour ce mois."
    ws.merge_cells(start_row=_PRIX_RESEAU_ROW, start_column=3, end_row=_TVA_ROW, end_column=ncol)
    note_prix = ws.cell(
        _PRIX_RESEAU_ROW, 3,
        f"{origine} Les montants sont TTC : kWh × prix HT × (1 + TVA). "
        "Les cases jaunes sont modifiables : les montants se recalculent. "
        "Une quantité vide ne donne pas de montant.",
    )
    note_prix.font = _FONT_SUB
    note_prix.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    ref_prix_reseau = f"$B${_PRIX_RESEAU_ROW}"
    ref_prix_solaire = f"$B${_PRIX_SOLAIRE_ROW}"
    ref_tva = f"$B${_TVA_ROW}"

    for key in cles:
        cell = ws.cell(_XLSX_HEADER_ROW, col[key], _ELEC_COLONNES[key][0])
        cell.font = _FONT_HEADER
        cell.fill = _FILL_HEADER
        cell.alignment = _ALIGN_CENTER
        cell.border = _THIN
    ws.row_dimensions[_XLSX_HEADER_ROW].height = 32

    first_data = _XLSX_HEADER_ROW + 1
    for i, ln in enumerate(lignes):
        row = first_data + i
        incomplet = ln["etat"] != "facturable"
        fill = _FILL_AMBER if incomplet else (_FILL_STRIPE if i % 2 else None)
        chf_reseau, chf_solaire = ref("chf_reseau", row), ref("chf_solaire", row)
        valeurs = {
            "zone": ln["zone"],
            "total": ln["total"],
            "solaire": ln["solaire"],
            "reseau": ln["reseau"],
            "buanderie": ln["buanderie"],
            "chf_reseau": _formule_ttc(ref("reseau", row), ref_prix_reseau, ref_tva),
            "chf_solaire": _formule_ttc(ref("solaire", row), ref_prix_solaire, ref_tva),
            "ttc": f'=IF(OR({chf_reseau}="",{chf_solaire}=""),"",{chf_reseau}+{chf_solaire})',
            "etat": ln["etat"],
        }
        for key in cles:
            cell = ws.cell(row, col[key], valeurs[key])
            cell.border = _THIN
            if fill is not None:
                cell.fill = fill
            if key in _ELEC_KWH:
                _style_kwh(cell, valeurs[key])
                if key == "total" and valeurs[key] is not None:
                    cell.font = Font(bold=True)
            elif key in _ELEC_CHF:
                cell.number_format = _CHF_FMT
                cell.alignment = _ALIGN_RIGHT
                if key == "ttc":
                    cell.font = Font(bold=True)
            elif key == "etat":
                cell.font = _FONT_ETAT.get(ln["etat"], Font())
                cell.alignment = _ALIGN_CENTER
            else:
                cell.alignment = _ALIGN_LEFT
        ws.row_dimensions[row].height = 18

    last_data = first_data + len(lignes) - 1
    total_row = last_data + 1
    for key in cles:
        cell = ws.cell(total_row, col[key], None)
        cell.font = _FONT_TOTAL
        cell.fill = _FILL_TOTAL
        cell.border = _TOTAL_BORDER
        if key == "zone":
            cell.value = "Total immeuble"
            cell.alignment = _ALIGN_LEFT
        elif key in _ELEC_KWH:
            cell.value = totaux[key]
            _style_kwh(cell, totaux[key])
        elif key in _ELEC_CHF:
            letter = get_column_letter(col[key])
            cell.value = _formule_somme(letter, first_data, last_data)
            cell.number_format = _CHF_FMT
            cell.alignment = _ALIGN_RIGHT
        else:
            cell.alignment = _ALIGN_CENTER
    ws.row_dimensions[total_row].height = 22

    ws.auto_filter.ref = f"A{_XLSX_HEADER_ROW}:{get_column_letter(ncol)}{last_data}"

    rate_row = total_row + 2
    if taux is not None:
        phrase_taux = (
            f"Taux d'autoproduction : {taux * 100:.0f} % — part de la "
            "consommation couverte par le solaire autoconsommé."
        )
    else:
        phrase_taux = (
            "Taux d'autoproduction : pas calculé, la consommation du mois "
            "n'est pas connue."
        )
    ws.merge_cells(start_row=rate_row, start_column=1, end_row=rate_row, end_column=ncol)
    expl = ws.cell(rate_row, 1, phrase_taux)
    expl.font = Font(bold=True, size=12, color=_NAVY)
    expl.alignment = _ALIGN_WRAP
    ws.row_dimensions[rate_row].height = 22

    largeur_zone = max((len(ln["zone"]) for ln in lignes), default=12)
    largeurs = {k: _ELEC_COLONNES[k][1] for k in cles}
    largeurs["zone"] = min(42, max(28, largeur_zone + 3))
    for key, width in largeurs.items():
        ws.column_dimensions[get_column_letter(col[key])].width = width

    last_row = _ecrire_notes(
        ws, rate_row + 2, _notes_classeur(payload, manquants, postes), ncol,
        sum(largeurs.values()),
    )
    _mise_en_page(ws, ncol, last_row, _XLSX_HEADER_ROW, site, label, "Électricité · kWh et CHF")

    if postes:
        _ecrire_onglets_postes(wb, postes, site, label, couverture, taux_tva)
    _ecrire_onglet_releves(
        wb, entries, payload, period_key, site, label, couverture, tz, conn,
    )

    wb.properties.title = f"Décompte {site} {label}".strip()
    wb.properties.creator = "Loxone Collector"
    return wb


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
