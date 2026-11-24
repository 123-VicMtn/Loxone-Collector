"""
Classeur Excel du décompte d'une période : onglets Récapitulatif,
Électricité, (Détail par mois), Chauffage, Eau chaude, Buanderie, Frais
supplémentaires et Relevés. Mise en forme seulement : les lignes viennent de
exports/decompte.py, les kWh de services/billing, les montants de
services/billing/chiffrage.py (valeurs calculées par l'application avec les
paramètres de la page Paramètres, pas des formules : décision du
2026-11-24).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.page import PageMargins

from loxone_collector.exports.decompte import (
    _BUANDERIE_HEADER,
    _INDEX_DECIMALES,
    _POSTE_TABLES,
    _buanderie_par_zone,
    _fr,
    _mois_label,
    _postes_du_mois,
    chiffrage_du_mois,
    etat_zone,
    libelle_total,
    lignes_buanderie,
    lignes_compteurs,
    lignes_detail,
    lignes_recapitulatif,
)
from loxone_collector.services.billing import (
    TIMEZONE,
    ZONE_PAC,
    parse_period_key,
    period_bounds,
    repartition_du_mois,
)

# Mise en page du classeur téléchargé : le fichier doit s'ouvrir déjà
# lisible, sans élargir les colonnes ni deviner le site et la période.
# Colonnes de l'onglet Électricité : (titre, largeur). La colonne
# buanderie n'existe que si le mois en a une, d'où des positions
# calculées et non figées.
_ELEC_COLONNES = {
    "zone": ("Zone", 28),
    "total": ("Consommation totale (kWh)", 22),
    "solaire": ("Solaire (kWh)", 18),
    "reseau": ("Réseau (kWh)", 18),
    "buanderie": (_BUANDERIE_HEADER, 18),
    "chf_solaire": ("Solaire HT (CHF)", 16),
    "chf_reseau": ("Réseau HT (CHF)", 16),
    "chf_location": ("Location HT (CHF)", 16),
    "tva": ("TVA (CHF)", 14),
    "ttc": ("Total TTC (CHF)", 18),
    "etat": ("État", 24),
}
_ELEC_KWH = ("total", "solaire", "reseau", "buanderie")
_ELEC_CHF = ("chf_solaire", "chf_reseau", "chf_location", "tva", "ttc")
_PARAMS_ROW = 4
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
    label, (start, end) = _mois_label(payload, period_key), _bornes_mois(payload, period_key)

    bat = ((payload.get("batiment") or {}).get("periodes") or {}).get(period_key) or {}
    generated = payload.get("generated_at")
    en_cours = bool(bat.get("en_cours")) or any(e.get("en_cours") for _, e in entries)
    if generated and end > generated:
        en_cours = True
    # Une plage de dates porte déjà ses dates dans son libellé.
    plage = bool(payload.get("detail"))

    if not en_cours:
        if plage:
            return label, "heure de Suisse"
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
    if stamps and plage:
        return label, f"dernier relevé le {_jour(max(stamps), tz)}, mois en cours"
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


def _notes_classeur(payload: dict, period_key: str, manquants: list[str],
                    postes: dict | None = None, methode: bool = True) -> list[tuple[str, str]]:
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
            "dans les montants de cet onglet, ni déduite des Communs. Elle est "
            "facturée dans l'onglet Buanderie.",
            "note",
        ))
    elif postes and postes.get("buanderie_avant_mise_en_service"):
        notes.append((
            "Buanderie : pas de décompte avant sa mise en service le 17.07.2026.",
            "note",
        ))
    # Méthode des parts réseau / solaire : admin seulement (décision de
    # l'utilisateur, 2026-10-29), comme sur la page.
    rep = repartition_du_mois(payload, period_key) if methode else {}
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
    elif rep.get("source") == "mixte":
        notes.append((
            "Selon le mois, ces parts sont calculées (compteur du lot réparti selon le "
            "mix du bâtiment) ou mesurées par le Miniserver : voir la colonne « Parts "
            "réseau / solaire » de l'onglet Détail par mois.",
            "note",
        ))
    c = chiffrage_du_mois(payload, period_key)
    if c and any(z.get("zone") == ZONE_PAC for z in payload.get("zones") or []):
        notes.append((
            "Ligne PAC : son électricité est le coût de la chaleur. Elle est répartie "
            "entre le chauffage et l'eau chaude des lots (onglet Récapitulatif) et "
            "n'est pas facturée telle quelle.",
            "note",
        ))
    if c:
        notes.append((
            "Montants HT : kWh × prix HT. TVA sur le réseau et la location"
            + (", et sur le solaire." if c["parametres"]["valeurs"]["tva_solaire"]["valeur"]
               else " ; pas de TVA sur le solaire."),
            "note",
        ))
    if payload.get("detail"):
        notes.append((
            "Chaque ligne additionne les mois de la période, détaillés dans l'onglet "
            "Détail par mois. Un mois sans relevé laisse la case vide : le total de "
            "la période n'est jamais incomplet en silence.",
            "note",
        ))
    notes.append((
        "Une case vide n'est pas un zéro : le relevé manque, et le total "
        "ne compte que les relevés présents.",
        "note",
    ))
    if not payload.get("zone_unique"):
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


def _formule_ttc(qte: str, prix: str, tva: str) -> str:
    """Montant TTC, vide si la quantité, le prix ou la TVA manque : un
    0 CHF ne doit pas se confondre avec une ligne sans relevé."""
    return f'=IF(OR({qte}="",{prix}="",{tva}=""),"",{qte}*{prix}*(1+{tva}/100))'


def _formule_somme(col: str, first: int, last: int) -> str:
    return f'=IF(COUNT({col}{first}:{col}{last})=0,"",SUM({col}{first}:{col}{last}))'


def _jour_fr(jour: str | None) -> str:
    return f"{jour[8:10]}.{jour[5:7]}.{jour[:4]}" if jour else ""


# Paramètres affichés en tête de l'onglet Électricité : (clé, libellé, format).
_PARAMS_AFFICHES = (
    ("prix_reseau", "Prix réseau HT (CHF/kWh)", _PRIX_FMT),
    ("prix_solaire", "Prix solaire HT (CHF/kWh)", _PRIX_FMT),
    ("taux_tva", "TVA (%)", _TVA_FMT),
    ("tva_solaire", "TVA sur le solaire", None),
    ("location_compteurs_an", "Location de compteurs (CHF HT par lot et par an)", _CHF_FMT),
)


def _ecrire_parametres(ws, c: dict | None, ncol: int) -> int:
    """Paramètres appliqués (page Paramètres), en lecture seule : les
    montants du classeur sont calculés par l'application. Retourne la
    dernière ligne écrite."""
    row = _PARAMS_ROW
    if not c:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncol)
        cell = ws.cell(row, 1, "Montants non calculés : paramètres du décompte indisponibles.")
        cell.font = _FONT_SUB
        return row
    valeurs = c["parametres"]["valeurs"]
    for cle, libelle, fmt in _PARAMS_AFFICHES:
        v = valeurs[cle]
        if cle == "location_compteurs_an" and v["valeur"] is None:
            continue
        ws.cell(row, 1, libelle).font = Font(bold=True, color=_NAVY)
        valeur = v["valeur"]
        if cle == "tva_solaire":
            valeur = "oui" if valeur else "non"
        cell = ws.cell(row, 2, valeur)
        cell.alignment = _ALIGN_RIGHT
        if fmt and isinstance(valeur, float):
            cell.number_format = fmt
        if v["origine"] == "defaut":
            origine = "non renseigné" if v["valeur"] is None else "valeur par défaut"
        else:
            origine = f"depuis le {_jour_fr(v['depuis'])}"
            if v["origine"] == "global":
                origine += " (valeur commune à tous les sites)"
        ws.cell(row, 3, origine).font = _FONT_NOTE
        ws.row_dimensions[row].height = 18
        row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncol)
    texte = ("Paramètres de la page Paramètres, en vigueur au premier jour de la période. "
             "Pour corriger un prix, le modifier dans la page et télécharger à nouveau le décompte.")
    if c["parametres"]["changent"]:
        texte += (" Attention : " + ", ".join(c["parametres"]["changent"])
                  + " change pendant la période ; la valeur du premier jour s'applique à toute la période.")
    cell = ws.cell(row, 1, texte)
    cell.font = _FONT_SUB
    cell.alignment = _ALIGN_WRAP
    ws.row_dimensions[row].height = 32
    return row


def _montants_electricite(payload: dict, period_key: str, zone: str | None) -> dict:
    """Montants d'une ligne de l'onglet Électricité (vides sans paramètres
    ou si un relevé manque). La ligne PAC porte le coût de la chaleur."""
    c = chiffrage_du_mois(payload, period_key)
    if not c:
        return {}
    m = c["montants"]
    e = m["pac"] if zone == ZONE_PAC else m["electricite"].get(zone)
    if not e:
        return {}
    out = {
        "chf_solaire": e["solaire"]["ht"],
        "chf_reseau": e["reseau"]["ht"],
        "tva": e["total"]["tva"],
        "ttc": e["total"]["ttc"],
    }
    if "location" in e:
        out["chf_location"] = e["location"]["ht"]
    return out


def _ecrire_onglet_recapitulatif(wb: Workbook, payload: dict, c: dict, site: str,
                                 label: str, couverture: str) -> None:
    """Une ligne par zone : TTC de chaque composante, puis HT, TVA, TTC."""
    colonnes_recap, lignes, total = lignes_recapitulatif(c)
    colonnes = [("Zone", 26, None)]
    colonnes += [(f"{t} TTC (CHF)", 16, _CHF_FMT) for _k, t in colonnes_recap]
    colonnes += [("Total HT (CHF)", 16, _CHF_FMT), ("TVA (CHF)", 14, _CHF_FMT),
                 ("Total TTC (CHF)", 18, _CHF_FMT)]
    rows = [[ln["zone"], *[ln.get(k) for k, _t in colonnes_recap], ln["ht"], ln["tva"], ln["ttc"]]
            for ln in lignes]
    total_row = [libelle_total(payload)]
    total_row += [_somme_affichee([ln.get(k) for ln in lignes]) for k, _t in colonnes_recap]
    total_row += [total["ht"], total["tva"], total["ttc"]] if total else [None, None, None]
    m = c["montants"]
    notes = [("Montants calculés par l'application avec les paramètres de la page "
              "Paramètres (détail dans chaque onglet).", "note")]
    if m["pac"] is not None and (m["chauffage"] or m["eau_chaude"]):
        part = c["parametres"]["valeurs"]["pac_part_chauffage"]["valeur"]
        notes.append((
            f"Chauffage et eau chaude : électricité de la PAC ({_fr(m['pac']['total']['ttc'], 2)} "
            f"CHF TTC) répartie à {_fr(part, 0)} % sur le chauffage, au prorata des kWh th, "
            "et le reste sur l'eau chaude, au prorata des m³. La PAC n'a donc pas de ligne ici.",
            "note"))
    if m["location_periode"] is not None:
        notes.append((
            f"Location de compteurs : {_fr(m['location_annuelle'], 2)} CHF HT par lot et par "
            f"an, soit {_fr(m['location_periode'], 2)} CHF HT sur la période (lots seulement), "
            "comprise dans l'électricité.", "note"))
    notes.append(("Une case vide n'est pas un zéro : un relevé ou un paramètre manque, "
                  "et le total de la ligne reste vide.", "note"))
    if m["messages"]:
        notes.append(("À compléter", "head"))
        notes.extend((msg, "note") for msg in m["messages"])
    hors = c["parametres"]["frais_hors_periode"]
    if hors:
        notes.append(("Frais non comptés", "head"))
        notes.extend((
            f"{f['zone']} — {f['libelle']} ({_jour_fr(f['du'])} → {_jour_fr(f['au'])}) : "
            "période à cheval sur celle du décompte.", "note") for f in hors)
    _ecrire_onglet_tableau(
        wb, "Récapitulatif", f"Récapitulatif — {site}" if site else "Récapitulatif",
        f"{label}  ·  {couverture}", colonnes, rows, total_row, notes,
        site, label, "Récapitulatif · CHF",
    )


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
    for p in payload.get("periodes") or []:
        if p.get("key") == period_key and "start" in p and "end" in p:
            return p["start"], p["end"]
    year, month = parse_period_key(period_key)
    return period_bounds(year, month, payload.get("timezone") or TIMEZONE)


def _ecrire_onglet_releves(wb: Workbook, entries: list, payload: dict, period_key: str,
                           site: str, label: str, couverture: str, tz: str, conn) -> None:
    """Une ligne par point horaire des statistiques du Miniserver, pour
    le compteur de chaque appartement."""
    calculee = repartition_du_mois(payload, period_key).get("source") == "calculee"
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


def _ecrire_tableau(ws, header_row: int, colonnes: list[tuple[str, int, str | None]],
                   lignes: list[list], total: list | None, filtre: bool = True) -> int:
    """En-tête en `header_row`, une ligne par entrée, puis le total.
    `filtre` : filtre automatique sur le tableau (un seul par onglet).
    Retourne la dernière ligne écrite."""
    ncol = len(colonnes)
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
    if lignes and filtre:
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

    return row


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
    row = _ecrire_tableau(ws, header_row, colonnes, lignes, total)
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


def _bloc_infos(ws, row: int, infos: list[tuple[str, object, str | None]], ncol: int) -> int:
    """Lignes « libellé : valeur » au-dessus d'un tableau. Retourne la
    dernière ligne écrite."""
    for libelle, valeur, fmt in infos:
        ws.cell(row, 1, libelle).font = Font(bold=True, color=_NAVY)
        if fmt is None:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=ncol)
            cell = ws.cell(row, 2, valeur)
            cell.font = _FONT_SUB
            cell.alignment = _ALIGN_WRAP
        else:
            cell = ws.cell(row, 2, valeur)
            cell.alignment = _ALIGN_RIGHT
            if isinstance(valeur, (int, float)):
                cell.number_format = fmt
        ws.row_dimensions[row].height = 18
        row += 1
    return row - 1


def _ecrire_onglet_poste(wb: Workbook, nom: str, titre: str, sous: str, infos: list,
                         releves: tuple[list, list, list | None],
                         repartition: tuple[list, list, list | None] | None,
                         notes: list, site: str, label: str, footer: str) -> None:
    """Onglet de poste : informations, tableau des relevés, puis (si les
    montants sont calculés) tableau de répartition du coût, et notes."""
    ws = wb.create_sheet(nom)
    colonnes, lignes, total = releves
    ncol = max(len(colonnes), len(repartition[0]) if repartition else 0)
    _ecrire_titre(ws, titre, sous, ncol)
    ws.row_dimensions[2].height = 36
    row = _bloc_infos(ws, 4, infos, ncol) if infos else 3
    header_row = row + 2
    row = _ecrire_tableau(ws, header_row, colonnes, lignes, total)
    largeurs = [w for _n, w, _f in colonnes]
    if repartition:
        r_cols, r_lignes, r_total = repartition
        row = _ecrire_tableau(ws, row + 3, r_cols, r_lignes, r_total, filtre=False)
        largeurs = [max(a, b) for a, b in zip(largeurs + [0] * ncol, [w for _n, w, _f in r_cols] + [0] * ncol)][:ncol]
    for c, width in enumerate(largeurs, start=1):
        ws.column_dimensions[get_column_letter(c)].width = width
    last_row = _ecrire_notes(ws, row + 2, notes, ncol, sum(largeurs))
    _mise_en_page(ws, ncol, max(last_row, header_row), header_row, site, label, footer)


def _ecrire_onglets_postes(wb: Workbook, postes: dict, c: dict | None, site: str, label: str,
                           couverture: str, libelle: str = "Total immeuble") -> None:
    """Onglets Chauffage, Eau chaude et Buanderie. Un onglet n'est créé que
    si le site a des compteurs de ce poste.

    Chauffage et eau chaude : le coût est l'électricité de la PAC, réparti
    selon la clé (page Paramètres) puis au prorata des quantités de chaque
    zone (services/billing/chiffrage.py). Les montants sont ceux de
    l'application, pas des formules."""
    m = c["montants"] if c else None
    labels = {z["zone"]: z["label"] for z in postes.get("chauffage", []) + postes.get("eau_chaude", [])}
    explications = {
        "chauffage": "Énergie de chauffage = index de fin − index de début du "
                     "compteur de chaleur. Le volume circulé n'est pas repris.",
        "eau_chaude": "Volume d'eau chaude sanitaire = index de fin − index de "
                      "début. L'eau froide n'est pas reprise.",
    }
    for role, (titre, grandeur, unite, dec) in _POSTE_TABLES.items():
        lignes, total_qte = lignes_compteurs(postes, role)
        if not lignes:
            continue
        qte_fmt = _M3_FMT if unite == "m³" else _KWH_FMT
        colonnes = [
            ("Zone", 22, None),
            ("Compteur", 46, None),
            (f"Index début ({unite})", 18, _INDEX_FMT),
            (f"Index fin ({unite})", 18, _INDEX_FMT),
            (f"{grandeur} ({unite})", 18, qte_fmt),
            ("État", 22, "etat"),
        ]
        rows = [[ln["zone"], ln["compteur"], ln["debut"], ln["fin"], ln["quantite"], ln["etat"]]
                for ln in lignes]

        poste = (m or {}).get(role)
        infos, repartition = [], None
        if poste:
            part = c["parametres"]["valeurs"]["pac_part_chauffage"]["valeur"]
            cle = part if role == "chauffage" else 100 - part
            infos = [
                (f"Coût {titre.lower()} HT (CHF)", poste["cout"]["ht"], _CHF_FMT),
                ("TVA (CHF)", poste["cout"]["tva"], _CHF_FMT),
                (f"Coût {titre.lower()} TTC (CHF)", poste["cout"]["ttc"], _CHF_FMT),
                ("Clé", f"{_fr(cle, 0)} % de l'électricité de la PAC "
                        f"({_fr(m['pac']['total']['ht'], 2)} CHF HT), au prorata "
                        f"{'des kWh th' if role == 'chauffage' else 'des m³'} de chaque zone.", None),
            ]
            par_zone = poste["par_zone"]
            if par_zone:
                somme = poste["quantite_totale"]
                r_cols = [
                    ("Zone", 22, None),
                    (f"{grandeur} ({unite})", 18, qte_fmt),
                    ("Part (%)", 12, "0.00"),
                    ("Montant HT (CHF)", 18, _CHF_FMT),
                    ("TVA (CHF)", 18, _CHF_FMT),
                    ("Montant TTC (CHF)", 18, _CHF_FMT),
                ]
                r_lignes = [[labels.get(z, z), v["quantite"], round(v["quantite"] / somme * 100, 2),
                             v["ht"], v["tva"], v["ttc"]] for z, v in par_zone.items()]
                r_total = [libelle, *[_somme_affichee([r[i] for r in r_lignes]) for i in (1, 2)],
                           *[_somme_affichee([v[k] for v in par_zone.values()])
                             for k in ("ht", "tva", "ttc")]]
                repartition = (r_cols, r_lignes, r_total)
        notes = [(explications[role], "note")]
        if poste and poste["par_zone"]:
            notes.append((
                "Répartition : le centime d'arrondi va à la plus grosse quantité, pour que "
                "la somme des zones égale le coût. Une zone relevée à 0 ne paie rien.", "note"))
        elif m is not None:
            notes.append(("Coût non réparti : voir « À compléter » dans l'onglet Récapitulatif.",
                          "note"))
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
        _ecrire_onglet_poste(
            wb, titre, f"{titre} — {site}" if site else titre, f"{label}  ·  {couverture}",
            infos, (colonnes, rows, [libelle, "", None, None, total_qte, ""]), repartition,
            notes, site, label, f"{titre} · {unite} et CHF",
        )

    lignes, totaux = lignes_buanderie(postes)
    if not lignes:
        return
    montants_b = (m or {}).get("buanderie") or {}
    colonnes = [
        ("Zone", 22, None),
        ("Cycles", 12, _CYCLES_FMT),
        ("Énergie (kWh)", 16, _KWH_FMT),
        ("Cycles (CHF)", 16, _CHF_FMT),
        ("Énergie HT (CHF)", 16, _CHF_FMT),
        ("TVA (CHF)", 14, _CHF_FMT),
        ("Total TTC (CHF)", 16, _CHF_FMT),
        ("État", 22, "etat"),
    ]
    rows = []
    for ln, b in zip(lignes, postes.get("buanderie") or []):
        mb = montants_b.get(b["zone"])
        rows.append([ln["zone"], ln["cycles"], ln["energie"],
                     mb["cycles"]["ht"] if mb else None, mb["kwh"]["ht"] if mb else None,
                     mb["total"]["tva"] if mb else None, mb["total"]["ttc"] if mb else None,
                     ln["etat"]])
    total = [libelle, totaux["cycles"], totaux["energie"],
             *[_somme_affichee([r[i] for r in rows]) for i in (3, 4, 5, 6)], ""]
    infos = []
    if c:
        prix_cycle = c["parametres"]["valeurs"]["buanderie_prix_cycle"]["valeur"]
        infos = [("Prix du cycle (CHF)", prix_cycle, _CHF_FMT),
                 ("Énergie", "au prix du kWh réseau, TVA en sus ; cycles sans TVA "
                             "(montant final).", None)]
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
    _ecrire_onglet_poste(
        wb, "Buanderie", f"Buanderie — {site}" if site else "Buanderie",
        f"{label}  ·  {couverture}", infos, (colonnes, rows, total), None,
        notes, site, label, "Buanderie · kWh, cycles et CHF",
    )


