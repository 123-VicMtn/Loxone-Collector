import { fetchLatest } from '@shared/api/series'
import { fmtNumber } from '@shared/format'
import type { PeriodTile } from './periodGroup'
import type { ZoneEnergySeries } from './seriesFor'

export interface AutoconsoResult {
  tiles: PeriodTile[]
  notes: string[]
  visible: boolean
}

/** Autoconsommation : Scd = Pd - Ed (production du jour moins export du
 * jour), formule documentée par Loxone. Repli sur un taux de "couverture
 * solaire" (approximatif, clairement étiqueté comme tel) si l'export
 * réseau (totalNeg) n'est pas disponible pour cette zone. Ajoute aussi, à
 * titre indicatif seulement, la valeur brute exposée par le bloc EFM
 * Loxone (state selfConsumption) dont la sémantique exacte n'est pas
 * confirmée -- voir CLAUDE.md. Port direct de
 * energy-tab.js::renderAutoconso. */
export async function computeAutoconso(
  sids: ZoneEnergySeries,
  gridDayV: number | null,
  gridNegDayV: number | null,
  solarDayV: number | null,
): Promise<AutoconsoResult> {
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

  const efmRaw = await fetchLatest(sids.efmSelfConsumption?.series_id)
  if (efmRaw !== null) {
    tiles.push({ label: 'Autoconsommation (brute Loxone)', value: fmtNumber(efmRaw, 1), unit: '' })
    notes.push(
      'Valeur exposée directement par le bloc "Moniteur de flux d\'énergie" Loxone (state selfConsumption) -- ' +
      'échelle et unité non confirmées, affichée à titre indicatif seulement.',
    )
    visible = true
  }

  return { tiles, notes, visible }
}
