<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  value: string
  unit?: string
  accent?: 'grid' | 'solar' | 'auto' | 'none'
}>(), {
  unit: '',
  accent: 'none',
})

const accentClasses: Record<string, string> = {
  grid: 'border-amber-200 bg-amber-50',
  solar: 'border-green-200 bg-green-50',
  auto: 'border-blue-200 bg-blue-50',
  none: 'border-neutral-200 bg-white',
}
</script>

<template>
  <div :class="['rounded-lg border p-4', accentClasses[accent]]">
    <div class="text-sm text-neutral-500">{{ label }}</div>
    <div class="mt-1 text-2xl font-semibold text-neutral-900">
      {{ value }}
      <span v-if="unit" class="text-sm font-normal text-neutral-500">{{ unit }}</span>
    </div>
  </div>
</template>
