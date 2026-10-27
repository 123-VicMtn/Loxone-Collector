<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppSidebar from '@shared/components/AppSidebar.vue'

const route = useRoute()
const showNav = computed(() => route.path !== '/login')
const menuOpen = ref(false)

watch(() => route.path, (path) => {
  if (path === '/login') menuOpen.value = false
})
</script>

<template>
  <div v-if="showNav" class="min-h-screen bg-white lg:pl-64">
    <AppSidebar v-model:open="menuOpen" />
    <div class="min-w-0" :inert="menuOpen || undefined">
      <RouterView />
    </div>
  </div>
  <RouterView v-else />
</template>
