(function () {
  "use strict";

  const PALETTE = [
    "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
    "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4338ca",
  ];

  let currentRange = "24h";
  let chart = null;
  const selected = new Map(); // series_id -> {label}

  function fmtTs(ts, range) {
    const d = new Date(ts * 1000);
    if (range === "1h" || range === "24h") {
      return d.toLocaleString("fr-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("fr-CH", { day: "2-digit", month: "2-digit", year: "2-digit" }) +
      (range === "7d" ? " " + d.toLocaleTimeString("fr-CH", { hour: "2-digit", minute: "2-digit" }) : "");
  }

  async function fetchSeriesData(seriesId, range) {
    const res = await fetch(`/api/series/${encodeURIComponent(seriesId)}/data?range=${encodeURIComponent(range)}`);
    if (!res.ok) throw new Error(`Erreur API pour ${seriesId}: ${res.status}`);
    return res.json();
  }

  async function refreshChart() {
    const canvas = document.getElementById("chart");
    const hint = document.getElementById("chart-hint");

    if (selected.size === 0) {
      hint.style.display = "block";
      hint.textContent = "Coche un ou plusieurs capteurs à gauche pour afficher leur historique.";
      if (chart) { chart.destroy(); chart = null; }
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
      const labels = data.points.map((p) => fmtTs(p.ts, currentRange));
      if (!labelSet) labelSet = labels;

      const color = PALETTE[colorIdx % PALETTE.length];
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
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 12, autoSkip: true } },
          y: { beginAtZero: false },
        },
        plugins: {
          legend: { position: "bottom" },
        },
      },
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
    document.querySelectorAll(".range-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".range-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentRange = btn.dataset.range;
        refreshChart();
      });
    });
  }

  function setupClearButton() {
    document.getElementById("clear-selection").addEventListener("click", () => {
      selected.clear();
      document.querySelectorAll(".series-checkbox").forEach((cb) => (cb.checked = false));
      refreshChart();
    });
  }

  async function refreshHealth() {
    const footer = document.getElementById("health-footer");
    try {
      const res = await fetch("/health");
      const h = await res.json();
      const lines = Object.keys(h.last_poll_ts || {}).map((name) => {
        const ts = h.last_poll_ts[name];
        const ok = h.last_poll_ok[name];
        const err = h.last_error[name];
        const when = ts ? new Date(ts * 1000).toLocaleString("fr-CH") : "jamais";
        const count = h.series_count[name] || 0;
        return `${name}: ${ok ? "OK" : "ERREUR"} — dernier poll ${when} — ${count} capteurs${err ? " — " + err : ""}`;
      });
      footer.textContent = lines.join("\n") || "En attente du premier cycle de poll…";
    } catch (err) {
      footer.textContent = "Statut indisponible.";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupCheckboxes();
    setupRangeButtons();
    setupClearButton();
    refreshHealth();
    setInterval(refreshHealth, 30000);
  });
})();
