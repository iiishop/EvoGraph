<script setup lang="ts">
import { computed } from 'vue';
import { BaseEdge, getBezierPath, type EdgeProps } from '@vue-flow/core';
const props = defineProps<EdgeProps>();
const path = computed(() => {
  return props.targetX - props.sourceX > 150 && Math.abs(props.targetY - props.sourceY) > 25
    ? `M ${props.sourceX} ${props.sourceY} C ${props.sourceX + 38} ${props.sourceY}, ${props.sourceX + 24} ${props.targetY}, ${props.sourceX + 64} ${props.targetY} C ${props.targetX - 70} ${props.targetY}, ${props.targetX - 32} ${props.targetY}, ${props.targetX} ${props.targetY}`
    : getBezierPath({
        sourceX: props.sourceX,
        sourceY: props.sourceY,
        sourcePosition: props.sourcePosition,
        targetX: props.targetX,
        targetY: props.targetY,
        targetPosition: props.targetPosition,
        curvature: 0.32,
      })[0];
});
</script>
<template>
  <g
    ><title>{{ data?.reason }}</title
    ><BaseEdge
      :path="path"
      :marker-end="markerEnd"
      :style="style"
      :label="label"
      :label-x="(sourceX + targetX) / 2"
      :label-y="(sourceY + targetY) / 2"
  /></g>
</template>
