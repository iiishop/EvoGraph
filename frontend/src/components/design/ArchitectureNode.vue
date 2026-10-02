<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { architectureRole } from '../../lib/architectureRoles';
import type { Diagram } from '../../types';
import type { ArchitecturePort } from '../../lib/architectureRouting';
defineProps<{
  data: Diagram['nodes'][number] & {
    dimmed?: boolean;
    source?: boolean;
    previewed?: boolean;
    previewHandles?: string[];
    ports?: { incoming: ArchitecturePort[]; outgoing: ArchitecturePort[] };
  };
  selected?: boolean;
}>();
const positions = {
  left: Position.Left,
  right: Position.Right,
  top: Position.Top,
  bottom: Position.Bottom,
};
const portStyle = (port: ArchitecturePort) => ({
  left: `${port.x}px`,
  top: `${port.y}px`,
  right: 'auto',
  bottom: 'auto',
  transform: 'translate(-50%, -50%)',
});
</script>
<template>
  <div
    class="architecture-node"
    :class="{ selected, dimmed: data.dimmed, 'is-edge-preview': data.previewed }"
    :style="{
      '--role-color': architectureRole(data.role).color,
      '--role-tint': architectureRole(data.role).tint,
    }"
  >
    <Handle
      v-for="port in data.ports?.incoming ?? []"
      :id="port.id"
      :key="port.id"
      type="target"
      class="architecture-port architecture-port-incoming"
      :class="{ 'is-edge-preview': data.previewHandles?.includes(port.id) }"
      :position="positions[port.side]"
      :style="portStyle(port)"
      :title="`接收 · ${port.edges.length} 条关系`"
      :aria-label="`接收端口，${port.edges.length} 条关系`"
    />
    <div class="architecture-node-meta">
      <span>{{ architectureRole(data.role).label }}</span
      ><b v-if="data.source" class="source-badge">SRC</b>
    </div>
    <strong :title="data.label">{{ data.label }}</strong>
    <p>{{ data.description || '选择组件，查看系统边界与关系' }}</p>
    <small>{{
      data.source_refs?.length ? `${data.source_refs.length} 个源码引用` : '设计组件'
    }}</small>
    <Handle
      v-for="port in data.ports?.outgoing ?? []"
      :id="port.id"
      :key="port.id"
      type="source"
      class="architecture-port architecture-port-outgoing"
      :class="{ 'is-edge-preview': data.previewHandles?.includes(port.id) }"
      :position="positions[port.side]"
      :style="portStyle(port)"
      :title="`发送 · ${port.edges.length} 条关系`"
      :aria-label="`发送端口，${port.edges.length} 条关系`"
    />
  </div>
</template>
<style scoped>
.architecture-node.is-edge-preview {
  outline: 2px solid var(--role-color);
  outline-offset: 3px;
}
.architecture-node .architecture-port {
  width: 9px;
  height: 9px;
  min-width: 9px;
  min-height: 9px;
  border: 2px solid var(--role-color);
}
.architecture-node .architecture-port-incoming {
  background: #fff;
  border-radius: 50%;
}
.architecture-node .architecture-port-outgoing {
  background: var(--role-color);
  border-radius: 2px;
}
.architecture-node .architecture-port.is-edge-preview {
  outline: 3px solid var(--role-tint);
  outline-offset: 2px;
  box-shadow: 0 0 0 2px var(--role-color);
}
</style>
