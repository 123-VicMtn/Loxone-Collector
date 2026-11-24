"""
Postes hors électricité : chauffage, eau chaude et buanderie, une
ligne par compteur (ou par zone pour la buanderie).
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from loxone_collector.services.billing.compteurs import (
    series_total,
    source_ref,
    zone_label,
    zone_sort_key,
)
from loxone_collector.services.billing.periodes import TIMEZONE
from loxone_collector.services.billing.releves import (
    RUPTURE_MIN_DROP_EAU_M3,
    RUPTURE_MIN_DROP_KWH,
    lecture_absente,
    reading_delta,
)

# Chauffage : seule l'énergie thermique (kWh) est décomptée, jamais le
# volume circulé. Sequoia nomme ses compteurs « APP 01 compteur chaleur [5] »
# et « APP 01 compteur chaleur volume [5] » (« Ccaleur » à l'App 34) ;
# Arlopi « Chauffage App 1 », La Calbasse « APP35 Chauffage ».
CHAUFFAGE_RE = re.compile(r"(?i)(chauffage|c[ch]aleur)")


VOLUME_RE = re.compile(r"(?i)\bvolume\b")


# Arlopy, Commerce : « Commerce Chauffage » (Statistics depuis le
# 27.10.2026) remplacera « Commerce Chauffage ancien » et « … ancien 2 ».
# Décision de l'utilisateur (2026-11-02) : le chauffage du Commerce se lit
# sur les deux anciens tant qu'ils ne sont pas abandonnés ; le nouveau
# n'entre pas dans le décompte d'ici là. Retirer cette règle à la bascule.
CHAUFFAGE_PAS_ENCORE_RE = re.compile(r"(?i)^commerce chauffage \(total\)$")


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


# DETTE TECHNIQUE, à retirer une fois le décompte de novembre 2026 de
# La Calbasse terminé : jusqu'à l'enregistrement de la configuration Loxone
# du 20.11.2026 à 15:13:19, ses compteurs de chaleur comptaient en Wh
# (malgré l'unité « kWhth ») ; ils sont alors repartis de 0, en kWh.
# Décision de l'utilisateur (2026-11-24) : les index d'avant sont divisés
# par 1 000, et la remise à zéro n'est pas une rupture.
CHAUFFAGE_EN_WH_JUSQU_AU = {
    "La Calbasse": int(datetime(2026, 11, 20, 15, 13, 19, tzinfo=ZoneInfo(TIMEZONE)).timestamp()),
}

# DETTE TECHNIQUE (même échéance) : le 10.11.2026 vers 9 h, l'entrée M-Bus
# est passée de Wh à kWh ; le bloc Compteur (mesure absolue) a refusé cette
# baisse et ses index sont restés figés jusqu'au 20.11. Les index M-Bus
# physiques, eux, ont continué : l'index au redémarrage du 20.11 est
# l'« écart index » (index M-Bus − index du bloc) relevé en LiveView le
# 24.11.2026 à 11:34 (docs/La_Calbasse_chauffage_novembre_2026.md), à
# 1 kWh th près (valeurs entières). Il remplace l'index figé comme fin du
# morceau en Wh. Contrôle : rythme du 10 au 20.11 / rythme du 01 au 10.11
# entre 0,89 et 1,06 sur les 12 lots mesurés.
CALBASSE_GEL_DEPUIS = int(datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo(TIMEZONE)).timestamp())

CALBASSE_INDEX_A_LA_REPRISE = {
    "APP35": 607, "APP36": 230, "APP37": 1127, "APP38": 696, "APP39": 326,
    "APP40": 747, "APP41": 443, "APP42": 365, "APP43": 245, "APP44": 723,
    "APP45": 440, "APP46": 713, "APP47": 988, "APP48": 298, "APP49": 865,
}

# Index du 01.11.2026 00:00 que les Statistics ne donnent pas (kWh th).
#  - APP36 : son registre d'index était faux ; l'index était sur la sortie
#    « actual » (24 000 Wh à minuit, Statistics et sauvegarde du 19.11).
#  - APP41 : registre à 0 jusqu'au 10.11, puis 366 kWh th lus le 10.11 vers
#    9 h 30 (seule lecture acceptée avant le gel) ; 01-10.11 estimé par la
#    puissance mesurée × 1,0496 (rapport index / puissance des 11 lots,
#    décompte du 10.11) = 111,6 → 366 − 111,6.
#  - APP48 : aucun index avant le 20.11 ; 01-10.11 estimé par la puissance
#    (82,4, décompte du 10.11), 10-20.11 au rythme des autres lots (rythme
#    du gel / rythme du 01 au 10.11 = 0,9785 sur 11 lots) = 88,2
#    → 298 − 82,4 − 88,2.
CALBASSE_INDEX_AU_1ER_NOVEMBRE = {
    "APP36": (24.0, "index du 01.11 lu sur la sortie de puissance (registre d'index faux)"),
    "APP41": (254.4, "estimé : index du 01.11 reconstitué (366 kWh th lus le 10.11 − "
                     "111,6 estimés par la puissance du 01 au 10.11)"),
    "APP48": (127.4, "estimé : index du 01.11 reconstitué (puissance du 01 au 10.11, "
                     "puis rythme des autres lots du 10 au 20.11 ; aucun index avant le 20.11)"),
}
CALBASSE_1ER_NOVEMBRE = int(datetime(2026, 11, 1, tzinfo=ZoneInfo(TIMEZONE)).timestamp())

# Signalé, sans correction (décision de l'utilisateur en attente).
CALBASSE_A_VERIFIER = {
    "APP43": "compteur M-Bus bloqué à 245 kWh th depuis le 10.11 : à vérifier",
}


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
        if VOLUME_RE.search(label) or CHAUFFAGE_PAS_ENCORE_RE.match(label):
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
    for s in series_total(series):
        apt = (s.get("apartment") or "").strip()
        if not apt or apt.upper() in ZONES_FANTOMES:
            continue
        role = _role_poste(s)
        if role is None:
            continue
        zones.setdefault(apt, {r: [] for r in POSTES})[role].append(source_ref(s))
    out = []
    for apt in sorted(zones, key=zone_sort_key):
        compteurs = {r: sorted(v, key=lambda c: c["label"]) for r, v in zones[apt].items()}
        out.append({"zone": apt, "label": zone_label(apt), "compteurs": compteurs})
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

    def lire_chauffage(src, zone):
        """Relevé de chauffage ; None si la ligne ne doit pas apparaître."""
        site = src["series_id"].split(":", 1)[0]
        bascule = CHAUFFAGE_EN_WH_JUSQU_AU.get(site)
        if bascule is None or p["end"] <= p["start"] or not p["start"] < bascule:
            return lire(src, "chauffage", p["start"])
        calbasse = site == "La Calbasse"
        if p["end"] <= bascule:
            r = _wh_en_kwh(lire(src, "chauffage", p["start"]))
            if calbasse and p["end"] > CALBASSE_GEL_DEPUIS:
                r = {**r, "kwh": None,
                     "alertes": [*r["alertes"], "index figés du 10.11 au 20.11.2026 15:13"]}
            return r
        apres = lire(src, "chauffage", bascule)
        fin = CALBASSE_INDEX_A_LA_REPRISE.get(zone) if calbasse else None
        if fin is None:
            avant = _wh_en_kwh(reading_delta(conn, src["series_id"], p["start"], bascule, now_ts,
                                             min_drop=RUPTURE_MIN_DROP_KWH, statistics=True))
            return _assembler(avant, apres)
        if _sans_releve_depuis(conn, src, bascule):
            return None  # ancien compteur remplacé le 20.11 (APP48)
        return _assembler(_avant_calbasse(conn, src, zone, p["start"], bascule, fin, now_ts), apres)

    out = {
        "chauffage": [], "eau_chaude": [], "buanderie": [],
        "en_cours": en_cours,
        "buanderie_depuis": buanderie_start if buanderie_ouverte and buanderie_start > p["start"] else None,
        "buanderie_avant_mise_en_service": False,
    }
    for z in postes:
        for role in ("chauffage", "eau_chaude"):
            for src in z["compteurs"][role]:
                r = lire_chauffage(src, z["zone"]) if role == "chauffage" else lire(src, role, p["start"])
                if r is None:
                    continue
                out[role].append({
                    "zone": z["zone"], "label": z["label"],
                    "compteur": src["label"], "series_id": src["series_id"],
                    "en_cours": en_cours,
                    **r,
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
            "energie": lire(kwh_src, "buanderie", buanderie_start) if kwh_src else lecture_absente(),
            "cycles": lire(cycles_src, "buanderie_cycles", buanderie_start) if cycles_src else lecture_absente(),
        })
    return out


def _wh_en_kwh(r: dict) -> dict:
    """Relevé d'un compteur qui comptait en Wh, ramené en kWh
    (`CHAUFFAGE_EN_WH_JUSQU_AU`)."""
    out = dict(r)
    for cle in ("kwh", "releve_debut", "releve_fin"):
        if out.get(cle) is not None:
            out[cle] = out[cle] / 1000
    out["ruptures"] = [{**x, "avant": x["avant"] / 1000, "apres": x["apres"] / 1000}
                       for x in r.get("ruptures") or []]
    return out


def _sans_releve_depuis(conn, src: dict, ts: int) -> bool:
    """Compteur sans aucun index Statistics depuis `ts` (remplacé)."""
    return conn.execute(
        "SELECT 1 FROM statistic_samples WHERE series_id = ? AND ts >= ? LIMIT 1",
        (src["series_id"], ts),
    ).fetchone() is None


def _avant_calbasse(conn, src: dict, zone: str, start: int, bascule: int,
                    fin: float, now_ts: int) -> dict:
    """Morceau en Wh du chauffage de La Calbasse (`CALBASSE_INDEX_A_LA_REPRISE`) :
    index M-Bus au redémarrage − index du début de période."""
    alertes = ["index du 20.11 pris sur le compteur M-Bus (index Loxone figés du 10 au 20.11)"]
    connu = CALBASSE_INDEX_AU_1ER_NOVEMBRE.get(zone)
    if connu is not None:
        debut, alerte = connu
        alertes.append(alerte)
        if start != CALBASSE_1ER_NOVEMBRE:
            debut = None
            alertes.append("index connu seulement au 01.11.2026 00:00")
    elif start > CALBASSE_GEL_DEPUIS:
        debut = None
        alertes.append("index figés du 10.11 au 20.11.2026 15:13")
    else:
        r = _wh_en_kwh(reading_delta(conn, src["series_id"], start, bascule, now_ts,
                                     min_drop=RUPTURE_MIN_DROP_KWH, statistics=True))
        debut = r["releve_debut"]
        alertes += r["alertes"]
    if zone in CALBASSE_A_VERIFIER:
        alertes.append(CALBASSE_A_VERIFIER[zone])
    return {
        "kwh": None if debut is None else round(fin - debut, 3),
        "releve_debut": debut,
        "releve_fin": float(fin),
        "releve_debut_ts": start,
        "releve_fin_ts": bascule,
        "alertes": alertes,
    }


def _assembler(avant: dict, apres: dict) -> dict:
    """Consommation de part et d'autre de la remise à zéro du 20.11.2026
    (`CHAUFFAGE_EN_WH_JUSQU_AU`) : les deux morceaux s'additionnent."""
    kwh = None if avant["kwh"] is None or apres["kwh"] is None else avant["kwh"] + apres["kwh"]
    alertes = [*avant["alertes"], *apres["alertes"]]
    alertes.append("index en Wh jusqu'au 20.11.2026 15:13 (divisés par 1 000), "
                   "puis compteur remis à zéro en kWh")
    out = {
        "kwh": kwh,
        "releve_debut": avant["releve_debut"],
        "releve_fin": apres["releve_fin"],
        "releve_debut_ts": avant["releve_debut_ts"],
        "releve_fin_ts": apres["releve_fin_ts"],
        "alertes": alertes,
    }
    ruptures = [*(avant.get("ruptures") or []), *(apres.get("ruptures") or [])]
    if ruptures:
        out["ruptures"] = ruptures
    return out
