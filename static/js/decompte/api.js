/**
 * Accès aux endpoints du décompte de charges. Même principe que
 * core/api.js pour le dashboard : aucun `fetch()` ailleurs dans les modules
 * de la page, un seul endroit à corriger si l'API change.
 */

import { fetchJSON } from "../core/api.js";

export async function fetchDecompte({ from, to } = {}) {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return fetchJSON(`/api/decompte${qs ? "?" + qs : ""}`);
}

export async function fetchTarifs() {
  return fetchJSON("/api/tarifs");
}

/** Crée ou remplace le tarif prenant effet à `valid_from`. Retourne la
 * liste complète des tarifs telle que le serveur la voit après écriture. */
export async function saveTarif(tarif) {
  const res = await fetch("/api/tarifs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(tarif),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTarif(id) {
  const res = await fetch(`/api/tarifs/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
