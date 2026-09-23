<script setup lang="ts">
/**
 * Page / -- port Vue de templates/index.html + static/js/main.js.
 * La sidebar reste visible en permanence (colonne de gauche), seul le
 * contenu de droite change avec l'onglet actif -- voir
 * composables/useExplorerSelection pour pourquoi la sélection Explorer vit
 * ici plutôt que dans l'onglet ou dans la sidebar.
 *
 * Migration en plusieurs commits (voir CLAUDE.md, "pages/dashboard/") :
 * Explorer et Énergie sont fonctionnels, Consommations par zone arrive
 * dans une prochaine étape.
 */

import { ref } from 'vue'
import { useHealthFooter } from '@shared/composables/useHealthFooter'
import Sidebar from '../components/Sidebar.vue'
import ExplorerTab from '../tabs/ExplorerTab.vue'
import EnergyTab from '../tabs/EnergyTab.vue'
import { useExplorerSelection } from '../composables/useExplorerSelection'

type TabKey = 'explorer' | 'energie' | 'zone'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'explorer', label: 'Explorer' },
  { key: 'energie', label: 'Énergie' },
  { key: 'zone', label: 'Consommations par zone' },
]

const activeTab = ref<TabKey>('explorer')
const { text: healthText } = useHealthFooter()
const { selected, isSelected, onToggle, clearSelection } = useExplorerSelection()
</script>

<template>
  <div class="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 lg:flex-row">
    <aside class="lg:w-64 lg:shrink-0">
      <h1 class="text-xl font-bold text-neutral-900">Loxone</h1>
      <p class="mb-4 text-sm text-neutral-500">Capteurs suivis</p>
      <div class="mb-4 flex flex-col gap-1 text-sm">
        <a href="/admin" class="text-blue-600 hover:underline" title="Corriger la classification des capteurs">⚙ Classification</a>
        <a href="/decompte" class="text-blue-600 hover:underline" title="Décompte de charges mensuel par zone">🧾 Décompte de charges</a>
      </div>
      <Sidebar :is-selected="isSelected" :on-toggle="onToggle" />
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
          @click="activeTab = t.key"
        >{{ t.label }}</button>
      </div>

      <section v-show="activeTab === 'explorer'">
        <ExplorerTab :selected="selected" @clear="clearSelection" />
      </section>
      <section v-show="activeTab === 'energie'">
        <EnergyTab />
      </section>
      <section v-if="activeTab === 'zone'" class="text-sm text-neutral-500">
        Onglet « Consommations par zone » en cours de migration -- disponible
        sur l'ancien dashboard en attendant.
      </section>

      <footer class="mt-8 whitespace-pre-line border-t border-neutral-200 pt-4 text-xs text-neutral-400">
        {{ healthText }}
      </footer>
    </main>
  </div>
</template>
