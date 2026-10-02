<script setup lang="ts">
import { shallowRef, watch, useId, nextTick, ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { Maximize } from 'lucide-vue-next';
import { layoutArchitecture } from '../../lib/layoutArchitecture';
import { routeArchitectureEdge, architectureLabels } from '../../lib/graphGeometry';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import { architectureGroupBounds } from '../../lib/architectureLayout';
import ArchitectureNode from './ArchitectureNode.vue';
import ArchitectureGroup from './ArchitectureGroup.vue';
import GraphEdge from '../graph/GraphEdge.vue';
import { architectureRole } from '../../lib/architectureRoles';
import type { Diagram } from '../../types';
import {
  ARCHITECTURE_MIN_ZOOM,
  architectureVisibleIds,
  validArchitectureViewport,
} from '../../lib/architectureBrowse';
import type { ArchitectureViewport } from '../../lib/architectureBrowse';
const props = withDefaults(
  defineProps<{
    diagram: Diagram;
    query?: string;
    source?: boolean;
    focusedId?: string;
    relation?: string;
    role?: string;
    browseKey?: string;
    initialViewport?: ArchitectureViewport | null;
  }>(),
  {
    query: '',
    source: false,
    focusedId: '',
    relation: 'all',
    role: 'all',
    browseKey: '',
    initialViewport: null,
  },
);
const emit = defineEmits<{
  select: [id: string];
  viewport: [value: { key: string; viewport: ArchitectureViewport }];
}>();
// The owner remounts for another browse context. Never chase our own saved camera updates.
const cameraKey = props.browseKey;
const initialCamera = validArchitectureViewport(props.initialViewport)
  ? { ...props.initialViewport }
  : undefined;
const flowId = `design-${useId()}`;
const { fitView, updateNodeInternals, setCenter, findNode } = useVueFlow(flowId);
const canvas = ref<HTMLElement>();
let viewportMoved = Boolean(initialCamera);
let initialFocus = !initialCamera && Boolean(props.focusedId);
let layoutPending = true;
let pendingFocus: { id: string; sequence: number; generation: number } | null = null;
let disposed = false;
let focusSequence = 0;
let frame = 0;
let resizeObserver: ResizeObserver | undefined;
let previousSize = '';
let initialCameraPending = false;
let initialCameraRetry = false;
// Establish a readable camera once per fresh view, independently of Agent-follow.
// Restored/manual cameras and later resizes or snapshots never restart this choice.
function fitWhenReady() {
  if (initialCameraPending) {
    initialCameraRetry = true;
    return;
  }
  cancelAnimationFrame(frame);
  if (disposed) return;
  frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(async () => {
      if (
        disposed ||
        layoutPending ||
        !canvas.value?.clientWidth ||
        !canvas.value.clientHeight ||
        viewportMoved ||
        props.focusedId
      )
        return;
      const first = nodes.value[0];
      if (!first) return;
      const bounds = [
        ...nodes.value.map((node) => ({ ...node.position, width: 236, height: 150 })),
        ...(props.diagram.groups ?? []).flatMap((group) => {
          const rectangle = architectureGroupBounds(positions.value, group);
          return rectangle ? [rectangle] : [];
        }),
      ];
      const width =
        Math.max(...bounds.map((b) => b.x + b.width)) - Math.min(...bounds.map((b) => b.x));
      const height =
        Math.max(...bounds.map((b) => b.y + b.height)) - Math.min(...bounds.map((b) => b.y));
      const readableFit =
        Math.min(
          canvas.value.clientWidth / (width * 1.32),
          canvas.value.clientHeight / (height * 1.32),
        ) >= 0.65;
      const ticket = generation;
      const sequence = focusSequence;
      initialCameraPending = true;
      try {
        const applied = readableFit
          ? await fitView({ padding: 0.16, duration: 0 })
          : await setCenter(first.position.x + 118, first.position.y + 75, {
              zoom: 0.85,
              duration: 0,
            });
        if (applied && !disposed && ticket === generation && sequence === focusSequence)
          viewportMoved = true;
      } catch {
        // A later initialization/resize may retry; never claim an unapplied camera.
      } finally {
        initialCameraPending = false;
        if (initialCameraRetry) {
          initialCameraRetry = false;
          fitWhenReady();
        }
      }
    });
  });
}
onMounted(() => {
  resizeObserver = new ResizeObserver(([entry]) => {
    if (!entry || entry.contentRect.width <= 0 || entry.contentRect.height <= 0) return;
    const size = `${Math.round(entry.contentRect.width)}:${Math.round(entry.contentRect.height)}`;
    if (size === previousSize) return;
    previousSize = size;
    fitWhenReady();
  });
  if (canvas.value) resizeObserver.observe(canvas.value);
  fitWhenReady();
});
onBeforeUnmount(() => {
  disposed = true;
  focusSequence++;
  generation++;
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect();
});
const positions = shallowRef(new Map<string, { x: number; y: number }>());
const error = ref('');
const agent = useAgent();
const workspace = useWorkspace();
const follows = computed(() => agent.state.follow[workspace.state.project?.id ?? ''] !== false);
function manual() {
  initialFocus = false;
  pendingFocus = null;
  focusSequence++;
  cancelAnimationFrame(frame);
  viewportMoved = true;
  if (workspace.state.project) agent.freeView(workspace.state.project.id);
}
function moved({ event }: { event: unknown }) {
  if (event) manual();
}
function fit() {
  manual();
  void fitView({ padding: 0.2, duration: 0 });
}
function rememberViewport(viewport: ArchitectureViewport) {
  if (!disposed && cameraKey && validArchitectureViewport(viewport))
    emit('viewport', { key: cameraKey, viewport: { ...viewport } });
}
const visible = computed(() =>
  architectureVisibleIds(props.diagram, props.query, props.role, props.focusedId, props.relation),
);
const nodes = computed(() =>
  props.diagram.nodes
    .filter((n) => positions.value.has(n.id))
    .map((n) => ({
      id: n.id,
      type: 'architecture',
      position: positions.value.get(n.id)!,
      zIndex: 2,
      selected: n.id === props.focusedId,
      data: { ...n, source: props.source, dimmed: !visible.value.has(n.id) },
    })),
);
const groupNodes = computed(() =>
  (props.diagram.groups ?? []).flatMap((group) => {
    const bounds = architectureGroupBounds(positions.value, group);
    if (!bounds) return [];
    return [
      {
        id: `group:${group.id}`,
        type: 'architecture-group',
        position: { x: bounds.x, y: bounds.y },
        selectable: false,
        draggable: false,
        zIndex: 1,
        data: {
          ...group,
          width: bounds.width,
          height: bounds.height,
          count: group.member_node_ids.filter((id) => positions.value.has(id)).length,
        },
      },
    ];
  }),
);
const flowNodes = computed(() => [...groupNodes.value, ...nodes.value]);
const obstacles = computed(() => [
  ...[...positions.value].map(([id, p]) => ({ id, ...p, width: 236, height: 150 })),
  ...(props.diagram.groups ?? []).flatMap((g) => {
    const bounds = architectureGroupBounds(positions.value, g);
    return bounds ? [{ id: `header:${g.id}`, ...bounds.header }] : [];
  }),
]);
const edges = computed(() => {
  const routed = props.diagram.edges.map((e, index) => {
    const color = architectureRole(props.diagram.nodes.find((n) => n.id === e.source)?.role).color;
    return {
      id: `${props.diagram.id}-edge-${index}`,
      source: e.source,
      target: e.target,
      sourceHandle: 'out',
      targetHandle: 'in',
      type: 'architecture',
      label: e.label,
      data: {
        obstacles: obstacles.value,
        route: (() => {
          const a = positions.value.get(e.source),
            b = positions.value.get(e.target);
          return a && b
            ? routeArchitectureEdge(
                { x: a.x + 236, y: a.y + 75 },
                { x: b.x, y: b.y + 75 },
                obstacles.value,
              )
            : [];
        })(),
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
      style: {
        stroke: color,
        strokeWidth: 1.8,
        opacity: visible.value.has(e.source) && visible.value.has(e.target) ? 0.85 : 0.1,
      },
    };
  });
  const labels = architectureLabels(
    routed.map((e) => ({ route: e.data.route, label: e.label ?? '' })),
    obstacles.value,
  );
  return routed.map((e, i) => ({ ...e, data: { ...e.data, labelPosition: labels[i] } }));
});
let generation = 0;
watch(
  () => props.diagram,
  async (diagram) => {
    const ticket = ++generation;
    layoutPending = true;
    error.value = '';
    try {
      const result = await layoutArchitecture(diagram);
      if (ticket !== generation) return;
      positions.value = result;
      await nextTick();
      if (ticket !== generation || disposed) return;
      updateNodeInternals(diagram.nodes.map((n) => n.id));
      await nextTick();
      if (ticket !== generation || disposed) return;
      layoutPending = false;
      if (pendingFocus) applyFocus(pendingFocus);
      if (initialFocus) {
        initialFocus = false;
        locate(props.focusedId);
      } else fitWhenReady();
    } catch {
      if (ticket !== generation || disposed) return;
      error.value = '布局未完成，请切换版本后重试';
    }
  },
  { immediate: true },
);
function applyFocus(intent: { id: string; sequence: number; generation: number }) {
  if (
    disposed ||
    intent.sequence !== focusSequence ||
    intent.generation !== generation ||
    intent.id !== props.focusedId
  )
    return;
  if (layoutPending) return;
  pendingFocus = null;
  const node = findNode(intent.id);
  if (node) {
    viewportMoved = true;
    void setCenter(node.position.x + 118, node.position.y + 75, { zoom: 0.95, duration: 0 });
  }
}
function locate(id: string) {
  const intent = { id, sequence: ++focusSequence, generation };
  initialFocus = false;
  pendingFocus = intent;
  void nextTick().then(() => applyFocus(intent));
}
watch(() => props.focusedId, locate, { flush: 'sync' });
watch(
  () => agent.state.pulse,
  () => {
    if (follows.value) {
      focusSequence++;
      void fitView({ padding: 0.2, duration: 0 });
    }
  },
);
defineExpose({ fit, reset: fit, locate });
</script>
<template>
  <div ref="canvas" class="design-diagram">
    <p v-if="error" role="alert">{{ error }}</p>
    <VueFlow
      v-else-if="flowNodes.length"
      :id="flowId"
      :default-viewport="initialCamera"
      :nodes="flowNodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :delete-key-code="null"
      :min-zoom="ARCHITECTURE_MIN_ZOOM"
      :max-zoom="1.8"
      @nodes-initialized="fitWhenReady"
      @move-start="moved"
      @viewport-change="rememberViewport"
      @node-click="({ node }) => node.type === 'architecture' && emit('select', node.id)"
    >
      <Background :gap="24" pattern-color="#d3ddea" /><Controls
        :show-interactive="false"
        @zoom-in="manual"
        @zoom-out="manual"
      >
        <template #control-fit-view>
          <button
            type="button"
            class="vue-flow__controls-button"
            title="适应画布"
            aria-label="适应画布"
            @click="fit"
          >
            <Maximize :size="16" />
          </button>
        </template>
      </Controls>
      <template #node-architecture-group="nodeProps"
        ><ArchitectureGroup v-bind="nodeProps"
      /></template>
      <template #node-architecture="nodeProps"><ArchitectureNode v-bind="nodeProps" /></template>
      <template #edge-architecture="edgeProps"><GraphEdge v-bind="edgeProps" /></template>
    </VueFlow>
  </div>
</template>
