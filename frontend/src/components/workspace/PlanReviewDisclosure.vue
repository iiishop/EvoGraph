<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { PlanCandidate } from '../../types';
import { planReviewDisclosure } from '../../lib/planPreview';
const props = defineProps<{
  candidate?: PlanCandidate | null;
  canonicalRevision?: number;
  align?: 'start' | 'end';
}>();
const disclosure = ref<HTMLDetailsElement>();
const evidence = computed(() => planReviewDisclosure(props.candidate, props.canonicalRevision));
watch(
  () => `${props.candidate?.id}:${props.candidate?.candidate_hash}:${props.canonicalRevision}`,
  () => {
    if (disclosure.value) disclosure.value.open = false;
  },
);
function close(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !disclosure.value?.open) return;
  event.preventDefault();
  event.stopPropagation();
  disclosure.value.open = false;
  disclosure.value.querySelector('summary')?.focus();
}
</script>
<template>
  <details
    ref="disclosure"
    :class="['plan-review-disclosure', { 'align-end': align === 'end' }]"
    @keydown="close"
  >
    <summary>{{ evidence.label }}</summary>
    <div class="plan-review-layers">
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
      <p v-if="evidence.legacyNotice" class="plan-review-legacy">{{ evidence.legacyNotice }}</p>
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
  position: absolute;
  z-index: 35;
  inset: calc(100% + 3px) auto auto 0;
  width: 360px;
  max-width: min(360px, calc(100vw - 100px));
  max-height: min(55vh, 330px);
  overflow: auto;
  padding: 13px 15px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: white;
  box-shadow: 0 8px 24px #1d34551a;
  font-size: 11px;
  overflow-wrap: anywhere;
}
.align-end .plan-review-layers {
  left: auto;
  right: 0;
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
