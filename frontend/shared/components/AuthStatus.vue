<script setup lang="ts">
/** Compte connecté et déconnexion, en bas de la barre latérale. */

import { useRouter } from 'vue-router'
import { authState, logout } from '../auth'

const router = useRouter()

async function onLogout() {
  await logout()
  router.push('/login')
}
</script>

<template>
  <div v-if="authState.username" class="flex min-w-0 flex-col items-start gap-1 text-sm text-neutral-500">
    <span class="max-w-full truncate font-medium text-neutral-800">{{ authState.username }}</span>
    <span v-if="authState.role === 'user'" class="text-xs text-neutral-400">Lecture seule</span>
    <button type="button" class="text-blue-600 hover:underline" @click="onLogout">Déconnexion</button>
  </div>
</template>
