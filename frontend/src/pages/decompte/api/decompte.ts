import { fetchJSON, postJSON, downloadFile } from '@shared/api/http'
import type { DecomptePayload } from '../types/decompte'

/** Sites configurés (config.yaml) -- alimente le sélecteur de site qui
 * scope tout le reste de la page. */
export async function fetchMiniservers(): Promise<string[]> {
  return fetchJSON<string[]>('/api/miniservers')
}

export async function fetchDecompte(opts: {
  miniserver?: string
  from?: string
  to?: string
} = {}): Promise<DecomptePayload> {
  const params = new URLSearchParams()
  if (opts.miniserver) params.set('miniserver', opts.miniserver)
  if (opts.from) params.set('from', opts.from)
  if (opts.to) params.set('to', opts.to)
  const qs = params.toString()
  return fetchJSON<DecomptePayload>(`/api/decompte${qs ? '?' + qs : ''}`)
}

/** Décompte des dates choisies (YYYY-MM-DD, `au` inclus), ramené à une
 * seule période (`periode_cle`). */
export async function fetchDecomptePlage(miniserver: string, du: string, au: string): Promise<DecomptePayload> {
  const params = new URLSearchParams({ miniserver, du, au })
  return fetchJSON<DecomptePayload>(`/api/decompte?${params}`)
}

/** Relit tout de suite les Statistics du site sur le Miniserver (la page ne
 * lit que la base, alimentée chaque heure par la synchro de fond). Recharger
 * ensuite le décompte. */
export async function actualiserDecompte(miniserver: string): Promise<void> {
  await postJSON<unknown>('/api/decompte/actualiser', { miniserver })
}

function nomSur(texte: string): string {
  return texte.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_|_$/g, '')
}

/** `zone` (code, ex. APP35) : le décompte de cette seule zone. */
function telecharger(
  format: 'xlsx' | 'csv', params: Record<string, string>, miniserver: string,
  suffixe: string, zone?: string,
): Promise<void> {
  const query = new URLSearchParams({ miniserver, ...params })
  if (zone) query.set('zone', zone)
  const nom = (nomSur(miniserver) || 'site') + (zone ? `-${nomSur(zone)}` : '')
  return downloadFile(`/api/decompte/export.${format}?${query}`, `decompte-${nom}-${suffixe}.${format}`)
}

export function downloadDecompte(
  format: 'xlsx' | 'csv', miniserver: string, mois: string, zone?: string,
): Promise<void> {
  return telecharger(format, { mois }, miniserver, mois, zone)
}

/** Décompte sur des dates choisies (YYYY-MM-DD, `au` inclus). */
export function downloadDecomptePeriode(
  format: 'xlsx' | 'csv', miniserver: string, du: string, au: string, zone?: string,
): Promise<void> {
  return telecharger(format, { du, au }, miniserver, `${du}_${au}`, zone)
}
