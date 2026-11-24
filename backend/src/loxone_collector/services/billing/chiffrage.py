"""
Montants du décompte (CHF), à partir des kWh déjà calculés et des
paramètres de la période (services/parametres.py). Aucun accès à la base :
les quantités ne changent pas, seuls les montants s'ajoutent.

Règles (docs/plan-page-parametres.md, § 3 et § 10) :

- prix saisis HT ; TVA sur le réseau, la location, les kWh de buanderie et
  les frais marqués « TVA » ; sur le solaire seulement avec `tva_solaire` ;
  jamais sur le cycle de buanderie (montant final) ;
- électricité de chaque zone, Communs compris : solaire × prix solaire +
  réseau × prix réseau ; location (forfait annuel au prorata) pour les
  lots seulement ;
- **le coût de la chaleur est l'électricité du compteur de la PAC seul**
  (zone `PAC`), sans location. Chauffage = coût × part chauffage, eau
  chaude = le reste ; puis chaque poste au prorata des kWh th ou des m³
  de chaque zone. HT et TVA sont répartis séparément ; le reste d'arrondi
  va à la plus grosse quantité, pour que la somme des zones retombe au
  centime sur le coût. La PAC n'est donc pas une ligne du récapitulatif ;
- buanderie : cycles × prix du cycle + kWh × prix réseau ;
- frais accessoires repris tels quels ;
- une donnée manquante (prix, quantité) laisse le montant vide avec un
  message, jamais un 0 silencieux.

Tous les montants sont arrondis au centime (demi supérieur).
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from loxone_collector.services.billing.compteurs import ZONE_PAC

# Zones du bâtiment, sans location de compteurs (décision du 2026-11-09 :
# lots seulement).
ZONES_SANS_LOCATION = {"COMMUN", ZONE_PAC, "SALLESCOMMUNES", "WALLBOX"}

_CENTIME = Decimal("0.01")


def _dec(x) -> Decimal:
    return Decimal(str(x))


def arrondi(x) -> float:
    """Au centime, demi supérieur (pas l'arrondi bancaire de `round`)."""
    return float(_dec(x).quantize(_CENTIME, rounding=ROUND_HALF_UP))


def _montant(ht: float, tva: float) -> dict:
    return {"ht": ht, "tva": tva, "ttc": arrondi(_dec(ht) + _dec(tva))}


def _somme(montants: list[dict]) -> dict:
    ht = sum((_dec(m["ht"]) for m in montants), Decimal(0))
    tva = sum((_dec(m["tva"]) for m in montants), Decimal(0))
    return _montant(float(ht), float(tva))


def fraction_annee(du: str, au: str) -> float:
    """Part de l'année couverte par la période (`du`, `au` inclus) pour la
    location : mois calendaires entiers à 1/12 chacun ; sinon les jours sur
    365 (décision du 2026-11-24)."""
    d, a = date.fromisoformat(du), date.fromisoformat(au)
    fin_de_mois = calendar.monthrange(a.year, a.month)[1]
    if d.day == 1 and a.day == fin_de_mois:
        mois = (a.year - d.year) * 12 + a.month - d.month + 1
        return mois / 12
    return ((a - d).days + 1) / 365


def repartir(total: float, quantites: dict[str, float]) -> dict[str, float]:
    """`total` réparti au prorata de `quantites`, au centime ; le reste
    d'arrondi va à la plus grosse quantité. Σ résultat = total exactement."""
    somme = sum((_dec(q) for q in quantites.values()), Decimal(0))
    t = _dec(total)
    parts = {z: (t * _dec(q) / somme).quantize(_CENTIME, rounding=ROUND_HALF_UP)
             for z, q in quantites.items()}
    reste = t - sum(parts.values(), Decimal(0))
    if reste:
        plus_gros = max(quantites, key=lambda z: quantites[z])
        parts[plus_gros] += reste
    return {z: float(v) for z, v in parts.items()}


def _electricite(reseau_kwh, solaire_kwh, prix: dict, location: float | None) -> dict | None:
    """Montant de l'électricité d'une zone (location comprise si donnée),
    ou None si une quantité ou un prix manque."""
    if None in (reseau_kwh, solaire_kwh, prix["prix_reseau"], prix["prix_solaire"],
                prix["taux_tva"]):
        return None
    solaire = arrondi(_dec(solaire_kwh) * _dec(prix["prix_solaire"]))
    reseau = arrondi(_dec(reseau_kwh) * _dec(prix["prix_reseau"]))
    taux = _dec(prix["taux_tva"]) / 100
    tva_solaire = arrondi(_dec(solaire) * taux) if prix["tva_solaire"] else 0.0
    tva_reseau = arrondi(_dec(reseau) * taux)
    out = {
        "solaire": _montant(solaire, tva_solaire),
        "reseau": _montant(reseau, tva_reseau),
    }
    if location is not None:
        out["location"] = _montant(location, arrondi(_dec(location) * taux))
    out["total"] = _somme(list(out.values()))
    return out


def _repartir_poste(cout: dict, quantites: dict[str, float | None]) -> tuple[dict | None, str | None]:
    """Coût d'un poste de chaleur réparti sur les zones. Une quantité
    manquante bloque la répartition entière (sinon les autres zones
    paieraient sa part)."""
    manquantes = sorted(z for z, q in quantites.items() if q is None)
    if manquantes:
        return None, f"relevé manquant pour {', '.join(manquantes)}"
    utiles = {z: q for z, q in quantites.items() if q}
    if not utiles:
        return None, "aucune consommation relevée"
    ht = repartir(cout["ht"], utiles)
    tva = repartir(cout["tva"], utiles)
    return {z: {**_montant(ht[z], tva[z]), "quantite": utiles[z]} for z in utiles}, None


def entrees_du_decompte(payload: dict, period_key: str, frais: list[dict]) -> dict:
    """Quantités d'une période du décompte (`compute_decompte` +
    `compute_postes`, éventuellement `cumul_periodes`) mises dans la forme
    attendue par `calculer_montants`. Une zone qui a plusieurs compteurs de
    chaleur ou d'eau chaude les additionne ; un relevé manquant rend la
    quantité de la zone inconnue."""
    zones = []
    for z in payload.get("zones") or []:
        e = (z.get("periodes") or {}).get(period_key)
        if e is None:
            continue
        zones.append({"zone": z["zone"], "label": z.get("label") or z["zone"],
                      "reseau": e["reseau"]["kwh"], "solaire": e["solaire"]["kwh"]})
    postes = (payload.get("postes") or {}).get(period_key) or {}

    def par_zone(lignes: list[dict]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for ligne in lignes:
            zone, q = ligne["zone"], ligne.get("kwh")
            if zone in out and out[zone] is None:
                continue
            out[zone] = None if q is None else (out.get(zone) or 0.0) + q
        return out

    return {
        "zones": zones,
        "chauffage": par_zone(postes.get("chauffage") or []),
        "eau_chaude": par_zone(postes.get("eau_chaude") or []),
        "buanderie": {b["zone"]: {"cycles": b["cycles"]["kwh"], "kwh": b["energie"]["kwh"]}
                      for b in postes.get("buanderie") or []},
        "frais": frais,
    }


def calculer_montants(entrees: dict, valeurs: dict, fraction: float) -> dict:
    """Montants d'une période.

    `entrees` : `zones` (liste de `{zone, label, reseau, solaire}` en kWh),
    `chauffage` et `eau_chaude` (`{zone: quantité}` en kWh th et m³, None
    si un relevé manque), `buanderie` (`{zone: {cycles, kwh}}`), `frais`
    (liste de `{zone, libelle, montant_ht, tva}`).
    `valeurs` : `{cle: valeur}` des paramètres en vigueur.
    `fraction` : part de l'année pour la location (`fraction_annee`).
    """
    messages: list[str] = []
    prix = {c: valeurs.get(c) for c in ("prix_reseau", "prix_solaire", "taux_tva")}
    prix["tva_solaire"] = bool(valeurs.get("tva_solaire"))
    manquants = [c for c in ("prix_reseau", "prix_solaire", "taux_tva") if prix[c] is None]
    if manquants:
        messages.append("Paramètres manquants : " + ", ".join(manquants)
                        + ". Les montants concernés restent vides.")
    taux = _dec(prix["taux_tva"]) / 100 if prix["taux_tva"] is not None else None

    location_an = valeurs.get("location_compteurs_an")
    location = arrondi(_dec(location_an) * _dec(fraction)) if location_an is not None else None

    # Électricité de chaque zone, PAC comprise (son coût sert à la chaleur).
    electricite, labels, pac = {}, {}, None
    for z in entrees.get("zones") or []:
        labels[z["zone"]] = z.get("label") or z["zone"]
        loc = location if z["zone"] not in ZONES_SANS_LOCATION else None
        m = _electricite(z.get("reseau"), z.get("solaire"), prix, loc)
        if z["zone"] == ZONE_PAC:
            pac = m
            if m is None and not manquants:
                messages.append("Électricité de la PAC incomplète : chauffage et eau chaude sans montant.")
        else:
            electricite[z["zone"]] = m
            if m is None and not manquants:
                messages.append(f"{labels[z['zone']]} : relevé électrique incomplet.")

    # Chaleur : coût de la PAC, clé chauffage / eau chaude, prorata.
    chauffage = eau_chaude = None
    a_chaleur = bool(entrees.get("chauffage") or entrees.get("eau_chaude"))
    part = valeurs.get("pac_part_chauffage")
    if a_chaleur:
        if not any(z["zone"] == ZONE_PAC for z in entrees.get("zones") or []):
            messages.append("Pas de compteur de PAC : chauffage et eau chaude sans montant.")
        elif part is None:
            messages.append("Part du coût de la PAC au chauffage non renseignée : "
                            "chauffage et eau chaude sans montant.")
        elif pac is not None:
            cout = pac["total"]
            ch_ht = arrondi(_dec(cout["ht"]) * _dec(part) / 100)
            ch_tva = arrondi(_dec(cout["tva"]) * _dec(part) / 100)
            couts = {
                "chauffage": _montant(ch_ht, ch_tva),
                "eau_chaude": _montant(arrondi(_dec(cout["ht"]) - _dec(ch_ht)),
                                       arrondi(_dec(cout["tva"]) - _dec(ch_tva))),
            }
            resultats = {}
            for poste, libelle in (("chauffage", "Chauffage"), ("eau_chaude", "Eau chaude")):
                quantites = entrees.get(poste) or {}
                if not quantites:
                    resultats[poste] = None
                    continue
                par_zone, erreur = _repartir_poste(couts[poste], quantites)
                if erreur:
                    messages.append(f"{libelle} : {erreur}, coût non réparti.")
                total_q = None if None in quantites.values() else sum(quantites.values())
                resultats[poste] = {"cout": couts[poste], "par_zone": par_zone,
                                    "quantite_totale": total_q}
            chauffage, eau_chaude = resultats["chauffage"], resultats["eau_chaude"]

    # Buanderie : cycles (montant final, sans TVA) + kWh au prix réseau.
    buanderie = {}
    prix_cycle = valeurs.get("buanderie_prix_cycle")
    for zone, b in (entrees.get("buanderie") or {}).items():
        cycles, kwh = b.get("cycles"), b.get("kwh")
        if None in (cycles, kwh, prix_cycle, prix["prix_reseau"], taux):
            buanderie[zone] = None
            continue
        m_cycles = _montant(arrondi(_dec(cycles) * _dec(prix_cycle)), 0.0)
        kwh_ht = arrondi(_dec(kwh) * _dec(prix["prix_reseau"]))
        m_kwh = _montant(kwh_ht, arrondi(_dec(kwh_ht) * taux))
        buanderie[zone] = {"cycles": m_cycles, "kwh": m_kwh, "total": _somme([m_cycles, m_kwh])}
    if entrees.get("buanderie") and prix_cycle is None:
        messages.append("Prix du cycle de buanderie non renseigné : buanderie sans montant.")

    # Frais accessoires, repris tels quels.
    frais = []
    for f in entrees.get("frais") or []:
        ht = arrondi(f["montant_ht"])
        if f.get("tva") and taux is None:
            frais.append({**f, "montant": None})
            continue
        tva = arrondi(_dec(ht) * taux) if f.get("tva") else 0.0
        frais.append({**f, "montant": _montant(ht, tva)})

    # Récapitulatif : une ligne par zone (hors PAC, déjà répartie). Une
    # composante absente (zone sans compteur de chaleur) n'est pas dans la
    # ligne ; une composante vide (None) rend le total de la ligne vide.
    zones = [z for z in labels if z != ZONE_PAC]
    for source in (entrees.get("chauffage"), entrees.get("eau_chaude"), buanderie,
                   {f["zone"]: None for f in frais}):
        zones += [z for z in (source or {}) if z not in zones and z != ZONE_PAC]
    recap = []
    for zone in zones:
        ligne = {"zone": zone, "label": labels.get(zone, zone)}
        if zone in electricite:
            ligne["electricite"] = electricite[zone]["total"] if electricite[zone] else None
        for poste, res in (("chauffage", chauffage), ("eau_chaude", eau_chaude)):
            if zone in (entrees.get(poste) or {}):
                par_zone = res["par_zone"] if res else None
                # Zone relevée à 0 : hors du prorata, elle ne paie rien.
                part = par_zone.get(zone) if par_zone else None
                if par_zone is None:
                    ligne[poste] = None
                else:
                    ligne[poste] = _montant(part["ht"], part["tva"]) if part else _montant(0.0, 0.0)
        if zone in buanderie:
            ligne["buanderie"] = buanderie[zone]["total"] if buanderie[zone] else None
        frais_zone = [f["montant"] for f in frais if f["zone"] == zone]
        if frais_zone:
            ligne["frais"] = None if None in frais_zone else _somme(frais_zone)
        composantes = [v for k, v in ligne.items() if k not in ("zone", "label")]
        ligne["total"] = None if None in composantes else _somme(composantes)
        recap.append(ligne)

    totaux = [ligne["total"] for ligne in recap]
    return {
        "messages": messages,
        "location_annuelle": location_an,
        "location_periode": location,
        "electricite": electricite,
        "pac": pac,
        "chauffage": chauffage,
        "eau_chaude": eau_chaude,
        "buanderie": buanderie,
        "frais": frais,
        "recapitulatif": recap,
        "total": None if (not totaux or None in totaux) else _somme(totaux),
    }
