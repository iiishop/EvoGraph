<script setup lang="ts">
import { shallowRef, watch, useId, nextTick, ref, computed } from 'vue';
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { layout } from '../../composables/useGraphLayout';
import ArchitectureNode from './ArchitectureNode.vue';
import ArchitectureEdge from './ArchitectureEdge.vue';
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
const positions = shallowRef(new Map<string, { x: number; y: number }>());
const error = ref('');
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
      selected: n.id === props.focusedId,
      data: { ...n, source: props.source, dimmed: !visible.value.has(n.id) },
    })),
);
const edges = computed(() =>
  props.diagram.edges.map((e, index) => {
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
        returnLane: Math.max(...[...positions.value.values()].map((p) => p.y + 150), 150) + 65 + index * 18,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
      style: {
        stroke: color,
        strokeWidth: 1.8,
        opacity: visible.value.has(e.source) && visible.value.has(e.target) ? 0.85 : 0.1,
      },
    };
  }),
);
let generation = 0;
watch(
  () => props.diagram,
  async (diagram) => {
    const ticket = ++generation;
    error.value = '';
    try {
      const result = await layout(
        diagram.nodes.map((n) => ({
          id: n.id,
          dependencies: [
            ...new Set(
              diagram.edges
                .filter((e) => e.target === n.id && e.source !== n.id)
                .map((e) => e.source),
            ),
          ],
        })),
      );
      if (ticket !== generation) return;
      positions.value = result.positions;
      await nextTick();
      updateNodeInternals(diagram.nodes.map((n) => n.id));
      await nextTick();
      fitView({ padding: 0.2, duration: 0 });
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
    if (node)
      setCenter(node.position.x + 118, node.position.y + 75, {
        zoom: 0.95,
        duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220,
      });
  },
);
defineExpose({ fit: () => fitView({ padding: 0.2 }), reset: () => fitView({ padding: 0.2 }) });
</script>
<template>
  <div class="design-diagram">
    <p v-if="error" role="alert">{{ error }}</p>
    <VueFlow
      v-else-if="nodes.length"
      :id="flowId"
      :nodes="nodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :delete-key-code="null"
      :min-zoom="0.12"
      :max-zoom="1.8"
      fit-view-on-init
      @nodes-initialized="fitView({ padding: 0.2 })"
      @node-click="({ node }) => emit('select', node.id)"
    >
      <Background :gap="24" pattern-color="#d3ddea" /><Controls :show-interactive="false" />
      <template #node-architecture="nodeProps"><ArchitectureNode v-bind="nodeProps" /></template>
      <template #edge-architecture="edgeProps"><ArchitectureEdge v-bind="edgeProps" /></template>
    </VueFlow>
  </div>
</template>
