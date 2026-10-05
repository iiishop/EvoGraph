<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue';
import type { PlanCandidate } from '../../types';
import { planReviewDisclosure } from '../../lib/planPreview';
const props = defineProps<{
  candidate?: PlanCandidate | null;
  canonicalRevision?: number;
  align?: 'start' | 'end';
}>();
const uid = useId();
const disclosure = ref<HTMLDetailsElement>();
const trigger = ref<HTMLElement>();
const reader = ref<HTMLElement>();
const open = ref(false);
const placement = ref({ left: '0px', top: '0px', width: '360px', maxHeight: '330px' });
const evidence = computed(() => planReviewDisclosure(props.candidate, props.canonicalRevision));
let observer: ResizeObserver | undefined;
let frame = 0;
let mounted = true;

function contains(target: EventTarget | null) {
  return (
    target instanceof Node && (disclosure.value?.contains(target) || reader.value?.contains(target))
  );
}
function stopListening() {
  observer?.disconnect();
  observer = undefined;
  if (frame) window.cancelAnimationFrame(frame);
  frame = 0;
  document.removeEventListener('keydown', escape, true);
  document.removeEventListener('pointerdown', outside, true);
  document.removeEventListener('focusin', focusOutside);
  document.removeEventListener('scroll', schedulePosition, true);
  window.removeEventListener('resize', schedulePosition);
  window.visualViewport?.removeEventListener('resize', schedulePosition);
  window.visualViewport?.removeEventListener('scroll', schedulePosition);
}
function close(restoreFocus = false) {
  open.value = false;
  if (disclosure.value) disclosure.value.open = false;
  stopListening();
  if (restoreFocus && trigger.value?.isConnected) trigger.value.focus({ preventScroll: true });
}
function escape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || !open.value) return;
  event.preventDefault();
  event.stopPropagation();
  close(true);
}
function outside(event: PointerEvent) {
  // Let the clicked control receive the pointer and focus normally.
  if (!contains(event.target)) close();
}
function focusOutside(event: FocusEvent) {
  if (!contains(event.target)) close();
}
function position() {
  frame = 0;
  if (!open.value || !trigger.value || !reader.value) return;
  const anchor = trigger.value.getBoundingClientRect();
  const viewport = window.visualViewport;
  const left = (viewport?.offsetLeft ?? 0) + 12;
  const top = (viewport?.offsetTop ?? 0) + 12;
  const right = left + (viewport?.width ?? window.innerWidth) - 24;
  const bottom = top + (viewport?.height ?? window.innerHeight) - 24;
  if (
    !trigger.value.getClientRects().length ||
    anchor.bottom < top ||
    anchor.top > bottom ||
    anchor.right < left ||
    anchor.left > right
  ) {
    close(reader.value.contains(document.activeElement));
    return;
  }
  // Do not leave a detached reader behind when its trigger scrolls out of a clipped panel.
  for (let parent = trigger.value.parentElement; parent; parent = parent.parentElement) {
    const style = window.getComputedStyle(parent);
    const bounds = parent.getBoundingClientRect();
    if (
      (/(auto|scroll|hidden|clip)/.test(style.overflowY) &&
        (anchor.bottom <= bounds.top || anchor.top >= bounds.bottom)) ||
      (/(auto|scroll|hidden|clip)/.test(style.overflowX) &&
        (anchor.right <= bounds.left || anchor.left >= bounds.right))
    ) {
      close();
      return;
    }
  }
  const gap = 5;
  const below = Math.max(0, bottom - anchor.bottom - gap);
  const above = Math.max(0, anchor.top - top - gap);
  const upward = below < Math.min(330, reader.value.scrollHeight) && above > below;
  const maxHeight = Math.max(0, Math.min(330, upward ? above : below));
  const height = Math.min(reader.value.scrollHeight + 2, maxHeight);
  const width = Math.max(0, Math.min(360, right - left));
  const x = props.align === 'end' ? anchor.right - width : anchor.left;
  const y = upward ? anchor.top - gap - height : anchor.bottom + gap;
  placement.value = {
    left: `${Math.max(left, Math.min(x, right - width))}px`,
    top: `${Math.max(top, Math.min(y, bottom - height))}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
  };
}
function schedulePosition(event?: Event) {
  if (
    !open.value ||
    frame ||
    (event?.target instanceof Node && reader.value?.contains(event.target))
  )
    return;
  frame = window.requestAnimationFrame(position);
}
async function syncOpen() {
  if (!mounted || !disclosure.value?.open) {
    close();
    return;
  }
  if (open.value) return;
  open.value = true;
  const focusAtOpen = document.activeElement;
  await nextTick();
  if (!mounted || !open.value || !disclosure.value?.open) return;
  position();
  if (!open.value) return;
  document.addEventListener('keydown', escape, true);
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('focusin', focusOutside);
  document.addEventListener('scroll', schedulePosition, true);
  window.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('scroll', schedulePosition);
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => schedulePosition());
    if (reader.value) observer.observe(reader.value);
    // Resizing the chat dock can move the trigger without resizing the window.
    for (let parent = trigger.value?.parentElement; parent; parent = parent.parentElement)
      observer.observe(parent);
  }
  if (document.activeElement === focusAtOpen) reader.value?.focus({ preventScroll: true });
}
watch(
  [
    () => props.candidate?.id,
    () => props.candidate?.candidate_hash,
    () => props.candidate?.revision,
    () => props.candidate?.project?.id,
    () => props.candidate?.project?.created_at,
    () => props.canonicalRevision,
  ],
  () => close(Boolean(reader.value?.contains(document.activeElement))),
);
onBeforeUnmount(() => {
  mounted = false;
  close();
});
</script>
<template>
  <details ref="disclosure" class="plan-review-disclosure" @toggle="syncOpen">
    <summary
      :id="`${uid}-trigger`"
      ref="trigger"
      :aria-expanded="open"
      :aria-controls="`${uid}-reader`"
      aria-haspopup="dialog"
    >
      {{ evidence.label }}
    </summary>
    <Teleport to="body" :disabled="!open">
      <div
        v-show="open"
        :id="`${uid}-reader`"
        ref="reader"
        class="plan-review-layers"
        :style="placement"
        role="dialog"
        aria-modal="false"
        :aria-labelledby="`${uid}-trigger`"
        tabindex="-1"
      >
        <div class="plan-review-heading">
          <strong>检查详情</strong>
          <button type="button" @click="close(true)">收起</button>
        </div>
        <dl>
          <template v-if="evidence.harness">
            <div
              v-for="check in evidence.harness.checks"
              :key="check.id"
              :data-harness-check="check.id"
            >
              <dt class="plan-review-check-title">
                <span :title="check.id">{{ check.name }}</span>
                <small>{{ check.kindLabel }} · {{ check.version }}</small>
              </dt>
              <dd>
                {{ check.status
                }}<span v-if="check.findings">
                  · {{ check.findings }} {{ check.verdict === 'pass' ? '项建议' : '项问题' }}</span
                >
                <p
                  v-if="
                    check.message && (check.execution !== 'completed' || check.verdict !== 'pass')
                  "
                  class="plan-review-check-detail"
                >
                  {{ check.message }}
                </p>
              </dd>
            </div>
            <div v-if="!evidence.harness.checks.length">
              <dt>程序检查 · 模型意见</dt>
              <dd>尚无插件执行记录；状态未知</dd>
            </div>
          </template>
          <template v-else>
            <div>
              <dt>结构检查</dt>
              <dd>{{ evidence.structural }}</dd>
            </div>
            <div>
              <dt>模型评审</dt>
              <dd>{{ evidence.semantic }}</dd>
            </div>
          </template>
          <div>
            <dt>{{ evidence.implementationTitle }}</dt>
            <dd>{{ evidence.implementation }}</dd>
          </div>
        </dl>
        <p v-if="evidence.harness" class="plan-review-policy">
          {{ evidence.harness.decisionLabel }}
        </p>
        <details v-if="evidence.observations.length" class="plan-review-history">
          <summary>非阻断观察 · {{ evidence.observations.length }} 项</summary>
          <p>这些意见供实现或文字整理参考，不覆盖未决项，也不代表实现已验证。</p>
          <ul>
            <li v-for="observation in evidence.observations" :key="observation.id">
              <strong>
                {{ observation.id }} ·
                {{ observation.kind === 'editorial' ? '文字整理' : '实现余地' }}
              </strong>
              <p>{{ observation.reason }}</p>
              <p>{{ observation.subjects.join(' · ') }}</p>
              <p>依据：{{ observation.basis }} · {{ observation.evidence_refs.join(' · ') }}</p>
              <p v-if="observation.execution_evidence_ids.length">
                既有执行记录：{{ observation.execution_evidence_ids.join(' · ') }}
              </p>
            </li>
          </ul>
        </details>
        <p v-if="evidence.legacyNotice && !evidence.history" class="plan-review-legacy">
          {{ evidence.legacyNotice }}
        </p>
        <details
          v-if="evidence.history"
          :key="`${evidence.history.sourceCandidateId}:${evidence.history.fingerprint}`"
          class="plan-review-history"
        >
          <summary>此前模型意见 · {{ evidence.history.issues.length }} 项待复核</summary>
          <p>以下是历史意见，尚未针对当前候选复核，不代表当前版本仍有这些问题或已通过检查。</p>
          <p>
            来源候选 {{ evidence.history.sourceCandidateId }} · 原评审指纹
            {{ evidence.history.fingerprint || '未记录' }}
          </p>
          <ul>
            <li v-for="issue in evidence.history.issues" :key="issue.key">
              <strong
                >{{ issue.id ? `${issue.id} · ` : ''
                }}{{ issue.verdict === 'contradicted' ? '当时发现矛盾' : '当时尚无法判断' }}</strong
              >
              <p>{{ issue.subjects.join(' · ') }}</p>
              <p>{{ issue.reason }}</p>
              <p v-if="issue.counterexample">反例：{{ issue.counterexample }}</p>
            </li>
          </ul>
        </details>
        <p class="plan-review-source">
          {{ evidence.source
          }}<span v-if="evidence.fingerprint" :title="evidence.fingerprint">
            · 指纹 {{ evidence.fingerprint.slice(0, 12) }}</span
          >
        </p>
        <details v-if="evidence.harness" class="plan-review-identity">
          <summary>检查版本与快照</summary>
          <p>策略 {{ evidence.harness.policy_version }}</p>
          <p>快照 {{ evidence.harness.snapshot_id }}</p>
          <p>记录 {{ evidence.harness.schema_version }}</p>
        </details>
        <p class="plan-review-limit">
          {{
            evidence.harness
              ? '程序检查通过不代表语义正确；模型未发现问题仍可能有遗漏'
              : '模型评审可能遗漏问题，不代表实现已验证'
          }}
        </p>
      </div>
    </Teleport>
  </details>
</template>
<style scoped>
.plan-review-disclosure {
  position: relative;
  width: fit-content;
  max-width: 100%;
  color: var(--text-secondary);
  font-size: 10px;
  line-height: 1.6;
}
.plan-review-disclosure > summary {
  cursor: pointer;
  padding: 5px 0;
  color: var(--accent);
}
.plan-review-disclosure > summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 3px;
}
.plan-review-layers {
  position: fixed;
  z-index: 120;
  box-sizing: border-box;
  width: 360px;
  max-width: calc(100vw - 24px);
  overflow: auto;
  padding: 13px 15px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: white;
  box-shadow: 0 8px 24px #1d34551a;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
  overflow-wrap: anywhere;
  overscroll-behavior: contain;
}
.plan-review-layers:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.plan-review-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.plan-review-heading button {
  border: 0;
  padding: 2px 4px;
  background: transparent;
  color: var(--accent);
  font-size: 10px;
}
.plan-review-layers dl {
  margin: 0;
}
.plan-review-layers dl > div + div {
  margin-top: 10px;
}
.plan-review-layers dt {
  color: var(--ink);
  font-weight: 600;
}
.plan-review-layers dd {
  margin: 3px 0 0;
}
.plan-review-check-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 2px 8px;
}
.plan-review-check-title small {
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 400;
}
.plan-review-check-detail {
  margin: 3px 0 0;
}
.plan-review-policy {
  margin: 10px 0 0;
}
.plan-review-history {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  font-size: 10px;
}
.plan-review-history > summary {
  cursor: pointer;
  color: var(--accent);
}
.plan-review-history p {
  margin: 5px 0 0;
}
.plan-review-history ul {
  margin: 8px 0 0;
  padding-left: 16px;
}
.plan-review-history li + li {
  margin-top: 10px;
}
.plan-review-identity {
  margin-top: 6px;
  font-size: 10px;
}
.plan-review-identity > summary {
  cursor: pointer;
}
.plan-review-identity p {
  margin: 3px 0 0;
}
.plan-review-source {
  margin: 11px 0 0;
  padding-top: 9px;
  border-top: 1px solid var(--line);
  font-size: 10px;
}
.plan-review-legacy,
.plan-review-limit {
  margin: 8px 0 0;
  font-size: 10px;
}
</style>
