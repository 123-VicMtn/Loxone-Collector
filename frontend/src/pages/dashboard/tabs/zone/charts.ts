import { fetchDaily } from '@shared/api/series'
import { fmtDateShort, fmtMonthShort } from '@shared/format'
import { PALETTE_GENERIC, aggregateMonthly } from '@shared/charts'

type BarChartData = { labels: string[]; datasets: Record<string, unknown>[] }

/** Une seule série cumulative ("total") -- contrairement à l'onglet
 * Énergie (toujours réseau + solaire), l'onglet zone affiche une
 * ressource à la fois. Port de zone-tab.js::renderDailyChart. */
export async function buildSingleDailyChart(seriesId: string, unit: string): Promise<BarChartData> {
  const points = await fetchDaily(seriesId, 30)
  return {
    labels: points.map((p) => fmtDateShort(p.date_ts)),
    datasets: [{ label: `Consommation (${unit || 'unité'})`, data: points.map((p) => p.consumption), backgroundColor: PALETTE_GENERIC }],
  }
}

/** Port de zone-tab.js::renderMonthlyChart. */
export async function buildSingleMonthlyChart(seriesId: string, unit: string): Promise<BarChartData> {
  const points = await fetchDaily(seriesId, 380)
  const monthly = aggregateMonthly(points)
  return {
    labels: monthly.map((m) => fmtMonthShort(m.ts)),
    datasets: [{ label: `Consommation (${unit || 'unité'})`, data: monthly.map((m) => m.sum), backgroundColor: PALETTE_GENERIC }],
  }
}
