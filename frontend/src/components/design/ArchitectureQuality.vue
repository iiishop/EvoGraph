<script setup lang="ts">
import type { Architecture } from '../../types';
defineProps<{ architecture: Architecture }>();
</script>
<template>
  <details class="architecture-quality">
    <summary>
      设计质量与风险
      <span
        >{{ architecture.quality_scenarios?.length ?? 0 }} 项质量场景 ·
        {{ architecture.risks?.length ?? 0 }} 项风险</span
      >
    </summary>
    <p v-if="!architecture.quality_scenarios?.length" class="muted">
      尚未定义可验证的质量目标，可让 Agent 补充可靠性、安全、性能与扩展性场景。
    </p>
    <article v-for="(item, index) in architecture.quality_scenarios" :key="index">
      <h3>{{ item.concern }}</h3>
      <p>{{ item.scenario }}</p>
      <dl>
        <dt>验证目标</dt>
        <dd>{{ item.measure }}</dd>
        <dt>设计措施</dt>
        <dd>{{ item.approach }}</dd>
      </dl>
    </article>
    <ul v-if="architecture.risks?.length">
      <li v-for="risk in architecture.risks" :key="risk">{{ risk }}</li>
    </ul>
  </details>
</template>
<style scoped>
.architecture-quality {
  margin: 12px 22px;
  border-top: 1px solid var(--line);
  padding: 14px 0;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
summary span {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 12px;
}
article {
  padding: 14px 0;
  border-bottom: 1px solid var(--line);
}
h3 {
  font-size: 14px;
  margin-bottom: 8px;
}
p,
dd,
li {
  font-size: 13px;
  line-height: 1.7;
}
dl {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 8px;
  margin-top: 10px;
}
dt {
  font-size: 12px;
  color: var(--text-secondary);
}
dd {
  margin: 0;
}
</style>
