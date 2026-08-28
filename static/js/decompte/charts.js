/**
 * Les quatre graphs de la page de décompte. Chacun répond à une question
 * que se pose un propriétaire, dans cet ordre :
 *   1. evolution  -- combien l'immeuble consomme-t-il chaque mois, et quelle
 *                    part vient du soleil ?
 *   2. zones      -- comment cette consommation se répartit-elle entre les
 *                    zones sur le mois choisi ?
 *   3. solaire    -- que produit le photovoltaïque, qu'est-ce qui est
 *                    consommé sur place et qu'est-ce qui repart au réseau ?
 *   4. taux       -- les deux indicateurs d'autonomie, côte à côte.
 *
 * Palette : orange = réseau, vert = solaire, cohérent avec l'onglet Énergie
 * du dashboard (core/charts.js).
 */

import {
  PALETTE_GRID, PALETTE_SOLAR, PALETTE_GENERIC, baseBarOptions,
} from "../core/charts.js";
import { fmtNumber } from "../core/format.js";

const charts = {};

function render(canvasId, config) {
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), config);
}

function sumZones(payload, periodKey, pick) {
  let sum = 0;
  for (const z of payload.zones) {
    const e = z.periodes[periodKey];
    const v = e ? pick(e) : null;
    if (v === null || v === undefined) return null;
    sum += v;
  }
  return sum;
}

const kwhTooltip = {
  callbacks: { label: (ctx) => `${ctx.dataset.label} : ${fmtNumber(ctx.parsed.y, 0)} kWh` },
};

function stackedKwhOptions() {
  const options = baseBarOptions(true);
  options.scales = {
    x: { stacked: true },
    y: { stacked: true, beginAtZero: true, title: { display: true, text: "kWh" } },
  };
  options.plugins.tooltip = kwhTooltip;
  return options;
}

/** Consommation mensuelle de l'immeuble, empilée réseau + solaire. La
 * hauteur totale est la consommation facturée, la part verte celle couverte
 * par le solaire. */
export function renderEvolutionChart(payload, periodes) {
  render("chart-evolution", {
    type: "bar",
    data: {
      labels: periodes.map((p) => p.label_court),
      datasets: [
        { label: "Réseau", data: periodes.map((p) => sumZones(payload, p.key, (e) => e.reseau.kwh)), backgroundColor: PALETTE_GRID },
        { label: "Solaire autoconsommé", data: periodes.map((p) => sumZones(payload, p.key, (e) => e.solaire.kwh)), backgroundColor: PALETTE_SOLAR },
      ],
    },
    options: stackedKwhOptions(),
  });
}

export function renderZonesChart(payload, periodKey) {
  render("chart-zones", {
    type: "bar",
    data: {
      labels: payload.zones.map((z) => z.label),
      datasets: [
        { label: "Réseau", data: payload.zones.map((z) => (z.periodes[periodKey] || {}).reseau?.kwh ?? null), backgroundColor: PALETTE_GRID },
        { label: "Solaire autoconsommé", data: payload.zones.map((z) => (z.periodes[periodKey] || {}).solaire?.kwh ?? null), backgroundColor: PALETTE_SOLAR },
      ],
    },
    options: stackedKwhOptions(),
  });
}

/** Devenir de la production photovoltaïque : ce qui est consommé sur place
 * (empilé, vert) et ce qui repart au réseau (empilé, bleu). La hauteur
 * totale de la pile est donc la production du mois. */
export function renderSolaireChart(payload, periodes) {
  const val = (pick) => periodes.map((p) => pick(payload.batiment.periodes[p.key]));
  render("chart-solaire", {
    type: "bar",
    data: {
      labels: periodes.map((p) => p.label_court),
      datasets: [
        { label: "Autoconsommé sur place", data: val((b) => b.autoconsommation), backgroundColor: PALETTE_SOLAR },
        { label: "Injecté au réseau", data: val((b) => b.injection), backgroundColor: PALETTE_GENERIC },
      ],
    },
    options: stackedKwhOptions(),
  });
}

/** Les deux taux côte à côte. Ils évoluent en sens INVERSE au fil des
 * saisons, et c'est normal : les afficher ensemble est la seule façon
 * d'éviter qu'on lise l'un pour l'autre. */
export function renderTauxChart(payload, periodes) {
  const val = (pick) => periodes.map((p) => pick(payload.batiment.periodes[p.key]));
  render("chart-taux", {
    type: "line",
    data: {
      labels: periodes.map((p) => p.label_court),
      datasets: [
        {
          label: "Autoproduction (solaire ÷ consommation)",
          data: val((b) => b.taux_autoproduction),
          borderColor: PALETTE_SOLAR, backgroundColor: PALETTE_SOLAR,
          tension: 0.25, spanGaps: false,
        },
        {
          label: "Autoconsommation (solaire ÷ production)",
          data: val((b) => b.taux_autoconsommation),
          borderColor: PALETTE_GENERIC, backgroundColor: PALETTE_GENERIC,
          borderDash: [6, 4], tension: 0.25, spanGaps: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: { y: { beginAtZero: true, max: 100, title: { display: true, text: "%" } } },
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label} : ${fmtNumber(ctx.parsed.y, 1)} %` } },
      },
    },
  });
}
