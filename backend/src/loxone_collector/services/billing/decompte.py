"""
Décompte électrique complet : par zone et par période, part réseau et
part solaire autoconsommée, tarifs et montants, synthèse du bâtiment,
et cumul de plusieurs mois sur une plage de dates.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from loxone_collector.services import repartition
from loxone_collector.services.billing.compteurs import (
    RESERVES_ZONES,
    production_couvrante,
    productions_sur_fenetre,
    resolve_batiment,
    resolve_zones,
)
from loxone_collector.services.billing.periodes import TIMEZONE
from loxone_collector.services.billing.releves import (
    _absent,
    delta_compteurs_successifs,
    reading_delta,
)

# Précision des parts réseau/solaire recalculées : celle de l'affichage,
# pour que réseau + solaire = consommation à l'affichage près.
KWH_DECIMALES = 2


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
        # Zone de chaque note de `reserves`, dans le même ordre : la page
        # filtrée sur une zone n'affiche que la sienne.
        "reserves_zones": [z["zone"] for z in zones if z["zone"] in RESERVES_ZONES],
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
# facturer : 0 kWh, pas un relevé manquant.
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
