<script setup lang="ts">
/**
 * Calendrier unique de la page /decompte : un mois entier (bouton « Tout
 * le mois ») ou des dates libres (un clic sur le premier jour, un clic sur
 * le dernier). Les dates sont des chaînes YYYY-MM-DD, `au` inclus, comme
 * l'API (`du` / `au`).
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

export interface Plage {
  du: string
  au: string
}

const props = defineProps<{
  modelValue: Plage
  /** Premier jour sélectionnable (YYYY-MM-DD). */
  min: string
  /** Dernier jour sélectionnable (YYYY-MM-DD), en général aujourd'hui. */
  max: string
}>()
const emit = defineEmits<{ 'update:modelValue': [Plage] }>()

/** Même limite que le serveur (EXPORT_MAX_MOIS). */
const MAX_MOIS = 36
const MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
  'août', 'septembre', 'octobre', 'novembre', 'décembre']
const JOURS = ['Lu', 'Ma', 'Me', 'Je', 'Ve', 'Sa', 'Di']

const ouvert = ref(false)
const racine = ref<HTMLElement | null>(null)
const declencheur = ref<HTMLButtonElement | null>(null)
/** Mois affiché dans le calendrier : [année, mois 1-12]. */
const vue = ref<[number, number]>([2026, 1])
/** Premier jour cliqué, en attente du second. */
const ancre = ref<string | null>(null)
const survol = ref<string | null>(null)
const message = ref('')

function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function joursDansLeMois(y: number, m: number): number {
  return new Date(y, m, 0).getDate()
}

function court(date: string): string {
  const [y, m, d] = date.split('-')
  return `${d}.${m}.${y}`
}

function moisEntier(p: Plage): [number, number] | null {
  const [y, m, d] = p.du.split('-').map(Number)
  if (d !== 1 || p.au !== iso(y, m, joursDansLeMois(y, m))) return null
  return [y, m]
}

function nomDuMois(y: number, m: number): string {
  return `${MOIS[m - 1]} ${y}`
}

const libelle = computed(() => {
  const mois = moisEntier(props.modelValue)
  if (mois) {
    const nom = nomDuMois(...mois)
    return nom.charAt(0).toUpperCase() + nom.slice(1)
  }
  const { du, au } = props.modelValue
  return du === au ? court(du) : `${court(du)} → ${court(au)}`
})

const titreVue = computed(() => {
  const nom = nomDuMois(...vue.value)
  return nom.charAt(0).toUpperCase() + nom.slice(1)
})

/** Cases du mois affiché, précédées de cases vides jusqu'au lundi. */
const cases = computed(() => {
  const [y, m] = vue.value
  const decalage = (new Date(y, m - 1, 1).getDay() + 6) % 7
  const out: (string | null)[] = Array(decalage).fill(null)
  for (let d = 1; d <= joursDansLeMois(y, m); d++) out.push(iso(y, m, d))
  return out
})

/** Plage montrée : celle en cours de choix (ancre + survol), sinon la valeur. */
const plageVisible = computed<Plage>(() => {
  if (ancre.value) {
    const autre = survol.value ?? ancre.value
    return ancre.value <= autre ? { du: ancre.value, au: autre } : { du: autre, au: ancre.value }
  }
  return props.modelValue
})

const peutReculer = computed(() => iso(...vue.value, 1) > props.min)
const peutAvancer = computed(() => iso(...vue.value, joursDansLeMois(...vue.value)) < props.max)

function horsLimites(date: string): boolean {
  return date < props.min || date > props.max
}

function classeJour(date: string): string {
  const { du, au } = plageVisible.value
  const base = 'h-9 w-full rounded text-sm tabular-nums'
  if (horsLimites(date)) return `${base} cursor-not-allowed text-neutral-300`
  if (date === du || date === au) return `${base} bg-blue-600 font-semibold text-white`
  if (date > du && date < au) return `${base} bg-blue-50 text-blue-900 hover:bg-blue-100`
  return `${base} text-neutral-800 hover:bg-neutral-100`
}

function nombreDeMois(du: string, au: string): number {
  const [y1, m1] = du.split('-').map(Number)
  const [y2, m2] = au.split('-').map(Number)
  return (y2 - y1) * 12 + (m2 - m1) + 1
}

function valider(p: Plage) {
  if (nombreDeMois(p.du, p.au) > MAX_MOIS) {
    message.value = `La période est limitée à ${MAX_MOIS} mois.`
    return
  }
  emit('update:modelValue', p)
  fermer()
}

