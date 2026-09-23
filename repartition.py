"""
repartition.py
--------------
Reconstruction de la part solaire / part réseau de chaque zone, à partir des
seuls compteurs fiables, quand le bloc "Répartition Solaire" du Miniserver
ne l'est pas.

POURQUOI CE MODULE EXISTE (établi empiriquement sur MS-PPE-Sequoia,
2026-09-09 -- ne pas re-supposer) : à Arlopi, chaque zone a un bloc EFM dont
les sorties Grid/Solaire sont de VRAIES mesures, validées contre
`Gpwr`/`Ppwr`/`selfConsumption` du bloc (voir CLAUDE.md). À Sequoia, les
séries homonymes `App XX Grid`/`App XX Solaire` proviennent d'un bloc
"Répartition Solaire" et sont inexploitables :

  - sur 881 heures où le bâtiment n'a RIEN importé du réseau, les 881 voient
    malgré tout les compteurs "Grid" des lots augmenter (4 999 kWh d'achat
    au réseau physiquement impossible) ;
  - 575 heures de production > 1 kWh ont TOUS les "Solaire" de lot à zéro ;
  - la somme des Grid+Solaire de toutes les zones (36 000 kWh sur
    janvier-mai 2026) correspond à l'énergie ENTRÉE dans le bâtiment, pas à
    l'énergie consommée : les 9 926 kWh réinjectés au réseau sont
    redistribués aux lots comme s'ils avaient été consommés.

En revanche le compteur de consommation propre de chaque lot
(`Appartement N` chez Sequoia, `sources["controle"]` de billing.resolve_zones)
est fiable : la nuit, quand le bloc de répartition n'a rien à distribuer, il
concorde à 1-3 % près avec Grid+Solaire sur tous les lots testés. Et le bilan
du bâtiment se ferme à -0,64 % sur janvier-mai 2026 (27 426,6 kWh entrés
contre 27 251,5 kWh aux compteurs de consommation).

MÉTHODE : le modèle RCP standard, celui qu'appliquent les prestataires du
marché (Climkit : « calcule toutes les 15 minutes la part solaire et la part
réseau de chaque consommateur »). Pour chaque heure h :

    solaire_autoconsomme(h) = production(h) - injection(h)
    consommation_batiment(h) = import(h) + production(h) - injection(h)
    part_solaire(h) = solaire_autoconsomme(h) / consommation_batiment(h)

puis, pour une zone de consommation mesurée conso_i(h) :

    solaire_i(h) = conso_i(h) x part_solaire(h)
    reseau_i(h)  = conso_i(h) x (1 - part_solaire(h))

Deux propriétés à connaître avant de facturer sur cette base :

  - `reseau_i + solaire_i = conso_i` EXACTEMENT, pour chaque zone et chaque
    heure. Le total facturé à un lot reste donc toujours sa consommation
    réellement mesurée : la répartition ne fait que la scinder en deux
    prix, elle n'invente pas de kWh.
  - au niveau du bâtiment, la somme des parts solaires attribuées égale le
    solaire autoconsommé À L'ERREUR DE BOUCLAGE DU BILAN PRÈS (-0,64 % sur
    la période mesurée), parce que le dénominateur vient des compteurs de
    bâtiment et non de la somme des compteurs de zone. `qualite_bilan()`
    mesure cet écart pour qu'il soit affiché plutôt que supposé.

Résolution : horaire, c'est ce que l'historique Statistics de Loxone fournit
(Climkit travaille au quart d'heure). Sur une facture mensuelle l'effet est
faible mais non nul : une heure où le lot consomme surtout en fin d'heure,
alors que le solaire produisait en début d'heure, se voit attribuer une part
solaire un peu trop favorable. À mentionner si un client conteste au kWh
près.
"""
from __future__ import annotations

import billing
import db

# Une part solaire calculée doit rester dans [0, 1]. Elle peut sortir de cet
# intervalle sur une heure où les compteurs de bâtiment ne concordent pas
# tout à fait (tolérances de mesure, horodatages décalés d'une minute) : on
# borne, et on compte les cas pour pouvoir les signaler.
_TOLERANCE_PART = 0.02


