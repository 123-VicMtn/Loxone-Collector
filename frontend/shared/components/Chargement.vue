<script setup lang="ts">
/** Indicateur de chargement (spinner DaisyUI) suivi d'un texte court. */

withDefaults(defineProps<{ texte?: string }>(), { texte: 'Chargement…' })
</script>

<template>
  <p class="flex items-center gap-2 text-sm text-neutral-500" role="status" aria-live="polite">
    <span class="loading loading-spinner loading-sm text-neutral-400" aria-hidden="true" />
    {{ texte }}
  </p>
</template>
