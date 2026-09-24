import { reactive } from 'vue'
import type { Series } from '@shared/types/series'

/** Sélection multi-capteurs de l'onglet Explorer -- vit au niveau de la
 * page (DashboardPage.vue) car la sidebar (qui l'alimente) est un frère de
 * l'onglet Explorer (qui l'affiche), pas un parent/enfant : la sidebar
 * reste visible en permanence, seul le contenu de droite change avec
 * l'onglet actif (voir templates/index.html d'origine : `.sidebar` est en
 * dehors de `.content`, qui contient les 3 panels d'onglet). */
export function useExplorerSelection() {
  const selected = reactive(new Map<string, string>())

  function isSelected(seriesId: string): boolean {
    return selected.has(seriesId)
  }

  function onToggle(series: Series, checked: boolean) {
    if (checked) {
      selected.set(series.series_id, series.unit ? `${series.label} (${series.unit})` : series.label)
    } else {
      selected.delete(series.series_id)
    }
  }

  function clearSelection() {
    selected.clear()
  }

  return { selected, isSelected, onToggle, clearSelection }
}
