/**
 * Présentation des relevés : regrouper total / totalNeg, libellés,
 * famille lisible déduite du nom du compteur. Aucune donnée nouvelle :
 * tout vient du payload /api/releves.
 */

export interface Releve {
  series_id: string
  miniserver: string
  label: string
  apartment: string | null
  unit: string
  state_name: string
  ts: number | null
  value: number | null
}

export type MeterFamily = 'electricite' | 'eau' | 'chaleur' | 'autre'

export const FAMILY_ORDER: MeterFamily[] = ['electricite', 'eau', 'chaleur', 'autre']

export const FAMILY_LABELS: Record<MeterFamily, string> = {
  electricite: 'Électricité',
  eau: 'Eau',
  chaleur: 'Chaleur',
  autre: 'Autre',
}

/** Au-delà, le dernier point n'est plus celui du poll en cours. */
export const STALE_AFTER_SEC = 6 * 3600

const STATE_SUFFIX = / \((total|totalNeg)\)$/
const TZ = 'Europe/Zurich'

export interface ReadingSide {
  seriesId: string
  value: number | null
  ts: number | null
}

export interface MeterRow {
  key: string
  apartment: string
  name: string
  family: MeterFamily
  unit: string
  index: ReadingSide | null
  inverse: ReadingSide | null
}

export function meterBaseName(label: string): string {
  return label.replace(STATE_SUFFIX, '').replaceAll('m^3', 'm³')
}

/** Famille d'affichage, lue dans le libellé. Le type en base range
 * souvent la chaleur avec l'électricité, ce qui ne sert pas à filtrer. */
export function meterFamily(label: string, unit: string): MeterFamily {
  const text = label.toLowerCase()
  if (/chaleur|chauffage|ccaleur/.test(text)) return 'chaleur'
  if (/\beau\b|water|m\^3|m³/.test(text)) return 'eau'
  if (/cnrmachine|nrmachine/.test(text)) return 'autre'
  if ((unit || '').toLowerCase() === 'kwh' || /kwh/.test(text)) return 'electricite'
  return 'autre'
}

export function displayUnit(label: string, unit: string): string {
  const clean = (unit || '').trim()
  if (clean) return clean.replace('m^3', 'm³').replace(/^m3$/i, 'm³')
  if (/m\^3|m³|\bm3\b/i.test(label)) return 'm³'
  if (/kwh/i.test(label)) return 'kWh'
  return ''
}

function sideOf(row: Releve): ReadingSide {
  return { seriesId: row.series_id, value: row.value, ts: row.ts }
}

/** Une ligne par compteur : l'index (total) et, s'il existe, le sens inverse (totalNeg). */
export function groupMeters(rows: Releve[]): MeterRow[] {
  const slots = new Map<string, { index?: Releve; inverse?: Releve; apartment: string; name: string }>()
  for (const row of rows) {
    const name = meterBaseName(row.label)
    const apartment = row.apartment || ''
    const key = `${apartment}\0${name}`
    const slot = slots.get(key) ?? { apartment, name }
    if (row.state_name === 'totalNeg') slot.inverse = row
    else slot.index = row
    slots.set(key, slot)
  }

  const meters: MeterRow[] = []
  for (const slot of slots.values()) {
    const source = slot.index ?? slot.inverse
    const unit = displayUnit(slot.name, source?.unit || '')
    meters.push({
      key: [slot.index?.series_id, slot.inverse?.series_id].filter(Boolean).join('|'),
      apartment: slot.apartment,
      name: slot.name,
      family: meterFamily(slot.name, source?.unit || ''),
      unit,
      index: slot.index ? sideOf(slot.index) : null,
      inverse: slot.inverse ? sideOf(slot.inverse) : null,
    })
  }
  meters.sort((a, b) => a.name.localeCompare(b.name, 'fr'))
  return meters
}

export function shownTs(meter: MeterRow): number | null {
  return meter.index?.ts ?? meter.inverse?.ts ?? null
}

export function isStale(ts: number | null, nowMs: number): boolean {
  if (!ts) return false
  return nowMs / 1000 - ts > STALE_AFTER_SEC
}

export function meterIsStale(meter: MeterRow, nowMs: number): boolean {
  return isStale(shownTs(meter), nowMs)
}

function zurichDay(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleDateString('en-CA', { timeZone: TZ })
}

export function zurichStamp(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleString('fr-CH', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: TZ,
  })
}

function relativeFr(ageSec: number): string {
  if (ageSec < 90) return "à l'instant"
  if (ageSec < 3600) return `il y a ${Math.round(ageSec / 60)} min`
  if (ageSec < 86400) return `il y a ${Math.round(ageSec / 3600)} h`
  const days = Math.round(ageSec / 86400)
  return days === 1 ? 'il y a 1 jour' : `il y a ${days} jours`
}

export function readingClock(ts: number | null, nowMs: number): { text: string; title: string; stale: boolean } {
  if (!ts) return { text: '—', title: 'Aucun relevé', stale: false }
  const age = nowMs / 1000 - ts
  const stamp = zurichStamp(ts)
  const sameDay = zurichDay(ts) === zurichDay(nowMs / 1000)
  const text = sameDay
    ? new Date(ts * 1000).toLocaleTimeString('fr-CH', { hour: '2-digit', minute: '2-digit', timeZone: TZ })
    : stamp
  return { text, title: `${stamp} · ${relativeFr(age)}`, stale: age > STALE_AFTER_SEC }
}

export function compteurCount(n: number): string {
  return n === 1 ? '1 compteur' : `${n} compteurs`
}

function normalizeSearch(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/(\p{L})(\d)/gu, '$1 $2')
    .replace(/(\d)(\p{L})/gu, '$1 $2')
    .replace(/\b0+(\d+)\b/g, '$1')
}

function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Recherche tolérante : sans accent, « app1 » trouve App 1,
 * « app 1 » ne prend pas App 10, « eau » ne prend pas « réseau ». */
export function matchesQuery(haystack: string, query: string): boolean {
  const q = query.trim()
  if (!q) return true
  const hay = normalizeSearch(haystack)
  const tokens = normalizeSearch(q).split(/\s+/).filter(Boolean)
  return tokens.every((token) => {
    if (/^\d+$/.test(token)) return new RegExp(`(?:^|\\D)${escapeRe(token)}(?:\\D|$)`).test(hay)
    // Début de mot : « sol » trouve « solaire », pas une suite au milieu d'un mot.
    return new RegExp(`(?:^|\\P{L})${escapeRe(token)}`, 'u').test(hay)
  })
}
