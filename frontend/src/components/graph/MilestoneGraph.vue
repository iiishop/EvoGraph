<script setup lang="ts">
import { computed, nextTick, watch, shallowRef, onBeforeUnmount } from 'vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { GitBranch, Maximize } from 'lucide-vue-next';
import MilestoneNode from './MilestoneNode.vue';
import GraphEdge from './GraphEdge.vue';
import { layout, edgeId, type LayoutResult } from '../../composables/useGraphLayout';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import { edgeKind, edgeKinds } from '../../lib/edgeKinds';
import { separateBoxes, routeAroundBoxes } from '../../lib/graphGeometry';
import { fitMilestoneBounds, graphBounds, keepMilestoneVisible } from '../../lib/milestoneViewport';
import BaselineMilestoneStatus from './BaselineMilestoneStatus.vue';
import GoalMarker from './GoalMarker.vue';

import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const allMilestones = computed(() => [
  ...(props.project.source_milestones ?? []),
  ...props.project.milestones,
]);
const flowId = `milestones-${props.project.id}`;
const { dimensions, setViewport, getViewport } = useVueFlow(flowId);
const { state, selectNode, perform } = useWorkspace();
const agent = useAgent();
const follows = computed(() => agent.state.follow[props.project.id] !== false);
const computedLayout = shallowRef<LayoutResult>({
  direction: 'RIGHT',
  positions: new Map(),
  routes: new Map(),
});
let layoutGeneration = 0;
const topology = computed(() =>
  JSON.stringify(allMilestones.value.map((m) => [m.id, m.dependencies])),
);
watch(
  topology,
  async () => {
    const generation = ++layoutGeneration;
    try {
      const result = await layout(allMilestones.value);
      if (generation === layoutGeneration) computedLayout.value = result;
    } catch (error) {
      console.error('Graph layout failed', error);
    }
  },
  { immediate: true },
);
const positions = computed(() => computedLayout.value.positions);
const boxes = computed(() =>
  separateBoxes(
    allMilestones.value
      .filter((m) => positions.value.has(m.id))
      .map((m) => ({
        id: m.id,
        ...(m.position ?? positions.value.get(m.id)!),
        width: 236,
        height: 150,
      })),
  ),
);
const displayPositions = computed(
  () => new Map(boxes.value.map((b) => [b.id, { x: b.x, y: b.y }])),
);
function route(source: string, target: string) {
  const a = displayPositions.value.get(source),
    b = displayPositions.value.get(target);
  if (!a || !b) return [];
  if (!allMilestones.value.some((m) => m.position))
    return computedLayout.value.routes.get(edgeId(source, target));
  return computedLayout.value.direction === 'DOWN'
    ? routeAroundBoxes({ x: a.x + 118, y: a.y + 150 }, { x: b.x + 118, y: b.y }, boxes.value)
    : routeAroundBoxes({ x: a.x + 236, y: a.y + 75 }, { x: b.x, y: b.y + 75 }, boxes.value);
}
async function dragged({ node }: { node: { id: string; position: { x: number; y: number } } }) {
  const arranged = separateBoxes(
    boxes.value
      .filter((b) => b.id !== node.id)
      .concat({ id: node.id, ...node.position, width: 236, height: 150 }),
  );
  await perform('graph.positions', {
    project_id: props.project.id,
    positions: Object.fromEntries(arranged.map((b) => [b.id, { x: b.x, y: b.y }])),
  });
}
const nodes = computed(() =>
  allMilestones.value
    .filter((m) => positions.value.has(m.id))
    .map((m) => ({
      id: m.id,
      type: 'milestone',
      position: displayPositions.value.get(m.id)!,
      selected: state.selectedId === m.id,
      data: {
        milestone: m,
        vertical: computedLayout.value.direction === 'DOWN',
        ready: props.project.readiness[m.id]?.safe_to_execute,
        agentFocused: agent.state.projectId === props.project.id && agent.state.focusId === m.id,
        agentActive: agent.state.running,
        updateTick:
          agent.state.projectId === props.project.id ? (agent.state.updates[m.id] ?? 0) : 0,
      },
    })),
);
const edges = computed(() =>
  allMilestones.value.flatMap((m) =>
    m.dependencies.map((dep) => ({
      id: edgeId(dep, m.id),
      source: dep,
      target: m.id,
      sourceHandle: 'out',
      targetHandle: 'in',
      type: 'prerequisite',
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeKind(m.dependency_types?.[dep]).color },
      style: {
        stroke: edgeKind(m.dependency_types?.[dep]).color,
        strokeWidth: state.selectedId === dep || state.selectedId === m.id ? 2.5 : 1.7,
      },
      data: {
        kind: m.dependency_types?.[dep] ?? 'implementation',
        routeKind: allMilestones.value.some((n) => n.position) ? 'waypoints' : 'spline',
        reason: m.dependency_reasons[dep],
        route: route(dep, m.id),
      },
    })),
  ),
);
type CameraMode = 'overview' | 'selected' | 'agent' | 'manual';
let cameraMode: CameraMode = 'overview';
let focusedId = '';
let readableFocus = false;
let frame = 0;
let disposed = false;
let cameraSequence = 0;
let applicationSequence = 0;
let pendingApplication = 0;
const size = computed(() => ({ width: dimensions.value.width, height: dimensions.value.height }));
const overview = computed(() => fitMilestoneBounds(graphBounds(boxes.value), size.value));
const minZoom = computed(() => Math.min(0.25, (overview.value?.zoom ?? 0.02) / 2));
const duration = (milliseconds: number) =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
    ? 0
    : milliseconds;

