/**
 * Palette et options Chart.js de base, partagées par toutes les pages.
 * Port direct de static/js/core/charts.js -- `kpiTile()` n'a pas
 * d'équivalent ici, remplacé par le composant Vue KpiTile.
 */

import type { ChartOptions } from 'chart.js'

// Couleurs dédiées : orange = réseau (grid/import), vert = solaire
// (production). Cohérent entre tous les graphs de l'onglet Énergie et du
// décompte.
export const PALETTE_GRID = '#d97706'
export const PALETTE_SOLAR = '#16a34a'
export const PALETTE_GENERIC = '#2563eb'
export const PALETTE_BATTERY = '#7c3aed'

// Rotation de couleurs pour l'onglet Explorer (sélection libre multi-
// capteurs, nombre de courbes non borné à l'avance).
export const PALETTE_ROTATING = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed',
  '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4338ca',
]

export function baseLineOptions(beginAtZero = true): ChartOptions<'line'> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    scales: {
      x: { ticks: { maxTicksLimit: 12, autoSkip: true } },
      y: { beginAtZero },
    },
    plugins: { legend: { position: 'bottom' } },
  }
}

export function baseBarOptions(showLegend: boolean): ChartOptions<'bar'> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: { y: { beginAtZero: true } },
    plugins: { legend: { display: showLegend, position: 'bottom' } },
  }
}
