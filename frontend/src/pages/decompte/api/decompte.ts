import { fetchJSON, postJSON, deleteJSON, downloadFile } from '@shared/api/http'
import type { DecomptePayload, Tarif } from '../types/decompte'

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

export async function fetchTarifs(miniserver: string): Promise<Tarif[]> {
  const params = new URLSearchParams()
  if (miniserver) params.set('miniserver', miniserver)
  const qs = params.toString()
  return fetchJSON<Tarif[]>(`/api/tarifs${qs ? '?' + qs : ''}`)
}

export interface TarifInput {
  miniserver: string
  valid_from: string
  prix_reseau: number
  prix_solaire: number
  taux_tva: number
  note: string
}

/** Crée ou remplace le tarif d'un site prenant effet à `valid_from`.
 * Retourne la liste des tarifs de CE site telle que le serveur la voit
 * après écriture. */
export async function saveTarif(tarif: TarifInput): Promise<Tarif[]> {
  return postJSON<Tarif[]>('/api/tarifs', tarif)
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

export async function deleteTarif(id: number, miniserver: string): Promise<Tarif[]> {
  const params = new URLSearchParams()
  if (miniserver) params.set('miniserver', miniserver)
  const qs = params.toString()
  return deleteJSON<Tarif[]>(`/api/tarifs/${id}${qs ? '?' + qs : ''}`)
}
