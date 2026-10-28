<script setup lang="ts">
/**
 * Site dont les données sont affichées, en haut de la barre latérale :
 * visible quelle que soit la page. Une liste déroulante si le compte a
 * plusieurs sites, le nom seul sinon.
 */

import { useSiteActif } from '../composables/useSiteActif'

const { sites, site, selectSite } = useSiteActif()
</script>

<template>
  <div v-if="site" class="min-w-0">
    <label
      for="site-actif"
      class="block text-xs font-semibold uppercase tracking-wide text-neutral-400"
    >Site affiché</label>
    <select
      v-if="sites.length > 1"
      id="site-actif"
      class="mt-1 w-full truncate rounded-md border border-neutral-300 bg-white py-2 pr-8 pl-3 text-sm font-semibold text-neutral-900 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
      :value="site"
      @change="selectSite(($event.target as HTMLSelectElement).value)"
    >
      <option v-for="s in sites" :key="s" :value="s">{{ s }}</option>
    </select>
    <p v-else id="site-actif" class="mt-1 truncate text-sm font-semibold text-neutral-900">{{ site }}</p>
  </div>
</template>
