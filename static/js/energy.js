(function () {
  "use strict";

  // Couleurs dédiées : orange = réseau (grid/import), vert = solaire
  // (production). Cohérent entre tous les graphs de l'onglet Énergie.
  const PALETTE_GRID = "#d97706";
  const PALETTE_SOLAR = "#16a34a";
  const PALETTE_GENERIC = "#2563eb";

  let allSeries = [];
  let seriesLoaded = false;

  async function loadAllSeries() {
    if (seriesLoaded) return allSeries;
    const res = await fetch("/api/series");
    allSeries = await res.json();
    seriesLoaded = true;
    return allSeries;
  }

  function findSeries(predicate) {
    return allSeries.find(predicate) || null;
  }

  function zoneLabel(apt) {
    return apt ? apt : "Bâtiment (non affecté)";
  }

  function resourceLabel(rtype) {
    const labels = window.RESOURCE_TYPE_LABELS || {};
    if (!rtype) return "Non classé";
    return labels[rtype] || rtype;
  }

  // ---------- Onglets ----------
  function setupTabs() {
    const buttons = document.querySelectorAll(".tab-btn");
    const panels = {
      explorer: document.getElementById("tab-explorer"),
      energie: document.getElementById("tab-energie"),
      zone: document.getElementById("tab-zone"),
    };
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => {
          b.classList.remove("active");
          b.setAttribute("aria-selected", "false");
        });
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        Object.entries(panels).forEach(([key, el]) => {
          el.hidden = key !== btn.dataset.tab;
        });
        if (btn.dataset.tab === "energie") initEnergyTab();
        if (btn.dataset.tab === "zone") initZoneTab();
      });
    });
  }

  // ---------- Helpers formatage / API ----------
  function fmtNumber(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return v.toLocaleString("fr-CH", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtDateShort(ts) {
    return new Date(ts * 1000).toLocaleDateString("fr-CH", { day: "2-digit", month: "2-digit" });
  }

  function fmtMonthShort(ts) {
    return new Date(ts * 1000).toLocaleDateString("fr-CH", { month: "short", year: "2-digit" });
  }

  function fmtDateTimeShort(ts) {
    return new Date(ts * 1000).toLocaleString("fr-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Erreur API ${url}: ${res.status}`);
    return res.json();
  }

  async function fetchLatest(seriesId) {
    if (!seriesId) return null;
    try {
      const data = await fetchJSON(`/api/series/${encodeURIComponent(seriesId)}/latest`);
      return data.value;
    } catch (err) {
      console.error(err);
      return null;
    }
  }

  async function fetchDaily(seriesId, days) {
    if (!seriesId) return [];
    try {
      const data = await fetchJSON(`/api/series/${encodeURIComponent(seriesId)}/daily?days=${days}`);
      return data.points;
    } catch (err) {
      console.error(err);
      return [];
    }
  }

  function monthKey(ts) {
    const d = new Date(ts * 1000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  }

  function aggregateMonthly(points) {
    // Regroupe des points journaliers {date_ts, consumption} par mois
    // calendaire (UTC), en sommant les consommations. Les deltas négatifs
    // (reset de compteur, remplacement) sont exclus de la somme pour ne pas
    // fausser le total mensuel -- ils restent visibles tels quels sur le
    // graph journalier, qui n'agrège rien.
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

  function kpiTile(label, value, unit, colorClass) {
    const div = document.createElement("div");
    div.className = `kpi-tile${colorClass ? " " + colorClass : ""}`;
    div.innerHTML =
      `<div class="kpi-label">${label}</div>` +
      `<div class="kpi-value">${value}${unit ? ` <span class="kpi-unit">${unit}</span>` : ""}</div>`;
    return div;
  }

  function baseLineOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 12, autoSkip: true } },
        y: { beginAtZero: true },
      },
      plugins: { legend: { position: "bottom" } },
    };
  }

  function baseBarOptions(showLegend) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { y: { beginAtZero: true } },
      plugins: { legend: { display: showLegend, position: "bottom" } },
    };
  }

  // ---------- Onglet "Énergie" (réseau vs solaire) ----------
  let energyPowerChart = null;
  let energyDailyChart = null;
  let energyMonthlyChart = null;
  let energyRange = "24h";
  let energyZonesBuilt = false;

  function buildEnergyZoneOptions() {
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

  function energySeriesFor(zone) {
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

  async function renderEnergyKpis(sids) {
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

  async function renderEnergyPowerChart(sids, range) {
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
    if (energyPowerChart) energyPowerChart.destroy();
    if (!datasets.length) return;
    energyPowerChart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: labels || [], datasets },
      options: baseLineOptions(),
    });
  }

  async function renderGridSolarDailyChart(gridSid, solarSid) {
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
    if (energyDailyChart) energyDailyChart.destroy();
    energyDailyChart = new Chart(canvas.getContext("2d"), {
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

  async function renderGridSolarMonthlyChart(gridSid, solarSid) {
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
    if (energyMonthlyChart) energyMonthlyChart.destroy();
    energyMonthlyChart = new Chart(canvas.getContext("2d"), {
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

  async function refreshEnergyTab() {
    const select = document.getElementById("energy-zone-select");
    const zone = select.value;
    const sids = energySeriesFor(zone);

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

    await renderEnergyKpis(sids);
    await renderEnergyPowerChart(sids, energyRange);
    await renderGridSolarDailyChart(sids.gridTotal && sids.gridTotal.series_id, sids.solarTotal && sids.solarTotal.series_id);
    await renderGridSolarMonthlyChart(sids.gridTotal && sids.gridTotal.series_id, sids.solarTotal && sids.solarTotal.series_id);
  }

  function setupEnergyRangeButtons() {
    document.querySelectorAll("#energy-range-buttons .range-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#energy-range-buttons .range-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        energyRange = btn.dataset.range;
        refreshEnergyTab();
      });
    });
  }

  async function initEnergyTab() {
    await loadAllSeries();
    if (!energyZonesBuilt) {
      buildEnergyZoneOptions();
      document.getElementById("energy-zone-select").addEventListener("change", refreshEnergyTab);
      setupEnergyRangeButtons();
      energyZonesBuilt = true;
    }
    refreshEnergyTab();
  }

  // ---------- Onglet "Consommations par zone" (générique, toute ressource) ----------
  let zoneDailyChart = null;
  let zoneMonthlyChart = null;
  let zoneOptionsBuilt = false;

  function buildZoneOptions() {
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

  function buildResourceOptions() {
    const zone = document.getElementById("zone-zone-select").value;
    const resourceSelect = document.getElementById("zone-resource-select");
    const types = new Set();
    allSeries.forEach((s) => {
      if ((s.apartment || "") === zone && s.state_name === "total") types.add(s.resource_type || "autre");
    });
    const sorted = Array.from(types).sort((a, b) => resourceLabel(a).localeCompare(resourceLabel(b)));
    resourceSelect.innerHTML = "";
    sorted.forEach((rtype) => {
      const opt = document.createElement("option");
      opt.value = rtype;
      opt.textContent = resourceLabel(rtype);
      resourceSelect.appendChild(opt);
    });
  }

  async function renderZoneKpis(zone, rtype, totalSeriesId, unit) {
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

  async function renderSingleDailyBarChart(seriesId, unit) {
    const points = await fetchDaily(seriesId, 30);
    const labels = points.map((p) => fmtDateShort(p.date_ts));
    const data = points.map((p) => p.consumption);
    const canvas = document.getElementById("zone-daily-chart");
    if (zoneDailyChart) zoneDailyChart.destroy();
    zoneDailyChart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label: `Consommation (${unit || "unité"})`, data, backgroundColor: PALETTE_GENERIC }] },
      options: baseBarOptions(false),
    });
  }

  async function renderSingleMonthlyBarChart(seriesId, unit) {
    const points = await fetchDaily(seriesId, 380);
    const monthly = aggregateMonthly(points);
    const labels = monthly.map((m) => fmtMonthShort(m.ts));
    const data = monthly.map((m) => m.sum);
    const canvas = document.getElementById("zone-monthly-chart");
    if (zoneMonthlyChart) zoneMonthlyChart.destroy();
    zoneMonthlyChart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label: `Consommation (${unit || "unité"})`, data, backgroundColor: PALETTE_GENERIC }] },
      options: baseBarOptions(false),
    });
  }

  async function refreshZoneTab() {
    const zone = document.getElementById("zone-zone-select").value;
    const rtype = document.getElementById("zone-resource-select").value;

    const hint = document.getElementById("zone-empty-hint");
    const body = document.getElementById("zone-body");

    const totalSeries = findSeries((s) => (s.apartment || "") === zone && s.resource_type === rtype && s.state_name === "total");
    if (!totalSeries) {
      hint.hidden = false;
      hint.textContent = "Aucune série cumulative (\"total\") pour cette combinaison zone/ressource.";
      body.hidden = true;
      return;
    }
    hint.hidden = true;
    body.hidden = false;

    const unit = totalSeries.unit || "";
    await renderZoneKpis(zone, rtype, totalSeries.series_id, unit);
    await renderSingleDailyBarChart(totalSeries.series_id, unit);
    await renderSingleMonthlyBarChart(totalSeries.series_id, unit);
  }

  async function initZoneTab() {
    await loadAllSeries();
    if (!zoneOptionsBuilt) {
      buildZoneOptions();
      buildResourceOptions();
      document.getElementById("zone-zone-select").addEventListener("change", () => {
        buildResourceOptions();
        refreshZoneTab();
      });
      document.getElementById("zone-resource-select").addEventListener("change", refreshZoneTab);
      zoneOptionsBuilt = true;
    }
    refreshZoneTab();
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupTabs();
  });
})();
