import type { ParametreCatalogue, ValeurParametre } from '../types/parametres'

/** '2026-11-01' → '01.11.2026'. */
export function fmtJour(jour: string | null | undefined): string {
  if (!jour) return '—'
  const [a, m, j] = jour.split('-')
  return `${j}.${m}.${a}`
}

export function fmtNombre(v: number, maxDecimales = 4): string {
  return v.toLocaleString('fr-CH', { maximumFractionDigits: maxDecimales })
}

/** Montant en francs, toujours deux décimales : « 45,50 ». */
export function fmtFrancs(v: number): string {
  return v.toLocaleString('fr-CH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Valeur d'un paramètre avec son unité : « 0,25 CHF/kWh HT », « 80 % »,
 * « oui », « Aucune » (location retirée). */
export function fmtValeur(param: ParametreCatalogue, v: ValeurParametre): string {
  if (v === null) return param.facultatif ? 'Aucune' : '—'
  if (typeof v === 'boolean') return v ? 'oui' : 'non'
  return param.unite ? `${fmtNombre(v)} ${param.unite}` : fmtNombre(v)
}
