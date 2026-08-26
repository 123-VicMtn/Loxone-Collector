"""
classification.py
------------------
Devine automatiquement, à partir du nom d'un capteur Loxone (label du
contrôle), deux informations que Loxone ne fournit pas nativement :

  - l'appartement concerné (ex: "APP01"), via une expression régulière
    configurable qui exploite la seule convention observée dans
    l'installation existante ;
  - le type de ressource (eau chaude/froide, énergie solaire/réseau/
    injectée/consommée), via une liste de règles mots-clés configurable,
    faute de convention de nommage fiable pour ça dans Loxone Config.

Ces devinettes sont volontairement best-effort : l'objectif est de
pré-remplir correctement la majorité des capteurs pour limiter le travail
de correction manuelle via /admin, pas d'être infaillible. Toute correction
faite dans /admin est mémorisée en base (voir db.set_series_classification)
et n'est plus jamais écrasée par ce module.
"""

from __future__ import annotations

import re

# Motif par défaut : capture "APP" suivi de chiffres, avec ou sans espace
# (ex: "APP01", "App 12", "app3"), insensible à la casse -- plus quelques
# noms de zones non numérotées observées en pratique sur une installation
# mixte (immeuble avec appartements + local commercial + rez-jardin +
# parties communes) : "Commerce", "Rez Jardin", "Commun(s)". Un seul groupe
# capturant est attendu. Reste volontairement best-effort : toute zone non
# reconnue ici (ex: un compteur d'immeuble global comme "Réseau" ou
# "Production") atterrit dans "Sans appartement" et peut être classée à la
# main via /admin.
DEFAULT_APARTMENT_PATTERN = r"(?i)(APP\s*\d+|Commerce|Rez\s*Jardin|Commun)"

# Règles appliquées dans l'ordre : la première dont le motif matche le nom
# du capteur l'emporte. Chaque règle est un dict {"match": <regex>, "type": <clé>}.
DEFAULT_RESOURCE_TYPE_RULES: list[dict] = [
    {"match": r"(?i)(ecs\b|eau[ _-]?chaude|ww\b|warmwasser)", "type": "eau_chaude"},
    {"match": r"(?i)(eau[ _-]?froide|kw\b|kaltwasser)", "type": "eau_froide"},
    {"match": r"(?i)(pv\b|photovolta|solaire|solar)", "type": "energie_solaire"},
    {"match": r"(?i)(injection|export|feed[ -]?in|einspeisung)", "type": "energie_injectee"},
    {"match": r"(?i)(r[ée]seau|grid|netz|import)", "type": "energie_reseau"},
    # Fallback générique "eau" (sans qualificatif) : on suppose eau froide,
    # hypothèse la plus courante pour un compteur d'eau non qualifié.
    {"match": r"(?i)(eau|water)", "type": "eau_froide"},
]

# Libellés affichés dans le dashboard / la page d'admin pour chaque type.
DEFAULT_RESOURCE_TYPE_LABELS: dict[str, str] = {
    "eau_chaude": "Eau chaude",
    "eau_froide": "Eau froide",
    "energie_solaire": "Énergie solaire",
    "energie_reseau": "Énergie réseau (import)",
    "energie_injectee": "Énergie injectée (export)",
    "energie_consommee": "Énergie consommée",
    "autre": "Autre",
}


def extract_apartment(text: str, pattern: str | None = None) -> str:
    """Retourne l'identifiant d'appartement trouvé dans `text` (ex: "APP01"),
    normalisé en majuscules sans espace, ou "" si rien ne matche."""
    if not text:
        return ""
    pattern = pattern or DEFAULT_APARTMENT_PATTERN
    try:
        m = re.search(pattern, text)
    except re.error:
        return ""
    if not m:
        return ""
    value = m.group(1) if m.groups() else m.group(0)
    return re.sub(r"\s+", "", value).upper()


def guess_resource_type(text: str, control_type: str = "",
                         rules: list[dict] | None = None) -> str:
    """Devine le type de ressource à partir du nom du capteur, puis, à
    défaut de correspondance, du type de contrôle Loxone (un "Meter" sans
    mot-clé reconnu est supposé être un compteur d'énergie générique).
    Retourne "autre" si rien ne permet de trancher."""
    rules = rules if rules is not None else DEFAULT_RESOURCE_TYPE_RULES
    if text:
        for rule in rules:
            pattern = rule.get("match")
            rtype = rule.get("type")
            if not pattern or not rtype:
                continue
            try:
                if re.search(pattern, text):
                    return rtype
            except re.error:
                continue
    if control_type == "Meter":
        return "energie_consommee"
    return "autre"


def resource_type_label(rtype: str, labels: dict[str, str] | None = None) -> str:
    labels = labels or DEFAULT_RESOURCE_TYPE_LABELS
    if not rtype:
        return "Non classé"
    return labels.get(rtype, rtype)
