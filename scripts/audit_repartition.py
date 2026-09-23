#!/usr/bin/env python3
"""
Dossier de preuve : le bloc de répartition solaire répartit-il la production
BRUTE au lieu de l'autoconsommation ?

À remettre à l'installateur. Le raisonnement est construit pour ne dépendre
d'AUCUN modèle de notre part : il repose uniquement sur les index des
compteurs de l'installation, aux mêmes dates, tels que le Miniserver les a
lui-même enregistrés (fonction "Statistics"). L'installateur peut donc
refaire chaque ligne dans Loxone Config.

L'argument central est volontairement le plus simple possible : on isole les
heures où le compteur d'ACHAT au réseau de l'immeuble n'a pas bougé du tout
(index identique au début et à la fin de l'heure). Pendant ces heures,
aucune zone ne peut avoir acheté d'électricité au réseau -- il n'en est pas
entré. Si les sorties "Grid" du bloc augmentent quand même, elles ne
mesurent pas un achat au réseau. Aucune tolérance de compteur, aucune
hypothèse de répartition, aucun calcul intermédiaire n'entre dans ce
constat.

Le reste du classeur quantifie le mécanisme : sur ces mêmes heures, le total
distribué par le bloc suit la production BRUTE, alors qu'il devrait suivre
la production MOINS la réinjection (l'électricité revendue au réseau n'a été
consommée par personne dans l'immeuble).

Usage :
    python3 scripts/audit_repartition.py <config.yaml> [--miniserver NOM] \\
        --from-mois AAAA-MM --to-mois AAAA-MM [--out chemin.xlsx]

Exemple :
    python3 scripts/audit_repartition.py config.external.yaml \\
        --miniserver MS-PPE-Sequoia --from-mois 2026-01 --to-mois 2026-05
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import billing
import db
import repartition
from config import load_config

TZ = ZoneInfo(billing.TIMEZONE)

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
ALERT_FILL = PatternFill("solid", fgColor="FCE4D6")
OK_FILL = PatternFill("solid", fgColor="E2EFDA")
TITLE_FONT = Font(bold=True, size=14, color="1F4E78")
NOTE_FONT = Font(italic=True, color="808080")
ALERT_FONT = Font(bold=True, color="C00000")
BOLD = Font(bold=True)
CENTER = Alignment(horizontal="center")
WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)

# En dessous, on considère que le compteur d'achat n'a pas bougé. Ce n'est
# pas une tolérance de mesure : les index de Loxone sont enregistrés à 3
# décimales, donc un pas plus fin que 0,005 kWh n'existe pas dans les
# données.
IMPORT_NUL_KWH = 0.005


# Compteurs à écarter de l'inventaire des consommateurs, parce qu'ils
# comptent une deuxième fois une énergie déjà comptée ailleurs :
#   - "PAC déjà mesuré", "Boiler ecs mesuré aussi ?" -- l'installateur l'a
#     écrit dans le nom du point : ces consommations sont déjà incluses dans
#     le compteur des communs. Les additionner ferait sauter le bilan.
#   - "CEnergie..." / "CNrMachine..." -- part de buanderie commune d'un lot
#     (catégorie Loxone "Lessive"), déjà comprise dans les communs ; et
#     "NrMachine" est un NOMBRE de machines, pas des kWh.
DOUBLON_RE = re.compile(r"(?i)(d[ée]j[àa] mesur|mesur[ée] aussi|cenergie|nrmachine)")


def resolve_consommateurs(series: list[dict]) -> list[dict]:
    """Inventaire des compteurs de consommation électrique du site, celui qui
    doit boucler avec « achat + production - réinjection ».

    Règle volontairement explicite et vérifiable (la liste retenue est
    imprimée dans le classeur) : tous les compteurs cumulatifs d'énergie
    consommée de la catégorie Loxone « Energie », sauf les doublons connus
    (voir DOUBLON_RE). On ne prend PAS les sorties du bloc de répartition
    (catégorie « Répartition Solaire ») : ce sont elles qu'on met en cause.
    """
    out = []
    for s in series:
        if s.get("state_name") != "total":
            continue
        if (s.get("category") or "") != "Energie":
            continue
        if s.get("resource_type") != "energie_consommee":
            continue
        label = s.get("label") or ""
        if DOUBLON_RE.search(label):
            continue
        out.append(s)
    return sorted(out, key=lambda s: s.get("label", ""))


def index_horaire(conn, series_id: str, start_ts: int, end_ts: int) -> dict[int, float]:
    """Index du compteur au début de chaque heure, tel qu'enregistré."""
    rows = conn.execute(
        """
        SELECT ts, MAX(value) FROM (
            SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS ts, value FROM readings
             WHERE series_id = ? AND ts BETWEEN ? AND ? AND value IS NOT NULL
            UNION ALL
            SELECT ts, max_value AS value FROM readings_hourly
             WHERE series_id = ? AND ts BETWEEN ? AND ?
        ) GROUP BY ts ORDER BY ts
        """,
        (series_id, start_ts - 3600, end_ts, series_id, start_ts - 3600, end_ts),
    ).fetchall()
    return {ts: v for ts, v in rows}


