<script setup lang="ts">
/**
 * Vérifie que chaque Miniserver remonte encore des données.
 * Le seuil est celui de GET /api/collecte (`stale_alert_minutes`).
 */

import { useCollecte } from '@shared/composables/useCollecte'
import { useSiteActif } from '@shared/composables/useSiteActif'
import { fmtDateTimeShort } from '@shared/format'

const { collecte, indisponible } = useCollecte()
const { site } = useSiteActif()
</script>

<template>
  <div class="w-full px-4 py-6">
    <main class="min-w-0 w-full">
      <h1 class="mb-4 text-xl font-bold text-neutral-900">Communication</h1>
      <p class="mb-6 text-sm text-neutral-500">
        Dernier contact de chaque Miniserver accessible avec ce compte.
        <template v-if="collecte">
          Un site qui n'a rien envoyé depuis {{ collecte.seuil_minutes }} minutes ne répond plus.
        </template>
      </p>

      <p v-if="indisponible" class="text-sm text-neutral-500">La vérification n'a pas abouti. Réessayez dans quelques minutes.</p>
      <p v-else-if="!collecte" class="text-sm text-neutral-500">Vérification en cours…</p>
      <ul v-else class="divide-y divide-neutral-200 rounded-lg border border-neutral-200">
        <li
          v-for="s in collecte.sites"
          :key="s.miniserver"
          class="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
        >
          <div class="min-w-0">
            <p class="font-medium text-neutral-900">
              {{ s.miniserver }}
              <span v-if="s.miniserver === site" class="ml-2 text-xs font-normal text-neutral-400">site affiché</span>
            </p>
            <p class="text-sm text-neutral-500">
              <template v-if="s.dernier_releve_ts">Dernier relevé le {{ fmtDateTimeShort(s.dernier_releve_ts) }}</template>
              <template v-else>Aucun relevé reçu</template>
            </p>
          </div>
          <span
            :class="[
              'rounded-full px-2.5 py-0.5 text-xs font-medium',
              s.en_retard ? 'bg-amber-100 text-amber-900' : 'bg-green-100 text-green-800',
            ]"
          >{{ s.en_retard ? 'Ne répond plus' : 'En communication' }}</span>
        </li>
      </ul>

    </main>
  </div>
</template>
