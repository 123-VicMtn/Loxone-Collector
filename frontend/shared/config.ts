/**
 * Libellés des types de ressource (GET /api/resource-types). Remplace
 * l'injection Jinja `window.RESOURCE_TYPE_LABELS` (core/config.js) --
 * nécessaire dès qu'une page est servie en statique pur, sans template.
 * Mis en cache comme loadAllSeries() : une page ne le redemande pas à
 * chaque composant qui en a besoin.
 */

import { fetchJSON } from './api/http'

let cache: Record<string, string> | null = null
let inflight: Promise<Record<string, string>> | null = null

export async function loadResourceTypeLabels(): Promise<Record<string, string>> {
  if (cache) return cache
  if (!inflight) {
    inflight = fetchJSON<Record<string, string>>('/api/resource-types').then((data) => {
      cache = data
      return data
    })
  }
  return inflight
}
