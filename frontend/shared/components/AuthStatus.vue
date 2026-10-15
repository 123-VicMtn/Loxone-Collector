<script setup lang="ts">
/** "Nom d'utilisateur · Déconnexion", affiché dans la barre de navigation. */

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