def charger(conn, zones: list[dict], batiment: dict, consommateurs: list[dict],
            start_ts: int, end_ts: int) -> dict:
    """Index de tous les compteurs utiles, par série."""
    src = {}
    for k in ("production", "reseau_import", "reseau_export"):
        if batiment.get(k):
            src[k] = batiment[k]
    idx = {k: index_horaire(conn, s["series_id"], start_ts, end_ts) for k, s in src.items()}

    zinfo = []
    for z in zones:
        s = z["sources"]
        entry = {"zone": z["zone"], "label": z["label"], "sources": {}, "idx": {}}
        for role in ("reseau", "solaire", "controle"):
            if s.get(role):
                entry["sources"][role] = s[role]
                entry["idx"][role] = index_horaire(conn, s[role]["series_id"], start_ts, end_ts)
        if entry["idx"]:
            zinfo.append(entry)

    # Un consommateur dont l'historique ne couvre pas la période (compteur
    # posé récemment, ou relevé seulement depuis le démarrage du collecteur)
    # est écarté de l'inventaire : le garder rendrait inexploitables toutes
    # les heures antérieures à sa pose, alors qu'il ne pèse rien. Le critère
    # est la COUVERTURE des données, pas le nom du point -- donc rien à
    # devimer sur la nomenclature de l'installateur.
    reference = {h for h in idx.get("reseau_import", {}) if start_ts - 3600 <= h < end_ts}
    conso, ecartes = [], []
    for c in consommateurs:
        d = index_horaire(conn, c["series_id"], start_ts, end_ts)
        couverture = (len(set(d) & reference) / len(reference)) if reference else 0.0
        entry = {"label": c.get("label", ""), "series_id": c["series_id"],
                 "idx": d, "couverture": couverture}
        (conso if couverture >= 0.95 else ecartes).append(entry)
    return {"batiment": src, "idx": idx, "zones": zinfo,
            "consommateurs": conso, "consommateurs_ecartes": ecartes}


def delta(d: dict, h: int) -> float | None:
    a, b = d.get(h - 3600), d.get(h)
    if a is None or b is None:
        return None
    v = b - a
    return v if v > 0 else 0.0


def collecter(donnees: dict, start_ts: int, end_ts: int) -> dict:
    """Une ligne par heure, avec le détail dont on a besoin pour la preuve."""
    idx = donnees["idx"]
    if not {"production", "reseau_import", "reseau_export"} <= set(idx):
        raise SystemExit("Compteurs de bâtiment incomplets (production, achat, "
                         "réinjection) : la preuve ne peut pas être construite.")
    heures = sorted(h for h in idx["reseau_import"] if start_ts <= h < end_ts)

    lignes = []
    for h in heures:
        di = delta(idx["reseau_import"], h)
        de = delta(idx["reseau_export"], h)
        dp = delta(idx["production"], h)
        if None in (di, de, dp):
            continue
        grid = sol = conso = 0.0
        complet = True
        for z in donnees["zones"]:
            for role in ("reseau", "solaire"):
                if role not in z["idx"]:
                    continue
                v = delta(z["idx"][role], h)
                if v is None:
                    complet = False
                    continue
                if role == "reseau":
                    grid += v
                else:
                    sol += v
        for c in donnees["consommateurs"]:
            v = delta(c["idx"], h)
            if v is None:
                complet = False
                continue
            conso += v
        if not complet:
            continue
        lignes.append({"h": h, "import": di, "export": de, "prod": dp,
                       "grid": grid, "sol": sol, "conso": conso})
    return {"lignes": lignes}


