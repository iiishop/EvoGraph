<script setup lang="ts">
import { computed } from 'vue';
import { BaseEdge, getBezierPath, type EdgeProps } from '@vue-flow/core';
const props = defineProps<EdgeProps>();
const geometry = computed(() => {
  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty } = props;
  if (props.source === props.target) {
    return [
      `M ${sx} ${sy} C ${sx + 95} ${sy - 160}, ${tx - 95} ${ty - 160}, ${tx} ${ty}`,
      (sx + tx) / 2,
      sy - 120,
    ];
  }
  if (tx < sx) {
    const lane = Number(props.data?.returnLane ?? Math.max(sy, ty) + 170);
    return [
      `M ${sx} ${sy} C ${sx + 75} ${sy}, ${sx + 75} ${lane}, ${sx} ${lane} C ${sx - 100} ${lane}, ${tx + 100} ${lane}, ${tx} ${lane} C ${tx - 75} ${lane}, ${tx - 75} ${ty}, ${tx} ${ty}`,
      (sx + tx) / 2,
      lane,
    ];
  }
  return getBezierPath({
    sourceX: sx,
    sourceY: sy,
    targetX: tx,
    targetY: ty,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
    curvature: 0.42,
  });
});
</script>
<template>
  <g
    ><title>{{ label }}</title
    ><BaseEdge
      :id="id"
      :path="String(geometry[0])"
      :marker-end="markerEnd"
      :style="style"
      :label="label"
      :label-x="Number(geometry[1])"
      :label-y="Number(geometry[2])"
      :label-show-bg="true"
      :label-bg-padding="[7, 4]"
      :label-bg-border-radius="4"
  /></g>
</template>
