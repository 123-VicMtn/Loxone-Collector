/**
 * Onglet "Énergie" : réseau (import/export) vs solaire vs batterie, par
 * zone. Chaque groupe de tuiles jour/semaine/mois/année lit directement
 * les states totalX/totalNegX du Miniserver (déjà recalculés côté Loxone,
 * jamais redérivés côté dashboard) ; seuls les graphs journaliers/mensuels
 * (historique passé, non disponible nativement au-delà d'aujourd'hui)
 * utilisent un delta jour-sur-jour du compteur cumulatif ("total"/"totalNeg").
 * Voir CLAUDE.md, section "Dashboard énergie", pour le détail du modèle
 * (mesuré / recalculé par le Miniserver / calculé par nous).
 */

import { loadAllSeries, findSeries, fetchJSON, fetchLatest, fetchDaily } from "../core/api.js";
import { fmtNumber, fmtDateShort, fmtMonthShort, fmtDateTimeShort, zoneLabel } from "../core/format.js";
import {
  PALETTE_GRID, PALETTE_SOLAR, PALETTE_BATTERY,
  baseLineOptions, baseBarOptions, kpiTile, noteEl, aggregateMonthly,
} from "../core/charts.js";

let powerChart = null;
let dailyChart = null;
let monthlyChart = null;
let batteryChart = null;
let range = "24h";
let zonesBuilt = false;

