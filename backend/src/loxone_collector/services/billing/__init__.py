"""
billing
-------
Calcul des décomptes de charges MENSUELS (page /decompte), suivant le modèle
RCP (regroupement dans le cadre de la consommation propre) — le même que ce
que proposent les prestataires du marché comme Climkit : la consommation de
chaque zone est scindée en une part solaire autoconsommée et une part
achetée au réseau, facturées à deux prix différents.

## Ce que mesure quoi (établi empiriquement, pas supposé)

Chaque zone a un bloc Loxone "Moniteur de flux d'énergie" (EFM) alimenté par
deux compteurs, plus un compteur plus ancien de périmètre différent :

  - `<Zone> Grid (total)` — énergie ACHETÉE AU RÉSEAU par la zone.
    Vérifié : sa valeur instantanée est identique à la sortie `Gpwr` de
    l'EFM de la zone (99-100 % des échantillons), et n'est jamais négative
    — c'est de l'import pur, pas un compteur bidirectionnel.

  - `<Zone> Solaire (total)` — énergie solaire AUTOCONSOMMÉE par la zone.
    Vérifié de deux façons : (a) sa valeur instantanée est identique à la
    sortie `Ppwr` de l'EFM de la zone, et (b) son cumul est identique, à
    0,01 kWh près sur les incréments, à la sortie `selfConsumption` du même
    bloc EFM. C'est donc Loxone lui-même qui qualifie cette série
    d'autoconsommation. Enfin, la somme des six zones (28,78 kWh sur la
    fenêtre de données brutes) égale la sortie `selfConsumption` du bloc EFM
    du BÂTIMENT (28,79 kWh) : les zones se répartissent bien l'intégralité
    de l'autoconsommation de l'immeuble.

  => **Consommation facturable d'une zone = Grid + Solaire.**

  - `Appartement N / Commerce / Rez jardin (total)` — compteur plus ancien
    (UUID de génération antérieure aux compteurs EFM, posés en octobre 2025),
    d'un PÉRIMÈTRE DIFFÉRENT : en août 2026 il enregistre 6,3 kWh/jour sur
    App 1 quand le seul compteur Grid en enregistre 7,0. Ce n'est donc PAS
    une seconde mesure de la même chose, et l'écart avec Grid+Solaire n'est
    pas une anomalie de compteur (confirmé par l'installateur). Il est gardé
    en information de contrôle, jamais utilisé pour facturer.

## Les deux taux, à ne pas confondre

  - **Taux d'autoproduction** = solaire autoconsommé / consommation totale.
    "Quelle part de ce que je consomme vient du soleil ?" Monte en été
    (1 % en février 2026, 23 % en juillet). C'est l'indicateur qui parle à
    un propriétaire, donc celui mis en avant.

  - **Taux d'autoconsommation** = solaire autoconsommé / production totale.
    "Quelle part de ce que je produis est consommée sur place plutôt que
    réinjectée ?" BAISSE en été (84 % en février 2026, 29 % en juillet) —
    mathématiquement correct (on produit beaucoup plus que ce qu'on peut
    absorber) mais contre-intuitif : à afficher uniquement avec son
    explication, et jamais seul.

## Deux choix de calcul

1. **Découpage mensuel calendaire, en heure locale Europe/Zurich** — bornes
   à minuit LOCAL, pas UTC comme le reste du dashboard
   (`db.query_daily_last`) : sur une facture, un mois commence à 00:00 chez
   le propriétaire. Le mensuel remplace un découpage bimestriel essayé
   d'abord : il isole le mois d'installation des compteurs (octobre 2025,
   incomplet) au lieu de perdre tout un bimestre.

2. **Consommation = relevé de fin − relevé de début** (`db.query_value_at`),
   jamais une somme de deltas journaliers : c'est la logique d'un relevé de
   compteur physique à deux dates, insensible aux trous de collecte au
   milieu de la période, et sans double comptage à cheval sur deux mois.
"""

# Le paquet garde l'interface de l'ancien module unique : les appelants
# écrivent `billing.compute_decompte`, `billing.period_bounds`, etc.
# Le détail est dans les sous-modules (voir leur docstring).

from loxone_collector.services.billing.compteurs import (
    BATIMENT_PRODUCTION_RE,
    BATIMENT_RESEAU_RE,
    CONTROLE_EXCLUDE_RE,
    PAC_BATIMENT_RE,
    RESERVES_ZONES,
    SOLAIRE_BATIMENT_RE,
    SOLAR_LABEL_RE,
    ZONE_LABEL_OVERRIDES,
    ZONE_PAC,
    available_range,
    production_couvrante,
    productions_sur_fenetre,
    resolve_batiment,
    resolve_zones,
)
from loxone_collector.services.billing.decompte import (
    KWH_DECIMALES,
    WALLBOX_LABEL_RE,
    WALLBOX_VALID_FROM,
    compute_decompte,
    cumul_periodes,
    montants,
    repartition_du_mois,
    tarif_for,
    taux,
)
from loxone_collector.services.billing.periodes import (
    MONTH_LABELS,
    TIMEZONE,
    parse_jour,
    parse_period_key,
    period_bounds,
    period_key,
    period_label,
    period_label_short,
    periodes_plage,
    periods_covering,
)
from loxone_collector.services.billing.postes import (
    BUANDERIE_CYCLES_RE,
    BUANDERIE_KWH_RE,
    BUANDERIE_VALID_FROM,
    CHAUFFAGE_RE,
    POSTES,
    RUPTURE_MIN_DROP_CYCLES,
    VOLUME_RE,
    ZONES_FANTOMES,
    compute_postes,
    resolve_postes,
)
from loxone_collector.services.billing.releves import (
    RUPTURE_MIN_DROP_EAU_M3,
    RUPTURE_MIN_DROP_KWH,
    delta_compteurs_successifs,
    min_drop_for_resource_type,
    reading_delta,
)

__all__ = [
    "BATIMENT_PRODUCTION_RE",
    "BATIMENT_RESEAU_RE",
    "BUANDERIE_CYCLES_RE",
    "BUANDERIE_KWH_RE",
    "BUANDERIE_VALID_FROM",
    "CHAUFFAGE_RE",
    "CONTROLE_EXCLUDE_RE",
    "KWH_DECIMALES",
    "MONTH_LABELS",
    "PAC_BATIMENT_RE",
    "POSTES",
    "RESERVES_ZONES",
    "RUPTURE_MIN_DROP_CYCLES",
    "RUPTURE_MIN_DROP_EAU_M3",
    "RUPTURE_MIN_DROP_KWH",
    "SOLAIRE_BATIMENT_RE",
    "SOLAR_LABEL_RE",
    "TIMEZONE",
    "VOLUME_RE",
    "WALLBOX_LABEL_RE",
    "WALLBOX_VALID_FROM",
    "ZONES_FANTOMES",
    "ZONE_LABEL_OVERRIDES",
    "ZONE_PAC",
    "available_range",
    "compute_decompte",
    "compute_postes",
    "cumul_periodes",
    "delta_compteurs_successifs",
    "min_drop_for_resource_type",
    "montants",
    "parse_jour",
    "parse_period_key",
    "period_bounds",
    "period_key",
    "period_label",
    "period_label_short",
    "periodes_plage",
    "periods_covering",
    "production_couvrante",
    "productions_sur_fenetre",
    "reading_delta",
    "repartition_du_mois",
    "resolve_batiment",
    "resolve_postes",
    "resolve_zones",
    "tarif_for",
    "taux",
]
