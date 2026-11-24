"""
Paramètres du décompte (page Paramètres) : catalogue, validation, valeur
en vigueur à une date, frais accessoires d'une période.

Chaque paramètre est versionné : une valeur s'applique à partir de sa date
de prise d'effet jusqu'à la version suivante. Une valeur du site l'emporte
sur la valeur globale (site '') ; seuls les paramètres `globale` en ont
une (la TVA). Voir docs/plan-page-parametres.md, § 4 et § 11.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from loxone_collector import db

GLOBAL = ""


@dataclass(frozen=True)
class Parametre:
    cle: str
    libelle: str
    unite: str
    theme: str
    type: str = "nombre"          # « nombre » ou « oui_non »
    minimum: float | None = None
    maximum: float | None = None
    globale: bool = False         # une valeur globale (site '') est permise
    facultatif: bool = False      # « aucune » est une valeur permise
    defaut: object = None         # valeur sans aucune version saisie


CATALOGUE: tuple[Parametre, ...] = (
    Parametre("prix_reseau", "Prix du kWh réseau", "CHF/kWh HT", "electricite",
              minimum=0, maximum=10),
    Parametre("prix_solaire", "Prix du kWh solaire", "CHF/kWh HT", "electricite",
              minimum=0, maximum=10),
    Parametre("taux_tva", "TVA", "%", "electricite", minimum=0, maximum=100, globale=True),
    Parametre("tva_solaire", "Appliquer la TVA au solaire", "", "electricite",
              type="oui_non", defaut=False),
    Parametre("location_compteurs_an", "Location de compteurs (forfait par lot)",
              "CHF HT par an", "location", minimum=0, maximum=100_000, facultatif=True),
    Parametre("pac_part_chauffage", "Part du coût de la PAC au chauffage", "%", "pac",
              minimum=0, maximum=100),
    Parametre("buanderie_prix_cycle", "Buanderie, prix par cycle",
              "CHF par cycle, sans TVA", "buanderie", minimum=0, maximum=1000),
)

_PAR_CLE = {p.cle: p for p in CATALOGUE}

_OUI = {"1", "oui", "true", "vrai"}
_NON = {"0", "non", "false", "faux"}


def parametre(cle: str) -> Parametre:
    """Lève ValueError pour une clé hors du catalogue."""
    try:
        return _PAR_CLE[cle]
    except KeyError:
        raise ValueError(f"paramètre inconnu : {cle!r}") from None


def catalogue() -> list[dict]:
    return [asdict(p) for p in CATALOGUE]


def valider_jour(texte) -> str:
    """Date locale 'YYYY-MM-DD'. Lève ValueError sinon."""
    try:
        return date.fromisoformat(str(texte)).isoformat()
    except ValueError:
        raise ValueError(f"date invalide : {texte!r} (attendu AAAA-MM-JJ)") from None


def lire_valeur(cle: str, brut):
    """Valeur saisie → valeur Python validée (float, bool ou None pour
    « aucune »). Accepte la virgule décimale (« 0,25 »). Lève ValueError
    avec un message lisible."""
    p = parametre(cle)
    if brut is None or (isinstance(brut, str) and not brut.strip()):
        if p.facultatif:
            return None
        raise ValueError(f"{p.libelle} : valeur obligatoire")
    if p.type == "oui_non":
        if isinstance(brut, bool):
            return brut
        texte = str(brut).strip().lower()
        if texte in _OUI:
            return True
        if texte in _NON:
            return False
        raise ValueError(f"{p.libelle} : oui ou non attendu")
    if isinstance(brut, bool):
        raise ValueError(f"{p.libelle} : nombre attendu")
    try:
        valeur = float(str(brut).strip().replace("'", "").replace(",", "."))
    except ValueError:
        raise ValueError(f"{p.libelle} : nombre attendu") from None
    if valeur != valeur or valeur in (float("inf"), float("-inf")):
        raise ValueError(f"{p.libelle} : nombre attendu")
    if p.minimum is not None and valeur < p.minimum:
        raise ValueError(f"{p.libelle} : au moins {p.minimum:g} {p.unite}".rstrip())
    if p.maximum is not None and valeur > p.maximum:
        raise ValueError(f"{p.libelle} : au plus {p.maximum:g} {p.unite}".rstrip())
    return valeur


def vers_texte(cle: str, valeur) -> str | None:
    if valeur is None:
        return None
    if parametre(cle).type == "oui_non":
        return "1" if valeur else "0"
    return repr(float(valeur))


def depuis_texte(cle: str, texte: str | None):
    if texte is None:
        return None
    if parametre(cle).type == "oui_non":
        return texte == "1"
    return float(texte)


def ajouter_version(conn, site: str, cle: str, valid_from, brut, note: str = "",
                    updated_by: str = "") -> None:
    """Valide puis enregistre une version. Une valeur globale (site '')
    n'est permise que pour un paramètre `globale`. Lève ValueError."""
    p = parametre(cle)
    if site == GLOBAL and not p.globale:
        raise ValueError(f"{p.libelle} : pas de valeur globale, seulement par site")
    jour = valider_jour(valid_from)
    valeur = lire_valeur(cle, brut)
    db.upsert_parametre(conn, site, cle, jour, vers_texte(cle, valeur),
                        note=(note or "").strip(), updated_by=updated_by)


