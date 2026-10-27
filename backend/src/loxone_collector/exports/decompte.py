"""
Lignes des tableaux du décompte d'un mois (électricité, chauffage, eau
chaude, buanderie) et export CSV. Le classeur Excel
(exports/decompte_xlsx.py) part des mêmes lignes. Aucun calcul ici : les
kWh viennent de services/billing.py (`compute_decompte`, `compute_postes`).
"""

from __future__ import annotations

import re

from loxone_collector.services.billing import (
    parse_period_key,
    period_label,
)

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

