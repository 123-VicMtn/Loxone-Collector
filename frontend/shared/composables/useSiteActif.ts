/**
 * Site affiché par les graphiques, le décompte, les matériels et la
 * classification. Le dernier choix est repris (`site-actif`, ou
 * `decompte-site` s'il existe déjà) ; sinon le premier site du compte.
 * Choisi dans la barre latérale (SiteSwitcher.vue), visible sur toutes
 * les pages.
 */

import { onMounted, ref, watch } from 'vue'
import { fetchJSON } from '../api/http'
import { authState } from '../auth'

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
    })().catch((err) => {
      started = null  // un échec n'est pas gardé : le prochain appel réessaie
      throw err
    }).finally(() => {
      loaded.value = true
    })
  }
  return started
}

// Changement de compte sans recharger la page : la liste chargée pour le
// compte précédent ne doit pas rester affichée (sites d'autres clients).
watch(() => authState.username, (nouveau, ancien) => {
  if (nouveau === ancien) return
  started = null
  sites.value = []
  site.value = ''
  loaded.value = false
  if (nouveau) void loadSites().catch(() => { /* idem */ })
})

export function useSiteActif() {
  onMounted(() => {
    // La barre latérale est montée un instant avant que le routeur ne sache
    // qu'on est sur /login : sans compte, pas d'appel (un 401 ici renvoyait
    // vers /login?redirect=/login). Le chargement suit alors la connexion.
    if (!authState.username) return
    void loadSites().catch(() => { /* la page qui en a besoin affiche son message */ })
  })

  function selectSite(name: string) {
    if (!sites.value.includes(name)) return
    remember(name)
  }

  return { sites, site, loaded, selectSite, loadSites }
}
