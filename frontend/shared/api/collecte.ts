import { fetchJSON } from './http'

/** Un site vu par GET /api/collecte (sites accessibles au compte). */
export interface SiteCollecte {
  miniserver: string
  /** Dernier relevé du collecteur (epoch), null si jamais relevé. */
  dernier_releve_ts: number | null
  en_retard: boolean
}

export interface Collecte {
  seuil_minutes: number
  sites: SiteCollecte[]
}

export function fetchCollecte(): Promise<Collecte> {
  return fetchJSON<Collecte>('/api/collecte')
}
