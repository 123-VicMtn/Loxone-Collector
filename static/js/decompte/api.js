/**
 * Accès aux endpoints du décompte de charges. Même principe que
 * core/api.js pour le dashboard : aucun `fetch()` ailleurs dans les modules
 * de la page, un seul endroit à corriger si l'API change.
 */

import { fetchJSON } from "../core/api.js";

/** Sites configurés (config.yaml) -- alimente le sélecteur de site qui
 * scope tout le reste de la page (voir main.js). */
export async function fetchMiniservers() {
  return fetchJSON("/api/miniservers");
}

export async function fetchDecompte({ miniserver, from, to } = {}) {
  const params = new URLSearchParams();
  if (miniserver) params.set("miniserver", miniserver);
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const qs = params.toString();
  return fetchJSON(`/api/decompte${qs ? "?" + qs : ""}`);
}

export async function fetchTarifs(miniserver) {
  const params = new URLSearchParams();
  if (miniserver) params.set("miniserver", miniserver);
  const qs = params.toString();
  return fetchJSON(`/api/tarifs${qs ? "?" + qs : ""}`);
}

/** Crée ou remplace le tarif d'un site prenant effet à `valid_from`.
 * Retourne la liste des tarifs de CE site telle que le serveur la voit
 * après écriture -- `tarif.miniserver` doit être renseigné. */
export async function saveTarif(tarif) {
  const res = await fetch("/api/tarifs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(tarif),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTarif(id, miniserver) {
  const params = new URLSearchParams();
  if (miniserver) params.set("miniserver", miniserver);
  const qs = params.toString();
  const res = await fetch(`/api/tarifs/${id}${qs ? "?" + qs : ""}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
