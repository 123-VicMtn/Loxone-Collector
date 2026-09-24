import type { Series } from '@shared/types/series'
import { findSeries } from '@shared/api/series'
import { matchesZone } from '@shared/format'

/** Toutes les séries qui intéressent l'onglet Énergie pour une zone --
 * port de energy-tab.js::seriesFor, simplifié : les états Loxone
 * totalDay/Week/Month/Year (+ totalNegDay/etc) ont disparu -- les tuiles
 * jour/semaine/mois/année sont désormais calculées depuis les seules
 * séries "total"/"totalNeg" via /api/series/<id>/range (voir
 * energy/periodGroup.ts et CLAUDE.md, "Refactor extraction/lecture des
 * données dashboard"). Suppose que loadAllSeries() a déjà rempli le cache
 * (voir Sidebar.vue / EnergyTab.vue). */
export interface ZoneEnergySeries {
  gridActual: Series | null
  gridTotal: Series | null
  gridNegTotal: Series | null

  solarActual: Series | null
  solarTotal: Series | null

  batteryActual: Series | null
  batteryTotal: Series | null
  batteryNegTotal: Series | null
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
    gridNegTotal: find('energie_reseau', 'totalNeg'),

    solarActual: find('energie_solaire', 'actual'),
    solarTotal: find('energie_solaire', 'total'),

    batteryActual: find('energie_batterie', 'actual'),
    batteryTotal: find('energie_batterie', 'total'),
    batteryNegTotal: find('energie_batterie', 'totalNeg'),
    batteryStorage: find('energie_batterie', 'storage'),

    efmGpwr: find('energie_flux', 'Gpwr'),
    efmPpwr: find('energie_flux', 'Ppwr'),
    efmSpwr: find('energie_flux', 'Spwr'),
    efmSelfConsumption: find('energie_flux', 'selfConsumption'),
  }
}
