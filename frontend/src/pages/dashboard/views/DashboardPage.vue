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
import RelevesTab from '../tabs/RelevesTab.vue'
import ApercuTab from '../tabs/ApercuTab.vue'
import { useExplorerSelection } from '../composables/useExplorerSelection'

type TabKey = 'apercu' | 'explorer' | 'releves'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'apercu', label: 'Aperçu' },
  { key: 'releves', label: 'Relevés' },
  { key: 'explorer', label: 'Mode avancé' },
]

const activeTab = ref<TabKey>('apercu')
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
  <div class="flex w-full flex-col gap-6 px-4 py-6" :class="activeTab === 'explorer' ? 'lg:flex-row' : ''">
    <aside v-if="activeTab === 'explorer'" class="lg:w-64 lg:shrink-0">
      <h1 class="text-xl font-bold text-neutral-900">Loxone</h1>
      <p class="mb-4 text-sm text-neutral-500">Capteurs suivis</p>
      <div class="mb-4 flex flex-col gap-1 text-sm">
        <router-link
          v-if="authState.role === 'admin'"
          to="/admin"
          class="text-blue-600 hover:underline"
          title="Corriger la classification des capteurs"
        >Classification</router-link>
        <router-link to="/decompte" class="text-blue-600 hover:underline" title="Décompte de charges mensuel par zone">Décompte de charges</router-link>
      </div>
      <AuthStatus class="mb-4" />
      <Sidebar :is-selected="isSelected" :on-toggle="onToggle" />
    </aside>

    <main class="min-w-0 w-full flex-1">
      <div v-if="activeTab !== 'explorer'" class="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 class="text-xl font-bold text-neutral-900">Loxone</h1>
        <router-link
          v-if="authState.role === 'admin'"
          to="/admin"
          class="text-sm text-blue-600 hover:underline"
        >Classification</router-link>
        <router-link to="/decompte" class="text-sm text-blue-600 hover:underline">Décompte de charges</router-link>
        <AuthStatus class="sm:ml-auto" />
      </div>
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

      <section v-show="activeTab === 'apercu'">
        <ApercuTab />
      </section>
      <section v-show="activeTab === 'explorer'">
        <ExplorerTab :selected="selected" @clear="clearSelection" />
      </section>
      <section v-show="activeTab === 'releves'">
        <RelevesTab />
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
