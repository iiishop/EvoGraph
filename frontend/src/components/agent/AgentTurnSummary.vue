<script setup lang="ts">
import { computed } from 'vue';
import type { Milestone, TurnSummary } from '../../types';
import {
  dependencyTypeLabels,
  turnFieldLabels,
  turnOtherLabels,
  turnSummaryHeadline,
  turnSummaryHasChanges,
  turnSummaryNotice,
  turnSummaryStatus,
} from '../../lib/turnSummary';

const props = defineProps<{ summary: TurnSummary; milestones: Milestone[] }>();
defineEmits<{ locate: [id: string] }>();
const exists = (id: string) => props.milestones.some((item) => item.id === id);
const changes = computed(() => props.summary.changes);
const milestoneGroups = computed(() => [
  { label: '新增', items: changes.value.milestones.added },
  { label: '更新', items: changes.value.milestones.updated },
  { label: '移除', items: changes.value.milestones.removed },
]);
const dependencyGroups = computed(() => [
  { label: '新增', items: changes.value.dependencies.added },
  { label: '移除', items: changes.value.dependencies.removed },
]);
const hasDependencies = computed(() =>
  Object.values(changes.value.dependencies).some((items) => items.length),
);
const membership = computed(() => {
  const target = changes.value.target;
  if (!target) return [];
  const items = target.required_behavior_changes
    .filter((item) => item.before_id !== null || item.after_id !== null)
    .map((item) => ({
      key: item.behavior_key,
      label: item.before_id === null ? '纳入' : item.after_id === null ? '移出' : '修订',
      fields: item.fields,
    }));
  // Unknown/orphan historical IDs remain factual, without inventing a behavior name.
  for (const [direction, label, idField] of [
    ['added', '纳入', 'after_id'],
    ['removed', '移出', 'before_id'],
  ] as const) {
    for (const id of target.required_behavior_ids[direction]) {
      if (!target.required_behavior_changes.some((item) => item[idField] === id)) {
        items.push({ key: id, label, fields: [] });
      }
    }
  }
  return items;
});
const hasTarget = computed(
  () => changes.value.target?.statement_changed || membership.value.length,
);
function milestoneTitle(id: string): string {
  const snapshot = [
    ...changes.value.milestones.added,
    ...changes.value.milestones.updated,
    ...changes.value.milestones.removed,
  ].find((item) => item.id === id);
  return snapshot?.title || props.milestones.find((item) => item.id === id)?.title || id;
}
function fieldNames(fields: string[]): string {
  return [...new Set(fields.map((field) => turnFieldLabels[field] || field))].join('、');
}
function dependencyDescription(value: { reason: string; type: string }): string {
  const type = dependencyTypeLabels[value.type] || value.type;
  return [type, value.reason].filter(Boolean).join(' · ');
}
function version(value: number | null, prefix: string): string {
  return value === null ? '未设置' : `${prefix}${value}`;
}
</script>

