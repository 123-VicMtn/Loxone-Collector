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


EXPORT_HEADERS = [
    "Mois", "Zone", "Réseau (kWh)", "Solaire (kWh)", "Consommation (kWh)", "État",
]


def etat_zone(entry: dict) -> str:
    """Même libellé que le badge de la page : facturable, mois en cours,
    ou données incomplètes."""
    if entry.get("facturable"):
        return "facturable"
    if entry.get("en_cours"):
        return "mois en cours"
    return "données incomplètes"


def export_lignes(payload: dict, period_key: str) -> list[list]:
    """Tableau du mois choisi, tel qu'affiché : une ligne par zone puis le
    total. Les kWh sont des nombres (ou None), pas des textes formatés.
    Lève KeyError si le mois n'est pas dans le décompte."""
    year, month = parse_period_key(period_key)
    mois_label = period_label(year, month)
    for p in payload.get("periodes") or []:
        if p.get("key") == period_key and p.get("label"):
            mois_label = p["label"]
            break

    rows: list[list] = []
    sums = {"reseau": 0.0, "solaire": 0.0, "total": 0.0}
    seen = {"reseau": False, "solaire": False, "total": False}
    found = False
    for z in payload.get("zones", []):
        e = z.get("periodes", {}).get(period_key)
        if e is None:
            continue
        found = True
        r, s, t = e["reseau"]["kwh"], e["solaire"]["kwh"], e["total"]
        for key, val in (("reseau", r), ("solaire", s), ("total", t)):
            if val is None:
                continue
            sums[key] += val
            seen[key] = True
        rows.append([mois_label, z.get("label") or z.get("zone"), r, s, t, etat_zone(e)])
    if not found:
        raise KeyError(period_key)
    rows.append([
        mois_label,
        "Total immeuble",
        sums["reseau"] if seen["reseau"] else None,
        sums["solaire"] if seen["solaire"] else None,
        sums["total"] if seen["total"] else None,
        "",
    ])
    return [EXPORT_HEADERS, *rows]


# Mise en page du classeur téléchargé. Même famille visuelle que
# scripts/export_appartement.py : le fichier doit s'ouvrir déjà lisible,
# sans élargir les colonnes ni deviner le site et le mois.
_XLSX_HEADERS = [
    "Zone", "Réseau (kWh)", "Solaire (kWh)", "Consommation (kWh)",
    "Montant réseau (CHF)", "Montant solaire (CHF)", "HT (CHF)", "TVA (CHF)", "TTC (CHF)",
    "État",
]
_XLSX_NCOL = len(_XLSX_HEADERS)
_PRIX_RESEAU_ROW = 4
_PRIX_SOLAIRE_ROW = 5
_TVA_ROW = 6
_XLSX_HEADER_ROW = 8
_COL_KWH_RESEAU, _COL_KWH_SOLAIRE = 2, 3
_COL_CHF_RESEAU, _COL_CHF_SOLAIRE = 5, 6
_COL_HT, _COL_TVA, _COL_TTC, _COL_ETAT = 7, 8, 9, 10
_KWH_FMT = "#,##0.00"
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


def _notes_classeur(payload: dict, manquants: list[str]) -> list[tuple[str, str]]:
    notes: list[tuple[str, str]] = [(
        "Chaque zone est scindée en kWh achetés au réseau et kWh solaires "
        "autoconsommés. La consommation est leur somme.",
        "note",
    )]
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


def _formule_produit(qte: str, prix: str) -> str:
    """Montant vide si la quantité ou le prix manque : un 0 CHF ne doit
    pas se confondre avec une ligne sans relevé."""
    return f'=IF(OR({qte}="",{prix}=""),"",{qte}*{prix})'


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


