/**
 * Onglet "Énergie" : réseau (grid) vs solaire, par zone. Puissance
 * instantanée + consommation/production journalière et mensuelle,
 * dérivées des compteurs cumulatifs ("total").
 */

import { loadAllSeries, findSeries, fetchJSON, fetchLatest, fetchDaily } from "../core/api.js";
import { fmtNumber, fmtDateShort, fmtMonthShort, fmtDateTimeShort, zoneLabel } from "../core/format.js";
import { PALETTE_GRID, PALETTE_SOLAR, baseLineOptions, baseBarOptions, kpiTile, aggregateMonthly } from "../core/charts.js";

let powerChart = null;
let dailyChart = null;
let monthlyChart = null;
let range = "24h";
let zonesBuilt = false;

function buildZoneOptions(allSeries) {
  const select = document.getElementById("energy-zone-select");
  const zones = new Set();
  allSeries.forEach((s) => {
    if (s.resource_type === "energie_reseau" || s.resource_type === "energie_solaire") {
      zones.add(s.apartment || "");
    }
  });
  const sorted = Array.from(zones).sort((a, b) => {
    if (a === "") return 1;
    if (b === "") return -1;
    return a.localeCompare(b);
  });
  select.innerHTML = "";
  sorted.forEach((apt) => {
    const opt = document.createElement("option");
    opt.value = apt;
    opt.textContent = zoneLabel(apt);
    select.appendChild(opt);
  });
}

function seriesFor(zone) {
  const find = (resourceType, stateName) =>
    findSeries((s) => (s.apartment || "") === zone && s.resource_type === resourceType && s.state_name === stateName);
  return {
    gridActual: find("energie_reseau", "actual"),
    gridTotal: find("energie_reseau", "total"),
    gridDay: find("energie_reseau", "totalDay"),
    gridMonth: find("energie_reseau", "totalMonth"),
    solarActual: find("energie_solaire", "actual"),
    solarTotal: find("energie_solaire", "total"),
    solarDay: find("energie_solaire", "totalDay"),
    solarMonth: find("energie_solaire", "totalMonth"),
  };
}

async function renderKpis(sids) {
  const container = document.getElementById("energy-kpis");
  container.innerHTML = "";

  const [gridDay, gridMonth, solarDay, solarMonth] = await Promise.all([
    fetchLatest(sids.gridDay && sids.gridDay.series_id),
    fetchLatest(sids.gridMonth && sids.gridMonth.series_id),
    fetchLatest(sids.solarDay && sids.solarDay.series_id),
    fetchLatest(sids.solarMonth && sids.solarMonth.series_id),
  ]);

  const gridUnit = (sids.gridDay && sids.gridDay.unit) || "kWh";
  const solarUnit = (sids.solarDay && sids.solarDay.unit) || "kWh";

  if (gridDay !== null) container.appendChild(kpiTile("Réseau — aujourd'hui", fmtNumber(gridDay, 2), gridUnit, "kpi-grid"));
  if (solarDay !== null) container.appendChild(kpiTile("Solaire — aujourd'hui", fmtNumber(solarDay, 2), solarUnit, "kpi-solar"));
  if (gridMonth !== null) container.appendChild(kpiTile("Réseau — ce mois", fmtNumber(gridMonth, 1), gridUnit, "kpi-grid"));
  if (solarMonth !== null) container.appendChild(kpiTile("Solaire — ce mois", fmtNumber(solarMonth, 1), solarUnit, "kpi-solar"));

  if (gridDay !== null && solarDay !== null && gridDay + solarDay > 0) {
    const autonomy = (solarDay / (gridDay + solarDay)) * 100;
    container.appendChild(kpiTile("Autoconsommation — aujourd'hui", fmtNumber(autonomy, 0), "%", "kpi-auto"));
  }
}

async function renderPowerChart(sids) {
  const canvas = document.getElementById("energy-power-chart");
  const datasets = [];
  const specs = [
    { s: sids.gridActual, label: "Réseau (kW)", color: PALETTE_GRID },
    { s: sids.solarActual, label: "Solaire (kW)", color: PALETTE_SOLAR },
  ];
  let labels = null;
  for (const { s, label, color } of specs) {
    if (!s) continue;
    const data = await fetchJSON(`/api/series/${encodeURIComponent(s.series_id)}/data?range=${range}`);
    const pointLabels = data.points.map((p) => fmtDateTimeShort(p.ts));
    if (!labels || pointLabels.length > labels.length) labels = pointLabels;
    datasets.push({
      label,
      data: data.points.map((p) => p.value),
      borderColor: color,
      backgroundColor: color,
      pointRadius: 0,
      borderWidth: 2,
      tension: 0.15,
      spanGaps: true,
    });
  }
  if (powerChart) powerChart.destroy();
  if (!datasets.length) return;
  powerChart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels: labels || [], datasets },
    options: baseLineOptions(),
  });
}