<template>
  <details class="agent-turn-summary" :data-status="summary.status">
    <summary>
      <span class="turn-summary-title">本轮变更</span>
      <span class="turn-summary-status">{{ turnSummaryStatus(summary) }}</span>
      <span class="turn-summary-preview">{{ turnSummaryHeadline(summary) }}</span>
      <span v-if="summary.history_warning" class="turn-history-warning">对话未完整保存</span>
      <span class="turn-summary-disclosure" aria-hidden="true">详情</span>
    </summary>
    <div class="turn-summary-scroll" aria-label="本轮已保存的规划变更" tabindex="0">
      <p class="turn-summary-notice">{{ turnSummaryNotice(summary) }}</p>
      <p v-if="summary.history_warning" class="turn-summary-notice" role="status">
        {{ summary.history_warning }}
      </p>
      <template v-if="turnSummaryHasChanges(summary)">
        <section v-if="milestoneGroups.some((group) => group.items.length)">
          <h3>里程碑</h3>
          <ul>
            <template v-for="group in milestoneGroups" :key="group.label">
              <li v-for="item in group.items" :key="`${group.label}-${item.id}`">
                <span class="turn-change-action">{{ group.label }}</span
                >「<button
                  v-if="group.label !== '移除' && exists(item.id)"
                  type="button"
                  class="turn-node-link"
                  :aria-label="`定位里程碑：${item.title || item.id}`"
                  @click="$emit('locate', item.id)"
                >
                  {{ item.title || item.id }}</button
                ><span v-else>{{ item.title || item.id }}</span
                >」<span v-if="item.fields.length" class="turn-change-detail"
                  >：{{ fieldNames(item.fields) }}</span
                >
              </li>
            </template>
          </ul>
        </section>
        <section v-if="hasDependencies">
          <h3>依赖 <small>前置里程碑 → 后续里程碑</small></h3>
          <ul>
            <template v-for="group in dependencyGroups" :key="group.label">
              <li v-for="item in group.items" :key="`${group.label}-${item.source}-${item.target}`">
                <span class="turn-change-action">{{ group.label }}</span
                >「<button
                  v-if="exists(item.source)"
                  type="button"
                  class="turn-node-link"
                  :aria-label="`定位里程碑：${milestoneTitle(item.source)}`"
                  @click="$emit('locate', item.source)"
                >
                  {{ milestoneTitle(item.source) }}</button
                ><span v-else>{{ milestoneTitle(item.source) }}</span
                >」→「<button
                  v-if="exists(item.target)"
                  type="button"
                  class="turn-node-link"
                  :aria-label="`定位里程碑：${milestoneTitle(item.target)}`"
                  @click="$emit('locate', item.target)"
                >
                  {{ milestoneTitle(item.target) }}</button
                ><span v-else>{{ milestoneTitle(item.target) }}</span
                >」
                <span class="turn-change-detail">：{{ dependencyDescription(item) }}</span>
              </li>
            </template>
            <li
              v-for="item in changes.dependencies.updated"
              :key="`updated-${item.source}-${item.target}`"
            >
              <span class="turn-change-action">更新</span>「<button
                v-if="exists(item.source)"
                type="button"
                class="turn-node-link"
                :aria-label="`定位里程碑：${milestoneTitle(item.source)}`"
                @click="$emit('locate', item.source)"
              >
                {{ milestoneTitle(item.source) }}</button
              ><span v-else>{{ milestoneTitle(item.source) }}</span
              >」→「<button
                v-if="exists(item.target)"
                type="button"
                class="turn-node-link"
                :aria-label="`定位里程碑：${milestoneTitle(item.target)}`"
                @click="$emit('locate', item.target)"
              >
                {{ milestoneTitle(item.target) }}</button
              ><span v-else>{{ milestoneTitle(item.target) }}</span
              >」
              <span class="turn-change-detail"
                >（{{ fieldNames(item.fields) }}）：{{ dependencyDescription(item.before) }} →
                {{ dependencyDescription(item.after) }}</span
              >
            </li>
          </ul>
        </section>
        <section v-if="hasTarget">
          <h3>最终目标与验收</h3>
          <ul>
            <li v-if="changes.target?.statement_changed">更新目标描述</li>
            <li v-for="item in membership" :key="`${item.label}-${item.key}`">
              <span class="turn-change-action">{{ item.label }}</span
              >「{{ item.key }}」<span v-if="item.fields.length" class="turn-change-detail"
                >：{{ fieldNames(item.fields) }}</span
              >
            </li>
          </ul>
        </section>
        <section v-if="changes.architecture">
          <h3>架构</h3>
          <p v-if="changes.architecture.before_revision === changes.architecture.after_revision">
            更新 {{ version(changes.architecture.after_revision, 'A') }} 的架构设计
          </p>
          <p v-else>
            {{ version(changes.architecture.before_revision, 'A') }} →
            {{ version(changes.architecture.after_revision, 'A') }}
          </p>
        </section>
        <section v-if="changes.other.length">
          <h3>其他变更</h3>
          <p>{{ changes.other.map((area) => turnOtherLabels[area] || area).join('、') }}</p>
        </section>
      </template>
    </div>
  </details>
</template>

<style scoped>
.turn-history-warning {
  color: var(--warning);
  font-size: 11px;
}

.turn-node-link {
  display: inline;
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  font: inherit;
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.turn-node-link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 3px;
}
.agent-turn-summary {
  margin: 0 0 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fafb;
  font-size: 12px;
  color: var(--text-secondary);
}
.agent-turn-summary > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  min-height: 32px;
  padding: 6px 10px;
  cursor: pointer;
  list-style: none;
}
.agent-turn-summary > summary::-webkit-details-marker {
  display: none;
}
.agent-turn-summary > summary::before {
  content: '▸';
  color: var(--accent);
}
.agent-turn-summary[open] > summary::before {
  content: '▾';
}
.turn-summary-title {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--accent);
}
.turn-summary-status {
  flex-shrink: 0;
  font-size: 11px;
}
.agent-turn-summary[data-status='failed'] .turn-summary-status,
.agent-turn-summary[data-status='stopped'] .turn-summary-status {
  color: var(--warning);
}
.turn-summary-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.turn-summary-disclosure {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--muted);
}
.turn-summary-scroll {
  max-height: min(160px, 24dvh);
  overflow-y: auto;
  overflow-wrap: anywhere;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 0 12px 10px;
  line-height: 1.6;
}
.turn-summary-notice {
  margin: 0;
  font-size: 11px;
}
.turn-summary-scroll section {
  margin-top: 8px;
}
.turn-summary-scroll h3 {
  margin: 0 0 3px;
  font-size: 12px;
  color: var(--ink);
}
.turn-summary-scroll h3 small {
  font-weight: 400;
  font-size: 10px;
  color: var(--muted);
}
.turn-summary-scroll ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.turn-summary-scroll p {
  margin: 0;
}
.turn-change-action {
  margin-right: 5px;
  color: var(--ink);
}
.turn-change-detail {
  color: var(--text-secondary);
}
.agent-turn-summary > summary:focus-visible,
.turn-summary-scroll:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 6px;
}
@media (max-width: 600px) {
  .turn-summary-disclosure {
    display: none;
  }
  .agent-turn-summary > summary {
    gap: 6px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .agent-turn-summary,
  .turn-summary-scroll {
    animation: none;
    transition: none;
    scroll-behavior: auto;
  }
}
</style>
