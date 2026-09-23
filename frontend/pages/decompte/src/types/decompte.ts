/**
 * Formes exactes renvoyées par /api/decompte et /api/tarifs -- voir
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

export interface ReadingDelta {
  kwh: number | null
  releve_debut: number | null
  releve_fin: number | null
  releve_debut_ts: number | null
  releve_fin_ts: number | null
  alertes: string[]
}

export interface Montants {
  ht: number | null
  tva: number | null
  ttc: number | null
  detail_reseau: number | null
  detail_solaire: number | null
}

export interface Tarif {
  id: number
  miniserver: string
  valid_from: string
  prix_reseau: number
  prix_solaire: number
  taux_tva: number
  note: string
  updated_at?: number
}

export interface ZonePeriod {
  reseau: ReadingDelta
  solaire: ReadingDelta
  controle: ReadingDelta
  total: number | null
  taux_autoproduction: number | null
  en_cours: boolean
  facturable: boolean
  tarif: Tarif | null
  montants: Montants
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

export interface DecomptePayload {
  timezone: string
  periodes: Period[]
  zones: Zone[]
  batiment: Batiment
  tarifs: Tarif[]
  generated_at: number
  miniserver: string
}