function buildZoneOptions(allSeries) {
  const select = document.getElementById("energy-zone-select");
  const zones = new Set();
  allSeries.forEach((s) => {
    if (["energie_reseau", "energie_solaire", "energie_batterie", "energie_flux"].includes(s.resource_type)) {
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
    gridWeek: find("energie_reseau", "totalWeek"),
    gridMonth: find("energie_reseau", "totalMonth"),
    gridYear: find("energie_reseau", "totalYear"),
    gridNegTotal: find("energie_reseau", "totalNeg"),
    gridNegDay: find("energie_reseau", "totalNegDay"),
    gridNegWeek: find("energie_reseau", "totalNegWeek"),
    gridNegMonth: find("energie_reseau", "totalNegMonth"),
    gridNegYear: find("energie_reseau", "totalNegYear"),

    solarActual: find("energie_solaire", "actual"),
    solarTotal: find("energie_solaire", "total"),
    solarDay: find("energie_solaire", "totalDay"),
    solarWeek: find("energie_solaire", "totalWeek"),
    solarMonth: find("energie_solaire", "totalMonth"),
    solarYear: find("energie_solaire", "totalYear"),

    batteryActual: find("energie_batterie", "actual"),
    batteryTotal: find("energie_batterie", "total"),
    batteryNegTotal: find("energie_batterie", "totalNeg"),
    batteryDay: find("energie_batterie", "totalDay"),
    batteryNegDay: find("energie_batterie", "totalNegDay"),
    batteryWeek: find("energie_batterie", "totalWeek"),
    batteryNegWeek: find("energie_batterie", "totalNegWeek"),
    batteryMonth: find("energie_batterie", "totalMonth"),
    batteryNegMonth: find("energie_batterie", "totalNegMonth"),
    batteryYear: find("energie_batterie", "totalYear"),
    batteryNegYear: find("energie_batterie", "totalNegYear"),
    batteryStorage: find("energie_batterie", "storage"),

    efmGpwr: find("energie_flux", "Gpwr"),
    efmPpwr: find("energie_flux", "Ppwr"),
    efmSpwr: find("energie_flux", "Spwr"),
    efmSelfConsumption: find("energie_flux", "selfConsumption"),
  };
}

/** Rend un groupe de tuiles jour/semaine/mois/année pour un compteur donné,
 * avec repli sur le relevé cumulatif brut si aucun des 4 n'existe. Toutes
 * ces valeurs sont lues via /latest (db.query_latest) : ce sont des
 * compteurs vivants recalculés par le Miniserver, jamais un delta calculé
 * ici. Retourne { any, dayV } pour que l'appelant réutilise dayV (ex: pour
 * l'autoconsommation) sans refaire l'appel réseau. */
async function renderPeriodGroup(container, labelPrefix, colorClass, s) {
  const [dayV, weekV, monthV, yearV, totalV] = await Promise.all([
    fetchLatest(s.day && s.day.series_id),
    fetchLatest(s.week && s.week.series_id),
    fetchLatest(s.month && s.month.series_id),
    fetchLatest(s.year && s.year.series_id),
    fetchLatest(s.total && s.total.series_id),
  ]);
  const unit = (s.day && s.day.unit) || (s.total && s.total.unit) || "kWh";
  let any = false;
  if (dayV !== null) { container.appendChild(kpiTile(`${labelPrefix} — Aujourd'hui`, fmtNumber(dayV, 2), unit, colorClass)); any = true; }
  if (weekV !== null) { container.appendChild(kpiTile(`${labelPrefix} — Cette semaine`, fmtNumber(weekV, 2), unit, colorClass)); any = true; }
  if (monthV !== null) { container.appendChild(kpiTile(`${labelPrefix} — Ce mois`, fmtNumber(monthV, 1), unit, colorClass)); any = true; }
  if (yearV !== null) { container.appendChild(kpiTile(`${labelPrefix} — Cette année`, fmtNumber(yearV, 1), unit, colorClass)); any = true; }
  if (!any && totalV !== null) { container.appendChild(kpiTile(`${labelPrefix} — Relevé actuel`, fmtNumber(totalV, 2), unit, colorClass)); any = true; }
  return { any, dayV };
}

async function renderKpis(sids) {
  const container = document.getElementById("energy-kpis");
  const note = document.getElementById("energy-kpis-note");
  container.innerHTML = "";
  note.innerHTML = "";

  const grid = await renderPeriodGroup(container, "Réseau (import)", "kpi-grid", {
    day: sids.gridDay, week: sids.gridWeek, month: sids.gridMonth, year: sids.gridYear, total: sids.gridTotal,
  });
  const gridExport = await renderPeriodGroup(container, "Réseau (export)", "kpi-grid", {
    day: sids.gridNegDay, week: sids.gridNegWeek, month: sids.gridNegMonth, year: sids.gridNegYear, total: sids.gridNegTotal,
  });
  const solar = await renderPeriodGroup(container, "Solaire", "kpi-solar", {
    day: sids.solarDay, week: sids.solarWeek, month: sids.solarMonth, year: sids.solarYear, total: sids.solarTotal,
  });

  if (grid.any || gridExport.any || solar.any) {
    note.appendChild(noteEl(
      "Valeurs recalculées et remises à zéro par le Miniserver Loxone lui-même (states totalDay/Week/Month/Year et totalNegDay/Week/Month/Year) -- pas un delta calculé côté dashboard."
    ));
  }

  return { gridDayV: grid.dayV, gridNegDayV: gridExport.dayV, solarDayV: solar.dayV };
}

/** Autoconsommation : Scd = Pd - Ed (production du jour moins export du
 * jour), formule documentée par Loxone. Repli sur un taux de "couverture
 * solaire" (approximatif, clairement étiqueté comme tel) si l'export
 * réseau (totalNeg) n'est pas disponible pour cette zone. Ajoute aussi,
 * à titre indicatif seulement, la valeur brute exposée par le bloc EFM
 * Loxone (state selfConsumption) dont la sémantique exacte n'est pas
 * confirmée -- voir CLAUDE.md. */
async function renderAutoconso(sids, kpiValues) {
  const wrap = document.getElementById("energy-autoconso-wrap");
  const container = document.getElementById("energy-autoconso-kpis");
  const note = document.getElementById("energy-autoconso-note");
  container.innerHTML = "";
  note.innerHTML = "";

  const { gridDayV, gridNegDayV, solarDayV } = kpiValues;
  let shown = false;

  if (solarDayV !== null && solarDayV > 0 && gridNegDayV !== null) {
    const selfConsumed = Math.max(0, solarDayV - gridNegDayV);
    const pct = Math.min(100, (selfConsumed / solarDayV) * 100);
    container.appendChild(kpiTile("Autoconsommation — aujourd'hui", fmtNumber(pct, 0), "%", "kpi-auto"));
    note.appendChild(noteEl(
      "Scd = Production du jour − Export du jour (formule Loxone officielle : autoconsommation = énergie consommée depuis une source propre, pas depuis le réseau)."
    ));
    shown = true;
  } else if (gridDayV !== null && solarDayV !== null && gridDayV + solarDayV > 0) {
    const pct = (solarDayV / (gridDayV + solarDayV)) * 100;
    container.appendChild(kpiTile("Couverture solaire (estimation) — aujourd'hui", fmtNumber(pct, 0), "%", "kpi-auto"));
    note.appendChild(noteEl(
      "Estimation approximative (part du solaire dans import + production) : l'export réseau n'est pas disponible pour cette zone, la vraie autoconsommation Loxone (production − export) ne peut pas être calculée ici."
    ));
    shown = true;
  }

  const efmRaw = await fetchLatest(sids.efmSelfConsumption && sids.efmSelfConsumption.series_id);
  if (efmRaw !== null) {
    container.appendChild(kpiTile("Autoconsommation (brute Loxone)", fmtNumber(efmRaw, 1), "", "kpi-battery"));
    note.appendChild(noteEl(
      "Valeur exposée directement par le bloc \"Moniteur de flux d'énergie\" Loxone (state selfConsumption) -- échelle et unité non confirmées, affichée à titre indicatif seulement."
    ));
    shown = true;
  }

  wrap.hidden = !shown;
}

/** Panneau Batterie : n'affiche les tuiles que si une vraie activité est
 * mesurée (évite d'afficher des zéros trompeurs pour une batterie pas
 * encore commissionnée). Charge = state "total"/totalX, décharge =
 * "totalNeg"/totalNegX, même logique bidirectionnelle que le compteur
 * Réseau. */
async function renderBattery(sids) {
  const wrap = document.getElementById("energy-battery-wrap");
  const hasSeries = sids.batteryActual || sids.batteryTotal || sids.batteryNegTotal || sids.batteryStorage;
  if (!hasSeries) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;

  const [actualV, totalV, negTotalV] = await Promise.all([
    fetchLatest(sids.batteryActual && sids.batteryActual.series_id),
    fetchLatest(sids.batteryTotal && sids.batteryTotal.series_id),
    fetchLatest(sids.batteryNegTotal && sids.batteryNegTotal.series_id),
  ]);
  const hasActivity = [actualV, totalV, negTotalV].some((v) => v !== null && v !== 0);

  const hint = document.getElementById("energy-battery-hint");
  const body = document.getElementById("energy-battery-body");
  if (!hasActivity) {
    hint.hidden = false;
    hint.textContent = "Compteur batterie détecté mais aucune activité mesurée pour l'instant (probablement pas encore commissionnée).";
    body.hidden = true;
    return;
  }
  hint.hidden = true;
  body.hidden = false;

  const container = document.getElementById("energy-battery-kpis");
  const note = document.getElementById("energy-battery-note");
  container.innerHTML = "";
  note.innerHTML = "";

  await renderPeriodGroup(container, "Charge", "kpi-battery", {
    day: sids.batteryDay, week: sids.batteryWeek, month: sids.batteryMonth, year: sids.batteryYear, total: sids.batteryTotal,
  });
  await renderPeriodGroup(container, "Décharge", "kpi-battery", {
    day: sids.batteryNegDay, week: sids.batteryNegWeek, month: sids.batteryNegMonth, year: sids.batteryNegYear, total: sids.batteryNegTotal,
  });
  if (sids.batteryStorage) {
    const storageV = await fetchLatest(sids.batteryStorage.series_id);
    if (storageV !== null) {
      container.appendChild(kpiTile("État de charge", fmtNumber(storageV, 0), sids.batteryStorage.unit || "", "kpi-battery"));
    }
  }
  note.appendChild(noteEl(
    "Charge/décharge recalculées par le Miniserver (jour/semaine/mois/année) à partir du compteur bidirectionnel de la batterie -- même logique que Réseau import/export."
  ));

  await renderBatteryChart(sids);
}

async function renderBatteryChart(sids) {
  const canvas = document.getElementById("energy-battery-chart");
  const chargeSid = sids.batteryTotal && sids.batteryTotal.series_id;
  const dischargeSid = sids.batteryNegTotal && sids.batteryNegTotal.series_id;
  const [chargePoints, dischargePoints] = await Promise.all([fetchDaily(chargeSid, 30), fetchDaily(dischargeSid, 30)]);
  const dateMap = new Map();
  chargePoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {});
    dateMap.get(p.date_ts).charge = p.consumption;
  });
  dischargePoints.forEach((p) => {
    if (!dateMap.has(p.date_ts)) dateMap.set(p.date_ts, {});
    dateMap.get(p.date_ts).discharge = p.consumption;
  });
  const sortedTs = Array.from(dateMap.keys()).sort((a, b) => a - b);
  const labels = sortedTs.map(fmtDateShort);
  const chargeData = sortedTs.map((t) => (dateMap.get(t).charge !== undefined ? dateMap.get(t).charge : null));
  const dischargeData = sortedTs.map((t) => (dateMap.get(t).discharge !== undefined ? -dateMap.get(t).discharge : null));

  if (batteryChart) batteryChart.destroy();
  batteryChart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Charge (kWh)", data: chargeData, backgroundColor: PALETTE_BATTERY },
        { label: "Décharge (kWh)", data: dischargeData, backgroundColor: PALETTE_GRID },
      ],
    },
    options: baseBarOptions(true),
  });
}