def hourly_deltas(conn, series_id: str, start_ts: int, end_ts: int) -> dict[int, float]:
    """Énergie enregistrée par un compteur cumulatif sur chaque heure de la
    plage : différence entre deux relevés horaires consécutifs.

    Les micro-baisses (écart d'arrondi entre le poller live pleine précision
    et l'historique arrondi par Loxone -- voir billing.RUPTURE_MIN_DROP_KWH)
    sont ramenées à 0 plutôt que propagées en énergie négative. Une vraie
    remise à zéro de compteur reste, elle, du ressort de
    billing._detect_ruptures, appelé séparément sur la période complète.
    """
    rows = conn.execute(
        """
        SELECT ts, MAX(value) FROM (
            SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS ts, value
              FROM readings
             WHERE series_id = ? AND ts BETWEEN ? AND ? AND value IS NOT NULL
            UNION ALL
            SELECT ts, max_value AS value
              FROM readings_hourly
             WHERE series_id = ? AND ts BETWEEN ? AND ?
        )
        GROUP BY ts ORDER BY ts
        """,
        (series_id, start_ts - 3600, end_ts, series_id, start_ts - 3600, end_ts),
    ).fetchall()

    out = {}
    for i in range(1, len(rows)):
        h_prev, v_prev = rows[i - 1]
        h, v = rows[i]
        if h < start_ts:
            continue
        d = v - v_prev
        out[h] = d if d > 0 else 0.0
    return out


def solar_fraction(conn, batiment: dict, start_ts: int, end_ts: int) -> dict:
    """Part solaire du mix électrique du bâtiment, heure par heure.

    `batiment` est la structure rendue par billing.resolve_batiment
    (production / reseau_import / reseau_export). Les trois compteurs sont
    nécessaires : sans l'injection, on ne peut pas distinguer le solaire
    autoconsommé du solaire revendu, et la part solaire serait surestimée.
    """
    manquants = [k for k in ("production", "reseau_import", "reseau_export")
                 if not batiment.get(k)]
    if manquants:
        return {"fraction": {}, "indisponible": manquants, "heures": 0,
                "bornees": 0, "production": 0.0, "autoconsomme": 0.0,
                "injection": 0.0, "import_reseau": 0.0}

    prod = hourly_deltas(conn, batiment["production"]["series_id"], start_ts, end_ts)
    imp = hourly_deltas(conn, batiment["reseau_import"]["series_id"], start_ts, end_ts)
    exp = hourly_deltas(conn, batiment["reseau_export"]["series_id"], start_ts, end_ts)

    fraction = {}
    bornees = 0
    tot_prod = tot_auto = tot_inj = tot_imp = 0.0
    for h in sorted(set(prod) & set(imp) & set(exp)):
        p, i, e = prod[h], imp[h], exp[h]
        auto = p - e                # solaire resté dans le bâtiment
        conso = i + p - e           # tout ce que le bâtiment a consommé
        if conso <= 0.001:
            # Heure sans consommation mesurable : aucune énergie à répartir.
            fraction[h] = 0.0
            continue
        f = auto / conso
        if f < -_TOLERANCE_PART or f > 1 + _TOLERANCE_PART:
            bornees += 1
        fraction[h] = min(1.0, max(0.0, f))
        tot_prod += p
        tot_auto += max(0.0, min(auto, conso))
        tot_inj += e
        tot_imp += i

    return {"fraction": fraction, "indisponible": [], "heures": len(fraction),
            "bornees": bornees, "production": tot_prod, "autoconsomme": tot_auto,
            "injection": tot_inj, "import_reseau": tot_imp}


def split_zone(conn, conso_series_id: str, frac: dict, start_ts: int, end_ts: int) -> dict:
    """Scinde la consommation mesurée d'une zone en part réseau et part
    solaire, heure par heure, selon la part solaire du bâtiment.

    `non_reparti` est la consommation des heures pour lesquelles aucune part
    solaire n'a pu être calculée (compteur de bâtiment manquant sur ces
    heures) : elle n'est PAS silencieusement versée au réseau, elle est
    remontée à part pour que l'appelant décide quoi en faire.
    """
    conso = hourly_deltas(conn, conso_series_id, start_ts, end_ts)
    total = reseau = solaire = non_reparti = 0.0
    heures_sans_part = 0
    for h, kwh in conso.items():
        total += kwh
        f = frac.get(h)
        if f is None:
            non_reparti += kwh
            if kwh > 0:
                heures_sans_part += 1
            continue
        solaire += kwh * f
        reseau += kwh * (1.0 - f)
    return {
        "total": total,
        "reseau": reseau,
        "solaire": solaire,
        "non_reparti": non_reparti,
        "heures_sans_part": heures_sans_part,
        "taux_solaire": (100.0 * solaire / total) if total > 0 else None,
    }


