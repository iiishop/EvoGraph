<script setup lang="ts">
import { computed, ref } from 'vue';
import { ScanSearch, ShieldCheck, Download } from 'lucide-vue-next';
import type { Project, Milestone } from '../../types';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import { command } from '../../api/client';
const props = defineProps<{ project: Project; milestone: Milestone }>();
const agent = useAgent(),
  { state } = useWorkspace();
const error = ref('');
const checks = computed(() =>
  props.project.light_checks
    .filter((c) => c.milestone_id === props.milestone.id)
    .slice(-5)
    .reverse(),
);
function investigate() {
  agent.send(
    props.project.id,
    `请调查里程碑 ${props.milestone.id} 的未完成调查项。先读相关源码，通过 resolve_investigation 逐项记录真实依据。证据不足时在输入区向我提问，不要编造结论。`,
  );
}
function verify() {
  agent.send(
    props.project.id,
    `请为里程碑 ${props.milestone.id} 执行轻量验收。我授权本轮运行仓库中已发现的代表性检查。先 discover_checks，读取相关测试，选择与本次交付相关的检查，说明覆盖的行为和未覆盖的部分后 run_light_check。不要把结果升级为正式验收完成。没有合适检查请提问。`,
    undefined,
    [],
    props.milestone.id,
  );
}
async function exportTask() {
  try {
    const task = await command('verification.export', {
      project_id: props.project.id,
      milestone_id: props.milestone.id,
    });
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(task, null, 2)], { type: 'application/json' }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = `${props.milestone.id}-verification.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error.value = String(e);
  }
}
</script>
<template>
  <section class="agent-assurance">
    <h4>交给 Agent</h4>
    <p>调查源码，选择与本次交付相关的检查。需要你补充的信息会出现在下方输入区。</p>
    <div class="assurance-buttons">
      <button
        class="button secondary"
        :disabled="state.busy || !!project.question"
        @click="investigate"
      >
        <ScanSearch :size="16" />Agent 调查</button
      ><button class="button primary" :disabled="state.busy || !!project.question" @click="verify">
        <ShieldCheck :size="16" />Agent 轻量验收
      </button>
    </div>
    <small
      >检查会执行仓库测试或脚本，单项最长 60 秒。轻量通过不会将节点标为正式完成。</small
    >
    <button class="text-button" :disabled="state.busy" @click="exportTask">
      <Download :size="14" />导出给外部验收 Agent
    </button>
    <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
    <details v-for="check in checks" :key="check.id" class="light-check">
      <summary>
        <b :class="'check-' + check.result.toLowerCase()">{{
          check.result === 'REVIEW' ? '待审阅' : check.result
        }}</b
        >{{ check.kind.split(':')[0]
        }}<small v-if="check.fingerprint !== project.baselines.at(-1)?.fingerprint"
          >基线已变更</small
        >
      </summary>
      <p>{{ check.rationale }}</p>
      <code>{{ check.command.join(' ') }}</code>
      <pre>{{ check.output }}</pre>
    </details>
  </section>
</template>
