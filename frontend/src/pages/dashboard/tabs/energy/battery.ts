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
 * encore commissionnée). Charge = state "total", décharge = "totalNeg",
 * même logique bidirectionnelle que le compteur Réseau -- tuiles
 * jour/semaine/mois/année recalculées via periodGroupData (voir
 * energy/periodGroup.ts), `batteryStorage` (état de charge %, pas un
 * compteur cumulatif) reste sur fetchLatest. Port de
 * energy-tab.js::renderBattery. */
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

  const charge = await periodGroupData('Charge', sids.batteryTotal)
  const discharge = await periodGroupData('Décharge', sids.batteryNegTotal)
  const tiles = [...charge.tiles, ...discharge.tiles]

  if (sids.batteryStorage) {
    const storageV = await fetchLatest(sids.batteryStorage.series_id)
    if (storageV !== null) {
      tiles.push({ label: 'État de charge', value: fmtNumber(storageV, 0), unit: sids.batteryStorage.unit || '' })
    }
  }

  const note = 'Charge/décharge calculées ici (relevé de fin - relevé de début, jour/semaine/mois/année) à partir du ' +
    'compteur bidirectionnel de la batterie -- même méthode que Réseau import/export.'

  const chart = await buildBatteryChart(sids.batteryTotal?.series_id, sids.batteryNegTotal?.series_id)

  return { hasSeries: true, hasActivity: true, hint: '', tiles, note, chart }
}
