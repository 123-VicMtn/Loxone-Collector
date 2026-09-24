<script setup lang="ts">
/**
 * "Connecté en tant que X · Déconnexion", identique sur les 3 pages --
 * un seul composant plutôt que dupliquer ce petit bout de markup/logique
 * trois fois (voir CLAUDE.md, "chaque page garde son propre <header>" --
 * ce composant s'insère DANS ces en-têtes, il ne les remplace pas).
 */

import { useRouter } from 'vue-router'
import { authState, logout } from '../auth'

const router = useRouter()

async function onLogout() {
  await logout()
  router.push('/login')
}
</script>

<template>
  <div v-if="authState.username" class="flex items-center gap-2 text-sm text-neutral-500">
    <span>{{ authState.username }}<span v-if="authState.role === 'user'" class="text-neutral-400"> (lecture seule)</span></span>
    <button type="button" class="text-blue-600 hover:underline" @click="onLogout">Déconnexion</button>
  </div>
</template>