def _ecrire_onglet_detail(wb: Workbook, payload: dict, site: str, label: str,
                          couverture: str, methode: bool = True) -> None:
    """Une ligne par mois et par zone, quand l'export couvre plusieurs mois.
    Colonne « Parts réseau / solaire » seulement avec `methode`."""
    lignes = lignes_detail(payload)
    if not lignes:
        return
    colonnes = [
        ("Mois", 30, None),
        ("Zone", 22, None),
        ("Consommation totale (kWh)", 18, _KWH_FMT),
        ("Solaire (kWh)", 16, _KWH_FMT),
        ("Réseau (kWh)", 16, _KWH_FMT),
        ("Parts réseau / solaire", 18, None),
        ("État", 22, "etat"),
    ]
    rows = [[ln["mois"], ln["zone"], _kwh_affiche(ln["total"]), _kwh_affiche(ln["solaire"]),
             _kwh_affiche(ln["reseau"]), ln["parts"], ln["etat"]] for ln in lignes]
    notes = [
        ("Décompte de chaque mois de la période, calculé comme un mois seul. Le "
         "premier et le dernier mois sont rognés aux dates demandées.", "note"),
        ("« calculées » : compteur du lot réparti selon le mix du bâtiment ; "
         "« Miniserver » : mesures réseau et solaire du Miniserver.", "note"),
        ("Le tableau peut se filtrer par zone : la somme de ses mois est sa ligne "
         "de l'onglet Électricité.", "note"),
    ]
    if not methode:
        del colonnes[5]
        rows = [row[:5] + row[6:] for row in rows]
        del notes[1]
    _ecrire_onglet_tableau(
        wb, "Détail par mois", f"Détail par mois — {site}" if site else "Détail par mois",
        f"{label}  ·  {couverture}", colonnes, rows, None, notes,
        site, label, "Détail par mois · kWh",
    )