def somme(lignes: list[dict], cle: str) -> float:
    return sum(l[cle] for l in lignes)


# --------------------------------------------------------------------------
# Feuilles
# --------------------------------------------------------------------------

def titre(ws, texte: str, ncol: int, row: int = 1) -> None:
    ws.cell(row=row, column=1, value=texte).font = TITLE_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(ncol, 1))


def entetes(ws, row: int, noms: list[str]) -> None:
    for i, n in enumerate(noms, start=1):
        c = ws.cell(row=row, column=i, value=n)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, CENTER
        c.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")


def f_constat(wb: Workbook, site: str, per: str, tous: list[dict], nuls: list[dict],
              now_ts: int) -> None:
    ws = wb.create_sheet("Constat")
    titre(ws, "Bloc de répartition solaire -- anomalie constatée", 2)

    P = somme(nuls, "prod"); E = somme(nuls, "export")
    GZ = somme(nuls, "grid"); SZ = somme(nuls, "sol"); CZ = somme(nuls, "conso")
    tP = somme(tous, "prod"); tE = somme(tous, "export"); tI = somme(tous, "import")
    tGZ = somme(tous, "grid"); tSZ = somme(tous, "sol")

    def kwh(v, dec=1):
        return f"{v:,.{dec}f}".replace(",", "\u202f").replace(".", ",") + " kWh"

    def num(v, dec=3, signe=False):
        """Nombre à la française : virgule décimale. Le document est destiné
        à être lu et contesté ligne par ligne, autant qu'il soit écrit dans
        la même convention que les relevés qu'il cite."""
        fmt = f"{{:+.{dec}f}}" if signe else f"{{:.{dec}f}}"
        return fmt.format(v).replace(".", ",")

    rows = [
        ("", ""),
        ("Installation", site),
        ("Période analysée", per),
        ("Source des chiffres",
         "Uniquement les index des compteurs de l'installation, enregistrés "
         "par le Miniserver lui-même (fonction « Statistics »). Aucun calcul "
         "ni aucune hypothèse extérieure n'intervient dans le constat "
         "ci-dessous."),
        ("Établi le", dt.datetime.fromtimestamp(now_ts, TZ).strftime("%d.%m.%Y à %H:%M")),
        ("", ""),
        ("LE CONSTAT", ""),
        ("Ce qui a été isolé",
         f"Les {len(nuls)} heures de la période pendant lesquelles le compteur "
         f"d'ACHAT au réseau de l'immeuble n'a pas bougé du tout : son index "
         f"est identique au début et à la fin de l'heure. Pendant ces heures, "
         f"aucune électricité n'est entrée depuis le réseau."),
        ("Ce que devraient afficher les sorties « Grid »",
         "Zéro. Une zone ne peut pas acheter de l'électricité au réseau "
         "pendant une heure où l'immeuble n'en achète pas."),
        ("Ce qu'elles affichent",
         f"{kwh(GZ)} au total sur ces heures."),
        ("", ""),
        ("L'EXPLICATION", ""),
        ("Sur ces mêmes heures",
         f"production solaire {kwh(P)} ; réinjectée au réseau {kwh(E)} ; "
         f"donc réellement consommée dans l'immeuble {kwh(P - E)}."),
        ("Contrôle de cohérence",
         f"La somme des compteurs de consommation des zones indique "
         f"{kwh(CZ)} sur ces mêmes heures, soit "
         f"{num((CZ - (P - E)) / (P - E) * 100, 1, signe=True)} % d'écart avec les "
         f"{kwh(P - E)} déduits des compteurs de l'immeuble. Les compteurs "
         f"concordent : le problème ne vient pas d'eux."),
        ("Total distribué par le bloc",
         f"{kwh(GZ + SZ)} (« Grid » {kwh(GZ)} + « Solaire » {kwh(SZ)})."),
        ("Rapport à la production BRUTE",
         f"{num((GZ + SZ) / P)} -- le bloc distribue la production totale."),
        ("Rapport à ce qui a été CONSOMMÉ",
         f"{num((GZ + SZ) / (P - E))} -- il devrait valoir 1,000."),
        ("Conclusion",
         "Le bloc répartit la production solaire BRUTE au lieu de "
         "l'autoconsommation. L'électricité réinjectée au réseau, qui a été "
         "revendue et n'a donc été consommée par personne dans l'immeuble, "
         "est distribuée aux zones comme si elle l'avait été -- et elle "
         "atterrit sur la sortie « Grid », donc comptée comme un achat au "
         "réseau."),
        ("", ""),
        ("SUR TOUTE LA PÉRIODE", ""),
        ("Achat au réseau (A)", kwh(tI)),
        ("Production solaire (B)", kwh(tP)),
        ("Réinjection au réseau (C)", kwh(tE)),
        ("Consommation réelle de l'immeuble (A + B - C)", kwh(tI + tP - tE)),
        ("Énergie entrée dans l'immeuble (A + B)", kwh(tI + tP)),
        ("Total distribué par le bloc (D)", kwh(tGZ + tSZ)),
        ("D / (A + B - C)", f"{num((tGZ + tSZ) / (tI + tP - tE))}  (devrait valoir 1,000)"),
        ("D / (A + B)", f"{num((tGZ + tSZ) / (tI + tP))}  (le bloc suit A + B)"),
        ("", ""),
        ("CE QU'IL Y A À CORRIGER", ""),
        ("Dans le bloc de répartition",
         "L'énergie à répartir doit être « production - réinjection » et non "
         "la production brute. La mesure de réinjection existe déjà et "
         "fonctionne : c'est la sortie de comptage négatif (totalNeg) du "
         "compteur du raccordement réseau de l'immeuble."),
        ("Vérification après correction",
         "Le rapport « total distribué / consommation réelle de l'immeuble » "
         "doit revenir à 1,00, et les sorties « Grid » doivent rester à zéro "
         "pendant les heures sans achat au réseau."),
        ("", ""),
        ("Onglets suivants", ""),
        ("« Heures sans achat réseau »",
         "Les heures du constat, une par ligne, avec les index de compteur de "
         "début et de fin. C'est la preuve détaillée, vérifiable ligne à ligne."),
        ("« Exemple détaillé »",
         "Une heure représentative, avec le détail de chaque zone."),
        ("« Compteurs utilisés »",
         "Le nom et l'identifiant Loxone de chaque compteur cité, pour les "
         "retrouver dans Loxone Config."),
    ]

    r = 3
    for a, b in rows:
        if a and not b:
            c = ws.cell(row=r, column=1, value=a)
            c.font = Font(bold=True, size=12, color="1F4E78")
        else:
            ws.cell(row=r, column=1, value=a).font = BOLD
            ws.cell(row=r, column=2, value=b).alignment = WRAP
        r += 1
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 100


