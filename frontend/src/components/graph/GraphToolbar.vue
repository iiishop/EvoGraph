<script setup lang="ts">
import { Network, ListChecks, History, Maximize, RotateCcw } from 'lucide-vue-next';
defineProps<{ tab: string; count: number }>();
defineEmits<{ tab: [tab: string]; fit: []; reset: [] }>();
const tabs = [
  { id: 'graph', label: '里程碑图', icon: Network },
  { id: 'evidence', label: '验证证据', icon: ListChecks },
  { id: 'activity', label: '演化记录', icon: History },
];
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
      <span class="prerequisite-label">仅前置依赖</span
      ><button class="icon-button" title="自动布局" aria-label="自动布局" @click="$emit('reset')">
        <RotateCcw :size="15" /></button
      ><button class="icon-button" title="适应画布" aria-label="适应画布" @click="$emit('fit')">
        <Maximize :size="15" />
      </button>
    </div>
  </div>
</template>