_FRAIS_COLONNES = [
    ("Zone", 24, None),
    ("Libellé", 34, None),
    ("Du", 12, None),
    ("Au", 12, None),
    ("Montant HT (CHF)", 16, _CHF_FMT),
    ("TVA (CHF)", 14, _CHF_FMT),
    ("Montant TTC (CHF)", 16, _CHF_FMT),
    ("Remarque", 40, None),
]


def _ecrire_onglet_frais(wb: Workbook, c: dict | None, zones: dict[str, tuple[str, int]],
                         site: str, label: str, couverture: str,
                         total: str = "Total immeuble") -> None:
    """Frais accessoires saisis dans la page Paramètres pour la période, un
    par ligne. `zones` : code de zone → (libellé, ligne dans Électricité) ;
    le nom de la zone est un lien vers sa ligne."""
    frais = c["montants"]["frais"] if c else []
    ws = wb.create_sheet("Frais supplémentaires")
    ncol = len(_FRAIS_COLONNES)
    _ecrire_titre(
        ws, f"Frais supplémentaires — {site}" if site else "Frais supplémentaires",
        f"{label}  ·  {couverture}", ncol,
    )
    ws.row_dimensions[2].height = 22
    header_row = 4
    rows = []
    for f in frais:
        mt = f["montant"]
        rows.append([zones.get(f["zone"], (f["zone"], None))[0], f["libelle"],
                     _jour_fr(f.get("du")), _jour_fr(f.get("au")),
                     mt["ht"] if mt else None, mt["tva"] if mt else None,
                     mt["ttc"] if mt else None, f.get("note") or ""])
    totaux = [total, "", "", "", *[_somme_affichee([r[i] for r in rows]) for i in (4, 5, 6)], ""]
    row = _ecrire_tableau(ws, header_row, _FRAIS_COLONNES, rows, totaux)
    for i, f in enumerate(frais):
        ligne_elec = zones.get(f["zone"], (None, None))[1]
        if ligne_elec:
            cell = ws.cell(header_row + 1 + i, 1)
            cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'Électricité'!A{ligne_elec}")
            cell.font = Font(color="0563C1", underline="single")
    for c_, (_n, width, _f) in enumerate(_FRAIS_COLONNES, start=1):
        ws.column_dimensions[get_column_letter(c_)].width = width
    if not c:
        notes = [("Montants non calculés : paramètres du décompte indisponibles.", "note")]
    elif not frais:
        notes = [("Aucun frais accessoire saisi pour cette période. Ils s'ajoutent dans la "
                  "page Paramètres (zone, période, libellé, montant HT, TVA oui ou non).", "note")]
    else:
        notes = [("Frais saisis dans la page Paramètres, dont la période est comprise dans "
                  "celle du décompte. Ils sont repris tels quels, sans calcul, dans le "
                  "Récapitulatif.", "note")]
    last_row = _ecrire_notes(ws, row + 2, notes, ncol, sum(w for _n, w, _f in _FRAIS_COLONNES))
    _mise_en_page(ws, ncol, last_row, header_row, site, label, "Frais supplémentaires · CHF")


