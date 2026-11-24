/**
 * Formes exactes renvoyées par /api/decompte -- voir
 * billing.py::compute_decompte pour la source de vérité côté serveur.
 * Le frontend ne fait QUE afficher ce payload, jamais recalculer une part
 * de cette logique (voir CLAUDE.md, "Choix de stack figé").
 */

export interface Period {
  key: string
  label: string
  label_court: string
  year: number
  month: number
  start: number
  end: number
}

export interface SourceRef {
  series_id: string
  label: string
  unit: string
}

import type { ReadingDelta } from '@shared/types/series'
export type { ReadingDelta }

export interface ZonePeriod {
  reseau: ReadingDelta
  solaire: ReadingDelta
  controle: ReadingDelta
  total: number | null
  taux_autoproduction: number | null
  en_cours: boolean
  facturable: boolean
  alertes: string[]
}

export interface Zone {
  zone: string
  label: string
  sources: {
    reseau: SourceRef | null
    solaire: SourceRef | null
    controle: SourceRef | null
  }
  ambigus: {
    reseau: string[]
    solaire: string[]
    controle: string[]
  }
  periodes: Record<string, ZonePeriod>
}

export interface BatimentPeriod {
  en_cours: boolean
  production: ReadingDelta
  autoconsommation: number | null
  achat_reseau: number | null
  consommation_totale: number | null
  injection: number | null
  taux_autoproduction: number | null
  taux_autoconsommation: number | null
  zones_incompletes: boolean
}

export interface Batiment {
  sources: {
    production: SourceRef | null
    reseau_import: SourceRef | null
    reseau_export: SourceRef | null
  }
  periodes: Record<string, BatimentPeriod>
}

/** Décision de repartition.evaluer_site, déjà appliquée aux kWh du payload. */
export interface RepartitionInfo {
  /** `mixte` : décompte de dates libres dont les mois n'ont pas tous la même source. */
  source: 'calculee' | 'loxone' | 'mixte'
  confiance_verifiee: boolean
  raisons: string[]
}

export interface DecomptePayload {
  timezone: string
  periodes: Period[]
  zones: Zone[]
  batiment: Batiment
  generated_at: number
  miniserver: string
  /** Un verdict par mois (clé YYYY-MM) : un mois faux ne juge pas les autres. */
  repartition: Record<string, RepartitionInfo>
  /** Zones dont le compteur reste à confirmer sur place (PAC de La Calbasse). */
  reserves: string[]
  /** Zone de chaque note de `reserves`, dans le même ordre. */
  reserves_zones?: string[]
  /** Renseigné si les Statistics du Miniserver n'ont pas pu être relues. */
  statistiques_erreur?: string | null
  /** Plus récent index Statistics importé (epoch) : « relevés à jour au … ». */
  statistiques_jusqu_au?: number | null
  /** Décompte de dates libres (`du` / `au`) : clé de son unique période. */
  periode_cle?: string
}
