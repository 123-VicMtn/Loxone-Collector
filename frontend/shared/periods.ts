/**
 * Bornes de périodes en heure LOCALE (Europe/Zurich), pour les tuiles KPI
 * du dashboard (aujourd'hui/semaine/mois/année + plage personnalisée) --
 * même convention que billing.py::period_bounds côté serveur (minuit
 * local, pas minuit UTC comme db.query_daily_last). Nécessaire depuis le
 * refactor qui fait lire les tuiles KPI via /api/series/<id>/range (voir
 * CLAUDE.md, "Refactor extraction/lecture des données dashboard") : avant,
 * ces bornes étaient calculées par le Miniserver Loxone lui-même, jamais
 * ici.
 *
 * Toutes les fonctions renvoient des timestamps Unix (secondes), bornes
 * [début, fin[ (fin exclusive), comme billing.period_bounds.
 */

const TIMEZONE = 'Europe/Zurich'

interface Ymd {
  y: number
  m: number
  d: number
}

function zonedYmd(date: Date, timeZone: string = TIMEZONE): Ymd {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const parts = Object.fromEntries(dtf.formatToParts(date).map((p) => [p.type, p.value]))
  return { y: Number(parts.year), m: Number(parts.month), d: Number(parts.day) }
}

/** Timestamp Unix (secondes) de minuit local Europe/Zurich pour la date
 * calendaire {y, m, d} -- conversion "aller-retour" classique et robuste
 * au changement d'heure (DST) : une première estimation traite le
 * Y-M-D-00:00:00 souhaité comme s'il était déjà en UTC, puis mesure
 * l'écart réel en relisant cette estimation dans le fuseau cible et
 * corrige en conséquence. */
function localMidnightToEpoch(y: number, m: number, d: number, timeZone: string = TIMEZONE): number {
  const utcGuess = Date.UTC(y, m - 1, d, 0, 0, 0)
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  const parts = Object.fromEntries(dtf.formatToParts(new Date(utcGuess)).map((p) => [p.type, p.value]))
  const hour = parts.hour === '24' ? 0 : Number(parts.hour)
  const asUtc = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), hour, Number(parts.minute), Number(parts.second))
  const diff = asUtc - utcGuess
  return Math.floor((utcGuess - diff) / 1000)
}

/** Arithmétique calendaire pure (indépendante du fuseau -- base UTC
 * neutre utilisée uniquement pour compter des jours). */
function addDaysYmd(ymd: Ymd, delta: number): Ymd {
  const dt = new Date(Date.UTC(ymd.y, ymd.m - 1, ymd.d))
  dt.setUTCDate(dt.getUTCDate() + delta)
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() }
}

function mondayIndex(date: Date, timeZone: string = TIMEZONE): number {
  const dtf = new Intl.DateTimeFormat('en-US', { timeZone, weekday: 'short' })
  const order: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 }
  return order[dtf.format(date)] ?? 0
}

export type Bounds = [number, number]

export function todayBounds(now: Date = new Date()): Bounds {
  const ymd = zonedYmd(now)
  return [localMidnightToEpoch(ymd.y, ymd.m, ymd.d), Math.floor(now.getTime() / 1000)]
}

/** Semaine calendaire commençant le lundi (convention CH/UE). */
export function weekBounds(now: Date = new Date()): Bounds {
  const ymd = zonedYmd(now)
  const monday = addDaysYmd(ymd, -mondayIndex(now))
  return [localMidnightToEpoch(monday.y, monday.m, monday.d), Math.floor(now.getTime() / 1000)]
}

/** Mois calendaire en cours -- mêmes bornes que billing.period_bounds
 * pour le même (year, month). */
export function monthBounds(now: Date = new Date()): Bounds {
  const ymd = zonedYmd(now)
  return [localMidnightToEpoch(ymd.y, ymd.m, 1), Math.floor(now.getTime() / 1000)]
}

export function yearBounds(now: Date = new Date()): Bounds {
  const ymd = zonedYmd(now)
  return [localMidnightToEpoch(ymd.y, 1, 1), Math.floor(now.getTime() / 1000)]
}

/** Plage personnalisée à partir de deux dates calendaires (valeurs
 * d'<input type="date">, format "YYYY-MM-DD") -- bornes inclusives des
 * deux jours choisis : `to` est repoussé à minuit local du jour SUIVANT
 * (borne exclusive), comme billing.period_bounds pour un mois. Retourne
 * null si l'une des deux dates est invalide ou si la plage est vide/à
 * l'envers. */
export function customRangeBounds(fromDateStr: string, toDateStr: string): Bounds | null {
  const from = parseDateInput(fromDateStr)
  const to = parseDateInput(toDateStr)
  if (!from || !to) return null
  const start = localMidnightToEpoch(from.y, from.m, from.d)
  const toNext = addDaysYmd(to, 1)
  const end = localMidnightToEpoch(toNext.y, toNext.m, toNext.d)
  if (end <= start) return null
  return [start, end]
}

function parseDateInput(value: string): Ymd | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '')
  if (!m) return null
  return { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) }
}
