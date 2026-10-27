<script setup lang="ts">
/** Sites sans nouveau relevé depuis plus de `seuil_minutes` (useCollecte). */
import type { SiteCollecte } from '../api/collecte'
import { fmtDateTimeShort } from '../format'

defineProps<{ sites: SiteCollecte[] }>()
</script>

<template>
  <div
    v-if="sites.length"
    class="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
    role="status"
  >
    <p v-for="s in sites" :key="s.miniserver">
      <span class="font-semibold">{{ s.miniserver }}</span>
      ne remonte plus de données<template v-if="s.dernier_releve_ts">
        depuis le {{ fmtDateTimeShort(s.dernier_releve_ts) }}</template>.
    </p>
  </div>
</template>
