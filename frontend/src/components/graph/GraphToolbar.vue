<script setup lang="ts">
import { workspaceViews } from '../../lib/workspaceViews';
import { Maximize, RotateCcw, Search } from 'lucide-vue-next';
defineProps<{ tab: string; count: number }>();
defineEmits<{ tab: [tab: string]; fit: []; reset: []; find: [] }>();
const tabs = workspaceViews;
</script>
<template>
  <div class="graph-toolbar">
    <div class="view-tabs" role="tablist">
      <button
        v-for="item in tabs"
        :key="item.id"
        role="tab"
        :aria-selected="tab === item.id"
        :class="{ active: tab === item.id }"
        @click="$emit('tab', item.id)"
      >
        <component :is="item.icon" :size="15" />{{ item.label
        }}<span v-if="item.id === 'graph'">{{ count }}</span>
      </button>
    </div>
    <div v-if="tab === 'graph'" class="graph-actions">
      <button
        class="icon-button"
        type="button"
        title="查找里程碑：ID、标题或范围"
        aria-label="查找里程碑"
        @click="$emit('find')"
      >
        <Search :size="15" />
      </button>
      <span class="prerequisite-label">仅前置依赖</span
      ><button class="icon-button" title="自动布局" aria-label="自动布局" @click="$emit('reset')">
        <RotateCcw :size="15" /></button
      ><button class="icon-button" title="适应画布" aria-label="适应画布" @click="$emit('fit')">
        <Maximize :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.view-tabs {
  min-width: 0;
  overflow-x: auto;
}
.view-tabs > button,
.graph-actions {
  flex-shrink: 0;
}
.view-tabs > button {
  white-space: nowrap;
}
</style>
