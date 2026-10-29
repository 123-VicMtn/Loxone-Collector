import { fetchJSON } from '@shared/api/http'
import type { HistoriqueMois } from '../types/historique'

/** Historique mensuel d'un immeuble, mois incomplets compris. */
export async function fetchHistorique(miniserver: string): Promise<HistoriqueMois[]> {
  const params = new URLSearchParams({ miniserver })
  const data = await fetchJSON<{ mois: HistoriqueMois[] }>(`/api/historique?${params}`)
  return data.mois
}
