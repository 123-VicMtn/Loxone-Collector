import type { SeriesDataPoint } from '@shared/api/series'
import type { Series } from '@shared/types/series'

/** Bilan d'un immeuble depuis minuit, lu sur les index cumulatifs.
 *
 * Deux taux, à ne pas confondre (mêmes définitions que /decompte) :
 * - autoconsommation = solaire consommé sur place / production solaire
 * - autonomie = solaire consommé sur place / consommation totale
 *   (le solaire « en direct ou via la batterie » est déjà dans la part
 *   solaire des zones : l'ajouter à part doublerait le compte)
 *
 * Le solaire sur place n'est pas le même compteur selon le site :
 * - zones Grid + Solaire (Arlopi) : somme des index « Solaire » / « Sol »
 * - pas de split par zone (Horizon, Sequoia) : production − export,
 *   seulement si l'export ne dépasse pas la production
 */
export interface DayBalance {
  production: number | null
  consumption: number | null
  solarOnSite: number | null
  autoconsommation: number | null
  autonomie: number | null
  note: string | null
}

const SOLAR_ZONE = /solaire|\bsol\b/i
const GRID_ZONE = /\bgrid\b/i
const PRODUCTION = /^(production\b|.*pv production)/i
export function pickProduction(series: Series[]): Series | null {
  const totals = series.filter((s) => !s.apartment && s.state_name === 'total')
  // À Sequoia, « Production Solaire » est figé. Le compteur qui avance
  // s'appelle « Solaire » (11,5 kWh le 25.09.2026). « Solaire & Batterie »
  // (Arlopi) ne doit pas prendre sa place.
  const solaire = totals.find((s) => /^solaire \(total\)$/i.test(s.label))
  if (solaire) return solaire
  return totals.find((s) => PRODUCTION.test(s.label)) || null
}

export function pickExport(series: Series[]): Series | null {
  const neg = series.find((s) => !s.apartment && s.state_name === 'totalNeg' && /r[ée]seau|alimentation/i.test(s.label))
  if (neg) return neg
  return series.find((s) => !s.apartment && s.state_name === 'total' && /export/i.test(s.label)) || null
}

export function zoneSolarSeries(series: Series[]): Series[] {
  return series.filter((s) => s.apartment && s.state_name === 'total' && SOLAR_ZONE.test(s.label))
}

export function zoneGridSeries(series: Series[]): Series[] {
  return series.filter((s) => s.apartment && s.state_name === 'total' && GRID_ZONE.test(s.label))
}

/** Même compteurs, état `actual` (puissance kW) — la courbe du jour. */
export function actualsOf(all: Series[], totals: Series[]): Series[] {
  const controls = new Set(totals.map((s) => s.control_uuid))
  return all.filter((s) => s.state_name === 'actual' && controls.has(s.control_uuid))
}

export function pickImport(series: Series[]): Series | null {
  return series.find((s) =>
    !s.apartment && s.state_name === 'total' && /import|r[ée]seau/i.test(s.label) && !/export/i.test(s.label),
  ) || null
}

/** Compteurs de consommation d'un lot, hors chaleur, buanderie et machines. */
export function lotConsumptionSeries(series: Series[]): Series[] {
  return series.filter((s) =>
    s.apartment
    && s.state_name === 'total'
    && s.resource_type === 'energie_consommee'
    && !/chaleur|ccaleur|cenergie|nrmachine|zähler|déjà mesuré|mesuré aussi/i.test(s.label)
    && !SOLAR_ZONE.test(s.label)
    && !GRID_ZONE.test(s.label),
  )
}

export function balanceFromKwh(
  production: number | null,
  solarOnSite: number | null,
  consumption: number | null,
): DayBalance {
  if (production === null || solarOnSite === null || consumption === null) {
    return { production, consumption, solarOnSite, autoconsommation: null, autonomie: null, note: 'Relevés incomplets depuis minuit.' }
  }
  if (solarOnSite < 0) {
    return {
      production, consumption, solarOnSite: null, autoconsommation: null, autonomie: null,
      note: 'L’export dépasse la production : l’autoconsommation n’est pas calculée.',
    }
  }
  if (production < 0 || consumption < 0) {
    return { production, consumption, solarOnSite, autoconsommation: null, autonomie: null, note: 'Un index a baissé : le delta du jour n’est pas exploitable.' }
  }
  if (solarOnSite > production * 1.02 || (consumption > 0 && solarOnSite > consumption * 1.02)) {
    return {
      production, consumption, solarOnSite, autoconsommation: null, autonomie: null,
      note: 'Le solaire consommé sur place dépasse la production ou la consommation : les compteurs ne se recoupent pas.',
    }
  }
  const autoconsommation = production > 0 ? (solarOnSite / production) * 100 : null
  const autonomie = consumption > 0 ? (solarOnSite / consumption) * 100 : null
  return { production, consumption, solarOnSite, autoconsommation, autonomie, note: null }
}

/** Sequoia a des séries « Grid » / « Solaire » par lot, mais elles redistribuent
 * la production brute : le solaire sur place est production − injection. */
const POWER_BUCKET_S = 180

function bucketSum(lists: SeriesDataPoint[][]): Map<number, number> {
  const sums = new Map<number, number>()
  for (const points of lists) {
    for (const p of points) {
      if (p.value === null) continue
      const bucket = Math.floor(p.ts / POWER_BUCKET_S) * POWER_BUCKET_S
      sums.set(bucket, (sums.get(bucket) || 0) + p.value)
    }
  }
  return sums
}

/** Puissance solaire et réseau alignées sur les mêmes instants, pour une
 * aire empilée : le solaire « prend le dessus » quand sa bande dépasse
 * celle du réseau. */
/** Tous les consommateurs électriques du bâtiment : sur un site avec
 * compteurs Grid + Solaire par zone, les deux (la conso d'une zone est
 * leur somme) ; sinon les compteurs de lot. */
export function buildingConsumerSeries(series: Series[]): Series[] {
  if (usesZoneSplit(series)) return [...zoneGridSeries(series), ...zoneSolarSeries(series)]
  return lotConsumptionSeries(series)
}

export function stackPower(
  solar: SeriesDataPoint[][],
  consumption: SeriesDataPoint[][],
): { ts: number; solar: number; consumption: number }[] {
  const solarSum = bucketSum(solar)
  const consumptionSum = bucketSum(consumption)
  const times = Array.from(new Set([...solarSum.keys(), ...consumptionSum.keys()])).sort((a, b) => a - b)
  return times.map((ts) => ({
    ts,
    solar: solarSum.get(ts) || 0,
    consumption: consumptionSum.get(ts) || 0,
  }))
}

export function usesZoneSplit(series: Series[]): boolean {
  const site = series[0]?.miniserver || ''
  if (/sequoia/i.test(site)) return false
  return zoneSolarSeries(series).length > 0 && zoneGridSeries(series).length > 0
}
