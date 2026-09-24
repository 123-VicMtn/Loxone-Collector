import type { Series } from '@shared/types/series'
import { findSeries } from '@shared/api/series'
import { matchesZone } from '@shared/format'

/** Séries de l'onglet Énergie pour une zone. Le collecteur ne suit que les
 * compteurs Meter, états actual / total / totalNeg : les tuiles de période
 * se calculent depuis total et totalNeg, la puissance instantanée depuis
 * actual. Suppose que loadAllSeries() a déjà rempli le cache. */
export interface ZoneEnergySeries {
  gridActual: Series | null
  gridTotal: Series | null
  gridNegTotal: Series | null

  solarActual: Series | null
  solarTotal: Series | null

  batteryActual: Series | null
  batteryTotal: Series | null
  batteryNegTotal: Series | null
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
  }
}
