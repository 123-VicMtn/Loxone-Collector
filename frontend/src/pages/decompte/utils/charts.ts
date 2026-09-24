/**
 * Options Chart.js propres aux 4 graphs du décompte (empilement kWh,
 * double taux %). La palette elle-même vient de @shared/charts (commune à
 * toutes les pages -- mêmes couleurs que l'onglet Énergie du dashboard).
 * Port direct de static/js/decompte/charts.js.
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

export function tauxChartOptions(): ChartOptions<'line'> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    scales: { y: { beginAtZero: true, max: 100, title: { display: true, text: '%' } } },
    plugins: {
      legend: { position: 'bottom' },
      tooltip: {
        callbacks: {
          label: (ctx: TooltipItem<'line'>) => `${ctx.dataset.label} : ${fmtNumber(ctx.parsed.y as number, 1)} %`,
        },
      },
    },
  }
}
