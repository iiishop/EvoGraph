<script setup lang="ts">
import { computed, nextTick, watch } from 'vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { GitBranch } from 'lucide-vue-next';
import MilestoneNode from './MilestoneNode.vue';
import PrerequisiteEdge from './PrerequisiteEdge.vue';
import { layout } from '../../composables/useGraphLayout';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import { edgeKind, edgeKinds } from '../../lib/edgeKinds';
import FollowAgentButton from './FollowAgentButton.vue';
import AgentQuestion from '../agent/AgentQuestion.vue';
import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const flowId = `milestones-${props.project.id}`;
const { fitView, setCenter, findNode } = useVueFlow(flowId);
const { state, selectNode, perform } = useWorkspace();
const agent = useAgent();
const follows = computed(() => agent.state.follow[props.project.id] !== false);
const positions = computed(() => layout(props.project.milestones));
const nodes = computed(() =>
  props.project.milestones.map((m) => ({
    id: m.id,
    type: 'milestone',
    position: m.position ?? positions.value.get(m.id)!,
    selected: state.selectedId === m.id,
    data: {
      milestone: m,
      ready: props.project.readiness[m.id]?.safe_to_execute,
      agentFocused: agent.state.projectId === props.project.id && agent.state.focusId === m.id,
      agentActive: agent.state.running,
      updateTick: agent.state.projectId === props.project.id ? (agent.state.updates[m.id] ?? 0) : 0,
    },
  })),
);
const edges = computed(() =>
  props.project.milestones.flatMap((m) =>
    m.dependencies.map((dep) => ({
      id: `${dep}-${m.id}`,
      source: dep,
      target: m.id,
      type: 'prerequisite',
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeKind(m.dependency_types?.[dep]).color },
      style: { stroke: edgeKind(m.dependency_types?.[dep]).color, strokeWidth: 1.7 },
      data: { kind: m.dependency_types?.[dep] ?? 'implementation' },
    })),
  ),
);
function fit() {
  agent.freeView(props.project.id);
  fitView({ padding: 0.17, duration: 250 });
}
async function follow() {
  if (!follows.value || agent.state.projectId !== props.project.id) return;
  await nextTick();
  const node = findNode(agent.state.focusId);
  if (node) setCenter(node.position.x + 118, node.position.y + 65, { zoom: 0.95, duration: 450 });
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
defineExpose({ fit, reset });
</script>
<template>
  <div class="graph-canvas">
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
      @move-start="moved"
      @node-drag-start="agent.freeView(project.id)"
      @node-click="({ node }) => selectNode(node.id)"
      @pane-click="selectNode(null)"
      @node-drag-stop="
        ({ node }) =>
          perform('graph.positions', {
            project_id: project.id,
            positions: { [node.id]: node.position },
          })
      "
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
    <div v-if="project.question" class="question-overlay">
      <AgentQuestion
        :key="project.question.id"
        :question="project.question"
        :project-id="project.id"
      />
    </div>
  </div>
</template>
