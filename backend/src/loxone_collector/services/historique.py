"""
Historique mois par mois d'un immeuble, pour les graphiques de la page
Graphiques : consommation, production, autoconsommation, injection.

Mêmes chiffres que le décompte pour un mois complet (même calcul, même
fenêtre de jugement du bilan). Deux différences, propres aux graphiques
(décision de l'utilisateur, 2026-10-28 : un graphique incomplet plutôt
qu'un axe vide) :

- **mois incomplets** : le mois où les relevés commencent part du premier
  minuit qui suit le premier relevé (La Calbasse : octobre 2026 dès le
  15.10). Le décompte, lui, n'a aucun chiffre pour ce mois : un décompte
  ne se fait pas sur un mois tronqué sans le dire. Le mois en cours va
  jusqu'au dernier relevé, comme sur la page du décompte. Chaque mois
  tronqué est signalé (`partiel`, `debut_ts`, `fin_ts`).
- **douze derniers mois** (décision de l'utilisateur, 2026-10-29) : la
  réponse couvre toujours les 12 mois qui finissent par le mois en cours.
  Un mois sans relevé est présent mais vide (`vide`), pour que l'axe des
  graphiques garde ses 12 mois.
- **site sans part réseau / solaire par lot** (Horizon) : la consommation
  est la somme des compteurs de lot, la production le compteur « PV
  Production » du bâtiment. Autoconsommation et injection restent vides :
  ses compteurs de bâtiment ne permettent pas de bilan (septembre 2026 :
  −56 % ; octobre : −102 %).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loxone_collector.services import billing

# Production d'un site que `billing` ne reconnaît pas : « Compteur PV
# Production » d'Horizon (le même que l'aperçu du jour, dayBalance.ts).
PRODUCTION_REPLI_RE = re.compile(r"(?i)\bpv production \(total\)$")

# Un mois dont les relevés s'arrêtent plus d'un jour avant sa fin (ou avant
# maintenant) est signalé comme incomplet (Horizon, muet depuis le 16.10.2026).
MARGE_FIN_S = 86400

# Nombre de mois affichés, mois en cours compris.
NB_MOIS = 12


def _minuit_suivant(ts: int, tz_name: str) -> int:
    tz = ZoneInfo(tz_name)
    jour = datetime.fromtimestamp(ts, tz).date() + timedelta(days=1)
    return int(datetime(jour.year, jour.month, jour.day, tzinfo=tz).timestamp())


def _premiers_releves(conn, sources: list[dict]) -> list[int]:
    """Premier index Statistics de chaque compteur."""
    out = []
    for sid in {s["series_id"] for s in sources}:
        row = conn.execute(
            "SELECT MIN(ts) FROM statistic_samples WHERE series_id = ?", (sid,)
        ).fetchone()
        if row and row[0] is not None:
            out.append(row[0])
    return out


def _debut_des_releves(premiers: list[int], tz_name: str) -> int:
    """Début du premier mois mesuré : le premier minuit qui suit le DERNIER
    compteur à démarrer ce mois-là. Un compteur seul en avance ne suffit
    pas (La Calbasse : « Compteur Intro » dès le 01.10, les lots le 14.10) ;
    un compteur ajouté un autre mois ne compte pas (Wallbox de Sequoia)."""
    tz = ZoneInfo(tz_name)
    mois = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%Y-%m")  # noqa: E731
    premier = min(premiers)
    return _minuit_suivant(max(ts for ts in premiers if mois(ts) == mois(premier)), tz_name)


def _periodes(debut_ts: int, dernier_ts: int, tz_name: str) -> list[dict]:
    """Mois couverts par les relevés, le premier rogné à `debut_ts` (un
    relevé doit précéder le début). Un mois qui n'en garde rien est retiré
    (Sequoia : lots relevés depuis le 30.11.2025 à 23 h, novembre n'a aucun
    jour mesuré)."""
    out = []
    for p in billing.periods_covering(debut_ts, dernier_ts, tz_name):
        if debut_ts > p["start"]:
            if debut_ts >= p["end"]:
                continue
            p = {**p, "start": debut_ts}
        out.append(p)
    return out


def _site_sans_parts(zones: list[dict]) -> bool:
    """Aucune zone n'a de compteur réseau ni solaire : un seul compteur par
    lot, consommation non scindée (Horizon)."""
    return bool(zones) and not any(
        z["sources"].get("reseau") or z["sources"].get("solaire") for z in zones
    )


def _somme_complete(valeurs: list) -> float | None:
    """Somme, ou rien si une valeur manque : jamais un total trop bas en
    silence."""
    if not valeurs or any(v is None for v in valeurs):
        return None
    return sum(valeurs)


def _zone_mesuree(entree: dict) -> bool:
    """La zone a-t-elle un vrai relevé ce mois-ci ? La Wallbox de Sequoia
    vaut 0 kWh par règle avant le 04.03.2026, sans aucun relevé : seule,
    elle donnait une consommation de 0 au lieu de rien."""
    return any(entree[k].get("releve_debut_ts") is not None for k in ("controle", "reseau", "solaire"))


def _production_repli(conn, series: list[dict], p: dict, now_ts: int) -> float | None:
    src = next((s for s in billing.series_total(series)
                if not s.get("apartment") and PRODUCTION_REPLI_RE.search(s.get("label", ""))), None)
    if not src:
        return None
    # Statistics d'abord, sinon les relevés du collecteur (les Statistics
    # d'un compteur hors décompte ne sont pas importées).
    for statistics in (True, False):
        lecture = billing.reading_delta(conn, src["series_id"], p["start"], p["end"], now_ts,
                                        statistics=statistics)
        if lecture["kwh"] is not None:
            return lecture["kwh"]
    return None


def _derniers_mois(now_ts: int, tz_name: str) -> list[dict]:
    """Les `NB_MOIS` mois qui finissent par le mois de `now_ts`."""
    maintenant = datetime.fromtimestamp(now_ts, ZoneInfo(tz_name))
    rang = maintenant.year * 12 + maintenant.month - 1 - (NB_MOIS - 1)
    debut = billing.period_bounds(rang // 12, rang % 12 + 1, tz_name)[0]
    return billing.periods_covering(debut, now_ts, tz_name)


def _mois_vide(p: dict, now_ts: int) -> dict:
    return {
        "key": p["key"],
        "label": p["label"],
        "label_court": p["label_court"],
        "debut_ts": p["start"],
        "fin_ts": p["start"],
        "en_cours": p["end"] > now_ts,
        "partiel": False,
        "releves_arretes": False,
        "vide": True,
        "consommation": None,
        "production": None,
        "autoconsommation": None,
        "injection": None,
    }


def historique_mensuel(conn, series: list[dict], tarifs: list[dict], now_ts: int,
                       tz_name: str = billing.TIMEZONE) -> list[dict]:
    """Les `NB_MOIS` derniers mois, du plus ancien au plus récent ; un mois
    sans relevé est vide (`vide`)."""
    fenetre_affichee = _derniers_mois(now_ts, tz_name)
    mesures = {m["key"]: m for m in _mois_mesures(conn, series, tarifs, now_ts, tz_name,
                                                   fenetre_affichee[0]["start"])}
    return [mesures.get(p["key"]) or _mois_vide(p, now_ts) for p in fenetre_affichee]


def _mois_mesures(conn, series: list[dict], tarifs: list[dict], now_ts: int,
                  tz_name: str, depuis_ts: int) -> list[dict]:
    """Chiffres des mois relevés depuis `depuis_ts`."""
    zones = billing.resolve_zones(series)
    rng = billing.available_range(conn, zones, billing.resolve_batiment(series))
    if rng is None:
        return []
    premier_ts, dernier_ts = rng
    sources = [s for z in zones for s in z["sources"].values() if s]
    sources += [s for s in billing.resolve_batiment(series).values() if s]
    periodes = [p for p in _periodes(_debut_des_releves(_premiers_releves(conn, sources), tz_name),
                                     dernier_ts, tz_name)
                if p["end"] > depuis_ts]
    if not periodes:
        return []
    complets = billing.periods_covering(premier_ts, dernier_ts, tz_name)
    payload = billing.compute_decompte(
        conn, series, periodes, tarifs, now_ts, tz_name,
        fenetre=(complets[0]["start"], complets[-1]["end"]),
    )
    sans_parts = _site_sans_parts(payload["zones"])

    mois = []
    for p in periodes:
        b = payload["batiment"]["periodes"][p["key"]]
        consommation = b["consommation_totale"]
        production = b["production"]["kwh"]
        autoconsommation, injection = b["autoconsommation"], b["injection"]
        if not any(_zone_mesuree(z["periodes"][p["key"]]) for z in payload["zones"]):
            consommation = autoconsommation = injection = None
        if sans_parts:
            consommation = _somme_complete(
                [z["periodes"][p["key"]]["controle"]["kwh"] for z in payload["zones"]
                 if z["sources"].get("controle")]
            )
            if production is None:
                production = _production_repli(conn, series, p, now_ts)
            autoconsommation = injection = None

        en_cours = p["end"] > now_ts
        fin_attendue = min(p["end"], now_ts)
        fin_ts = min(fin_attendue, dernier_ts)
        debut_mois = billing.period_bounds(p["year"], p["month"], tz_name)[0]
        arretes = fin_ts < fin_attendue - MARGE_FIN_S
        mois.append({
            "key": p["key"],
            "label": p["label"],
            "label_court": p["label_court"],
            "debut_ts": p["start"],
            "fin_ts": fin_ts,
            "en_cours": en_cours,
            "partiel": p["start"] > debut_mois or arretes,
            # Relevés arrêtés bien avant la fin du mois (ou avant maintenant
            # pour le mois en cours) : Horizon depuis le 16.10.2026.
            "releves_arretes": arretes,
            "vide": False,
            "consommation": consommation,
            "production": production,
            "autoconsommation": autoconsommation,
            "injection": injection,
        })
    return mois
