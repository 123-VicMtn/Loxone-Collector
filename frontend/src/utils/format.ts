/**
 * Formatages du décompte : montants en francs, kWh, taux en pourcentage.
 * Tous rendent "—" pour une valeur absente, jamais "0" : sur une facture,
 * un montant nul par manque de données ne doit pas se confondre avec un
 * montant nul réellement dû. Port direct de static/js/decompte/format.js
 * et static/js/core/format.js (fmtNumber).
 */

import type { Period } from '../types/decompte'

export function fmtNumber(v: number | null | undefined, digits: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('fr-CH', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtKwh(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return fmtNumber(v, digits)
}

export function fmtCHF(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('fr-CH', {
    style: 'currency',
    currency: 'CHF',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** `signe` à true pour un ÉCART (où "+3 %" et "-3 %" sont deux choses
 * différentes), à false pour un TAUX (où "+23 %" se lirait comme une
 * variation alors que c'est une part). */
export function fmtPct(v: number | null | undefined, digits = 1, signe = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${signe && v > 0 ? '+' : ''}${fmtNumber(v, digits)} %`
}

export function fmtDay(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString('fr-CH', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  })
}

/** Bornes affichées d'un mois : `end` est la borne EXCLUSIVE (minuit du
 * premier jour du mois suivant), donc le dernier jour couvert est la
 * veille -- c'est cette date-là qu'attend un propriétaire sur une facture. */
export function fmtPeriodBounds(period: Period | null): string {
  if (!period) return '—'
  return `${fmtDay(period.start)} → ${fmtDay(period.end - 1)}`
}
