/**
 * Formatages et petits utilitaires partagés par toutes les pages Vue
 * (decompte, dashboard, admin). Port direct de static/js/core/format.js --
 * aucune dépendance au DOM ni à l'API, facile à tester isolément.
 */

export function fmtNumber(v: number | null | undefined, digits: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('fr-CH', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtDateShort(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('fr-CH', { day: '2-digit', month: '2-digit' })
}

export function fmtMonthShort(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('fr-CH', { month: 'short', year: '2-digit' })
}

export function fmtDateTimeShort(ts: number): string {
  return new Date(ts * 1000).toLocaleString('fr-CH', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** Formatage adaptatif utilisé par l'onglet Explorer : granularité fine
 * (heure+minute) sur les plages courtes, date seule (+heure sur 7j) au-delà. */
export function fmtRangeTs(ts: number, range: string): string {
  const d = new Date(ts * 1000)
  if (range === '1h' || range === '24h') {
    return d.toLocaleString('fr-CH', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  }
  return (
    d.toLocaleDateString('fr-CH', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
    (range === '7d' ? ' ' + d.toLocaleTimeString('fr-CH', { hour: '2-digit', minute: '2-digit' }) : '')
  )
}

/** Clé de regroupement mensuel (UTC), ex: "2026-08". */
export function monthKey(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

export function zoneLabel(apartment: string | null | undefined): string {
  return apartment ? apartment : 'Bâtiment (non affecté)'
}

const ZONE_KEY_SEP = '::'

export interface ZoneLike {
  miniserver?: string | null
  apartment?: string | null
}

/** Clé composite (site, appartement) utilisée comme valeur d'<option> dans
 * les sélecteurs de zone (Énergie / Consommations par zone). Nécessaire dès
 * qu'il y a plus d'un miniserver : deux sites peuvent chacun avoir un
 * "App 1", et `apartment` seul ne suffit plus à identifier une zone. */
export function zoneKey(s: ZoneLike): string {
  return `${s.miniserver || ''}${ZONE_KEY_SEP}${s.apartment || ''}`
}

export function parseZoneKey(key: string): { miniserver: string; apartment: string } {
  const idx = (key || '').indexOf(ZONE_KEY_SEP)
  if (idx === -1) return { miniserver: key || '', apartment: '' }
  return { miniserver: key.slice(0, idx), apartment: key.slice(idx + ZONE_KEY_SEP.length) }
}

/** Un capteur appartient-il à la zone désignée par cette clé composite ? */
export function matchesZone(s: ZoneLike, key: string): boolean {
  const { miniserver, apartment } = parseZoneKey(key)
  return (s.miniserver || '') === miniserver && (s.apartment || '') === apartment
}

export function resourceLabel(resourceType: string | null | undefined, labels: Record<string, string>): string {
  if (!resourceType) return 'Non classé'
  return (labels && labels[resourceType]) || resourceType
}

/** Tri "naturel" par numéro d'appartement (App 2 avant App 10) -- port de
 * app.py::_apartment_sort_key / static/js/sidebar.js::apartmentSortKey. */
export function apartmentSortKey(name: string): [number, number | string] {
  const m = name.match(/\d+/)
  return m ? [0, parseInt(m[0], 10)] : [1, name]
}

export function compareApartments(a: string, b: string): number {
  const [orderA, valueA] = apartmentSortKey(a)
  const [orderB, valueB] = apartmentSortKey(b)
  if (orderA !== orderB) return orderA - orderB
  if (typeof valueA === 'number' && typeof valueB === 'number') return valueA - valueB
  return String(valueA).localeCompare(String(valueB))
}
