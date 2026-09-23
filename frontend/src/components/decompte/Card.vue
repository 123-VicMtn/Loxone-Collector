<script setup lang="ts">
withDefaults(defineProps<{
  title?: string
  hint?: string
}>(), {
  title: '',
  hint: '',
})
</script>

<template>
  <section class="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
    <h2 v-if="title" class="mb-4 flex flex-wrap items-baseline gap-2 text-lg font-semibold text-neutral-900">
      {{ title }}
      <span v-if="hint" class="text-sm font-normal text-neutral-500">{{ hint }}</span>
    </h2>
    <slot />
  </section>
</template>
