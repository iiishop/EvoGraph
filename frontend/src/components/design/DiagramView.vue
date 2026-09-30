<script setup lang="ts">
import { shallowRef, watch, useId, nextTick, ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
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
const props = withDefaults(
  defineProps<{
    diagram: Diagram;
    query?: string;
    source?: boolean;
    focusedId?: string;
    relation?: string;
  }>(),
  { query: '', source: false, focusedId: '', relation: 'all' },
);
const emit = defineEmits<{ select: [id: string] }>();
const flowId = `design-${useId()}`;
const { fitView, updateNodeInternals, setCenter, findNode } = useVueFlow(flowId);
const canvas = ref<HTMLElement>();
let viewportMoved = false;
let frame = 0;
let resizeObserver: ResizeObserver | undefined;
let previousSize = '';
// Initial fitting is independent of Agent-follow. Once the user moves or focuses,
// subsequent resizes (including returning from local detail) preserve their camera.
function fitWhenReady() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(() => {
      if (
        !canvas.value?.clientWidth ||
        !canvas.value.clientHeight ||
        viewportMoved ||
        props.focusedId
      )
        return;
      void fitView({ padding: 0.16, duration: 0 });
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
  generation++;
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect();
});
const positions = shallowRef(new Map<string, { x: number; y: number }>());
const error = ref('');
const agent = useAgent();
const workspace = useWorkspace();
const follows = computed(() => agent.state.follow[workspace.state.project?.id ?? ''] !== false);
function moved({ event }: { event: unknown }) {
  if (event) {
    viewportMoved = true;
    if (workspace.state.project) agent.freeView(workspace.state.project.id);
  }
}
const visible = computed(() => {
  const ids = new Set(
    props.diagram.nodes
      .filter((n) =>
        `${n.label} ${n.id} ${n.description}`.toLowerCase().includes(props.query.toLowerCase()),
      )
      .map((n) => n.id),
  );
  if (!props.focusedId || props.relation === 'all') return ids;
  const related = new Set([props.focusedId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of props.diagram.edges) {
      const from = props.relation === 'upstream' ? edge.target : edge.source;
      const to = props.relation === 'upstream' ? edge.source : edge.target;
      if (related.has(from) && !related.has(to)) {
        related.add(to);
        changed = true;
      }
    }
  }
  return new Set([...ids].filter((id) => related.has(id)));
});
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
    error.value = '';
    try {
      const result = await layoutArchitecture(diagram);
      if (ticket !== generation) return;
      positions.value = result;
      await nextTick();
      updateNodeInternals(diagram.nodes.map((n) => n.id));
      await nextTick();
      fitWhenReady();
    } catch {
      error.value = '布局未完成，请切换版本后重试';
    }
  },
  { immediate: true },
);
watch(
  () => props.focusedId,
  async (id) => {
    await nextTick();
    const node = findNode(id);
    if (node) {
      viewportMoved = true;
      setCenter(node.position.x + 118, node.position.y + 75, {
        zoom: 0.95,
        duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220,
      });
    }
  },
);
watch(
  () => agent.state.pulse,
  () => {
    if (follows.value) fitView({ padding: 0.2 });
  },
);
defineExpose({ fit: () => fitView({ padding: 0.2 }), reset: () => fitView({ padding: 0.2 }) });
</script>
<template>
  <div ref="canvas" class="design-diagram">
    <p v-if="error" role="alert">{{ error }}</p>
    <VueFlow
      v-else-if="flowNodes.length"
      :id="flowId"
      :nodes="flowNodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :delete-key-code="null"
      :min-zoom="0.12"
      :max-zoom="1.8"
      @nodes-initialized="fitWhenReady"
      @move-start="moved"
      @node-click="({ node }) => node.type === 'architecture' && emit('select', node.id)"
    >
      <Background :gap="24" pattern-color="#d3ddea" /><Controls :show-interactive="false" />
      <template #node-architecture-group="nodeProps"
        ><ArchitectureGroup v-bind="nodeProps"
      /></template>
      <template #node-architecture="nodeProps"><ArchitectureNode v-bind="nodeProps" /></template>
      <template #edge-architecture="edgeProps"><GraphEdge v-bind="edgeProps" /></template>
    </VueFlow>
  </div>
</template>
