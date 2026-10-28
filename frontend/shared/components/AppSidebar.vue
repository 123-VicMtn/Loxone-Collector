<script setup lang="ts">
/**
 * Navigation de l'app, à gauche de toutes les pages sauf /login.
 * En tête, le site affiché (SiteSwitcher), commun à toutes les pages ;
 * sur petit écran, son nom est aussi dans la barre du haut. Deux parties : Équipements (communication, matériels) et Outils
 * (graphiques, décompte). Sur grand écran elle reste affichée. En dessous
 * de lg (1024 px) elle devient un tiroir. Classification est masquée
 * (`SHOW_CLASSIFICATION`) : la page `/admin` reste accessible par son URL,
 * pour un admin.
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { authState } from '../auth'
import AuthStatus from './AuthStatus.vue'
import SiteSwitcher from './SiteSwitcher.vue'
import { useSiteActif } from '../composables/useSiteActif'

const open = defineModel<boolean>('open', { default: false })

const route = useRoute()
const { site } = useSiteActif()
const menuButton = ref<HTMLButtonElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)

const DESKTOP_QUERY = '(min-width: 1024px)'
const isDesktop = ref(typeof window !== 'undefined' && window.matchMedia(DESKTOP_QUERY).matches)
let desktopMedia: MediaQueryList | null = null

const equipements = [
  { to: '/equipements/communication', label: 'Communication' },
  { to: '/equipements/materiels', label: 'Matériels' },
]

/** Provisoire. `true` réaffiche le lien. La page `/admin` n'est pas retirée. */
const SHOW_CLASSIFICATION = false

const outils = computed(() => [
  { to: '/', label: 'Graphiques' },
  { to: '/decompte', label: 'Décompte' },
  ...(SHOW_CLASSIFICATION && authState.role === 'admin'
    ? [{ to: '/admin', label: 'Classification' }]
    : []),
])

const drawerOpen = computed(() => open.value && !isDesktop.value)

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}

function linkClass(to: string): string {
  const base = 'block rounded-md px-3 py-2.5 text-sm font-medium'
  return isActive(to)
    ? `${base} bg-blue-50 text-blue-700`
    : `${base} text-neutral-700 hover:bg-neutral-50 hover:text-neutral-900`
}

function onDesktopChange(event: MediaQueryListEvent) {
  isDesktop.value = event.matches
  if (event.matches) open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && drawerOpen.value) open.value = false
}

function openMenu() {
  open.value = true
  nextTick(() => closeButton.value?.focus())
}

watch(() => route.path, () => {
  open.value = false
})

watch(open, (isOpen, wasOpen) => {
  document.body.style.overflow = isOpen && !isDesktop.value ? 'hidden' : ''
  if (wasOpen && !isOpen && !isDesktop.value) {
    nextTick(() => {
      if (menuButton.value?.isConnected) menuButton.value.focus()
    })
  }
})

onMounted(() => {
  desktopMedia = window.matchMedia(DESKTOP_QUERY)
  isDesktop.value = desktopMedia.matches
  desktopMedia.addEventListener('change', onDesktopChange)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  desktopMedia?.removeEventListener('change', onDesktopChange)
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <header
    class="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-neutral-200 bg-white px-4 lg:hidden"
    :inert="drawerOpen || undefined"
  >
    <button
      ref="menuButton"
      type="button"
      class="inline-flex h-11 w-11 items-center justify-center rounded text-neutral-700 hover:bg-neutral-100"
      :aria-expanded="open"
      aria-controls="app-sidebar"
      @click="openMenu"
    >
      <span class="sr-only">Ouvrir le menu</span>
      <svg class="h-5 w-5" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>
    <router-link to="/" class="text-base font-bold text-neutral-900">Loxone</router-link>
    <button
      v-if="site"
      type="button"
      class="ml-auto min-w-0 truncate rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700"
      :aria-label="`Site affiché : ${site}. Changer de site`"
      @click="openMenu"
    >{{ site }}</button>
  </header>

  <button
    v-if="drawerOpen"
    type="button"
    class="fixed inset-0 z-40 bg-neutral-900/40 lg:hidden"
    aria-label="Fermer le menu"
    @click="open = false"
  />

  <aside
    id="app-sidebar"
    class="fixed top-0 left-0 z-50 flex h-dvh w-64 max-w-[85vw] flex-col border-r border-neutral-200 bg-white shadow-xl motion-safe:transition-transform motion-safe:duration-200 lg:z-30 lg:max-w-none lg:translate-x-0 lg:shadow-none"
    :class="open ? 'translate-x-0' : '-translate-x-full'"
    :inert="!isDesktop && !open"
    aria-label="Navigation principale"
  >
    <div class="flex h-14 shrink-0 items-center justify-between border-b border-neutral-200 px-4">
      <router-link to="/" class="text-base font-bold text-neutral-900" @click="open = false">Loxone Collector</router-link>
      <button
        ref="closeButton"
        type="button"
        class="inline-flex h-11 w-11 items-center justify-center rounded text-neutral-700 hover:bg-neutral-100 lg:hidden"
        @click="open = false"
      >
        <span class="sr-only">Fermer le menu</span>
        <svg class="h-5 w-5" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </button>
    </div>

    <div class="shrink-0 border-b border-neutral-200 px-4 py-4">
      <SiteSwitcher />
    </div>

    <nav class="flex-1 overflow-y-auto px-3 py-4">
      <section>
        <h2 class="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-400">Équipements</h2>
        <ul class="flex flex-col gap-1">
          <li v-for="l in equipements" :key="l.to">
            <router-link
              :to="l.to"
              :aria-current="isActive(l.to) ? 'page' : undefined"
              :class="linkClass(l.to)"
              @click="open = false"
            >{{ l.label }}</router-link>
          </li>
        </ul>
      </section>

      <section class="mt-6">
        <h2 class="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-400">Outils</h2>
        <ul class="flex flex-col gap-1">
          <li v-for="l in outils" :key="l.to">
            <router-link
              :to="l.to"
              :aria-current="isActive(l.to) ? 'page' : undefined"
              :class="linkClass(l.to)"
              @click="open = false"
            >{{ l.label }}</router-link>
          </li>
        </ul>
      </section>
    </nav>

    <div class="shrink-0 border-t border-neutral-200 px-4 py-4">
      <AuthStatus />
    </div>
  </aside>
</template>