def _version_au(versions: list[dict], site: str, cle: str, jour: str) -> dict | None:
    """Dernière version de `cle` pour `site` prenant effet au plus tard
    `jour`."""
    applicables = [v for v in versions
                   if v["miniserver"] == site and v["cle"] == cle and v["valid_from"] <= jour]
    return max(applicables, key=lambda v: v["valid_from"]) if applicables else None


def _valeurs_au(versions: list[dict], site: str, jour: str) -> dict[str, dict]:
    out = {}
    for p in CATALOGUE:
        v = _version_au(versions, site, p.cle, jour)
        origine = "site"
        if v is None and p.globale:
            v, origine = _version_au(versions, GLOBAL, p.cle, jour), "global"
        if v is None:
            out[p.cle] = {"valeur": p.defaut, "depuis": None, "origine": "defaut", "id": None}
        else:
            out[p.cle] = {"valeur": depuis_texte(p.cle, v["valeur"]), "depuis": v["valid_from"],
                          "origine": origine, "id": v["id"]}
    return out


def valeurs_au(conn, site: str, jour: str) -> dict[str, dict]:
    """Valeur en vigueur de chaque paramètre du catalogue au `jour` :
    `{cle: {valeur, depuis, origine, id}}`, `origine` valant « site »,
    « global » ou « defaut » (aucune version saisie : valeur par défaut du
    catalogue, None pour un prix)."""
    return _valeurs_au(db.list_parametres(conn, [site, GLOBAL]), site, valider_jour(jour))


def parametres_periode(conn, site: str, du: str, au: str) -> dict:
    """Ce qu'un décompte du `du` au `au` (inclus) applique :

    - `valeurs` : les valeurs en vigueur au premier jour (comme les tarifs :
      une hausse décidée en cours de période s'applique à la suivante) ;
    - `changent` : les clés dont la valeur change pendant la période, pour
      le signaler sur le décompte ;
    - `frais` : les frais accessoires dont la période est entièrement
      comprise dans celle du décompte ;
    - `frais_hors_periode` : ceux qui la chevauchent sans y tenir, signalés
      mais non comptés.
    """
    du, au = valider_jour(du), valider_jour(au)
    versions = db.list_parametres(conn, [site, GLOBAL])
    valeurs = _valeurs_au(versions, site, du)
    dates = sorted({v["valid_from"] for v in versions if du < v["valid_from"] <= au})
    changent = set()
    for jour in dates:
        apres = _valeurs_au(versions, site, jour)
        changent.update(c for c in valeurs if apres[c]["valeur"] != valeurs[c]["valeur"])
    frais, hors = [], []
    for f in db.list_frais(conn, site):
        f = {**f, "tva": bool(f["tva"])}
        if du <= f["du"] and f["au"] <= au:
            frais.append(f)
        elif f["du"] <= au and du <= f["au"]:
            hors.append(f)
    return {
        "valeurs": valeurs,
        "changent": [p.cle for p in CATALOGUE if p.cle in changent],
        "frais": frais,
        "frais_hors_periode": hors,
    }


def ajouter_frais(conn, site: str, du, au, zone: str, libelle: str, montant_ht, tva,
                  note: str = "", updated_by: str = "") -> int:
    """Valide puis enregistre un frais accessoire. Lève ValueError."""
    du, au = valider_jour(du), valider_jour(au)
    if au < du:
        raise ValueError("la fin de la période est avant son début")
    zone, libelle = (zone or "").strip(), (libelle or "").strip()
    if not zone:
        raise ValueError("zone obligatoire")
    if not libelle:
        raise ValueError("libellé obligatoire")
    try:
        montant = float(str(montant_ht).strip().replace("'", "").replace(",", "."))
    except ValueError:
        raise ValueError("montant HT : nombre attendu") from None
    if montant != montant or montant in (float("inf"), float("-inf")):
        raise ValueError("montant HT : nombre attendu")
    if isinstance(tva, str):
        texte = tva.strip().lower()
        if texte not in _OUI | _NON:
            raise ValueError("TVA : oui ou non attendu")
        tva = texte in _OUI
    return db.insert_frais(conn, site, du, au, zone, libelle, montant, bool(tva),
                           note=(note or "").strip(), updated_by=updated_by)
