import type { Series } from '@shared/types/series'
import { zoneKey, zoneLabel } from '@shared/format'

export interface ZoneOption {
  value: string
  label: string
}

export interface ZoneOptionGroup {
  site: string
  options: ZoneOption[]
}

/** Zones groupées par site (<optgroup>) -- deux sites peuvent chacun avoir
 * un "App 1", la valeur de chaque option est donc la clé composite
 * site+appartement (zoneKey), pas l'appartement seul. Port de
 * buildZoneOptions (energy-tab.js et zone-tab.js, dupliqué à l'identique
 * dans les deux -- une seule version ici). */
export function buildZoneOptionGroups(
  seriesArr: Series[],
  filter?: (s: Series) => boolean,
): { groups: ZoneOptionGroup[]; multiSite: boolean } {
  const bySite = new Map<string, Set<string>>()
  for (const s of seriesArr) {
    if (filter && !filter(s)) continue
    const site = s.miniserver || ''
    if (!bySite.has(site)) bySite.set(site, new Set())
    bySite.get(site)!.add(s.apartment || '')
  }
  const sites = Array.from(bySite.keys()).sort((a, b) => a.localeCompare(b))
  const multiSite = sites.length > 1

  const groups = sites.map((site) => {
    const sorted = Array.from(bySite.get(site)!).sort((a, b) => {
      if (a === '') return 1
      if (b === '') return -1
      return a.localeCompare(b)
    })
    return {
      site,
      options: sorted.map((apt) => ({
        value: zoneKey({ miniserver: site, apartment: apt }),
        label: zoneLabel(apt),
      })),
    }
  })
  return { groups, multiSite }
}
