<script setup lang="ts">
/**
 * Page / -- port Vue de templates/index.html + static/js/main.js.
 * La sidebar reste visible en permanence (colonne de gauche), seul le
 * contenu de droite change avec l'onglet actif -- voir
 * composables/useExplorerSelection pour pourquoi la sélection Explorer vit
 * ici plutôt que dans l'onglet ou dans la sidebar.
 *
 * Migration en plusieurs commits (voir CLAUDE.md, "pages/dashboard/") --
 * les 3 onglets sont désormais fonctionnels.
 */

import { ref } from 'vue'
import { useHealthFooter } from '@shared/composables/useHealthFooter'
import { authState } from '@shared/auth'
import AuthStatus from '@shared/components/AuthStatus.vue'
import Sidebar from '../components/Sidebar.vue'
import ExplorerTab from '../tabs/ExplorerTab.vue'
import EnergyTab from '../tabs/EnergyTab.vue'
import ZoneTab from '../tabs/ZoneTab.vue'
import { useExplorerSelection } from '../composables/useExplorerSelection'

type TabKey = 'explorer' | 'energie' | 'zone'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'energie', label: 'Énergie' },
  { key: 'zone', label: 'Consommations par zone' },
  { key: 'explorer', label: 'Mode avancé' },
]

const activeTab = ref<TabKey>('energie')
const advancedPrompt = ref(false)
const { text: healthText } = useHealthFooter()
const { selected, isSelected, onToggle, clearSelection } = useExplorerSelection()

function selectTab(key: TabKey) {
  if (key === 'explorer' && activeTab.value !== 'explorer') {
    advancedPrompt.value = true
    return
  }
  activeTab.value = key
}

function confirmAdvanced() {
  advancedPrompt.value = false
  activeTab.value = 'explorer'
}
</script>

<template>
  <div class="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 lg:flex-row">
    <aside class="lg:w-64 lg:shrink-0">
      <h1 class="text-xl font-bold text-neutral-900">Loxone</h1>
      <p v-if="activeTab === 'explorer'" class="mb-4 text-sm text-neutral-500">Capteurs suivis</p>
      <div class="mb-4 flex flex-col gap-1 text-sm" :class="activeTab === 'explorer' ? '' : 'mt-4'">
        <router-link
          v-if="authState.role === 'admin'"
          to="/admin"
          class="text-blue-600 hover:underline"
          title="Corriger la classification des capteurs"
        >Classification</router-link>
        <router-link to="/decompte" class="text-blue-600 hover:underline" title="Décompte de charges mensuel par zone">Décompte de charges</router-link>
      </div>
      <AuthStatus class="mb-4" />
      <Sidebar v-if="activeTab === 'explorer'" :is-selected="isSelected" :on-toggle="onToggle" />
    </aside>

    <main class="min-w-0 flex-1">
      <div class="mb-4 flex gap-1 border-b border-neutral-200" role="tablist">
        <button
          v-for="t in TABS"
          :key="t.key"
          type="button"
          role="tab"
          :aria-selected="activeTab === t.key"
          :class="[
            'border-b-2 px-3 py-2 text-sm font-medium',
            activeTab === t.key ? 'border-blue-600 text-blue-600' : 'border-transparent text-neutral-500 hover:text-neutral-800',
          ]"
          @click="selectTab(t.key)"
        >{{ t.label }}</button>
      </div>

      <section v-show="activeTab === 'explorer'">
        <ExplorerTab :selected="selected" @clear="clearSelection" />
      </section>
      <section v-show="activeTab === 'energie'">
        <EnergyTab />
      </section>
      <section v-show="activeTab === 'zone'">
        <ZoneTab />
      </section>

      <footer class="mt-8 whitespace-pre-line border-t border-neutral-200 pt-4 text-xs text-neutral-400">
        {{ healthText }}
      </footer>
    </main>

    <div
      v-if="advancedPrompt"
      class="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="advanced-mode-title"
    >
      <div class="w-full max-w-md rounded-lg bg-white p-6 shadow-lg">
        <h2 id="advanced-mode-title" class="text-lg font-semibold text-neutral-900">Mode avancé</h2>
        <p class="mt-2 text-sm text-neutral-600">
          Cet onglet affiche les séries brutes, capteur par capteur. Il ne calcule
          pas les consommations facturables. Continuer ?
        </p>
        <div class="mt-6 flex justify-end gap-2">
          <button
            type="button"
            class="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50"
            @click="advancedPrompt = false"
          >Annuler</button>
          <button
            type="button"
            class="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            @click="confirmAdvanced"
          >Accéder</button>
        </div>
      </div>
    </div>
  </div>
</template>
