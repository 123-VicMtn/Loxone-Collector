import { fetchLatest } from '@shared/api/series'
import { fmtNumber } from '@shared/format'
import { periodGroupData, type PeriodTile } from './periodGroup'
import { buildBatteryChart } from './charts'
import type { ZoneEnergySeries } from './seriesFor'

export interface BatteryResult {
  hasSeries: boolean
  hasActivity: boolean
  hint: string
  tiles: PeriodTile[]
  note: string
  chart: Awaited<ReturnType<typeof buildBatteryChart>> | null
}

/** Panneau Batterie : n'affiche les tuiles que si une vraie activité est
 * mesurée (évite d'afficher des zéros trompeurs pour une batterie pas
 * encore commissionnée). Charge = state "total"/totalX, décharge =
 * "totalNeg"/totalNegX, même logique bidirectionnelle que le compteur
 * Réseau. Port direct de energy-tab.js::renderBattery. */
export async function computeBattery(sids: ZoneEnergySeries): Promise<BatteryResult> {
  const hasSeries = !!(sids.batteryActual || sids.batteryTotal || sids.batteryNegTotal || sids.batteryStorage)
  if (!hasSeries) {
    return { hasSeries: false, hasActivity: false, hint: '', tiles: [], note: '', chart: null }
  }

  const [actualV, totalV, negTotalV] = await Promise.all([
    fetchLatest(sids.batteryActual?.series_id),
    fetchLatest(sids.batteryTotal?.series_id),
    fetchLatest(sids.batteryNegTotal?.series_id),
  ])
  const hasActivity = [actualV, totalV, negTotalV].some((v) => v !== null && v !== 0)
  if (!hasActivity) {
    return {
      hasSeries: true,
      hasActivity: false,
      hint: 'Compteur batterie détecté mais aucune activité mesurée pour l\'instant (probablement pas encore commissionnée).',
      tiles: [], note: '', chart: null,
    }
  }

  const charge = await periodGroupData('Charge', {
    day: sids.batteryDay, week: sids.batteryWeek, month: sids.batteryMonth, year: sids.batteryYear, total: sids.batteryTotal,
  })
  const discharge = await periodGroupData('Décharge', {
    day: sids.batteryNegDay, week: sids.batteryNegWeek, month: sids.batteryNegMonth, year: sids.batteryNegYear, total: sids.batteryNegTotal,
  })
  const tiles = [...charge.tiles, ...discharge.tiles]

  if (sids.batteryStorage) {
    const storageV = await fetchLatest(sids.batteryStorage.series_id)
    if (storageV !== null) {
      tiles.push({ label: 'État de charge', value: fmtNumber(storageV, 0), unit: sids.batteryStorage.unit || '' })
    }
  }

  const note = 'Charge/décharge recalculées par le Miniserver (jour/semaine/mois/année) à partir du compteur ' +
    'bidirectionnel de la batterie -- même logique que Réseau import/export.'

  const chart = await buildBatteryChart(sids.batteryTotal?.series_id, sids.batteryNegTotal?.series_id)

  return { hasSeries: true, hasActivity: true, hint: '', tiles, note, chart }
}
