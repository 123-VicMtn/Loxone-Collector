<script setup lang="ts">
import type { ZonePeriod } from '../types/decompte'

const props = defineProps<{ entry: ZonePeriod }>()

const variants: Record<string, string> = {
  ok: 'bg-green-100 text-green-800',
  pending: 'bg-amber-100 text-amber-800',
  bad: 'bg-red-100 text-red-800',
}

function status(): { text: string; variant: keyof typeof variants; title?: string } {
  if (props.entry.facturable) return { text: 'facturable', variant: 'ok' }
  if (props.entry.en_cours) return { text: 'mois en cours', variant: 'pending' }
  return { text: 'données', variant: 'bad', title: props.entry.alertes.join('\n') }
}
</script>

<template>
  <span
    :class="['inline-block rounded-full px-2 py-0.5 text-xs font-medium', variants[status().variant]]"
    :title="status().title"
  >{{ status().text }}</span>
</template>
