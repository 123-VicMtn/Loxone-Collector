import { fmtNumber } from '@shared/format'
import type { PeriodTile } from './periodGroup'

export interface AutoconsoResult {
  tiles: PeriodTile[]
  notes: string[]
  visible: boolean
}

/** Autoconsommation du jour : production − export, les deux lus sur les
 * index cumulatifs (total / totalNeg). Repli sur la part du solaire dans
 * import + production si l'export n'existe pas pour la zone. */
export function computeAutoconso(
  gridDayV: number | null,
  gridNegDayV: number | null,
  solarDayV: number | null,
): AutoconsoResult {
  const tiles: PeriodTile[] = []
  const notes: string[] = []
  let visible = false

  if (solarDayV !== null && solarDayV > 0 && gridNegDayV !== null) {
    const selfConsumed = Math.max(0, solarDayV - gridNegDayV)
    const pct = Math.min(100, (selfConsumed / solarDayV) * 100)
    tiles.push({ label: "Autoconsommation — aujourd'hui", value: fmtNumber(pct, 0), unit: '%' })
    notes.push(
      'Scd = Production du jour − Export du jour (formule Loxone officielle : autoconsommation = énergie ' +
      'consommée depuis une source propre, pas depuis le réseau).',
    )
    visible = true
  } else if (gridDayV !== null && solarDayV !== null && gridDayV + solarDayV > 0) {
    const pct = (solarDayV / (gridDayV + solarDayV)) * 100
    tiles.push({ label: "Couverture solaire (estimation) — aujourd'hui", value: fmtNumber(pct, 0), unit: '%' })
    notes.push(
      "Estimation approximative (part du solaire dans import + production) : l'export réseau n'est pas " +
      "disponible pour cette zone, la vraie autoconsommation Loxone (production − export) ne peut pas être " +
      'calculée ici.',
    )
    visible = true
  }

  return { tiles, notes, visible }
}
