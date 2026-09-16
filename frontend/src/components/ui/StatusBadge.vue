<script setup lang="ts">
import { statusLabels } from '../../lib/presentation';
defineProps<{ status: string; label?: string }>();
</script>
<template>
  <span class="status-badge" :class="status.toLowerCase()"
    ><i></i>{{ label ?? statusLabels[status] ?? status }}</span
  >
</template>
