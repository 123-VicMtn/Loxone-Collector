/**
 * Accès à /api/series + un petit cache mémoire (la liste complète des
 * capteurs), lue par tous les onglets du dashboard mais rafraîchie une
 * seule fois par chargement de page -- port direct de core/api.js.
 */

import { fetchJSON } from './http'
import type { ReadingDelta, Series } from '../types/series'

let allSeries: Series[] = []
let loaded = false
let inflight: Promise<Series[]> | null = null

/** Charge (une seule fois, puis depuis le cache) la liste de tous les
 * capteurs connus. */
export async function loadAllSeries(): Promise<Series[]> {
  if (loaded) return allSeries
  if (!inflight) {
    inflight = fetchJSON<Series[]>('/api/series').then((data) => {
      allSeries = data
      loaded = true
      return data
    })
  }
  return inflight
}

/** Premier capteur du cache vérifiant `predicate`, ou null. Suppose que
 * loadAllSeries() a déjà été appelé (cache simplement vide sinon). */
export function findSeries(predicate: (s: Series) => boolean): Series | null {
  return allSeries.find(predicate) || null
}

export interface SeriesDataPoint {
  ts: number
  value: number | null
}

export interface SeriesDataResponse {
  series_id: string
  start: number
  end: number
  points: SeriesDataPoint[]
}

export async function fetchSeriesData(seriesId: string, range: string): Promise<SeriesDataResponse> {
  return fetchJSON<SeriesDataResponse>(`/api/series/${encodeURIComponent(seriesId)}/data?range=${encodeURIComponent(range)}`)
}

/** Points bruts entre deux timestamps Unix (secondes), pour une courbe
 * du jour (minuit → maintenant) plutôt qu'un preset 1h/24h. */
export async function fetchSeriesWindow(seriesId: string, start: number, end: number): Promise<SeriesDataPoint[]> {
  const data = await fetchJSON<SeriesDataResponse>(
    `/api/series/${encodeURIComponent(seriesId)}/data?start=${start}&end=${end}`,
  )
  return data.points
}

/** Consommation d'une série cumulative sur [from, to[ (relevé de fin -
 * relevé de début) -- voir billing.py::reading_delta / app.py::api_series_range.
 * C'est la méthode utilisée par /decompte, désormais réutilisée pour les
 * tuiles KPI du dashboard à la place des compteurs vivants Loxone
 * totalDay/Week/Month/Year (voir CLAUDE.md, "Refactor extraction/lecture
 * des données dashboard"). `from`/`to` sont des timestamps Unix (secondes). */
export async function fetchRange(
  seriesId: string | null | undefined,
  from: number,
  to: number,
): Promise<ReadingDelta | null> {
  if (!seriesId) return null
  try {
    return await fetchJSON<ReadingDelta>(
      `/api/series/${encodeURIComponent(seriesId)}/range?from=${from}&to=${to}`,
    )
  } catch (err) {
    console.error(err)
    return null
  }
}

/** Dernière valeur connue d'une série (peu importe son âge). Utilisé pour
 * les valeurs qui ne sont PAS un compteur cumulatif (ex: état de charge %
 * d'une batterie) -- voir CLAUDE.md, "Refactor extraction/lecture des
 * données dashboard" pour la distinction avec fetchRange ci-dessus. */
export async function fetchLatest(seriesId: string | null | undefined): Promise<number | null> {
  if (!seriesId) return null
  try {
    const data = await fetchJSON<{ value: number | null }>(`/api/series/${encodeURIComponent(seriesId)}/latest`)
    return data.value
  } catch (err) {
    console.error(err)
    return null
  }
}

export interface DailyPoint {
  date_ts: number
  end_value: number
  consumption: number
}

/** Relevés de fin de journée + consommation dérivée (delta entre deux
 * relevés successifs) pour une série cumulative ("total"). */
export async function fetchDaily(seriesId: string | null | undefined, days: number): Promise<DailyPoint[]> {
  if (!seriesId) return []
  try {
    const data = await fetchJSON<{ points: DailyPoint[] }>(`/api/series/${encodeURIComponent(seriesId)}/daily?days=${days}`)
    return data.points
  } catch (err) {
    console.error(err)
    return []
  }
}
