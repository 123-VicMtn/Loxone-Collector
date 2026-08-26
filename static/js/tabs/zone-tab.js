/**
 * Onglet "Consommations par zone" : vue générique (toute ressource --
 * chauffage, eau chaude, énergie, etc.), une zone + une ressource à la
 * fois, avec les mêmes graphs journalier/mensuel que l'onglet Énergie
 * mais sur une seule série cumulative ("total").
 */

import { loadAllSeries, findSeries, fetchLatest, fetchDaily } from "../core/api.js";
import { fmtNumber, fmtDateShort, fmtMonthShort, zoneLabel, resourceLabel } from "../core/format.js";
import { getResourceTypeLabels } from "../core/config.js";
import { PALETTE_GENERIC, baseBarOptions, kpiTile, aggregateMonthly } from "../core/charts.js";

let dailyChart = null;
let monthlyChart = null;
let optionsBuilt = false;

function buildZoneOptions(allSeries) {
  const zoneSelect = document.getElementById("zone-zone-select");
  const zones = new Set();
  allSeries.forEach((s) => zones.add(s.apartment || ""));
  const sorted = Array.from(zones).sort((a, b) => {
    if (a === "") return 1;
    if (b === "") return -1;
    return a.localeCompare(b);
  });
  zoneSelect.innerHTML = "";
  sorted.forEach((apt) => {
    const opt = document.createElement("option");
    opt.value = apt;
    opt.textContent = zoneLabel(apt);
    zoneSelect.appendChild(opt);
  });
}

function buildResourceOptions(allSeries) {
  const zone = document.getElementById("zone-zone-select").value;
  const resourceSelect = document.getElementById("zone-resource-select");
  const labels = getResourceTypeLabels();
  const types = new Set();
  allSeries.forEach((s) => {
    if ((s.apartment || "") === zone && s.state_name === "total") types.add(s.resource_type || "autre");
  });
  const sorted = Array.from(types).sort((a, b) => resourceLabel(a, labels).localeCompare(resourceLabel(b, labels)));
  resourceSelect.innerHTML = "";
  sorted.forEach((rtype) => {
    const opt = document.createElement("option");
    opt.value = rtype;
    opt.textContent = resourceLabel(rtype, labels);
    resourceSelect.appendChild(opt);
  });
}

async function renderKpis(zone, rtype, totalSeriesId, unit) {
  const findState = (state) =>
    findSeries((s) => (s.apartment || "") === zone && s.resource_type === rtype && s.state_name === state);
  const dayS = findState("totalDay");
  const weekS = findState("totalWeek");
  const monthS = findState("totalMonth");
  const yearS = findState("totalYear");

  const [dayV, weekV, monthV, yearV, currentV] = await Promise.all([
    fetchLatest(dayS && dayS.series_id),
    fetchLatest(weekS && weekS.series_id),
    fetchLatest(monthS && monthS.series_id),
    fetchLatest(yearS && yearS.series_id),
    fetchLatest(totalSeriesId),
  ]);

  const kpis = document.getElementById("zone-kpis");
  kpis.innerHTML = "";
  if (dayV !== null) kpis.appendChild(kpiTile("Aujourd'hui", fmtNumber(dayV, 2), unit));
  if (weekV !== null) kpis.appendChild(kpiTile("Cette semaine", fmtNumber(weekV, 2), unit));
  if (monthV !== null) kpis.appendChild(kpiTile("Ce mois", fmtNumber(monthV, 1), unit));
  if (yearV !== null) kpis.appendChild(kpiTile("Cette année", fmtNumber(yearV, 1), unit));
  if (dayV === null && weekV === null && monthV === null && yearV === null && currentV !== null) {
    kpis.appendChild(kpiTile("Relevé actuel", fmtNumber(currentV, 2), unit));
  }
}

async function renderDailyChart(seriesId, unit) {
  const points = await fetchDaily(seriesId, 30);
  const labels = points.map((p) => fmtDateShort(p.date_ts));
  const data = points.map((p) => p.consumption);
  const canvas = document.getElementById("zone-daily-chart");
  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: { labels, datasets: [{ label: `Consommation (${unit || "unité"})`, data, backgroundColor: PALETTE_GENERIC }] },
    options: baseBarOptions(false),
  });
}

async function renderMonthlyChart(seriesId, unit) {
  const points = await fetchDaily(seriesId, 380);
  const monthly = aggregateMonthly(points);
  const labels = monthly.map((m) => fmtMonthShort(m.ts));
  const data = monthly.map((m) => m.sum);
  const canvas = document.getElementById("zone-monthly-chart");
  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: { labels, datasets: [{ label: `Consommation (${unit || "unité"})`, data, backgroundColor: PALETTE_GENERIC }] },
    options: baseBarOptions(false),
  });
}

async function refresh() {
  const zone = document.getElementById("zone-zone-select").value;
  const rtype = document.getElementById("zone-resource-select").value;

  const hint = document.getElementById("zone-empty-hint");
  const body = document.getElementById("zone-body");

  const totalSeries = findSeries((s) => (s.apartment || "") === zone && s.resource_type === rtype && s.state_name === "total");
  if (!totalSeries) {
    hint.hidden = false;
    hint.textContent = 'Aucune série cumulative ("total") pour cette combinaison zone/ressource.';
    body.hidden = true;
    return;
  }
  hint.hidden = true;
  body.hidden = false;

  const unit = totalSeries.unit || "";
  await renderKpis(zone, rtype, totalSeries.series_id, unit);
  await renderDailyChart(totalSeries.series_id, unit);
  await renderMonthlyChart(totalSeries.series_id, unit);
}

export async function initZoneTab() {
  const allSeries = await loadAllSeries();
  if (!optionsBuilt) {
    buildZoneOptions(allSeries);
    buildResourceOptions(allSeries);
    document.getElementById("zone-zone-select").addEventListener("change", () => {
      buildResourceOptions(allSeries);
      refresh();
    });
    document.getElementById("zone-resource-select").addEventListener("change", refresh);
    optionsBuilt = true;
  }
  refresh();
}
