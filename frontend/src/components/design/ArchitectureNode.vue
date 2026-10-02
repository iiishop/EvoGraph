<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { architectureRole } from '../../lib/architectureRoles';
import type { Diagram } from '../../types';
import type { ArchitecturePort } from '../../lib/architectureRouting';
defineProps<{
  data: Diagram['nodes'][number] & {
    dimmed?: boolean;
    source?: boolean;
    ports?: { incoming: ArchitecturePort[]; outgoing: ArchitecturePort[] };
  };
  selected?: boolean;
}>();
</script>
<template>
  <div
    class="architecture-node"
    :class="{ selected, dimmed: data.dimmed }"
    :style="{
      '--role-color': architectureRole(data.role).color,
      '--role-tint': architectureRole(data.role).tint,
    }"
  >
    <Handle
      v-for="port in data.ports?.incoming ?? [{ id: 'in', y: 75, side: 'left' }]"
      :id="port.id"
      :key="port.id"
      type="target"
      :position="port.side === 'left' ? Position.Left : Position.Right"
      :style="{ top: `${port.y}px` }"
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
      v-for="port in data.ports?.outgoing ?? [{ id: 'out', y: 75, side: 'right' }]"
      :id="port.id"
      :key="port.id"
      type="source"
      :position="port.side === 'left' ? Position.Left : Position.Right"
      :style="{ top: `${port.y}px` }"
    />
  </div>
</template>
