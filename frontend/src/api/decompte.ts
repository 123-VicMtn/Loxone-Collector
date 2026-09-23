import { fetchJSON, postJSON, deleteJSON } from './http'
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

export async function deleteTarif(id: number, miniserver: string): Promise<Tarif[]> {
  const params = new URLSearchParams()
  if (miniserver) params.set('miniserver', miniserver)
  const qs = params.toString()
  return deleteJSON<Tarif[]>(`/api/tarifs/${id}${qs ? '?' + qs : ''}`)
}
