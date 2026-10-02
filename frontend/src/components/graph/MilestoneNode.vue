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
    <span v-if="data.agentFocused" class="node-agent-label">{{
      data.agentActive ? 'Agent 正在操作' : 'Agent 最近定位'
    }}</span>
    <Handle id="in" type="target" :position="Position.Left" />
    <div class="node-top">
      <span class="node-id" :title="data.milestone.id"
        ><Layers3 :size="12" />
        <span class="node-id-value">{{
          data.milestone.origin === 'source' ? '已实现 · 基线' : data.milestone.id
        }}</span></span
      ><span
        v-if="data.updateTick"
        class="node-update-marker"
        title="本轮 Agent 已修改此节点，不代表已通过验收"
        >本轮已改</span
      ><ArrowUpRight v-else :size="13" class="node-arrow" />
    </div>
    <h3 :title="data.milestone.title">{{ data.milestone.title }}</h3>
    <div class="node-scope" :title="data.milestone.scope[0]">
      <Code2 :size="12" /> {{ data.milestone.scope[0] }}
    </div>
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
            ? `${data.milestone.source_behaviors?.length ?? 0} 项行为`
            : `${data.milestone.behavior_revision_ids.length} 项验收`
        }}</span
      >
    </div>
    <Handle id="out" type="source" :position="Position.Right" />
  </div>
</template>
