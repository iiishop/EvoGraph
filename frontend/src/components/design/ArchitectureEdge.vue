<script setup lang="ts">
import { computed, ref } from 'vue';
import { BaseEdge, type EdgeProps } from '@vue-flow/core';
import { roundedArchitecturePath, anchorArchitectureRoute } from '../../lib/architectureRouting';
const props = defineProps<EdgeProps>();
// Architecture always renders the reserved route. Substituting a free Bézier
// here would invalidate lane/label collision checks and merge opposite edges.
const path = computed(() =>
  roundedArchitecturePath(
    anchorArchitectureRoute(
      props.data?.route ?? [],
      { x: props.sourceX, y: props.sourceY },
      { x: props.targetX, y: props.targetY },
    ),
  ),
);
const labelBox = computed(() => props.data?.labelPosition);
const tooltip = ref<{ x: number; y: number } | null>(null);
function reveal(event: MouseEvent) {
  if (!props.label) return;
  tooltip.value = {
    x: Math.max(8, Math.min(event.clientX + 12, window.innerWidth - 368)),
    y: Math.max(8, Math.min(event.clientY + 14, window.innerHeight - 128)),
  };
}
</script>
<template>
  <g
    class="architecture-edge"
    :class="{ 'is-related': data?.related }"
    @mouseenter="reveal"
    @mouseleave="tooltip = null"
  >
    <title>{{ data?.description || label }}</title>
    <BaseEdge
      :id="id"
      :path="path"
      :marker-end="markerEnd"
      :style="style"
      :interaction-width="18"
    />
    <g v-if="labelBox" class="architecture-edge-label" :style="{ opacity: style?.opacity ?? 1 }">
      <title>{{ label }}</title>
      <rect
        :x="labelBox.x - labelBox.width / 2"
        :y="labelBox.y - labelBox.height / 2"
        :width="labelBox.width"
        :height="labelBox.height"
        rx="5"
      />
      <text :x="labelBox.x" :y="labelBox.y" text-anchor="middle" dominant-baseline="central">{{
        labelBox.text
      }}</text>
    </g>
  </g>
  <foreignObject width="0" height="0">
    <Teleport to="body">
      <div
        v-if="tooltip"
        class="architecture-edge-tooltip"
        role="tooltip"
        :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }"
      >
        {{ label }}
      </div>
    </Teleport>
  </foreignObject>
</template>
<style scoped>
.architecture-edge :deep(.vue-flow__edge-path) {
  stroke-linecap: round;
  stroke-linejoin: round;
}
.architecture-edge-label {
  pointer-events: all;
  cursor: help;
}
.architecture-edge-label rect {
  fill: #ffffff;
  fill-opacity: 0.97;
  stroke: #dbe5e9;
  stroke-width: 1;
}
.architecture-edge-label text {
  font-size: 11px;
  fill: #536b77;
}
.is-related .architecture-edge-label rect {
  stroke: #77aa9b;
}
.is-related .architecture-edge-label text {
  fill: #264c42;
}
.architecture-edge-tooltip {
  position: fixed;
  z-index: 1000;
  max-width: min(360px, calc(100vw - 16px));
  padding: 8px 10px;
  border: 1px solid #bacdc7;
  border-radius: 7px;
  background: #fff;
  color: #264c42;
  box-shadow: 0 3px 12px #20324b18;
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  pointer-events: none;
}
</style>
