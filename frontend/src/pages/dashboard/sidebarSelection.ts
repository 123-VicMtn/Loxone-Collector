import type { InjectionKey } from 'vue'
import type { Series } from '@shared/types/series'

/** Provide/inject plutôt que de faire descendre isSelected/onToggle sur 3
 * niveaux de composants purement structurels (site > appartement/pièce >
 * type > liste de capteurs) -- seul SeriesCheckboxList.vue, à la feuille,
 * en a réellement besoin. */
export interface SidebarSelection {
  isSelected: (seriesId: string) => boolean
  onToggle: (series: Series, checked: boolean) => void
}

export const SIDEBAR_SELECTION_KEY: InjectionKey<SidebarSelection> = Symbol('sidebarSelection')
