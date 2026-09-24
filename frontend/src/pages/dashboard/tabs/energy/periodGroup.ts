import type { Series } from '@shared/types/series'
import { fetchLatest } from '@shared/api/series'
import { fmtNumber } from '@shared/format'

export interface PeriodTile {
  label: string
  value: string
  unit: string
}

export interface PeriodGroupResult {
  tiles: PeriodTile[]
  any: boolean
  dayV: number | null
}

/** Groupe de tuiles jour/semaine/mois/année pour un compteur donné, avec
 * repli sur le relevé cumulatif brut si aucun des 4 n'existe. Toutes ces
 * valeurs sont lues via /latest (db.query_latest) : ce sont des compteurs
 * vivants recalculés par le Miniserver, jamais un delta calculé ici. Port
 * direct de energy-tab.js::renderPeriodGroup, mais retourne des données
 * plutôt que de manipuler le DOM.
 *
 * `labelPrefix` vide -> pas de préfixe ni de tiret (zone-tab.js::renderKpis
 * n'en a pas, une seule ressource affichée à la fois -- pas d'ambiguïté à
 * lever contrairement à l'onglet Énergie, qui affiche réseau/export/solaire
 * ensemble). */
export async function periodGroupData(
  labelPrefix: string,
  s: { day?: Series | null; week?: Series | null; month?: Series | null; year?: Series | null; total?: Series | null },
): Promise<PeriodGroupResult> {
  const [dayV, weekV, monthV, yearV, totalV] = await Promise.all([
    fetchLatest(s.day?.series_id),
    fetchLatest(s.week?.series_id),
    fetchLatest(s.month?.series_id),
    fetchLatest(s.year?.series_id),
    fetchLatest(s.total?.series_id),
  ])
  const unit = s.day?.unit || s.total?.unit || 'kWh'
  const withPrefix = (label: string) => (labelPrefix ? `${labelPrefix} — ${label}` : label)
  const tiles: PeriodTile[] = []
  let any = false
  if (dayV !== null) { tiles.push({ label: withPrefix("Aujourd'hui"), value: fmtNumber(dayV, 2), unit }); any = true }
  if (weekV !== null) { tiles.push({ label: withPrefix('Cette semaine'), value: fmtNumber(weekV, 2), unit }); any = true }
  if (monthV !== null) { tiles.push({ label: withPrefix('Ce mois'), value: fmtNumber(monthV, 1), unit }); any = true }
  if (yearV !== null) { tiles.push({ label: withPrefix('Cette année'), value: fmtNumber(yearV, 1), unit }); any = true }
  if (!any && totalV !== null) { tiles.push({ label: withPrefix('Relevé actuel'), value: fmtNumber(totalV, 2), unit }); any = true }
  return { tiles, any, dayV }
}
