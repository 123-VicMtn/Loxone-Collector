/**
 * Onglet "Explorer" : sélection libre multi-capteurs par checkbox dans la
 * sidebar, un graph ligne unique avec une plage de temps (1h/24h/7j/...).
 * C'est le comportement d'origine du dashboard, avant l'ajout des onglets
 * Énergie / Consommations par zone.
 */

import { fetchSeriesData } from "../core/api.js";
import { fmtRangeTs } from "../core/format.js";
import { PALETTE_ROTATING, baseLineOptions } from "../core/charts.js";

let currentRange = "24h";
let chart = null;
const selected = new Map(); // series_id -> {label}

async function refreshChart() {
  const canvas = document.getElementById("chart");
  const hint = document.getElementById("chart-hint");
  if (!canvas || !hint) return;

  if (selected.size === 0) {
    hint.style.display = "block";
    hint.textContent = "Coche un ou plusieurs capteurs à gauche pour afficher leur historique.";
    if (chart) {
      chart.destroy();
      chart = null;
    }
    return;
  }

  hint.style.display = "none";

  const datasets = [];
  let colorIdx = 0;
  let labelSet = null;

  for (const [seriesId, meta] of selected.entries()) {
    let data;
    try {
      data = await fetchSeriesData(seriesId, currentRange);
    } catch (err) {
      console.error(err);
      continue;
    }
    const labels = data.points.map((p) => fmtRangeTs(p.ts, currentRange));
    if (!labelSet) labelSet = labels;

    const color = PALETTE_ROTATING[colorIdx % PALETTE_ROTATING.length];
    colorIdx += 1;

    datasets.push({
      label: meta.label,
      data: data.points.map((p) => p.value),
      borderColor: color,
      backgroundColor: color,
      pointRadius: 0,
      borderWidth: 2,
      tension: 0.15,
      spanGaps: true,
    });
  }

  if (chart) chart.destroy();
  chart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels: labelSet || [], datasets },
    options: baseLineOptions(false),
  });
}

function onCheckboxChange(evt) {
  const cb = evt.target;
  const id = cb.dataset.seriesId;
  if (cb.checked) {
    selected.set(id, { label: cb.dataset.label });
  } else {
    selected.delete(id);
  }
  refreshChart();
}

function setupCheckboxes() {
  document.querySelectorAll(".series-checkbox").forEach((cb) => {
    cb.addEventListener("change", onCheckboxChange);
  });
}

function setupRangeButtons() {
  document.querySelectorAll("#range-buttons .range-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#range-buttons .range-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentRange = btn.dataset.range;
      refreshChart();
    });
  });
}

function setupClearButton() {
  const btn = document.getElementById("clear-selection");
  if (!btn) return;
  btn.addEventListener("click", () => {
    selected.clear();
    document.querySelectorAll(".series-checkbox").forEach((cb) => (cb.checked = false));
    refreshChart();
  });
}

export function initExplorerTab() {
  setupCheckboxes();
  setupRangeButtons();
  setupClearButton();
}
