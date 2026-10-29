/**
 * Options Chart.js des graphiques mensuels (barres kWh). La palette vient
 * de @shared/charts.
 */

import type { ChartOptions, TooltipItem } from 'chart.js'
import { fmtNumber } from '@shared/format'

export { PALETTE_GRID, PALETTE_SOLAR, PALETTE_GENERIC } from '@shared/charts'

export function stackedKwhOptions(): ChartOptions<'bar'> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: { stacked: true },
      y: { stacked: true, beginAtZero: true, title: { display: true, text: 'kWh' } },
    },
    plugins: {
      legend: { display: true, position: 'bottom' },
      tooltip: {
        callbacks: {
          label: (ctx: TooltipItem<'bar'>) => `${ctx.dataset.label} : ${fmtNumber(ctx.parsed.y, 0)} kWh`,
        },
      },
    },
  }
}

/** Barres côte à côte : comparer deux grandeurs (production vs consommation)
 * sans les additionner. */
export function groupedKwhOptions(): ChartOptions<'bar'> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      y: { beginAtZero: true, title: { display: true, text: 'kWh' } },
    },
    plugins: {
      legend: { display: true, position: 'bottom' },
      tooltip: {
        callbacks: {
          label: (ctx: TooltipItem<'bar'>) => `${ctx.dataset.label} : ${fmtNumber(ctx.parsed.y, 0)} kWh`,
        },
      },
    },
  }
}

/** Libellé d'un mois sur l'axe ; une étoile marque un mois incomplet ou
 * en cours (détail sous le graphique). */
export function libelleMois(m: { label_court: string; partiel: boolean; en_cours: boolean; vide: boolean }): string {
  return !m.vide && (m.partiel || m.en_cours) ? `${m.label_court} *` : m.label_court
}