def qualite_bilan(conn, zones: list[dict], batiment: dict, frac_info: dict,
                  start_ts: int, end_ts: int) -> dict:
    """Écart de bouclage : somme des consommations de zone contre la
    consommation déduite des compteurs de bâtiment.

    C'est l'indicateur de confiance de toute la répartition. Un écart de
    quelques pourcents est normal (tolérance des compteurs) ; un écart de
    plusieurs dizaines de pourcents signifie qu'il manque un consommateur
    dans la liste des zones, et que les parts solaires attribuées sont
    fausses -- à afficher, jamais à masquer.
    """
    conso_zones = 0.0
    for z in zones:
        src = z["sources"].get("controle")
        if not src:
            continue
        d = hourly_deltas(conn, src["series_id"], start_ts, end_ts)
        conso_zones += sum(d.values())

    conso_batiment = (frac_info.get("import_reseau", 0.0)
                      + frac_info.get("production", 0.0)
                      - frac_info.get("injection", 0.0))
    ecart_pct = None
    if conso_batiment > 0:
        ecart_pct = 100.0 * (conso_zones - conso_batiment) / conso_batiment
    return {"conso_zones": conso_zones, "conso_batiment": conso_batiment,
            "ecart_pct": ecart_pct}


# --------------------------------------------------------------------------
# Garde-fou : les séries Grid/Solaire du Miniserver sont-elles crédibles ?
# --------------------------------------------------------------------------

# Seuils de déclenchement. Volontairement peu sensibles : il s'agit de
# détecter une série structurellement fausse (des milliers de kWh
# impossibles), pas de chipoter sur une tolérance de compteur.
_IMPORT_NUL_KWH = 0.01        # en dessous, le bâtiment n'importe rien
_GRID_SIGNIFICATIF_KWH = 0.01  # au dessus, la zone prétend acheter au réseau
_PROD_SIGNIFICATIVE_KWH = 1.0  # production franche, du solaire à répartir


def audit_series_loxone(conn, zones: list[dict], batiment: dict,
                        start_ts: int, end_ts: int) -> dict:
    """Vérifie que les séries `reseau`/`solaire` des zones sont physiquement
    possibles, avant de facturer sur leur base.

    Deux impossibilités recherchées :
      1. une zone qui achète au réseau pendant une heure où le bâtiment
         n'importe rien (le courant devrait venir de nulle part) ;
      2. une production franche du bâtiment pendant laquelle AUCUNE zone
         n'enregistre de solaire autoconsommé.

    ATTENTION : les deux tests supposent que le compteur `reseau_import` est
    bien posé sur l'ALIMENTATION du bâtiment. Ce n'est pas toujours le cas
    -- à MS-Arlopi il est au raccordement de l'onduleur (voir CLAUDE.md), et
    l'audit y produisait un faux positif spectaculaire (1064 heures
    "impossibles" sur des séries par ailleurs validées contre les sorties du
    bloc EFM). C'est `bilan_exploitable()` qui tranche : sans bouclage du
    bilan, l'audit se déclare non testable au lieu d'accuser à tort.

    Retourne `testable` (les tests ont pu être menés) et `fiable` (verdict,
    qui n'a de sens que si `testable`).
    """
    res = {"fiable": True, "testable": False, "raisons": [],
           "heures_import_nul": 0, "heures_grid_impossible": 0,
           "kwh_grid_impossible": 0.0,
           "heures_prod_sans_solaire": 0, "heures_prod": 0}

    if not batiment.get("reseau_import") or not batiment.get("production"):
        res["raisons"].append("compteurs de bâtiment incomplets : audit impossible")
        return res

    imp = hourly_deltas(conn, batiment["reseau_import"]["series_id"], start_ts, end_ts)
    prod = hourly_deltas(conn, batiment["production"]["series_id"], start_ts, end_ts)

    grids, solaires = [], []
    for z in zones:
        if z["sources"].get("reseau"):
            grids.append(hourly_deltas(conn, z["sources"]["reseau"]["series_id"], start_ts, end_ts))
        if z["sources"].get("solaire"):
            solaires.append(hourly_deltas(conn, z["sources"]["solaire"]["series_id"], start_ts, end_ts))
    if not grids and not solaires:
        res["raisons"].append("aucune série réseau/solaire par zone : rien à auditer")
        return res
    res["testable"] = True

    for h, di in imp.items():
        if di >= _IMPORT_NUL_KWH:
            continue
        res["heures_import_nul"] += 1
        somme = sum(g.get(h, 0.0) for g in grids)
        if somme > _GRID_SIGNIFICATIF_KWH:
            res["heures_grid_impossible"] += 1
            res["kwh_grid_impossible"] += somme

    for h, dp in prod.items():
        if dp <= _PROD_SIGNIFICATIVE_KWH:
            continue
        res["heures_prod"] += 1
        if sum(s.get(h, 0.0) for s in solaires) <= _GRID_SIGNIFICATIF_KWH:
            res["heures_prod_sans_solaire"] += 1

    if res["heures_grid_impossible"]:
        res["fiable"] = False
        res["raisons"].append(
            f"{res['heures_grid_impossible']} heure(s) sur "
            f"{res['heures_import_nul']} sans import du bâtiment voient malgré tout "
            f"les compteurs réseau des zones augmenter "
            f"({res['kwh_grid_impossible']:.0f} kWh impossibles)"
        )
    if res["heures_prod"] and res["heures_prod_sans_solaire"] > 0.5 * res["heures_prod"]:
        res["fiable"] = False
        res["raisons"].append(
            f"{res['heures_prod_sans_solaire']} heure(s) sur {res['heures_prod']} "
            f"de production franche n'attribuent aucun solaire aux zones"
        )
    return res


