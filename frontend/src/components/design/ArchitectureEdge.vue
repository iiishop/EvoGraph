<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { BaseEdge, type EdgeProps } from '@vue-flow/core';
import { roundedArchitecturePath, anchorArchitectureRoute } from '../../lib/architectureRouting';
const props = defineProps<EdgeProps>();
type PreviewRelation = { index: number; description: string };
// Every relation keeps its entire reserved path, including any shared trunk.
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
const description = computed(() => props.data?.description || props.label);
const peers = computed<PreviewRelation[]>(() => props.data?.previewPeers ?? []);
const tooltipDescription = computed(
  () =>
    peers.value.find((peer) => peer.index === props.data?.previewIndex)?.description ??
    description.value,
);
const edgeElement = ref<SVGGElement>();
const tooltipElement = ref<HTMLElement>();
const tooltip = ref<{ x: number; y: number } | null>(null);
let closeTimer: ReturnType<typeof setTimeout> | undefined;
let hovering = false;
let focused = false;
function cancelClose() {
  clearTimeout(closeTimer);
  closeTimer = undefined;
}
function preview(index: number) {
  if (props.data?.previewEnabled === false) return;
  props.data?.onPreview?.(index);
}
function reveal(event: MouseEvent | FocusEvent | KeyboardEvent) {
  if (props.data?.previewEnabled === false) return;
  cancelClose();
  const bounds = edgeElement.value?.getBoundingClientRect();
  const x = 'clientX' in event ? event.clientX : (bounds?.left ?? 0) + (bounds?.width ?? 0) / 2;
  const y = 'clientY' in event ? event.clientY : (bounds?.top ?? 0) + (bounds?.height ?? 0) / 2;
  tooltip.value = {
    x: Math.max(8, Math.min(x + 12, window.innerWidth - 368)),
    y: Math.max(8, Math.min(y + 14, window.innerHeight - (peers.value.length > 1 ? 320 : 128))),
  };
  preview(props.data?.index);
}
function enter(event: MouseEvent) {
  hovering = true;
  reveal(event);
}
function clearPreview() {
  cancelClose();
  tooltip.value = null;
  hovering = false;
  focused = false;
  props.data?.onPreview?.(null);
}
function conceal() {
  if (hovering || focused) return;
  cancelClose();
  // A short bridge lets the pointer reach the shared-port relation picker.
  if (peers.value.length > 1) closeTimer = setTimeout(clearPreview, 160);
  else clearPreview();
}
function leave() {
  hovering = false;
  conceal();
}
function focus(event: FocusEvent) {
  focused = true;
  reveal(event);
}
function blur(event: FocusEvent) {
  const target = event.relatedTarget as Node | null;
  if (target && (edgeElement.value?.contains(target) || tooltipElement.value?.contains(target)))
    return;
  focused = false;
  conceal();
}
function retain() {
  hovering = true;
  cancelClose();
}
function retainFocus() {
  focused = true;
  cancelClose();
}
function cycle(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault();
    clearPreview();
    return;
  }
  if (peers.value.length < 2 || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  if (!tooltip.value) reveal(event);
  const current = peers.value.findIndex((peer) => peer.index === props.data?.previewIndex);
  const offset = event.key === 'ArrowRight' ? 1 : -1;
  preview(
    peers.value[(Math.max(0, current) + offset + peers.value.length) % peers.value.length].index,
  );
}
watch([() => props.id, () => props.data?.previewEnabled], clearPreview);
watch(
  () => props.data?.previewOwned,
  (owned) => {
    if (owned === false && tooltip.value) clearPreview();
  },
);
onBeforeUnmount(clearPreview);
</script>
<template>
  <g
    ref="edgeElement"
    class="architecture-edge"
    :class="{ 'is-related': data?.related, 'is-edge-preview': data?.previewed }"
    tabindex="0"
    role="group"
    :aria-label="description"
    :aria-describedby="tooltip ? `${id}-preview` : undefined"
    :aria-keyshortcuts="peers.length > 1 ? 'ArrowLeft ArrowRight Escape' : 'Escape'"
    @mouseenter="enter"
    @mouseleave="leave"
    @focus="focus"
    @blur="blur"
    @keydown="cycle"
  >
    <title>{{ description }}</title>
    <BaseEdge
      :id="id"
      :path="path"
      :marker-end="markerEnd"
      :style="style"
      :interaction-width="18"
    />
    <g v-if="labelBox" class="architecture-edge-label" :style="{ opacity: style?.opacity ?? 1 }">
      <title>{{ description }}</title>
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
        :id="`${id}-preview`"
        ref="tooltipElement"
        class="architecture-edge-tooltip"
        :role="peers.length > 1 ? 'dialog' : 'tooltip'"
        :aria-label="peers.length > 1 ? '共享端口的关系' : undefined"
        :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }"
        @mouseenter="retain"
        @mouseleave="leave"
        @focusin="retainFocus"
        @focusout="blur"
        @keydown="cycle"
      >
        <div>{{ tooltipDescription }}</div>
        <template v-if="peers.length > 1">
          <small>共享端口 · {{ peers.length }} 条关系 · ← → 切换</small>
          <div class="architecture-edge-peers">
            <button
              v-for="peer in peers"
              :key="peer.index"
              type="button"
              :class="{ active: peer.index === data?.previewIndex }"
              :aria-pressed="peer.index === data?.previewIndex"
              @mouseenter="preview(peer.index)"
              @focus="preview(peer.index)"
              @click="preview(peer.index)"
            >
              {{ peer.description }}
            </button>
          </div>
        </template>
      </div>
    </Teleport>
  </foreignObject>
</template>
<style scoped>
.architecture-edge {
  outline: none;
}
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
.is-related .architecture-edge-label rect,
.is-edge-preview .architecture-edge-label rect {
  stroke: #77aa9b;
}
.is-related .architecture-edge-label text,
.is-edge-preview .architecture-edge-label text {
  fill: #264c42;
}
.architecture-edge-tooltip {
  position: fixed;
  z-index: 1000;
  max-width: min(360px, calc(100vw - 16px));
  max-height: min(300px, calc(100vh - 16px));
  overflow: auto;
  padding: 8px 10px;
  border: 1px solid #bacdc7;
  border-radius: 7px;
  background: #fff;
  color: #264c42;
  box-shadow: 0 3px 12px #20324b18;
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  pointer-events: auto;
}
.architecture-edge-tooltip small {
  display: block;
  margin: 7px 0 3px;
  color: #657b86;
  font-size: 10px;
}
.architecture-edge-peers {
  display: grid;
  gap: 3px;
}
.architecture-edge-peers button {
  width: 100%;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 5px 6px;
  background: #f5f8f7;
  color: #536b77;
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}
.architecture-edge-peers button.active,
.architecture-edge-peers button:focus-visible {
  border-color: #77aa9b;
  background: #eaf4ef;
  color: #264c42;
  outline: none;
}
</style>
