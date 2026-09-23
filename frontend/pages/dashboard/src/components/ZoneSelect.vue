<script setup lang="ts">
import type { ZoneOptionGroup } from '../utils/zoneOptions'

defineProps<{
  modelValue: string
  groups: ZoneOptionGroup[]
  multiSite: boolean
}>()
defineEmits<{ 'update:modelValue': [string] }>()
</script>

<template>
  <select
    class="rounded border border-neutral-300 px-2 py-1"
    :value="modelValue"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <template v-if="multiSite">
      <optgroup v-for="g in groups" :key="g.site" :label="g.site || 'Sans site'">
        <option v-for="o in g.options" :key="o.value" :value="o.value">{{ o.label }}</option>
      </optgroup>
    </template>
    <template v-else>
      <option v-for="o in groups[0]?.options ?? []" :key="o.value" :value="o.value">{{ o.label }}</option>
    </template>
  </select>
</template>
