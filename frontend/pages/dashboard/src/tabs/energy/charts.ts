import { fetchDaily, fetchSeriesData } from '@shared/api/series'
import { fmtDateShort, fmtDateTimeShort, fmtMonthShort } from '@shared/format'
import { PALETTE_BATTERY, PALETTE_GRID, PALETTE_SOLAR, aggregateMonthly } from '@shared/charts'
import type { RangeKey } from '@shared/ranges'
import type { ZoneEnergySeries } from './seriesFor'

type BarChartData = { labels: string[]; datasets: Record<string, unknown>[] }

/** Fusionne deux séries de points journaliers {date_ts, consumption} sur
 * l'union de leurs dates -- port du dateMap de renderDailyChart/
 * renderBatteryChart (dupliqué à l'identique dans le JS d'origine, une
 * seule version ici). */
async function dailyPairChart(
  aSid: string | null | undefined, bSid: string | null | undefined,
  aLabel: string, bLabel: string, aColor: string, bColor: string,
  bSign: 1 | -1 = 1,
): Promise<BarChartData> {
  const [aPoints, bPoints] = await Promise.all([fetchDaily(aSid, 30), fetchDaily(bSid, 30)])
  const dateMap = new Map<number, { a?: number; b?: number }>()
  aPoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {})
    dateMap.get(p.date_ts)!.a = p.consumption
  })
  bPoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {})
    dateMap.get(p.date_ts)!.b = p.consumption
  })
  const sortedTs = Array.from(dateMap.keys()).sort((x, y) => x - y)
  const labels = sortedTs.map(fmtDateShort)
  const aData = sortedTs.map((t) => (dateMap.get(t)!.a !== undefined ? dateMap.get(t)!.a! : null))
  const bData = sortedTs.map((t) => (dateMap.get(t)!.b !== undefined ? bSign * dateMap.get(t)!.b! : null))

  return {
    labels,
    datasets: [
      { label: aLabel, data: aData, backgroundColor: aColor },
      { label: bLabel, data: bData, backgroundColor: bColor },
    ],
  }
}

export function buildDailyGridSolarChart(gridSid: string | null | undefined, solarSid: string | null | undefined) {
  return dailyPairChart(gridSid, solarSid, 'Réseau — import (kWh)', 'Solaire (kWh)', PALETTE_GRID, PALETTE_SOLAR)
}

export function buildBatteryChart(chargeSid: string | null | undefined, dischargeSid: string | null | undefined) {
  return dailyPairChart(chargeSid, dischargeSid, 'Charge (kWh)', 'Décharge (kWh)', PALETTE_BATTERY, PALETTE_GRID, -1)
}

export async function buildMonthlyGridSolarChart(
  gridSid: string | null | undefined, solarSid: string | null | undefined,
): Promise<BarChartData> {
  const [gridPoints, solarPoints] = await Promise.all([fetchDaily(gridSid, 380), fetchDaily(solarSid, 380)])
  const gridMonthly = aggregateMonthly(gridPoints)
  const solarMonthly = aggregateMonthly(solarPoints)
  const gridByKey = new Map(gridMonthly.map((m) => [m.key, m]))
  const solarByKey = new Map(solarMonthly.map((m) => [m.key, m]))
  const sortedKeys = Array.from(new Set([...gridByKey.keys(), ...solarByKey.keys()])).sort()
  const labels = sortedKeys.map((k) => fmtMonthShort((gridByKey.get(k) || solarByKey.get(k))!.ts))
  const gridData = sortedKeys.map((k) => (gridByKey.has(k) ? gridByKey.get(k)!.sum : null))
  const solarData = sortedKeys.map((k) => (solarByKey.has(k) ? solarByKey.get(k)!.sum : null))

  return {
    labels,
    datasets: [
      { label: 'Réseau — import (kWh)', data: gridData, backgroundColor: PALETTE_GRID },
      { label: 'Solaire (kWh)', data: solarData, backgroundColor: PALETTE_SOLAR },
    ],
  }
}

/** Puissance instantanée : si un bloc EFM ("Moniteur de flux d'énergie")
 * existe pour la zone, ses states Gpwr/Ppwr/Spwr sont déjà signés (import/
 * export, charge/décharge) et cohérents avec la doc Loxone -- préférés aux
 * `actual` des compteurs Meter séparés, qui ne portent qu'une grandeur non
 * signée par compteur. */
export async function buildPowerChart(sids: ZoneEnergySeries, range: RangeKey): Promise<BarChartData | null> {
  const useEfm = sids.efmGpwr || sids.efmPpwr || sids.efmSpwr
  const specs = useEfm
    ? [
        { s: sids.efmGpwr, label: 'Réseau — Gpwr (kW)', color: PALETTE_GRID },
        { s: sids.efmPpwr, label: 'Solaire — Ppwr (kW)', color: PALETTE_SOLAR },
        { s: sids.efmSpwr, label: 'Batterie — Spwr (kW)', color: PALETTE_BATTERY },
      ]
    : [
        { s: sids.gridActual, label: 'Réseau (kW)', color: PALETTE_GRID },
        { s: sids.solarActual, label: 'Solaire (kW)', color: PALETTE_SOLAR },
        { s: sids.batteryActual, label: 'Batterie (kW)', color: PALETTE_BATTERY },
      ]

  let labels: string[] | null = null
  const datasets: Record<string, unknown>[] = []
  for (const { s, label, color } of specs) {
    if (!s) continue
    const data = await fetchSeriesData(s.series_id, range)
    const pointLabels = data.points.map((p) => fmtDateTimeShort(p.ts))
    if (!labels || pointLabels.length > labels.length) labels = pointLabels
    datasets.push({
      label,
      data: data.points.map((p) => p.value),
      borderColor: color,
      backgroundColor: color,
      pointRadius: 0,
      borderWidth: 2,
      tension: 0.15,
      spanGaps: true,
    })
  }
  if (!datasets.length) return null
  return { labels: labels || [], datasets }
}
