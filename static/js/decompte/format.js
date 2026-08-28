/**
 * Formatages propres au décompte : montants en francs, kWh, écarts en
 * pourcentage. Tous rendent "—" pour une valeur absente, jamais "0" : sur
 * une facture, un montant nul par manque de données ne doit pas se
 * confondre avec un montant nul réellement dû.
 */

import { fmtNumber } from "../core/format.js";

export function fmtKwh(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return fmtNumber(v, digits);
}

export function fmtCHF(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("fr-CH", {
    style: "currency",
    currency: "CHF",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** `signe` à true pour un ÉCART (où "+3 %" et "-3 %" sont deux choses
 * différentes), à false pour un TAUX (où "+23 %" se lirait comme une
 * variation alors que c'est une part). */
export function fmtPct(v, digits = 1, signe = true) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${signe && v > 0 ? "+" : ""}${fmtNumber(v, digits)} %`;
}

export function fmtDay(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("fr-CH", {
    day: "2-digit", month: "2-digit", year: "numeric",
  });
}

/** Bornes affichées d'un mois : `end` est la borne EXCLUSIVE (minuit du
 * premier jour du mois suivant), donc le dernier jour couvert est la
 * veille -- c'est cette date-là qu'attend un propriétaire sur une facture. */
export function fmtPeriodBounds(period) {
  return `${fmtDay(period.start)} → ${fmtDay(period.end - 1)}`;
}
