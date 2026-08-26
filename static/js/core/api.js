/**
 * Accès à l'API JSON du serveur (endpoints /api/series/...) + un petit
 * cache mémoire pour /api/series (la liste complète des capteurs), lue
 * par tous les onglets mais rafraîchie une seule fois par chargement de
 * page. Aucun onglet ne doit faire de `fetch()` directement en dehors de
 * ce module : ça garde un seul endroit à modifier si l'API change.
 */

let allSeries = [];
let seriesLoaded = false;

export async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Erreur API ${url}: ${res.status}`);
  return res.json();
}

/** Charge (une seule fois, puis depuis le cache) la liste de tous les
 * capteurs connus, telle que renvoyée par GET /api/series. */
export async function loadAllSeries() {
  if (seriesLoaded) return allSeries;
  allSeries = await fetchJSON("/api/series");
  seriesLoaded = true;
  return allSeries;
}

/** Premier capteur du cache vérifiant `predicate`, ou null. Suppose que
 * loadAllSeries() a déjà été appelé (throw silencieux -> null sinon, le
 * cache est alors simplement vide). */
export function findSeries(predicate) {
  return allSeries.find(predicate) || null;
}

export async function fetchSeriesData(seriesId, range) {
  return fetchJSON(`/api/series/${encodeURIComponent(seriesId)}/data?range=${encodeURIComponent(range)}`);
}

/** Dernière valeur connue d'une série (peu importe son âge). Utilisé pour
 * les tuiles KPI (totalDay/Week/Month/Year : compteurs vivants sans
 * historique propre, voir db.query_daily_last côté serveur). */
export async function fetchLatest(seriesId) {
  if (!seriesId) return null;
  try {
    const data = await fetchJSON(`/api/series/${encodeURIComponent(seriesId)}/latest`);
    return data.value;
  } catch (err) {
    console.error(err);
    return null;
  }
}

/** Relevés de fin de journée + consommation dérivée (delta entre deux
 * relevés successifs) pour une série cumulative ("total"). */
export async function fetchDaily(seriesId, days) {
  if (!seriesId) return [];
  try {
    const data = await fetchJSON(`/api/series/${encodeURIComponent(seriesId)}/daily?days=${days}`);
    return data.points;
  } catch (err) {
    console.error(err);
    return [];
  }
}

export async function fetchHealth() {
  const res = await fetch("/health");
  return res.json();
}