def build_decompte_workbook(payload: dict, period_key: str, site: str, conn=None) -> Workbook:
    """Classeur du mois choisi, ouvrable tel quel.

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

    lignes = []
    for z, e in entries:
        lignes.append({
            "zone": z.get("label") or z.get("zone") or "",
            "reseau": _kwh_affiche((e.get("reseau") or {}).get("kwh")),
            "solaire": _kwh_affiche((e.get("solaire") or {}).get("kwh")),
            "total": _kwh_affiche(e.get("total")),
            "etat": etat_zone(e),
        })
    totaux = {
        "reseau": _somme_affichee([ln["reseau"] for ln in lignes]),
        "solaire": _somme_affichee([ln["solaire"] for ln in lignes]),
        "total": _somme_affichee([ln["total"] for ln in lignes]),
    }
    taux = None
    if totaux["total"]:
        taux = (totaux["solaire"] or 0.0) / totaux["total"]

    wb = Workbook()
    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    ws = wb.active
    ws.title = "Décompte"

    sous = label + "  ·  " + couverture
    if generated:
        sous += f"  ·  généré le {_horodatage(int(generated), tz)}"
    _ecrire_titre(
        ws,
        f"Décompte de charges — {site}" if site else "Décompte de charges",
        sous, _XLSX_NCOL,
    )
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 8

    tarif = _tarif_du_mois(entries)
    prix_reseau = tarif.get("prix_reseau") if tarif else None
    prix_solaire = tarif.get("prix_solaire") if tarif else None
    taux_tva = tarif.get("taux_tva") if tarif else None
    ws.cell(_PRIX_RESEAU_ROW, 1, "Prix réseau (CHF/kWh)").font = Font(bold=True, color=_NAVY)
    ws.cell(_PRIX_SOLAIRE_ROW, 1, "Prix solaire (CHF/kWh)").font = Font(bold=True, color=_NAVY)
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
    ws.merge_cells(start_row=_PRIX_RESEAU_ROW, start_column=3, end_row=_TVA_ROW, end_column=_XLSX_NCOL)
    note_prix = ws.cell(
        _PRIX_RESEAU_ROW, 3,
        f"{origine} Les cases jaunes sont modifiables : les montants se recalculent. "
        "Une quantité vide ne donne pas de montant.",
    )
    note_prix.font = _FONT_SUB
    note_prix.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    ref_prix_reseau = f"$B${_PRIX_RESEAU_ROW}"
    ref_prix_solaire = f"$B${_PRIX_SOLAIRE_ROW}"
    ref_tva = f"$B${_TVA_ROW}"

    for col, nom in enumerate(_XLSX_HEADERS, start=1):
        cell = ws.cell(_XLSX_HEADER_ROW, col, nom)
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
        kwh_reseau = f"{get_column_letter(_COL_KWH_RESEAU)}{row}"
        kwh_solaire = f"{get_column_letter(_COL_KWH_SOLAIRE)}{row}"
        chf_reseau = f"{get_column_letter(_COL_CHF_RESEAU)}{row}"
        chf_solaire = f"{get_column_letter(_COL_CHF_SOLAIRE)}{row}"
        ht = f"{get_column_letter(_COL_HT)}{row}"
        tva = f"{get_column_letter(_COL_TVA)}{row}"
        valeurs = {
            1: ln["zone"],
            _COL_KWH_RESEAU: ln["reseau"],
            _COL_KWH_SOLAIRE: ln["solaire"],
            4: ln["total"],
            _COL_CHF_RESEAU: _formule_produit(kwh_reseau, ref_prix_reseau),
            _COL_CHF_SOLAIRE: _formule_produit(kwh_solaire, ref_prix_solaire),
            _COL_HT: f'=IF(OR({chf_reseau}="",{chf_solaire}=""),"",{chf_reseau}+{chf_solaire})',
            _COL_TVA: f'=IF(OR({ht}="",{ref_tva}=""),"",{ht}*{ref_tva}/100)',
            _COL_TTC: f'=IF({ht}="","",{ht}+{tva})',
            _COL_ETAT: ln["etat"],
        }
        for col in range(1, _XLSX_NCOL + 1):
            cell = ws.cell(row, col, valeurs[col])
            cell.border = _THIN
            if fill is not None:
                cell.fill = fill
            if col in (_COL_KWH_RESEAU, _COL_KWH_SOLAIRE, 4):
                _style_kwh(cell, valeurs[col])
                if col == 4 and valeurs[col] is not None:
                    cell.font = Font(bold=True)
            elif col in (_COL_CHF_RESEAU, _COL_CHF_SOLAIRE, _COL_HT, _COL_TVA, _COL_TTC):
                cell.number_format = _CHF_FMT
                cell.alignment = _ALIGN_RIGHT
                if col == _COL_TTC:
                    cell.font = Font(bold=True)
            elif col == _COL_ETAT:
                cell.font = _FONT_ETAT.get(ln["etat"], Font())
                cell.alignment = _ALIGN_CENTER
            else:
                cell.alignment = _ALIGN_LEFT
        ws.row_dimensions[row].height = 18

    last_data = first_data + len(lignes) - 1
    total_row = last_data + 1
    for col in range(1, _XLSX_NCOL + 1):
        cell = ws.cell(total_row, col, None)
        cell.font = _FONT_TOTAL
        cell.fill = _FILL_TOTAL
        cell.border = _TOTAL_BORDER
        if col == 1:
            cell.value = "Total immeuble"
            cell.alignment = _ALIGN_LEFT
        elif col == _COL_KWH_RESEAU:
            cell.value = totaux["reseau"]
            _style_kwh(cell, totaux["reseau"])
        elif col == _COL_KWH_SOLAIRE:
            cell.value = totaux["solaire"]
            _style_kwh(cell, totaux["solaire"])
        elif col == 4:
            cell.value = totaux["total"]
            _style_kwh(cell, totaux["total"])
        elif col in (_COL_CHF_RESEAU, _COL_CHF_SOLAIRE, _COL_HT, _COL_TVA, _COL_TTC):
            letter = get_column_letter(col)
            cell.value = _formule_somme(letter, first_data, last_data)
            cell.number_format = _CHF_FMT
            cell.alignment = _ALIGN_RIGHT
        else:
            cell.alignment = _ALIGN_CENTER
    ws.row_dimensions[total_row].height = 22

    ws.auto_filter.ref = f"A{_XLSX_HEADER_ROW}:{get_column_letter(_XLSX_NCOL)}{last_data}"

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
    ws.merge_cells(start_row=rate_row, start_column=1, end_row=rate_row, end_column=_XLSX_NCOL)
    expl = ws.cell(rate_row, 1, phrase_taux)
    expl.font = Font(bold=True, size=12, color=_NAVY)
    expl.alignment = _ALIGN_WRAP
    ws.row_dimensions[rate_row].height = 22

    note_row = rate_row + 2
    for text, kind in _notes_classeur(payload, manquants):
        ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=_XLSX_NCOL)
        cell = ws.cell(note_row, 1, text)
        cell.font = _FONT_NOTE_HEAD if kind == "head" else _FONT_NOTE
        cell.alignment = _ALIGN_WRAP
        lignes_texte = max(1, (len(text) + 139) // 140)
        ws.row_dimensions[note_row].height = 16 * lignes_texte + 4
        note_row += 1
    last_row = note_row - 1

    largeur_zone = max((len(ln["zone"]) for ln in lignes), default=12)
    ws.column_dimensions["A"].width = min(42, max(28, largeur_zone + 3))
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 20
    for col in ("E", "F", "G", "I"):
        ws.column_dimensions[col].width = 18
    ws.column_dimensions["H"].width = 14
    ws.column_dimensions["J"].width = 24

    _mise_en_page(ws, _XLSX_NCOL, last_row, _XLSX_HEADER_ROW, site, label, "Décompte RCP · kWh et CHF")
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