async function renderDailyChart(gridSid, solarSid) {
  const [gridPoints, solarPoints] = await Promise.all([fetchDaily(gridSid, 30), fetchDaily(solarSid, 30)]);
  const dateMap = new Map();
  gridPoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {});
    dateMap.get(p.date_ts).grid = p.consumption;
  });
  solarPoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {});
    dateMap.get(p.date_ts).solar = p.consumption;
  });
  const sortedTs = Array.from(dateMap.keys()).sort((a, b) => a - b);
  const labels = sortedTs.map(fmtDateShort);
  const gridData = sortedTs.map((t) => (dateMap.get(t).grid !== undefined ? dateMap.get(t).grid : null));
  const solarData = sortedTs.map((t) => (dateMap.get(t).solar !== undefined ? dateMap.get(t).solar : null));

  const canvas = document.getElementById("energy-daily-chart");
  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Réseau (kWh)", data: gridData, backgroundColor: PALETTE_GRID },
        { label: "Solaire (kWh)", data: solarData, backgroundColor: PALETTE_SOLAR },
      ],
    },
    options: baseBarOptions(true),
  });
}

async function renderMonthlyChart(gridSid, solarSid) {
  const [gridPoints, solarPoints] = await Promise.all([fetchDaily(gridSid, 380), fetchDaily(solarSid, 380)]);
  const gridMonthly = aggregateMonthly(gridPoints);
  const solarMonthly = aggregateMonthly(solarPoints);
  const gridByKey = new Map(gridMonthly.map((m) => [m.key, m]));
  const solarByKey = new Map(solarMonthly.map((m) => [m.key, m]));
  const sortedKeys = Array.from(new Set([...gridByKey.keys(), ...solarByKey.keys()])).sort();
  const labels = sortedKeys.map((k) => fmtMonthShort((gridByKey.get(k) || solarByKey.get(k)).ts));
  const gridData = sortedKeys.map((k) => (gridByKey.has(k) ? gridByKey.get(k).sum : null));
  const solarData = sortedKeys.map((k) => (solarByKey.has(k) ? solarByKey.get(k).sum : null));

  const canvas = document.getElementById("energy-monthly-chart");
  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Réseau (kWh)", data: gridData, backgroundColor: PALETTE_GRID },
        { label: "Solaire (kWh)", data: solarData, backgroundColor: PALETTE_SOLAR },
      ],
    },
    options: baseBarOptions(true),
  });
}

async function refresh() {
  const select = document.getElementById("energy-zone-select");
  const zone = select.value;
  const sids = seriesFor(zone);

  const hasAny = sids.gridActual || sids.gridTotal || sids.solarActual || sids.solarTotal;
  const hint = document.getElementById("energy-empty-hint");
  const body = document.getElementById("energy-body");
  if (!hasAny) {
    hint.hidden = false;
    hint.textContent = "Aucune donnée Réseau/Solaire pour cette zone.";
    body.hidden = true;
    return;
  }
  hint.hidden = true;
  body.hidden = false;

  await renderKpis(sids);
  await renderPowerChart(sids);
  await renderDailyChart(sids.gridTotal && sids.gridTotal.series_id, sids.solarTotal && sids.solarTotal.series_id);
  await renderMonthlyChart(sids.gridTotal && sids.gridTotal.series_id, sids.solarTotal && sids.solarTotal.series_id);
}

function setupRangeButtons() {
  document.querySelectorAll("#energy-range-buttons .range-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#energy-range-buttons .range-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      range = btn.dataset.range;
      refresh();
    });
  });
}

export async function initEnergyTab() {
  const allSeries = await loadAllSeries();
  if (!zonesBuilt) {
    buildZoneOptions(allSeries);
    document.getElementById("energy-zone-select").addEventListener("change", refresh);
    setupRangeButtons();
    zonesBuilt = true;
  }
  refresh();
}
