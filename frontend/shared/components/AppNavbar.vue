<script setup lang="ts">
/**
 * Barre de navigation de l'app, au-dessus de toutes les pages sauf /login.
 * Classification n'apparaît que pour un admin (la route est déjà refusée
 * à un compte 'user' par le garde de router.ts).
 */

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { authState } from '../auth'
import AuthStatus from './AuthStatus.vue'

const route = useRoute()
const menuOpen = ref(false)

const links = computed(() => [
  { to: '/', label: 'Dashboard' },
  { to: '/decompte', label: 'Décompte' },
  ...(authState.role === 'admin' ? [{ to: '/admin', label: 'Classification' }] : []),
])

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}

watch(() => route.path, () => { menuOpen.value = false })
</script>

<template>
  <header class="sticky top-0 z-40 border-b border-neutral-200 bg-white">
    <nav class="flex h-14 items-center gap-6 px-4">
      <router-link to="/" class="text-base font-bold text-neutral-900">Loxone</router-link>

      <ul class="hidden h-full items-stretch gap-1 sm:flex">
        <li v-for="l in links" :key="l.to" class="flex">
          <router-link
            :to="l.to"
            :aria-current="isActive(l.to) ? 'page' : undefined"
            :class="[
              'flex items-center border-b-2 px-3 text-sm font-medium',
              isActive(l.to)
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-neutral-600 hover:text-neutral-900',
            ]"
          >{{ l.label }}</router-link>
        </li>
      </ul>

      <AuthStatus class="ml-auto hidden sm:flex" />

      <button
        type="button"
        class="ml-auto rounded px-2 py-1 text-sm text-neutral-700 hover:bg-neutral-100 sm:hidden"
        :aria-expanded="menuOpen"
        aria-controls="app-navbar-menu"
        @click="menuOpen = !menuOpen"
      >Menu</button>
    </nav>

    <div v-if="menuOpen" id="app-navbar-menu" class="border-t border-neutral-200 px-4 py-3 sm:hidden">
      <ul class="flex flex-col gap-1">
        <li v-for="l in links" :key="l.to">
          <router-link
            :to="l.to"
            :aria-current="isActive(l.to) ? 'page' : undefined"
            :class="[
              'block rounded px-3 py-2 text-sm font-medium',
              isActive(l.to) ? 'bg-blue-50 text-blue-700' : 'text-neutral-700 hover:bg-neutral-50',
            ]"
          >{{ l.label }}</router-link>
        </li>
      </ul>
      <AuthStatus class="mt-3 px-3" />
    </div>
  </header>
</template>
