"""
Quel compteur alimente quelle colonne du décompte : zones (réseau,
solaire, compteur du lot), compteurs du bâtiment, production, et la
plage des relevés disponibles.
"""

from __future__ import annotations

import re

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
