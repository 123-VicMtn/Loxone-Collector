import { fetchJSON, postJSON } from '@shared/api/http'
import type { Series } from '../types/series'

export async function fetchSeries(): Promise<Series[]> {
  return fetchJSON<Series[]>('/api/series')
}

export async function fetchResourceTypeLabels(): Promise<Record<string, string>> {
  return fetchJSON<Record<string, string>>('/api/resource-types')
}

export interface ClassifyBody {
  apartment?: string
  resource_type?: string
  reset?: boolean
}

/** Retourne un simple booléen (comme static/js/admin.js) : la page n'a
 * besoin de rien d'autre que "ça a marché ou pas" pour flasher le statut
 * d'une ligne. */
export async function classify(seriesId: string, body: ClassifyBody): Promise<boolean> {
  try {
    await postJSON(`/api/series/${encodeURIComponent(seriesId)}/classify`, body)
    return true
  } catch {
    return false
  }
}
