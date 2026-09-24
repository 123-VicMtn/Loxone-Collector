<script setup lang="ts">
defineProps<{ manual: 0 | 1 }>()
</script>

<template>
  <span
    v-if="manual"
    class="ml-1 inline-block rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800"
    title="Corrigé manuellement"
  >manuel</span>
</template>
