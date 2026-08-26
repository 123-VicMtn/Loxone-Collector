/**
 * Tout ce qui touche à Chart.js et aux tuiles KPI : palette de couleurs
 * partagée, options de base pour les graphs ligne/barres, construction
 * d'une tuile KPI, agrégation mensuelle de points journaliers. Chaque
 * onglet construit ses `datasets` lui-même (les données diffèrent trop
 * d'un onglet à l'autre pour factoriser plus), mais consomme ces
 * fonctions communes pour rester visuellement cohérent.
 */

import { monthKey } from "./format.js";

// Couleurs dédiées : orange = réseau (grid/import), vert = solaire
// (production). Cohérent entre tous les graphs de l'onglet Énergie.
export const PALETTE_GRID = "#d97706";
export const PALETTE_SOLAR = "#16a34a";
export const PALETTE_GENERIC = "#2563eb";

// Rotation de couleurs pour l'onglet Explorer (sélection libre multi-capteurs,
// nombre de courbes non borné à l'avance).
export const PALETTE_ROTATING = [
  "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4338ca",
];

export function baseLineOptions(beginAtZero = true) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearest", axis: "x", intersect: false },
    scales: {
      x: { ticks: { maxTicksLimit: 12, autoSkip: true } },
      y: { beginAtZero },
    },
    plugins: { legend: { position: "bottom" } },
  };
}

export function baseBarOptions(showLegend) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: { y: { beginAtZero: true } },
    plugins: { legend: { display: showLegend, position: "bottom" } },
  };
}

export function kpiTile(label, value, unit, colorClass) {
  const div = document.createElement("div");
  div.className = `kpi-tile${colorClass ? " " + colorClass : ""}`;
  div.innerHTML =
    `<div class="kpi-label">${label}</div>` +
    `<div class="kpi-value">${value}${unit ? ` <span class="kpi-unit">${unit}</span>` : ""}</div>`;
  return div;
}

/** Regroupe des points journaliers {date_ts, consumption} par mois
 * calendaire (UTC), en sommant les consommations. Les deltas négatifs
 * (reset de compteur, remplacement) sont exclus de la somme pour ne pas
 * fausser le total mensuel -- ils restent visibles tels quels sur le
 * graph journalier, qui n'agrège rien. */
export function aggregateMonthly(points) {
  const byMonth = new Map();
  for (const p of points) {
    const key = monthKey(p.date_ts);
    if (!byMonth.has(key)) byMonth.set(key, { sum: 0, ts: p.date_ts });
    const entry = byMonth.get(key);
    if (p.consumption > 0) entry.sum += p.consumption;
    entry.ts = Math.min(entry.ts, p.date_ts);
  }
  return Array.from(byMonth.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([key, v]) => ({ key, ts: v.ts, sum: v.sum }));
}
