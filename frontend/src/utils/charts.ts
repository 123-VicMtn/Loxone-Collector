/**
 * Palette et options Chart.js partagées entre les 4 graphs du décompte.
 * Port direct de static/js/core/charts.js et static/js/decompte/charts.js
 * -- mêmes couleurs (orange = réseau, vert = solaire), pour rester
 * cohérent avec l'onglet Énergie du dashboard historique tant que les deux
 * coexistent.
 */

import type { ChartOptions, TooltipItem } from 'chart.js'
import { fmtNumber } from './format'

export const PALETTE_GRID = '#d97706'
export const PALETTE_SOLAR = '#16a34a'
export const PALETTE_GENERIC = '#2563eb'

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
