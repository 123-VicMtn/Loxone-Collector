/**
 * Site affiché par les graphiques, le décompte, les matériels et la
 * classification. Le dernier choix est repris (`site-actif`, ou
 * `decompte-site` s'il existe déjà) ; sinon le premier site du compte.
 */

import { onMounted, ref } from 'vue'
import { fetchJSON } from '../api/http'

const STORAGE_KEY = 'site-actif'

const sites = ref<string[]>([])
const site = ref('')
const loaded = ref(false)
let started: Promise<void> | null = null

function remember(name: string) {
  site.value = name
  try {
    localStorage.setItem(STORAGE_KEY, name)
    localStorage.setItem('decompte-site', name)
  } catch {
    /* navigation privée : le choix reste en mémoire le temps de la session */
  }
}

function loadSites(): Promise<void> {
  if (!started) {
    started = (async () => {
      const list = await fetchJSON<string[]>('/api/miniservers')
      sites.value = list
      let saved = ''
      try {
        saved = localStorage.getItem(STORAGE_KEY) || localStorage.getItem('decompte-site') || ''
      } catch {
        saved = ''
      }
      const initial = saved && list.includes(saved) ? saved : (list[0] || '')
      if (initial) remember(initial)
    })().finally(() => {
      loaded.value = true
    })
  }
  return started
}

export function useSiteActif() {
  onMounted(() => {
    void loadSites().catch(() => { /* la page qui en a besoin affiche son message */ })
  })

  function selectSite(name: string) {
    if (!sites.value.includes(name)) return
    remember(name)
  }

  return { sites, site, loaded, selectSite, loadSites }
}