function applyCamera(milliseconds = 0, sequence = cameraSequence) {
  if (disposed || cameraMode === 'manual' || sequence !== cameraSequence) return;
  const current = getViewport();
  const focused = boxes.value.find((box) => box.id === focusedId);
  // A question or expanded composer can temporarily reduce readable focus.
  // Keep the explicit Locate/Follow target when space returns; a real gesture
  // switches to manual mode above and continues to own its zoom unchanged.
  const readable = focused ? fitMilestoneBounds(focused, size.value, 0.95) : null;
  const restoreReadable = readable && readable.zoom > current.zoom + 0.00001;
  const target =
    cameraMode === 'overview'
      ? overview.value
      : focused
        ? readableFocus || restoreReadable
          ? readable
          : keepMilestoneVisible(focused, size.value, current)
        : null;
  if (!target) return;
  const unchanged =
    Math.abs(target.x - current.x) < 0.01 &&
    Math.abs(target.y - current.y) < 0.01 &&
    Math.abs(target.zoom - current.zoom) < 0.00001;
  if (unchanged && !pendingApplication) {
    readableFocus = false;
    return;
  }
  const application = ++applicationSequence;
  pendingApplication = application;
  void setViewport(target, { duration: unchanged ? 0 : duration(milliseconds) })
    .then((applied) => {
      if (disposed || sequence !== cameraSequence || application !== pendingApplication) return;
      pendingApplication = 0;
      // Resizing mid-animation must retain the readable target rather than
      // adopting its intermediate overview zoom. Only the latest settled
      // application can release that intent.
      if (applied) readableFocus = false;
    })
    .catch(() => {
      if (sequence === cameraSequence && application === pendingApplication) pendingApplication = 0;
    });
}
function scheduleCamera(milliseconds = 0) {
  const sequence = ++cameraSequence;
  cancelAnimationFrame(frame);
  if (disposed || cameraMode === 'manual') return;
  // Inspector/composer updates and Vue Flow measurement settle before the
  // camera uses the actual remaining viewport, not the previous frame's size.
  frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(() => {
      frame = 0;
      applyCamera(milliseconds, sequence);
    });
  });
}
function manual() {
  stopCamera();
  cameraMode = 'manual';
  readableFocus = false;
  agent.freeView(props.project.id);
}
function stopCamera() {
  cameraSequence++;
  cancelAnimationFrame(frame);
  if (pendingApplication) {
    pendingApplication = 0;
    // A zero-duration transform interrupts Vue Flow's in-flight D3 transition
    // without replacing the user's current pan/zoom position.
    void setViewport(getViewport(), { duration: 0 });
  }
}
function fit() {
  agent.freeView(props.project.id);
  cameraMode = 'overview';
  readableFocus = false;
  scheduleCamera(250);
}
function initialized() {
  scheduleCamera();
}
function locate(id: string) {
  if (!allMilestones.value.some((milestone) => milestone.id === id)) return false;
  agent.freeView(props.project.id);
  cameraMode = 'selected';
  focusedId = id;
  readableFocus = true;
  scheduleCamera(220);
  return true;
}
function follow() {
  if (!follows.value || agent.state.projectId !== props.project.id) return;
  if (!allMilestones.value.some((milestone) => milestone.id === agent.state.focusId)) return;
  cameraMode = 'agent';
  focusedId = agent.state.focusId;
  readableFocus = true;
  scheduleCamera(450);
}
function moved({ event }: { event: unknown }) {
  if (event) manual();
}
watch(
  () => state.selectedId,
  (id) => {
    if (id) locate(id);
    else {
      stopCamera();
      cameraMode = 'manual';
      readableFocus = false;
    }
  },
  { flush: 'sync' },
);
watch(
  () => allMilestones.value.map((milestone) => milestone.id).join('\0'),
  () => {
    if (
      state.selectedId &&
      !allMilestones.value.some((milestone) => milestone.id === state.selectedId)
    ) {
      const automatic = cameraMode !== 'manual';
      selectNode(null);
      if (automatic) {
        cameraMode = 'overview';
        scheduleCamera();
      }
    }
  },
  { flush: 'sync' },
);
watch(boxes, () => scheduleCamera());
watch(computedLayout, () => {
  // A focus pulse can precede the graph's remount or the new node's layout.
  // Preserve explicit Follow ownership until the node is actually available.
  if (follows.value && agent.state.projectId === props.project.id) follow();
});
watch(
  () => [size.value.width, size.value.height],
  () => scheduleCamera(),
);
onBeforeUnmount(() => {
  disposed = true;
  cameraSequence++;
  pendingApplication = 0;
  layoutGeneration++;
  cancelAnimationFrame(frame);
});
if (follows.value && agent.state.projectId === props.project.id) follow();
else if (state.selectedId) locate(state.selectedId);
async function reset() {
  await perform('graph.positions', {
    project_id: props.project.id,
    positions: Object.fromEntries(positions.value),
  });
  await nextTick();
  fit();
}
watch(() => agent.state.pulse, follow);

