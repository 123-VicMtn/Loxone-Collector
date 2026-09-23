/**
 * Fonctions de formatage (nombres, dates) partagées par tous les onglets.
 * Aucune dépendance au DOM ni à l'API -- facile à tester isolément.
 */

export function fmtNumber(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("fr-CH", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtDateShort(ts) {
  return new Date(ts * 1000).toLocaleDateString("fr-CH", { day: "2-digit", month: "2-digit" });
}

export function fmtMonthShort(ts) {
  return new Date(ts * 1000).toLocaleDateString("fr-CH", { month: "short", year: "2-digit" });
}

export function fmtDateTimeShort(ts) {
  return new Date(ts * 1000).toLocaleString("fr-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Formatage adaptatif utilisé par l'onglet Explorer : granularité fine
 * (heure+minute) sur les plages courtes, date seule (+heure sur 7j) au-delà. */
export function fmtRangeTs(ts, range) {
  const d = new Date(ts * 1000);
  if (range === "1h" || range === "24h") {
    return d.toLocaleString("fr-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  return (
    d.toLocaleDateString("fr-CH", { day: "2-digit", month: "2-digit", year: "2-digit" }) +
    (range === "7d" ? " " + d.toLocaleTimeString("fr-CH", { hour: "2-digit", minute: "2-digit" }) : "")
  );
}

/** Clé de regroupement mensuel (UTC), ex: "2026-08". */
export function monthKey(ts) {
  const d = new Date(ts * 1000);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function zoneLabel(apartment) {
  return apartment ? apartment : "Bâtiment (non affecté)";
}

const ZONE_KEY_SEP = "::";

/** Clé composite (site, appartement) utilisée comme valeur d'<option> dans
 * les sélecteurs de zone (Énergie / Consommations par zone). Nécessaire
 * dès qu'il y a plus d'un miniserver : deux sites peuvent chacun avoir un
 * "App 1", et `apartment` seul ne suffit plus à identifier une zone. */
export function zoneKey(s) {
  return `${s.miniserver || ""}${ZONE_KEY_SEP}${s.apartment || ""}`;
}

export function parseZoneKey(key) {
  const idx = (key || "").indexOf(ZONE_KEY_SEP);
  if (idx === -1) return { miniserver: key || "", apartment: "" };
  return { miniserver: key.slice(0, idx), apartment: key.slice(idx + ZONE_KEY_SEP.length) };
}

/** Un capteur appartient-il à la zone désignée par cette clé composite ? */
export function matchesZone(s, key) {
  const { miniserver, apartment } = parseZoneKey(key);
  return (s.miniserver || "") === miniserver && (s.apartment || "") === apartment;
}

export function resourceLabel(resourceType, labels) {
  if (!resourceType) return "Non classé";
  return (labels && labels[resourceType]) || resourceType;
}