function choisirJour(date: string) {
  if (horsLimites(date)) return
  message.value = ''
  if (!ancre.value) {
    ancre.value = date
    return
  }
  const p = ancre.value <= date ? { du: ancre.value, au: date } : { du: date, au: ancre.value }
  ancre.value = null
  valider(p)
}

function choisirMoisEntier() {
  const [y, m] = vue.value
  ancre.value = null
  message.value = ''
  valider({ du: iso(y, m, 1), au: iso(y, m, joursDansLeMois(y, m)) })
}

function deplacer(pas: number) {
  let [y, m] = vue.value
  m += pas
  if (m < 1) { m = 12; y -= 1 }
  if (m > 12) { m = 1; y += 1 }
  vue.value = [y, m]
}

function ouvrir() {
  const [y, m] = props.modelValue.du.split('-').map(Number)
  vue.value = [y, m]
  ancre.value = null
  survol.value = null
  message.value = ''
  ouvert.value = true
}

function fermer() {
  ouvert.value = false
  ancre.value = null
  declencheur.value?.focus()
}

const consigne = computed(() => (
  ancre.value
    ? `Du ${court(ancre.value)} : cliquez le dernier jour.`
    : 'Cliquez le premier jour, puis le dernier.'
))

function surClicDehors(event: MouseEvent) {
  if (ouvert.value && racine.value && !racine.value.contains(event.target as Node)) {
    ouvert.value = false
    ancre.value = null
  }
}

function surTouche(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !ouvert.value) return
  if (ancre.value) ancre.value = null
  else fermer()
}

watch(() => props.modelValue, () => {
  message.value = ''
})

onMounted(() => {
  document.addEventListener('mousedown', surClicDehors)
  document.addEventListener('keydown', surTouche)
})
onUnmounted(() => {
  document.removeEventListener('mousedown', surClicDehors)
  document.removeEventListener('keydown', surTouche)
})
</script>

<template>
  <div ref="racine" class="relative">
    <button
      ref="declencheur"
      type="button"
      class="mt-1 inline-flex min-w-48 items-center justify-between gap-3 rounded border border-neutral-300 bg-white px-3 py-1.5 text-left text-sm font-medium text-neutral-900 hover:border-neutral-400"
      aria-haspopup="dialog"
      :aria-expanded="ouvert"
      @click="ouvert ? fermer() : ouvrir()"
    >
      <span>{{ libelle }}</span>
      <svg class="h-4 w-4 text-neutral-500" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="3" y="4.5" width="14" height="12" rx="1.5" stroke="currentColor" stroke-width="1.5" />
        <path d="M3 8h14M7 3v3M13 3v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>

    <div
      v-if="ouvert"
      role="dialog"
      aria-label="Choisir la période"
      class="absolute left-0 z-30 mt-2 w-[19rem] max-w-[calc(100vw-2rem)] rounded-lg border border-neutral-200 bg-white p-3 shadow-lg"
    >
      <div class="mb-2 flex items-center justify-between">
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded text-neutral-600 hover:bg-neutral-100 disabled:opacity-30"
          :disabled="!peutReculer"
          aria-label="Mois précédent"
          @click="deplacer(-1)"
        >‹</button>
        <span class="text-sm font-semibold text-neutral-900">{{ titreVue }}</span>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded text-neutral-600 hover:bg-neutral-100 disabled:opacity-30"
          :disabled="!peutAvancer"
          aria-label="Mois suivant"
          @click="deplacer(1)"
        >›</button>
      </div>

      <div class="grid grid-cols-7 gap-0.5 text-center" @mouseleave="survol = null">
        <span v-for="j in JOURS" :key="j" class="pb-1 text-xs font-medium text-neutral-400">{{ j }}</span>
        <template v-for="(date, i) in cases" :key="date ?? `vide-${i}`">
          <span v-if="!date" />
          <button
            v-else
            type="button"
            :class="classeJour(date)"
            :disabled="horsLimites(date)"
            :aria-label="court(date)"
            :aria-pressed="date === plageVisible.du || date === plageVisible.au"
            @click="choisirJour(date)"
            @mouseenter="survol = date"
          >{{ Number(date.slice(8)) }}</button>
        </template>
      </div>

      <button
        type="button"
        class="mt-3 w-full rounded border border-neutral-300 px-3 py-1.5 text-sm text-neutral-800 hover:bg-neutral-50"
        @click="choisirMoisEntier"
      >Tout le mois de {{ nomDuMois(...vue) }}</button>
      <p class="mt-2 text-xs text-neutral-500">{{ consigne }}</p>
      <p v-if="message" class="mt-1 text-xs text-red-600" role="alert">{{ message }}</p>
    </div>
  </div>
</template>