defineExpose({ fit, reset, locate });
</script>
<template>
  <div class="milestone-stage">
    <BaselineMilestoneStatus :project="project" />
    <div class="graph-canvas">
      <GoalMarker :project="project" />
      <VueFlow
        v-if="nodes.length"
        :id="flowId"
        :nodes="nodes"
        :edges="edges"
        :min-zoom="minZoom"
        :max-zoom="1.6"
        :nodes-connectable="false"
        :nodes-draggable="!agent.state.running"
        :delete-key-code="null"
        :fit-view-on-init="false"
        @nodes-initialized="initialized"
        @move-start="moved"
        @node-drag-start="manual"
        @node-click="({ node }) => selectNode(node.id)"
        @pane-click="selectNode(null)"
        @node-drag-stop="dragged"
        ><Background :gap="24" :size="1" pattern-color="#cdd5e4" /><Controls
          :show-interactive="false"
          position="bottom-left"
          @zoom-in="manual"
          @zoom-out="manual"
          ><template #control-fit-view
            ><button
              type="button"
              class="vue-flow__controls-button"
              title="适应画布"
              aria-label="适应画布"
              @click="fit"
            >
              <Maximize :size="16" /></button></template></Controls
        ><template #node-milestone="nodeProps"><MilestoneNode v-bind="nodeProps" /></template>
        <template #edge-prerequisite="edgeProps"><GraphEdge v-bind="edgeProps" /></template>
      </VueFlow>
      <div v-else class="empty-state">
        <span class="empty-icon"><GitBranch :size="32" /></span>
        <h3>下一步演化，从一个目标开始</h3>
        <p>在下方描述你的目标，让 Agent 帮你形成可执行的里程碑。</p>
      </div>
      <div v-if="nodes.length" class="edge-legend">
        <span v-for="kind in edgeKinds" :key="kind.label"
          ><i :style="{ background: kind.color }"></i>{{ kind.label }}</span
        >
      </div>
    </div>
  </div>
</template>

<style scoped>
.milestone-stage {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
}
.milestone-stage > .graph-canvas {
  flex: 1;
  min-height: 240px;
}
</style>
