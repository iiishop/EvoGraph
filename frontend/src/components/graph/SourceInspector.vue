<script setup lang="ts">
import { computed } from 'vue';
import { X, FileCode } from 'lucide-vue-next';
import type { Milestone, Project } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
import { milestoneTurnHistory, dependencyTypeLabels } from '../../lib/turnSummary';
import AgentTurnSummary from '../agent/AgentTurnSummary.vue';
const props = defineProps<{ milestone: Milestone; project: Project }>();
defineEmits<{ locate: [id: string] }>();
const { selectNode } = useWorkspace();
const allMilestones = computed(() => [
  ...props.project.milestones,
  ...(props.project.source_milestones ?? []),
]);
const history = computed(() => milestoneTurnHistory(props.project, props.milestone.id));
const preview = computed(() =>
  props.milestone.intent.length > 180
    ? `${props.milestone.intent.slice(0, 180)}…`
    : props.milestone.intent,
);
</script>
<template>
  <aside class="inspector explanatory-inspector" aria-label="源码里程碑详情">
    <header>
      <span class="source-badge">SRC 已实现里程碑 · {{ milestone.id }}</span
      ><button class="icon-button" aria-label="关闭源码详情" @click="selectNode(null)">
        <X :size="18" />
      </button>
    </header>
    <div class="inspector-scroll" tabindex="0" aria-label="源码里程碑完整说明">
      <div class="inspector-panel">
        <div class="inspector-title">
          <h2>{{ milestone.title }}</h2>
          <span class="source-badge">根据源码推导</span>
        </div>
        <p class="inspector-outcome">{{ preview || '尚未补充能力说明。' }}</p>
        <details class="inspector-intent">
          <summary>展开完整说明</summary>
          <strong>{{ milestone.title }}</strong>
          <p>{{ milestone.intent }}</p>
        </details>
        <p
          v-if="milestone.source_baseline_id !== project.baselines.at(-1)?.id"
          class="source-baseline-warning"
        >
          基线已变化，此里程碑依据旧基线推导，等待重新调查。
        </p>
        <details class="inspector-disclosure">
          <summary>
            <strong>交付约定</strong
            ><span>{{ milestone.source_behaviors?.length ?? 0 }} 项源码行为</span>
          </summary>
          <div class="disclosure-content">
            <section>
              <h3>交付范围</h3>
              <code v-for="path in milestone.scope" :key="path" class="scope-path">{{ path }}</code>
            </section>
            <section>
              <h3>已实现的行为契约</h3>
              <p v-if="!milestone.source_behaviors?.length" class="muted">尚未记录源码行为。</p>
              <article
                v-for="behavior in milestone.source_behaviors"
                :key="behavior.key"
                class="source-contract"
              >
                <p>{{ behavior.statement }}</p>
                <code v-for="path in behavior.source_refs" :key="path" class="scope-path">{{
                  path
                }}</code>
              </article>
            </section>
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong>依赖与依据</strong><span>{{ milestone.dependencies.length }} 项前置</span>
          </summary>
          <div class="disclosure-content">
            <p v-if="!milestone.dependencies.length" class="muted">没有记录前置能力。</p>
            <article v-for="id in milestone.dependencies" :key="id" class="source-contract">
              <small
                >{{ dependencyTypeLabels[milestone.dependency_types?.[id]] || '实现' }}前置 ·
                {{ id }}</small
              ><button
                class="dependency-link"
                :disabled="!allMilestones.some((item) => item.id === id)"
                @click="$emit('locate', id)"
              >
                {{ allMilestones.find((item) => item.id === id)?.title ?? id }}
              </button>
              <p>{{ milestone.dependency_reasons[id] || '尚未记录依赖依据。' }}</p>
            </article>
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong><FileCode :size="14" /> 源码依据</strong
            ><span>{{ milestone.source_refs?.length ?? 0 }} 项引用</span>
          </summary>
          <div class="disclosure-content">
            <code v-for="path in milestone.source_refs" :key="path" class="scope-path">{{
              path
            }}</code>
            <p>
              推导依据：基线 B{{
                project.baselines.find((b) => b.id === milestone.source_baseline_id)?.number ??
                '未知'
              }}
            </p>
            <p>{{ project.source_analysis_summary }}</p>
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong>变更记录</strong><span>{{ history.length }} 轮相关变化</span>
          </summary>
          <div class="disclosure-content">
            <p v-if="!history.length" class="muted">暂无明确关联此节点的已保存变更记录。</p>
            <article v-for="item in history" :key="item.id" class="node-change-record">
              <time>{{ new Date(item.createdAt).toLocaleString() }}</time
              ><AgentTurnSummary
                :summary="item.summary"
                :milestones="allMilestones"
                @locate="$emit('locate', $event)"
              />
            </article>
          </div>
        </details>
        <p class="inspector-composer-note">
          由 Agent 根据源码倒推的已实现交付能力，可作为未来里程碑的前置能力；不代表真实历史 PR
          或已通过独立验收，也不计入待交付任务。
        </p>
        <p class="inspector-composer-note">继续在项目对话中补充想法，由 Agent 判断影响范围。</p>
      </div>
    </div>
  </aside>
</template>