def build_decompte_workbook(payload: dict, period_key: str, site: str, conn=None,
                            methode: bool = True) -> Workbook:
    """Classeur du mois choisi, ouvrable tel quel.

    Onglets : Récapitulatif (montants par zone, si `payload["chiffrage"]`),
    Électricité (kWh, paramètres appliqués et montants, colonne buanderie
    si le mois en a une), puis Chauffage, Eau chaude et Buanderie quand le
    site a ces compteurs (`payload["postes"]`), Frais supplémentaires
    (frais saisis dans la page Paramètres) et enfin les Relevés horaires.
    Sans `chiffrage`, les montants restent vides.

    Les chiffres sont ceux de `export_lignes` (donc de la page), arrondis
    au centième de kWh pour que la somme des lignes égale le total affiché.
    `methode` : dire comment les parts réseau / solaire sont obtenues
    (notes, colonne du détail par mois) ; admin seulement.

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
            **_montants_electricite(payload, period_key, z.get("zone")),
        })
    totaux = {k: _somme_affichee([ln[k] for ln in lignes]) for k in _ELEC_KWH}
    taux = None
    if totaux["total"]:
        taux = (totaux["solaire"] or 0.0) / totaux["total"]

    c = chiffrage_du_mois(payload, period_key)
    m = c["montants"] if c else None
    avec_location = bool(m and m["location_periode"] is not None)

    cles = ["zone", "total", "solaire", "reseau"]
    if buanderie is not None:
        cles.append("buanderie")
    cles += ["chf_solaire", "chf_reseau"] + (["chf_location"] if avec_location else [])
    cles += ["tva", "ttc", "etat"]
    col = {k: i for i, k in enumerate(cles, start=1)}
    ncol = len(cles)

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

    last_param = _ecrire_parametres(ws, c, ncol)
    header_row = last_param + 2

    for key in cles:
        cell = ws.cell(header_row, col[key], _ELEC_COLONNES[key][0])
        cell.font = _FONT_HEADER
        cell.fill = _FILL_HEADER
        cell.alignment = _ALIGN_CENTER
        cell.border = _THIN
    ws.row_dimensions[header_row].height = 32

    first_data = header_row + 1
    for i, ln in enumerate(lignes):
        row = first_data + i
        incomplet = ln["etat"] != "facturable"
        fill = _FILL_AMBER if incomplet else (_FILL_STRIPE if i % 2 else None)
        valeurs = {k: ln.get(k) for k in cles}
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
            cell.value = libelle_total(payload)
            cell.alignment = _ALIGN_LEFT
        elif key in _ELEC_KWH:
            cell.value = totaux[key]
            _style_kwh(cell, totaux[key])
        elif key in _ELEC_CHF:
            cell.value = _somme_affichee([ln.get(key) for ln in lignes])
            cell.number_format = _CHF_FMT
            cell.alignment = _ALIGN_RIGHT
        else:
            cell.alignment = _ALIGN_CENTER
    ws.row_dimensions[total_row].height = 22

    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncol)}{last_data}"

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
        ws, rate_row + 2, _notes_classeur(payload, period_key, manquants, postes, methode), ncol,
        sum(largeurs.values()),
    )
    _mise_en_page(ws, ncol, last_row, header_row, site, label, "Électricité · kWh et CHF")

    _ecrire_onglet_detail(wb, payload, site, label, couverture, methode)
    if postes:
        _ecrire_onglets_postes(wb, postes, c, site, label, couverture, libelle_total(payload))
    _ecrire_onglet_frais(
        wb, c, {z.get("zone"): (ln["zone"], first_data + i)
                for i, ((z, _e), ln) in enumerate(zip(entries, lignes))},
        site, label, couverture, libelle_total(payload),
    )
    _ecrire_onglet_releves(
        wb, entries, payload, period_key, site, label, couverture, tz, conn,
    )

    if c:
        _ecrire_onglet_recapitulatif(wb, payload, c, site, label, couverture)
        wb.move_sheet("Récapitulatif", offset=-(len(wb.sheetnames) - 1))
        wb.active = 0

    wb.properties.title = f"Décompte {site} {label}".strip()
    wb.properties.creator = "Loxone Collector"
    return wb
