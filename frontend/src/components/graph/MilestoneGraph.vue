<script setup lang="ts">
import { computed, nextTick, watch, shallowRef } from 'vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { GitBranch } from 'lucide-vue-next';
import MilestoneNode from './MilestoneNode.vue';
import PrerequisiteEdge from './PrerequisiteEdge.vue';
import { layout, edgeId, type LayoutResult } from '../../composables/useGraphLayout';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import { edgeKind, edgeKinds } from '../../lib/edgeKinds';
import { separateBoxes, routeAroundBoxes } from '../../lib/graphGeometry';
import BaselineMilestoneStatus from './BaselineMilestoneStatus.vue';
import GoalMarker from './GoalMarker.vue';
import FollowAgentButton from './FollowAgentButton.vue';

import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const allMilestones = computed(() => [
  ...(props.project.source_milestones ?? []),
  ...props.project.milestones,
]);
const flowId = `milestones-${props.project.id}`;
const { fitView, setCenter, findNode } = useVueFlow(flowId);
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
      style: { stroke: edgeKind(m.dependency_types?.[dep]).color, strokeWidth: 1.7 },
      data: {
        kind: m.dependency_types?.[dep] ?? 'implementation',
        routeKind: allMilestones.value.some((n) => n.position) ? 'waypoints' : 'spline',
        reason: m.dependency_reasons[dep],
        route: route(dep, m.id),
      },
    })),
  ),
);
function fit() {
  agent.freeView(props.project.id);
  fitView({ padding: 0.17, duration: 250 });
}
function initialized() {
  if (!agent.state.running && follows.value) fitView({ padding: 0.22, duration: 0 });
}
async function follow() {
  if (!follows.value || agent.state.projectId !== props.project.id) return;
  await nextTick();
  const node = findNode(agent.state.focusId);
  if (node) setCenter(node.position.x + 118, node.position.y + 75, { zoom: 0.95, duration: 450 });
}
function moved({ event }: { event: unknown }) {
  if (event) agent.freeView(props.project.id);
}
async function reset() {
  await perform('graph.positions', {
    project_id: props.project.id,
    positions: Object.fromEntries(positions.value),
  });
  await nextTick();
  fit();
}
watch(() => agent.state.pulse, follow);
watch(computedLayout, follow);
defineExpose({ fit, reset });
</script>
<template>
  <div class="graph-canvas">
    <GoalMarker :project="project" />
    <BaselineMilestoneStatus :project="project" />
    <VueFlow
      v-if="nodes.length"
      :id="flowId"
      :nodes="nodes"
      :edges="edges"
      :min-zoom="0.25"
      :max-zoom="1.6"
      :nodes-connectable="false"
      :nodes-draggable="!agent.state.running"
      :delete-key-code="null"
      fit-view-on-init
      @nodes-initialized="initialized"
      @move-start="moved"
      @node-drag-start="agent.freeView(project.id)"
      @node-click="({ node }) => selectNode(node.id)"
      @pane-click="selectNode(null)"
      @node-drag-stop="dragged"
      ><Background :gap="20" :size="1" pattern-color="#d6dfdd" /><Controls
        :show-interactive="false"
        position="bottom-left"
      /><template #node-milestone="nodeProps"><MilestoneNode v-bind="nodeProps" /></template>
      <template #edge-prerequisite="edgeProps"><PrerequisiteEdge v-bind="edgeProps" /></template>
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
    <FollowAgentButton v-if="!follows" @resume="agent.resumeFollow(project.id)" />
  </div>
</template>
