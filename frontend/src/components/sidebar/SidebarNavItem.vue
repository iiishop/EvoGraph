<script setup lang="ts">
import type { Component } from 'vue';
defineProps<{
  icon: Component;
  label: string;
  active?: boolean;
  count?: number;
}>();
defineEmits<{ select: [] }>();
</script>
<template>
  <button
    class="nav-item"
    :class="{ active }"
    :aria-current="active ? 'page' : undefined"
    @click="$emit('select')"
  >
    <component :is="icon" :size="18" aria-hidden="true" /><span>{{ label }}</span
    ><span v-if="count !== undefined" class="nav-count">{{ count }}</span>
  </button>
</template>
