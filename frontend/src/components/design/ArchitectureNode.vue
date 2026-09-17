<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { architectureRole } from '../../lib/architectureRoles';
import type { Diagram } from '../../types';
defineProps<{
  data: Diagram['nodes'][number] & { dimmed?: boolean; source?: boolean };
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
    <Handle id="in" type="target" :position="Position.Left" />
    <div class="architecture-node-meta">
      <span>{{ architectureRole(data.role).label }}</span
      ><b v-if="data.source" class="source-badge">SRC</b>
    </div>
    <strong :title="data.label">{{ data.label }}</strong>
    <p>{{ data.description || '选择组件，查看系统边界与关系' }}</p>
    <small>{{
      data.source_refs?.length ? `${data.source_refs.length} 个源码引用` : '设计组件'
    }}</small>
    <Handle id="out" type="source" :position="Position.Right" />
  </div>
</template>
