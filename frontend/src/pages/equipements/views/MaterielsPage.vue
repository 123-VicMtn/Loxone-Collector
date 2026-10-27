<script setup lang="ts">
/** Liste des compteurs du site affiché (mémorisé, partagé avec les autres pages). */

import RelevesTab from '../../dashboard/tabs/RelevesTab.vue'
</script>

<template>
  <div class="w-full px-4 py-6">
    <main class="min-w-0 w-full">
      <h1 class="mb-4 text-xl font-bold text-neutral-900">Matériels</h1>
      <p class="mb-6 text-sm text-neutral-500">
        Dernier index de chaque compteur du site choisi.
      </p>
      <RelevesTab />
    </main>
  </div>
</template>
