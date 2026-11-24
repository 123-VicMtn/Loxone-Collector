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

    def lire_chauffage(src):
        site = src["series_id"].split(":", 1)[0]
        bascule = CHAUFFAGE_EN_WH_JUSQU_AU.get(site)
        if bascule is None or not p["start"] < bascule < p["end"]:
            r = lire(src, "chauffage", p["start"])
            if bascule is not None and p["end"] <= bascule:
                r = _wh_en_kwh(r)
            return r
        avant = _wh_en_kwh(reading_delta(conn, src["series_id"], p["start"], bascule, now_ts,
                                         min_drop=RUPTURE_MIN_DROP_KWH, statistics=True))
        apres = lire(src, "chauffage", bascule)
        return _assembler(avant, apres)

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
                    "en_cours": en_cours,
                    **(lire_chauffage(src) if role == "chauffage" else lire(src, role, p["start"])),
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