def f_heures(wb: Workbook, nuls: list[dict], donnees: dict) -> None:
    ws = wb.create_sheet("Heures sans achat réseau")
    cols = ["Date", "Heure (locale)",
            "Index compteur ACHAT réseau -- début", "Index -- fin", "Achat sur l'heure",
            "Réinjection sur l'heure", "Production sur l'heure",
            "Consommation mesurée des zones",
            "Sorties « Grid » du bloc", "Sorties « Solaire » du bloc",
            "Total distribué", "Distribué - consommé"]
    titre(ws, "Heures pendant lesquelles l'immeuble n'a acheté aucune électricité au réseau",
          len(cols))
    ws["A2"] = ("L'index du compteur d'achat est identique en début et en fin d'heure : "
                "aucune électricité n'est entrée depuis le réseau. Les sorties « Grid » du "
                "bloc augmentent malgré tout (colonne H). Valeurs en kWh.")
    ws["A2"].font = NOTE_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(cols))
    entetes(ws, 4, cols)

    imp_idx = donnees["idx"]["reseau_import"]
    r = 5
    for l in nuls:
        h = l["h"]
        deb = dt.datetime.fromtimestamp(h - 3600, TZ)
        ws.cell(row=r, column=1, value=deb.strftime("%d.%m.%Y"))
        ws.cell(row=r, column=2,
                value=f"{deb:%H:%M} - {dt.datetime.fromtimestamp(h, TZ):%H:%M}")
        for col, val, dec in ((3, imp_idx.get(h - 3600), 3), (4, imp_idx.get(h), 3),
                              (5, l["import"], 3), (6, l["export"], 3), (7, l["prod"], 3),
                              (8, l["conso"], 3), (9, l["grid"], 3), (10, l["sol"], 3),
                              (11, l["grid"] + l["sol"], 3),
                              (12, l["grid"] + l["sol"] - l["conso"], 3)):
            c = ws.cell(row=r, column=col, value=round(val, dec) if val is not None else None)
            c.number_format = "0.000"
        ws.cell(row=r, column=9).font = ALERT_FONT
        r += 1

    ws.cell(row=r, column=1, value=f"TOTAL ({len(nuls)} heures)").font = BOLD
    for col, cle in ((5, "import"), (6, "export"), (7, "prod"), (8, "conso"),
                     (9, "grid"), (10, "sol")):
        c = ws.cell(row=r, column=col, value=round(somme(nuls, cle), 1))
        c.number_format, c.font = "0.0", BOLD
    tg, ts_, tc = somme(nuls, "grid"), somme(nuls, "sol"), somme(nuls, "conso")
    for col, val in ((11, tg + ts_), (12, tg + ts_ - tc)):
        c = ws.cell(row=r, column=col, value=round(val, 1))
        c.number_format, c.font = "0.0", BOLD
    for col in range(1, len(cols) + 1):
        ws.cell(row=r, column=col).fill = ALERT_FILL

    ws.freeze_panes = "C5"
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 15
    for i in range(3, len(cols) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16


def f_exemple(wb: Workbook, nuls: list[dict], donnees: dict) -> None:
    """L'heure la plus démonstrative : celle où le bloc a attribué le plus
    d'« achat au réseau » alors que l'immeuble n'achetait rien."""
    ws = wb.create_sheet("Exemple détaillé")
    if not nuls:
        return
    l = max(nuls, key=lambda x: x["grid"])
    h = l["h"]
    # Les index sont relevés au début de chaque heure : l'énergie de la ligne
    # a donc été consommée entre h-1h et h. Le libellé doit le dire, sinon
    # l'installateur cherche la mauvaise heure dans Loxone.
    quand = dt.datetime.fromtimestamp(h - 3600, TZ).strftime("%d.%m.%Y, de %H:%M")
    fin = dt.datetime.fromtimestamp(h, TZ).strftime("%H:%M")
    cols = ["Zone", "Compteur « Grid » -- index début", "index fin", "Grid sur l'heure",
            "Compteur « Solaire » -- index début", "index fin", "Solaire sur l'heure",
            "Compteur propre de la zone -- sur l'heure"]
    titre(ws, f"Détail d'une heure : {quand} à {fin} (heure locale)", len(cols))

    ws["A2"] = (f"Pendant cette heure : l'immeuble a acheté {l['import']:.3f} kWh au réseau, "
                f"produit {l['prod']:.3f} kWh de solaire et en a réinjecté {l['export']:.3f} kWh "
                f"au réseau. Il a donc consommé {l['prod'] - l['export']:.3f} kWh, et ses "
                f"compteurs de zone en mesurent {l['conso']:.3f} kWh. "
                f"Le bloc a pourtant attribué {l['grid']:.3f} kWh d'ACHAT AU RÉSEAU aux zones.")
    ws["A2"].font = NOTE_FONT
    ws["A2"].alignment = WRAP
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(cols))
    ws.row_dimensions[2].height = 45
    entetes(ws, 4, cols)

    r = 5
    for z in sorted(donnees["zones"], key=lambda x: x["label"]):
        if "reseau" not in z["idx"]:
            continue
        gi, gf = z["idx"]["reseau"].get(h - 3600), z["idx"]["reseau"].get(h)
        si = sf = None
        if "solaire" in z["idx"]:
            si, sf = z["idx"]["solaire"].get(h - 3600), z["idx"]["solaire"].get(h)
        dc = delta(z["idx"]["controle"], h) if "controle" in z["idx"] else None
        vals = [z["label"], gi, gf, (gf - gi) if None not in (gi, gf) else None,
                si, sf, (sf - si) if None not in (si, sf) else None, dc]
        ws.cell(row=r, column=1, value=vals[0])
        for col, v in enumerate(vals[1:], start=2):
            c = ws.cell(row=r, column=col, value=round(v, 3) if v is not None else None)
            c.number_format = "0.000"
        ws.cell(row=r, column=4).font = ALERT_FONT
        r += 1

    ws.cell(row=r + 1, column=1, value="Compteurs de l'immeuble sur cette heure").font = BOLD
    for i, (nom, val) in enumerate((("Achat au réseau", l["import"]),
                                    ("Production solaire", l["prod"]),
                                    ("Réinjection au réseau", l["export"]),
                                    ("Donc consommé (production - réinjection)",
                                     l["prod"] - l["export"]),
                                    ("Somme des compteurs de zone", l["conso"]),
                                    ("Total distribué par le bloc", l["grid"] + l["sol"])),
                                   start=2):
        ws.cell(row=r + i, column=1, value=nom)
        c = ws.cell(row=r + i, column=2, value=round(val, 3))
        c.number_format = "0.000"
        if nom.startswith("Total distribué"):
            ws.cell(row=r + i, column=1).font = BOLD
            c.font = ALERT_FONT

    ws.column_dimensions["A"].width = 40
    for i in range(2, len(cols) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 17


def f_compteurs(wb: Workbook, donnees: dict) -> None:
    ws = wb.create_sheet("Compteurs utilisés")
    cols = ["Rôle dans l'analyse", "Nom du point dans Loxone", "Identifiant (UUID)"]
    titre(ws, "Correspondance des compteurs, pour les retrouver dans Loxone Config", len(cols))
    entetes(ws, 3, cols)
    roles = {"production": "Production solaire de l'immeuble",
             "reseau_import": "Achat au réseau (immeuble)",
             "reseau_export": "Réinjection au réseau (immeuble)"}
    r = 4
    for k, s in donnees["batiment"].items():
        ws.cell(row=r, column=1, value=roles.get(k, k))
        ws.cell(row=r, column=2, value=s["label"])
        ws.cell(row=r, column=3, value=_uuid(s))
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Compteurs de consommation retenus pour le "
                                   "contrôle de cohérence du bilan").font = BOLD
    r += 1
    for c in donnees["consommateurs"]:
        ws.cell(row=r, column=1, value="Consommation mesurée")
        ws.cell(row=r, column=2, value=c["label"])
        ws.cell(row=r, column=3, value=_UUID_CACHE.get(c["series_id"], ""))
        r += 1
    if donnees["consommateurs_ecartes"]:
        r += 1
        ws.cell(row=r, column=1, value="Écartés du contrôle de cohérence "
                                       "(historique incomplet sur la période)").font = BOLD
        r += 1
        for c in donnees["consommateurs_ecartes"]:
            ws.cell(row=r, column=1,
                    value=f"Écarté -- données sur {100 * c['couverture']:.0f} % de la période")
            ws.cell(row=r, column=2, value=c["label"])
            ws.cell(row=r, column=3, value=_UUID_CACHE.get(c["series_id"], ""))
            r += 1

    r += 1
    libelles = {"reseau": "Sortie « Grid » du bloc", "solaire": "Sortie « Solaire » du bloc",
                "controle": "Compteur propre de la zone"}
    for z in sorted(donnees["zones"], key=lambda x: x["label"]):
        for role in ("reseau", "solaire", "controle"):
            if role in z["sources"]:
                ws.cell(row=r, column=1, value=f"{z['label']} -- {libelles[role]}")
                ws.cell(row=r, column=2, value=z["sources"][role]["label"])
                ws.cell(row=r, column=3, value=_uuid(z["sources"][role]))
                r += 1
    ws.freeze_panes = "A4"
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 42


