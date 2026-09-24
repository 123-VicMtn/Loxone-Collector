<script setup lang="ts">
/**
 * Page de connexion (POST /api/login) -- voir CLAUDE.md, "Authentification
 * backend". Pas d'inscription en ligne (comptes créés via
 * scripts/create_admin_user.py, voir docs/plan-installation-auth-frontend-docker.md).
 * Redirige vers `redirect` (query param posé par le garde de route dans
 * router.ts) ou `/` par défaut après connexion.
 */

import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '@shared/auth'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch (err) {
    error.value = (err as Error).message || 'Identifiants invalides'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-neutral-50 px-4">
    <div class="w-full max-w-sm rounded-lg border border-neutral-200 bg-white p-8 shadow-sm">
      <h1 class="text-xl font-bold text-neutral-900">Loxone Collector</h1>
      <p class="mt-1 text-sm text-neutral-500">Connexion</p>

      <form class="mt-6 space-y-4" @submit.prevent="onSubmit">
        <label class="flex flex-col text-sm text-neutral-600">
          Nom d'utilisateur
          <input
            v-model="username"
            type="text"
            required
            autofocus
            autocomplete="username"
            class="mt-1 rounded border border-neutral-300 px-3 py-2"
          >
        </label>
        <label class="flex flex-col text-sm text-neutral-600">
          Mot de passe
          <input
            v-model="password"
            type="password"
            required
            autocomplete="current-password"
            class="mt-1 rounded border border-neutral-300 px-3 py-2"
          >
        </label>

        <p v-if="error" class="text-sm text-red-600">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading"
          class="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >{{ loading ? 'Connexion…' : 'Se connecter' }}</button>
      </form>
    </div>
  </div>
</template>
