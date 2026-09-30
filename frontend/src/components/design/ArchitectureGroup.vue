<script setup lang="ts">
defineProps<{
  data: {
    label: string;
    description: string;
    kind: string;
    width: number;
    height: number;
    count: number;
  };
}>();
</script>
<template>
  <div
    class="architecture-group"
    :class="`group-${data.kind}`"
    :style="{ width: `${data.width}px`, height: `${data.height}px` }"
    aria-hidden="true"
  >
    <div class="architecture-group-heading">
      <span>{{ data.label }}</span>
      <small>{{ data.count }} 个组件</small>
    </div>
    <p v-if="data.description">{{ data.description }}</p>
  </div>
</template>
