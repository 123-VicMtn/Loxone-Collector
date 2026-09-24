<script setup lang="ts">
import { inject } from 'vue'
import type { Series } from '@shared/types/series'
import type { SidebarSelection } from '../sidebarSelection'
import { SIDEBAR_SELECTION_KEY } from '../sidebarSelection'

defineProps<{ series: Series[] }>()

const selection = inject<SidebarSelection>(SIDEBAR_SELECTION_KEY)!
</script>

<template>
  <ul class="ml-4 space-y-1 py-1">
    <li v-for="s in series" :key="s.series_id">
      <label class="flex cursor-pointer items-center gap-2 text-sm text-neutral-700 hover:text-neutral-900">
        <input
          type="checkbox"
          :checked="selection.isSelected(s.series_id)"
          @change="selection.onToggle(s, ($event.target as HTMLInputElement).checked)"
        >
        <span>{{ s.label }}</span>
        <span v-if="s.unit" class="text-xs text-neutral-400">{{ s.unit }}</span>
      </label>
    </li>
  </ul>
</template>
