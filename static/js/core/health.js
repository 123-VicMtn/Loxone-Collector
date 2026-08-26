/**
 * Bandeau de statut en bas de page (dernier poll de chaque miniserver).
 * Indépendant des onglets -- affiché en permanence, se rafraîchit tout
 * seul toutes les 30s.
 */

import { fetchHealth } from "./api.js";

const REFRESH_MS = 30000;

async function refreshHealth() {
  const footer = document.getElementById("health-footer");
  if (!footer) return;
  try {
    const h = await fetchHealth();
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

export function initHealthFooter() {
  refreshHealth();
  setInterval(refreshHealth, REFRESH_MS);
}
