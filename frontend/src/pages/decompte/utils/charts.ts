/**
 * Options Chart.js des graphs du décompte (barres kWh). La palette vient
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
