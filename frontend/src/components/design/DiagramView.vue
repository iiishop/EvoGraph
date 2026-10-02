<script setup lang="ts">
import { shallowRef, watch, useId, nextTick, ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { Maximize } from 'lucide-vue-next';
import { layoutArchitecture } from '../../lib/layoutArchitecture';
import {
  routeArchitectureAsync,
  placeArchitectureLabels,
  architectureDrawingBounds,
} from '../../lib/architectureRouting';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import { architectureGroupBounds } from '../../lib/architectureLayout';
import ArchitectureNode from './ArchitectureNode.vue';
import ArchitectureGroup from './ArchitectureGroup.vue';
import ArchitectureEdge from './ArchitectureEdge.vue';
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
const displayDiagram = shallowRef(props.diagram);
const arranging = ref(true);
const routing = shallowRef<NonNullable<Awaited<ReturnType<typeof routeArchitectureAsync>>>>({
  routes: [],
  ports: new Map(),
});
const cameraKey = props.browseKey;
const initialCamera = validArchitectureViewport(props.initialViewport)
  ? { ...props.initialViewport }
  : undefined;
const flowId = `design-${useId()}`;
const { fitView, fitBounds, updateNodeInternals, setCenter, findNode } = useVueFlow(flowId);
const canvas = ref<HTMLElement>();
let viewportMoved = Boolean(initialCamera);
let initialFocus = !initialCamera && Boolean(props.focusedId);
let layoutPending = true;
let pendingFit = false;
let pendingFocus: { id: string; sequence: number; generation: number } | null = null;
let disposed = false;
let focusSequence = 0;
let frame = 0;
let resizeObserver: ResizeObserver | undefined;
let previousSize = '';
let initialCameraPending = false;
let initialCameraRetry = false;
const labelMeasure = shallowRef<((text: string) => number) | undefined>();
function fitDrawing(padding: number) {
  return drawingBounds.value
    ? fitBounds(drawingBounds.value, { padding, duration: 0 })
    : fitView({ padding, duration: 0 });
}
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
      const { width, height } = drawingBounds.value ?? { width: 236, height: 150 };
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
          ? await fitDrawing(0.16)
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
  if (typeof document !== 'undefined' && typeof CanvasRenderingContext2D !== 'undefined') {
    const context = document.createElement('canvas').getContext('2d');
    if (context) {
      context.font = `11px ${canvas.value ? getComputedStyle(canvas.value).fontFamily : 'sans-serif'}`;
      labelMeasure.value = (text) => context.measureText(text).width;
    }
  }
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
  pendingFit = false;
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
  if (layoutPending) pendingFit = true;
  else void fitDrawing(0.2);
}
function rememberViewport(viewport: ArchitectureViewport) {
  if (!disposed && cameraKey && validArchitectureViewport(viewport))
    emit('viewport', { key: cameraKey, viewport: { ...viewport } });
}
const visible = computed(() =>
  architectureVisibleIds(
    displayDiagram.value,
    props.query,
    props.role,
    props.focusedId,
    props.relation,
  ),
);
const nodes = computed(() =>
  displayDiagram.value.nodes
    .filter((n) => positions.value.has(n.id))
    .map((n) => ({
      id: n.id,
      type: 'architecture',
      position: positions.value.get(n.id)!,
      zIndex: 2,
      selected: n.id === props.focusedId,
      data: {
        ...n,
        source: props.source,
        dimmed: !visible.value.has(n.id),
        ports: routing.value.ports.get(n.id),
      },
    })),
);
const groupNodes = computed(() =>
  (displayDiagram.value.groups ?? []).flatMap((group) => {
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
const componentBoxes = computed(() =>
  [...positions.value].map(([id, p]) => ({ id, ...p, width: 236, height: 150 })),
);
const groupHeaders = computed(() =>
  (displayDiagram.value.groups ?? []).flatMap((g) => {
    const bounds = architectureGroupBounds(positions.value, g);
    return bounds ? [{ id: `header:${g.id}`, ...bounds.header }] : [];
  }),
);
const obstacles = computed(() => [...componentBoxes.value, ...groupHeaders.value]);
const relatedEdge = (source: string, target: string) =>
  Boolean(props.focusedId && (source === props.focusedId || target === props.focusedId));
const labels = computed(() =>
  placeArchitectureLabels(
    displayDiagram.value.edges.map((edge, index) => ({
      route: routing.value.routes[index]?.route ?? [],
      label: edge.label,
      priority: relatedEdge(edge.source, edge.target) ? 1 : 0,
    })),
    obstacles.value,
    labelMeasure.value,
  ),
);
const drawingBounds = computed(() =>
  architectureDrawingBounds(
    [
      ...obstacles.value,
      ...groupNodes.value.map((group) => ({
        id: group.id,
        ...group.position,
        width: group.data.width,
        height: group.data.height,
      })),
    ],
    routing.value.routes,
    labels.value,
  ),
);
const edges = computed(() =>
  displayDiagram.value.edges.map((edge, index) => {
    const color = architectureRole(
      displayDiagram.value.nodes.find((n) => n.id === edge.source)?.role,
    ).color;
    const related = relatedEdge(edge.source, edge.target);
    const visibleEdge = visible.value.has(edge.source) && visible.value.has(edge.target);
    const description = `${displayDiagram.value.nodes.find((n) => n.id === edge.source)?.label ?? edge.source} → ${displayDiagram.value.nodes.find((n) => n.id === edge.target)?.label ?? edge.target}：${edge.label}`;
    return {
      id: `${displayDiagram.value.id}-edge-${index}`,
      source: edge.source,
      target: edge.target,
      sourceHandle: routing.value.routes[index]?.sourceHandle ?? `out:${index}`,
      targetHandle: routing.value.routes[index]?.targetHandle ?? `in:${index}`,
      type: 'architecture',
      label: edge.label,
      ariaLabel: description,
      zIndex: related ? 4 : 0,
      data: {
        route: routing.value.routes[index]?.route ?? [],
        labelPosition: labels.value[index],
        related,
        description,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      style: {
        stroke: color,
        strokeWidth: related ? 2.3 : 1.6,
        opacity: !visibleEdge ? 0.08 : props.focusedId ? (related ? 1 : 0.16) : 0.78,
      },
    };
  }),
);
let generation = 0;
watch(
  () => props.diagram,
  async (diagram) => {
    const ticket = ++generation;
    layoutPending = true;
    arranging.value = true;
    error.value = '';
    try {
      const result = await layoutArchitecture(diagram);
      if (ticket !== generation || disposed) return;
      const boxes = [...result].map(([id, point]) => ({ id, ...point, width: 236, height: 150 }));
      const headers = (diagram.groups ?? []).flatMap((group) => {
        const bounds = architectureGroupBounds(result, group);
        return bounds ? [{ id: `header:${group.id}`, ...bounds.header }] : [];
      });
      const plan = await routeArchitectureAsync(
        diagram,
        boxes,
        headers,
        () => ticket !== generation || disposed,
      );
      if (!plan || ticket !== generation || disposed) return;
      routing.value = plan;
      positions.value = result;
      displayDiagram.value = diagram;
      await nextTick();
      if (ticket !== generation || disposed) return;
      updateNodeInternals(diagram.nodes.map((n) => n.id));
      await nextTick();
      if (ticket !== generation || disposed) return;
      layoutPending = false;
      arranging.value = false;
      if (pendingFit) {
        pendingFit = false;
        void fitDrawing(0.2);
      } else if (pendingFocus) applyFocus(pendingFocus);
      if (initialFocus) {
        initialFocus = false;
        locate(props.focusedId);
      } else fitWhenReady();
    } catch {
      if (ticket !== generation || disposed) return;
      arranging.value = false;
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
  pendingFit = false;
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
      void fitDrawing(0.2);
    }
  },
);
defineExpose({ fit, reset: fit, locate });
</script>
<template>
  <div ref="canvas" class="design-diagram">
    <p v-if="arranging" class="architecture-arranging" role="status">正在整理架构连线…</p>
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
      <template #edge-architecture="edgeProps"><ArchitectureEdge v-bind="edgeProps" /></template>
    </VueFlow>
  </div>
</template>

<style scoped>
.architecture-arranging {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 5;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fffffff0;
  color: #657b86;
  font-size: 12px;
  pointer-events: none;
}
</style>