# Au delà de cet écart entre la consommation déduite des compteurs de
# bâtiment et la somme des compteurs de zone, les deux ne mesurent pas le
# même périmètre : mesuré à +138,7 % sur MS-Arlopi (compteur réseau posé à
# l'onduleur) contre -3,9 % sur MS-PPE-Sequoia (compteur réellement sur
# l'alimentation). Le seuil est large exprès -- il sépare deux situations
# qui diffèrent d'un ordre de grandeur, pas deux tolérances de compteur.
BILAN_TOLERANCE_PCT = 15.0


def bilan_exploitable(bilan: dict, tolerance_pct: float = BILAN_TOLERANCE_PCT) -> bool:
    """Les compteurs de bâtiment mesurent-ils bien le même périmètre que la
    somme des zones ? Condition nécessaire pour calculer une part solaire
    ET pour auditer les séries Loxone."""
    e = bilan.get("ecart_pct")
    return e is not None and abs(e) <= tolerance_pct


def evaluer_site(conn, zones: list[dict], batiment: dict,
                 start_ts: int, end_ts: int) -> dict:
    """Point d'entrée unique : décide sur quoi facturer la part solaire d'un
    site, et pourquoi.

    `source_recommandee` vaut :
      - "calculee" : le bilan du bâtiment se ferme ET les séries Loxone par
        zone sont incohérentes -> on reconstruit la répartition (cas
        MS-PPE-Sequoia) ;
      - "loxone" : soit les séries Loxone passent l'audit, soit le bilan ne
        se ferme pas et on ne peut rien recalculer de mieux (cas MS-Arlopi,
        où ces séries ont été validées par ailleurs, contre les sorties du
        bloc EFM -- voir CLAUDE.md).

    Dans le second cas `confiance_verifiee` est False : la page/le classeur
    doit dire que la répartition vient du Miniserver sans avoir pu être
    revérifiée ici, plutôt que de laisser croire à un contrôle qui n'a pas
    eu lieu.
    """
    frac_info = solar_fraction(conn, batiment, start_ts, end_ts)
    bilan = qualite_bilan(conn, zones, batiment, frac_info, start_ts, end_ts)
    audit = audit_series_loxone(conn, zones, batiment, start_ts, end_ts)
    ok_bilan = bilan_exploitable(bilan)

    if not ok_bilan:
        audit["testable"] = False
        audit["fiable"] = True
        audit["raisons"] = [
            f"compteurs de bâtiment non comparables aux compteurs de zone "
            f"(écart de bouclage {bilan['ecart_pct']:+.1f} %) : ni l'audit des "
            f"séries du Miniserver ni une répartition recalculée ne sont "
            f"possibles sur ce site."
        ] if bilan.get("ecart_pct") is not None else [
            "bilan du bâtiment incalculable (compteur manquant)."
        ]

    if ok_bilan and not audit["fiable"]:
        source, confiance = "calculee", True
    else:
        source, confiance = "loxone", bool(ok_bilan and audit["testable"])

    return {"fraction": frac_info, "bilan": bilan, "audit": audit,
            "bilan_exploitable": ok_bilan,
            "source_recommandee": source,
            "confiance_verifiee": confiance}
