import type { Series } from '@shared/types/series'
import { fetchRange } from '@shared/api/series'
import { fmtNumber } from '@shared/format'
import { monthBounds, todayBounds, weekBounds, yearBounds, type Bounds } from '@shared/periods'

export interface PeriodTile {
  label: string
  value: string
  unit: string
}

export interface PeriodGroupResult {
  tiles: PeriodTile[]
  any: boolean
  todayKwh: number | null
}

/** Groupe de tuiles jour/semaine/mois/année pour une série cumulative
 * ("total"/"totalNeg"), calculées ici via relevé de fin - relevé de début
 * (`/api/series/<id>/range`, billing.reading_delta) -- la MÊME méthode que
 * /decompte, plutôt que de lire les compteurs vivants Loxone
 * totalDay/Week/Month/Year (voir CLAUDE.md, "Refactor extraction/lecture
 * des données dashboard", 2026-09-24 : ces compteurs Loxone s'écartaient
 * du relevé de compteur réel de 13,5 % sur un mois testé). Remplace
 * l'ancien `periodGroupData` basé sur fetchLatest() + 4 séries séparées.
 *
 * `labelPrefix` vide -> pas de préfixe ni de tiret (zone-tab.js::renderKpis
 * n'en a pas, une seule ressource affichée à la fois -- pas d'ambiguïté à
 * lever contrairement à l'onglet Énergie, qui affiche réseau/export/solaire
 * ensemble). */
export async function periodGroupData(
  labelPrefix: string,
  series: Series | null | undefined,
): Promise<PeriodGroupResult> {
  if (!series) return { tiles: [], any: false, todayKwh: null }

  const now = new Date()
  const [today, week, month, year] = await Promise.all([
    fetchRange(series.series_id, ...todayBounds(now)),
    fetchRange(series.series_id, ...weekBounds(now)),
    fetchRange(series.series_id, ...monthBounds(now)),
    fetchRange(series.series_id, ...yearBounds(now)),
  ])

  const unit = series.unit || 'kWh'
  const withPrefix = (label: string) => (labelPrefix ? `${labelPrefix} — ${label}` : label)
  const tiles: PeriodTile[] = []
  let any = false
  const push = (label: string, kwh: number | null | undefined, digits: number) => {
    if (kwh === null || kwh === undefined) return
    tiles.push({ label: withPrefix(label), value: fmtNumber(kwh, digits), unit })
    any = true
  }
  push("Aujourd'hui", today?.kwh, 2)
  push('Cette semaine', week?.kwh, 2)
  push('Ce mois', month?.kwh, 1)
  push('Cette année', year?.kwh, 1)

  return { tiles, any, todayKwh: today?.kwh ?? null }
}

/** Une seule tuile de consommation sur une plage de dates choisie par
 * l'utilisateur -- répond à l'exigence explicite de pouvoir sélectionner
 * des dates dans le dashboard, en plus des 4 presets fixes ci-dessus. */
export async function rangeTile(
  label: string,
  series: Series | null | undefined,
  bounds: Bounds,
): Promise<PeriodTile | null> {
  if (!series) return null
  const delta = await fetchRange(series.series_id, ...bounds)
  if (delta?.kwh === null || delta?.kwh === undefined) return null
  return { label, value: fmtNumber(delta.kwh, 2), unit: series.unit || 'kWh' }
}
