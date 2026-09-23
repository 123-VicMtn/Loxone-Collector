<script setup lang="ts">
import { RANGE_PRESETS, type RangeKey } from '@shared/ranges'

defineProps<{ modelValue: RangeKey }>()
defineEmits<{ 'update:modelValue': [RangeKey] }>()
</script>

<template>
  <div class="flex gap-1">
    <button
      v-for="r in RANGE_PRESETS"
      :key="r"
      type="button"
      :class="['rounded px-2 py-1 text-sm', modelValue === r ? 'bg-blue-600 text-white' : 'text-neutral-600 hover:bg-neutral-100']"
      @click="$emit('update:modelValue', r)"
    >{{ r }}</button>
  </div>
</template>
