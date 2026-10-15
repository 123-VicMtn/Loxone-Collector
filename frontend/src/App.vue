<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppNavbar from '@shared/components/AppNavbar.vue'

const route = useRoute()
const showNavbar = computed(() => route.path !== '/login')
</script>

<template>
  <AppNavbar v-if="showNavbar" />
  <RouterView />
</template>