async function renderPowerChart(sids) {
  const canvas = document.getElementById("energy-power-chart");
  const datasets = [];
  // Si un bloc EFM ("Moniteur de flux d'énergie") existe pour la zone, ses
  // states Gpwr/Ppwr/Spwr sont déjà signés (import/export, charge/décharge)
  // et cohérents avec la doc Loxone -- préférés aux `actual` des compteurs
  // Meter séparés, qui ne portent qu'une grandeur non signée par compteur.
  const useEfm = sids.efmGpwr || sids.efmPpwr || sids.efmSpwr;
  const specs = useEfm
    ? [
        { s: sids.efmGpwr, label: "Réseau — Gpwr (kW)", color: PALETTE_GRID },
        { s: sids.efmPpwr, label: "Solaire — Ppwr (kW)", color: PALETTE_SOLAR },
        { s: sids.efmSpwr, label: "Batterie — Spwr (kW)", color: PALETTE_BATTERY },
      ]
    : [
        { s: sids.gridActual, label: "Réseau (kW)", color: PALETTE_GRID },
        { s: sids.solarActual, label: "Solaire (kW)", color: PALETTE_SOLAR },
        { s: sids.batteryActual, label: "Batterie (kW)", color: PALETTE_BATTERY },
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
        { label: "Réseau — import (kWh)", data: gridData, backgroundColor: PALETTE_GRID },
        { label: "Solaire (kWh)", data: solarData, backgroundColor: PALETTE_SOLAR },
      ],
    },
    options: baseBarOptions(true),
  });

  const note = document.getElementById("energy-daily-note");
  note.innerHTML = "";
  note.appendChild(noteEl(
    "Calculé ici à partir de deux relevés du compteur cumulatif (\"total\"), comme un décompte de charges -- le Miniserver ne fournit nativement que le cumul du jour en cours, pas d'historique journalier au-delà."
  ));
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
        { label: "Réseau — import (kWh)", data: gridData, backgroundColor: PALETTE_GRID },
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

  const hasAny = sids.gridActual || sids.gridTotal || sids.solarActual || sids.solarTotal ||
    sids.batteryActual || sids.batteryTotal || sids.efmGpwr || sids.efmPpwr;
  const hint = document.getElementById("energy-empty-hint");
  const body = document.getElementById("energy-body");
  if (!hasAny) {
    hint.hidden = false;
    hint.textContent = "Aucune donnée Réseau/Solaire/Batterie pour cette zone.";
    body.hidden = true;
    return;
  }
  hint.hidden = true;
  body.hidden = false;

  const kpiValues = await renderKpis(sids);
  await renderAutoconso(sids, kpiValues);
  await renderBattery(sids);
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
