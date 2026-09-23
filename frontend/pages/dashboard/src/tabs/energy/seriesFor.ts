import type { Series } from '@shared/types/series'
import { findSeries } from '@shared/api/series'
import { matchesZone } from '@shared/format'

/** Toutes les séries qui intéressent l'onglet Énergie pour une zone --
 * port direct de energy-tab.js::seriesFor. Suppose que loadAllSeries() a
 * déjà rempli le cache (voir Sidebar.vue / EnergyTab.vue). */
export interface ZoneEnergySeries {
  gridActual: Series | null
  gridTotal: Series | null
  gridDay: Series | null
  gridWeek: Series | null
  gridMonth: Series | null
  gridYear: Series | null
  gridNegTotal: Series | null
  gridNegDay: Series | null
  gridNegWeek: Series | null
  gridNegMonth: Series | null
  gridNegYear: Series | null

  solarActual: Series | null
  solarTotal: Series | null
  solarDay: Series | null
  solarWeek: Series | null
  solarMonth: Series | null
  solarYear: Series | null

  batteryActual: Series | null
  batteryTotal: Series | null
  batteryNegTotal: Series | null
  batteryDay: Series | null
  batteryNegDay: Series | null
  batteryWeek: Series | null
  batteryNegWeek: Series | null
  batteryMonth: Series | null
  batteryNegMonth: Series | null
  batteryYear: Series | null
  batteryNegYear: Series | null
  batteryStorage: Series | null

  efmGpwr: Series | null
  efmPpwr: Series | null
  efmSpwr: Series | null
  efmSelfConsumption: Series | null
}

export function seriesFor(zone: string): ZoneEnergySeries {
  const find = (resourceType: string, stateName: string) =>
    findSeries((s) => matchesZone(s, zone) && s.resource_type === resourceType && s.state_name === stateName)

  return {
    gridActual: find('energie_reseau', 'actual'),
    gridTotal: find('energie_reseau', 'total'),
    gridDay: find('energie_reseau', 'totalDay'),
    gridWeek: find('energie_reseau', 'totalWeek'),
    gridMonth: find('energie_reseau', 'totalMonth'),
    gridYear: find('energie_reseau', 'totalYear'),
    gridNegTotal: find('energie_reseau', 'totalNeg'),
    gridNegDay: find('energie_reseau', 'totalNegDay'),
    gridNegWeek: find('energie_reseau', 'totalNegWeek'),
    gridNegMonth: find('energie_reseau', 'totalNegMonth'),
    gridNegYear: find('energie_reseau', 'totalNegYear'),

    solarActual: find('energie_solaire', 'actual'),
    solarTotal: find('energie_solaire', 'total'),
    solarDay: find('energie_solaire', 'totalDay'),
    solarWeek: find('energie_solaire', 'totalWeek'),
    solarMonth: find('energie_solaire', 'totalMonth'),
    solarYear: find('energie_solaire', 'totalYear'),

    batteryActual: find('energie_batterie', 'actual'),
    batteryTotal: find('energie_batterie', 'total'),
    batteryNegTotal: find('energie_batterie', 'totalNeg'),
    batteryDay: find('energie_batterie', 'totalDay'),
    batteryNegDay: find('energie_batterie', 'totalNegDay'),
    batteryWeek: find('energie_batterie', 'totalWeek'),
    batteryNegWeek: find('energie_batterie', 'totalNegWeek'),
    batteryMonth: find('energie_batterie', 'totalMonth'),
    batteryNegMonth: find('energie_batterie', 'totalNegMonth'),
    batteryYear: find('energie_batterie', 'totalYear'),
    batteryNegYear: find('energie_batterie', 'totalNegYear'),
    batteryStorage: find('energie_batterie', 'storage'),

    efmGpwr: find('energie_flux', 'Gpwr'),
    efmPpwr: find('energie_flux', 'Ppwr'),
    efmSpwr: find('energie_flux', 'Spwr'),
    efmSelfConsumption: find('energie_flux', 'selfConsumption'),
  }
}
