<script setup lang="ts">
import { Activity } from 'lucide-vue-next';
import { eventLabels, formatTime, eventDetail } from '../../lib/presentation';
import type { Project } from '../../types';
defineProps<{ project: Project }>();
const emit = defineEmits<{ receipt: [eventId: string, trigger: HTMLElement] }>();
function openReceipt(eventId: string, event: MouseEvent) {
  if (event.currentTarget instanceof HTMLElement) emit('receipt', eventId, event.currentTarget);
}
</script>
<template>
  <section class="activity-panel">
    <div class="metric-row">
      <div>
        <span>规划耗时</span
        ><strong>{{ Math.round(project.metrics.planning_seconds ?? 0) }}<small>s</small></strong>
      </div>
      <div>
        <span>验证耗时</span
        ><strong
          >{{ Math.round(project.metrics.verification_seconds ?? 0) }}<small>s</small></strong
        >
      </div>
      <div>
        <span>模型用量</span
        ><strong
          >{{ (project.metrics.model_tokens ?? 0).toLocaleString() }}<small>tokens</small></strong
        >
      </div>
      <div>
        <span>受阻尝试</span><strong>{{ project.metrics.blocked_attempts ?? 0 }}</strong>
      </div>
    </div>
    <p class="muted metric-note">仅统计本应用内已记录的耗时与用量，尚不代表目标的完整实现成本。</p>
    <div class="timeline">
      <article v-for="event in project.events" :key="event.id">
        <span class="timeline-icon"><Activity :size="13" /></span>
        <div>
          <button
            v-if="event.kind === 'agent_turn_finished'"
            type="button"
            class="timeline-receipt-trigger"
            aria-haspopup="dialog"
            :aria-label="`查看 ${formatTime(event.created_at)} 的规划回执`"
            @click="openReceipt(event.id, $event)"
          >
            {{ eventLabels[event.kind] }}
          </button>
          <strong v-else>{{ eventLabels[event.kind] ?? event.kind }}</strong>
          <p>
            {{ eventDetail(event) }}
          </p>
        </div>
        <time>{{ formatTime(event.created_at) }}</time>
      </article>
    </div>
  </section>
</template>

<style scoped>
.timeline-receipt-trigger {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent, #376d83);
  font: inherit;
  font-weight: 650;
  text-align: left;
  text-decoration: underline;
  text-decoration-color: transparent;
  text-underline-offset: 3px;
  cursor: pointer;
}
.timeline-receipt-trigger:hover {
  text-decoration-color: currentColor;
}
.timeline-receipt-trigger:focus-visible {
  outline: 2px solid var(--accent, #376d83);
  outline-offset: 3px;
  border-radius: 2px;
}
</style>
