<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { ArrowUpRight, CircleCheck, Code2, Layers3 } from 'lucide-vue-next';
import StatusBadge from '../ui/StatusBadge.vue';
import type { Milestone } from '../../types';
defineProps<{
  data: {
    milestone: Milestone;
    vertical?: boolean;
    ready: boolean;
    agentFocused?: boolean;
    agentActive?: boolean;
    updateTick?: number;
  };
  selected?: boolean;
}>();
</script>
<template>
  <div
    class="milestone-node"
    :class="{
      selected,
      complete: data.milestone.status === 'VERIFIED_COMPLETE',
      'source-node': data.milestone.origin === 'source',
      'agent-focused': data.agentFocused,
      'agent-active': data.agentFocused && data.agentActive,
    }"
  >
    <span v-if="data.updateTick" :key="data.updateTick" class="node-update-flash"></span>
    <span v-if="data.agentFocused" class="node-agent-label">{{
      data.agentActive ? 'Agent 正在操作' : '刚刚更新'
    }}</span>
    <Handle id="in" type="target" :position="Position.Left" />
    <div class="node-top">
      <span class="node-id"
        ><Layers3 :size="12" />
        {{ data.milestone.origin === 'source' ? '源码现状' : data.milestone.id }}</span
      ><ArrowUpRight :size="13" class="node-arrow" />
    </div>
    <h3>{{ data.milestone.title }}</h3>
    <div class="node-scope"><Code2 :size="12" /> {{ data.milestone.scope[0] }}</div>
    <div class="node-bottom">
      <StatusBadge
        v-if="data.milestone.origin !== 'source'"
        :status="
          data.ready && data.milestone.status === 'PLANNED' ? 'ready' : data.milestone.status
        "
        :label="data.ready && data.milestone.status === 'PLANNED' ? '可领取' : undefined"
      /><span v-else class="source-badge">SRC</span
      ><span
        ><CircleCheck :size="12" />
        {{
          data.milestone.origin === 'source'
            ? `${data.milestone.source_refs.length} 个源码引用`
            : `${data.milestone.behavior_revision_ids.length} 项验收`
        }}</span
      >
    </div>
    <Handle id="out" type="source" :position="Position.Right" />
  </div>
</template>
