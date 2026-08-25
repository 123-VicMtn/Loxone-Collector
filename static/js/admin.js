(function () {
  "use strict";

  async function postClassification(seriesId, body) {
    const res = await fetch(`/api/series/${encodeURIComponent(seriesId)}/classify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  }

  function flashStatus(row, text, ms) {
    const status = row.querySelector(".save-status");
    status.textContent = text;
    if (ms) {
      setTimeout(() => {
        if (status.textContent === text) status.textContent = "";
      }, ms);
    }
  }

  document.querySelectorAll(".save-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr");
      const seriesId = row.dataset.seriesId;
      const apartment = row.querySelector(".apartment-input").value.trim();
      const resourceType = row.querySelector(".resource-type-select").value;

      flashStatus(row, "…", 0);
      const ok = await postClassification(seriesId, {
        apartment: apartment,
        resource_type: resourceType,
      });
      flashStatus(row, ok ? "✓ enregistré" : "✗ erreur", 3000);
    });
  });

  document.querySelectorAll(".reset-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr");
      const seriesId = row.dataset.seriesId;

      flashStatus(row, "…", 0);
      const ok = await postClassification(seriesId, { reset: true });
      flashStatus(row, ok ? "✓ réinitialisé (recalculé au prochain poll)" : "✗ erreur", 4000);
    });
  });
})();