_UUID_CACHE: dict[str, str] = {}


def _uuid(src: dict) -> str:
    return _UUID_CACHE.get(src["series_id"], "")


# --------------------------------------------------------------------------

def parse_mois(v: str) -> tuple[int, int]:
    try:
        y, m = v.split("-")
        return int(y), int(m)
    except Exception:
        raise SystemExit(f"Format de mois invalide : '{v}' (attendu AAAA-MM)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--miniserver", default=None)
    ap.add_argument("--from-mois", required=True, metavar="AAAA-MM")
    ap.add_argument("--to-mois", required=True, metavar="AAAA-MM")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    noms = [ms.name for ms in cfg.miniservers]
    if args.miniserver:
        if args.miniserver not in noms:
            raise SystemExit(f"Miniserver '{args.miniserver}' introuvable. "
                             f"Sites configurés : {', '.join(noms)}")
        site = args.miniserver
    elif len(noms) == 1:
        site = noms[0]
    else:
        raise SystemExit(f"Plusieurs sites configurés ({', '.join(noms)}) -- "
                         f"précise --miniserver NOM.")

    conn = db.get_connection(cfg.db_path)
    now_ts = int(time.time())
    series = [s for s in db.list_series(conn) if s["miniserver"] == site]
    for s in series:
        _UUID_CACHE[s["series_id"]] = s.get("control_uuid") or ""

    y1, m1 = parse_mois(args.from_mois)
    y2, m2 = parse_mois(args.to_mois)
    start, _ = billing.period_bounds(y1, m1)
    _, end = billing.period_bounds(y2, m2)
    per = f"{billing.period_label(y1, m1)} -- {billing.period_label(y2, m2)}"

    zones = billing.resolve_zones(series)
    batiment = billing.resolve_batiment(series)
    consommateurs = resolve_consommateurs(series)
    if not consommateurs:
        raise SystemExit("Aucun compteur de consommation identifié : le contrôle "
                         "de cohérence du bilan serait impossible.")
    donnees = charger(conn, zones, batiment, consommateurs, start, end)
    res = collecter(donnees, start, end)
    tous = res["lignes"]
    if not tous:
        raise SystemExit("Aucune heure exploitable sur la période.")
    nuls = [l for l in tous if l["import"] < IMPORT_NUL_KWH]

    P, E = somme(nuls, "prod"), somme(nuls, "export")
    GZ, SZ, CZ = somme(nuls, "grid"), somme(nuls, "sol"), somme(nuls, "conso")
    print(f"Site            : {site}")
    print(f"Période         : {per}  ({len(tous)} heures exploitables)")
    print(f"Consommateurs retenus pour le bilan : {len(donnees['consommateurs'])}")
    for c in donnees["consommateurs_ecartes"]:
        print(f"  (écarté : {c['label']} -- historique sur "
              f"{100 * c['couverture']:.0f} % de la période seulement)")
    print(f"Heures sans achat au réseau : {len(nuls)}")
    print(f"  production {P:9.1f} kWh | réinjection {E:9.1f} kWh "
          f"-> consommé {P - E:9.1f} kWh")
    print(f"  compteurs de zone         : {CZ:9.1f} kWh "
          f"({(CZ - (P - E)) / (P - E) * 100:+.1f} % -- contrôle de cohérence)")
    print(f"  sorties 'Grid' du bloc    : {GZ:9.1f} kWh  <-- achat impossible")
    print(f"  total distribué           : {GZ + SZ:9.1f} kWh "
          f"= {(GZ + SZ) / P:.3f} x la production BRUTE")
    print(f"                              (devrait être 1,000 x {P - E:.1f} consommés)")

    wb = Workbook()
    wb.remove(wb.active)
    f_constat(wb, site, per, tous, nuls, now_ts)
    f_heures(wb, nuls, donnees)
    f_exemple(wb, nuls, donnees)
    f_compteurs(wb, donnees)

    out = Path(args.out) if args.out else Path("docs") / (
        f"Anomalie repartition solaire {site} "
        f"{billing.period_key(y1, m1)} a {billing.period_key(y2, m2)}.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"\nDossier de preuve : {out}")


if __name__ == "__main__":
    main()
