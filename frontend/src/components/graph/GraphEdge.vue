<script setup lang="ts">
import { computed } from 'vue';
import { BaseEdge, getBezierPath, type EdgeProps } from '@vue-flow/core';
import { smoothWaypoints, sampleCubicPath } from '../../lib/curves';
const props = defineProps<EdgeProps>();
const directPath = computed(() => {
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
const geometry = computed(() => {
  const samples = sampleCubicPath(directPath.value);
  const blocked = samples.some((p) =>
    (props.data?.obstacles ?? []).some(
      (b: { x: number; y: number; width: number; height: number }) =>
        p.x > b.x + 0.1 &&
        p.x < b.x + b.width - 0.1 &&
        p.y > b.y + 0.1 &&
        p.y < b.y + b.height - 0.1,
    ),
  );
  if (blocked)
    return { path: smoothWaypoints(props.data?.route ?? []), label: props.data?.labelPosition };
  return { path: directPath.value, label: samples[Math.floor(samples.length / 2)] };
});
</script>
<template>
  <g
    ><title>{{ data?.reason ?? label }}</title
    ><BaseEdge
      :id="id"
      :path="geometry.path"
      :marker-end="markerEnd"
      :style="style"
      :label="geometry.label ? label : undefined"
      :label-x="geometry.label?.x"
      :label-y="geometry.label?.y"
      :label-show-bg="true"
      :label-bg-padding="[7, 4]"
      :label-bg-border-radius="4"
      :label-bg-style="{ fill: '#ffffff', stroke: '#d7e0e7', strokeWidth: 1 }"
  /></g>
</template>
