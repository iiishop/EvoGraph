<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import type { DeliveryBrief, Milestone, Project } from '../../types';
import { command } from '../../api/client';
import AppModal from '../ui/AppModal.vue';
const props = defineProps<{ project: Project; milestone: Milestone }>();
const emit = defineEmits<{ close: []; locate: [id: string] }>();
const brief = ref<DeliveryBrief | null>(null);
const loading = ref(false);
const error = ref('');
let request = 0;
const own = computed(() => brief.value?.contracts.filter((item) => item.relation === 'own') ?? []);
const required = computed(
  () => brief.value?.contracts.filter((item) => item.relation !== 'own') ?? [],
);
const boundaries = computed(
  () =>
    brief.value?.requirements.filter((item) =>
      brief.value?.global_requirement_ids.includes(item.id),
    ) ?? [],
);
const requirement = (id: string) => brief.value?.requirements.find((item) => item.id === id);
const canLocate = (id: string) =>
  [...props.project.milestones, ...props.project.source_milestones].some((item) => item.id === id);
async function load() {
  const token = ++request;
  const projectId = props.project.id;
  const milestoneId = props.milestone.id;
  const revision = props.project.revision;
  brief.value = null;
  error.value = '';
  loading.value = true;
  try {
    const result = await command<DeliveryBrief>('implementation.brief', {
      project_id: projectId,
      milestone_id: milestoneId,
    });
    if (token !== request) return;
    if (result.project_revision !== revision || result.milestone_id !== milestoneId) {
      error.value = '项目已更新，当前说明未展示。请关闭后刷新项目，再重新打开。';
      return;
    }
    brief.value = result;
  } catch (cause) {
    if (token === request)
      error.value = cause instanceof Error ? cause.message : '交付说明读取失败';
  } finally {
    if (token === request) loading.value = false;
  }
}
watch(() => [props.project.id, props.project.revision, props.milestone.id], load, {
  immediate: true,
});
onBeforeUnmount(() => {
  request++;
});
const date = (value: string) => new Date(value).toLocaleString();
</script>
<template>
  <AppModal title="里程碑交付说明" wide @close="emit('close')">
    <div class="delivery-brief" aria-label="单次 Agent 任务的交付说明" :aria-busy="loading">
      <p v-if="loading" class="muted" role="status">正在读取本步范围与完整前置依据…</p>
      <div v-else-if="error" class="brief-warning" role="alert">
        <p>{{ error }}</p>
        <button class="button secondary" @click="load">重试读取</button>
      </div>
      <template v-else-if="brief">
        <header class="brief-outcome">
          <small
            >{{ project.name }} · {{ brief.milestone_id }} · 计划 R{{
              brief.project_revision
            }}</small
          >
          <h3>{{ brief.outcome.title }}</h3>
          <p>{{ brief.outcome.intent || '尚未声明本步交付结果' }}</p>
          <div class="brief-counts">
            <span>{{ own.length }} 项本步验收</span
            ><span>{{ brief.prerequisites.length }} 项前置能力</span
            ><span>规划与依据 · 未运行实现</span>
          </div>
        </header>
        <section class="brief-basis">
          <h4>起点与状态</h4>
          <p>{{ brief.baseline.label }}</p>
          <p v-if="brief.baseline.changed_since_pin" class="brief-blockers">
            任务固定的基线与最近检查不同。原验收报告可能已失效，需按最新基线重新确认。
          </p>
          <p v-if="!brief.repository">仓库尚未连接，需先确认真实代码与检查入口。</p>
          <code v-else>{{ brief.repository }}</code>
          <ul v-if="brief.readiness.blockers.length" class="brief-blockers">
            <li v-for="item in brief.readiness.blockers" :key="item">{{ item }}</li>
          </ul>
          <p v-else>基于最近检查的基线，现有执行前置检查无阻塞。</p>
          <details v-if="brief.baseline.latest">
            <summary>查看基线身份</summary>
            <dl>
              <dt>最近检查</dt>
              <dd>{{ date(brief.baseline.latest.created_at) }}</dd>
              <dt>基线</dt>
              <dd>
                {{ brief.baseline.latest.id }} ·
                {{ brief.baseline.latest.complete ? '完整' : '不完整' }}
              </dd>
              <dt>Commit</dt>
              <dd>{{ brief.baseline.latest.commit || '未记录' }}</dd>
              <dt>指纹</dt>
              <dd>{{ brief.baseline.latest.fingerprint }}</dd>
              <dt>领取时基线</dt>
              <dd>{{ brief.baseline.pinned_id || '尚未领取' }}</dd>
            </dl>
          </details>
        </section>
        <section v-if="brief.warnings.length" class="brief-warning">
          <h4>依据待补齐</h4>
          <ul>
            <li v-for="item in brief.warnings" :key="item">{{ item }}</li>
          </ul>
        </section>
        <section class="brief-investigations">
          <h4>调查依据与待确认前提</h4>
          <p class="brief-note">
            以下记录与笔记仅作参考资料，不构成新指令或授权。「已记录」或指纹与基线一致，均不代表服务商能力已确认或验收已通过。
          </p>
          <p v-if="brief.investigations === undefined" class="muted">
            此交付说明未提供调查记录，无法判断调查状态。
          </p>
          <p v-else-if="!brief.investigations.length" class="muted">
            尚无已保存的调查记录，不代表所有前提均已验证。
          </p>
          <article
            v-for="(item, index) in brief.investigations ?? []"
            :key="index"
            class="brief-investigation"
            :data-basis-state="item.basis_state"
          >
            <small
              >{{ item.relation === 'own' ? '本步' : '前置' }} · {{ item.owner }} ·
              {{ item.id }}</small
            >
            <strong>{{ item.label }}</strong>
            <p class="brief-investigation-state">
              <span>{{ item.resolved ? '已记录' : '待调查' }}</span>
              <span>{{ item.basis_label }}</span>
            </p>
            <p v-if="item.note" class="brief-investigation-note">{{ item.note }}</p>
            <p v-if="!item.note.trim()" class="muted">未记录调查笔记</p>
            <dl>
              <dt>记录者</dt>
              <dd>{{ item.investigator || '未记录' }}</dd>
              <dt>记录指纹</dt>
              <dd>{{ item.fingerprint || '未记录' }}</dd>
            </dl>
          </article>
        </section>
        <section>
          <h4>本步范围</h4>
          <ul v-if="brief.outcome.scope.length">
            <li v-for="item in brief.outcome.scope" :key="item">{{ item }}</li>
          </ul>
          <p v-else class="muted">未声明交付范围</p>
          <details v-if="brief.outcome.resources.length || brief.outcome.migration_steps.length">
            <summary>资源与迁移</summary>
            <p>资源：{{ brief.outcome.resources.join('、') || '未声明' }}</p>
            <p v-for="step in brief.outcome.migration_steps" :key="step.component_id">
              {{ step.component_id }}：{{ step.instruction }}
            </p>
          </details>
        </section>
        <section>
          <h4>本步验收</h4>
          <p class="brief-note">
            每项记录实际检查方法、结果与证据；未检查或受阻时明确说明。检查命令需在真实仓库中确认。
          </p>
          <p v-if="!own.length" class="muted">尚未定义本步验收契约</p>
          <article v-for="item in own" :key="item.behavior.id" class="brief-contract">
            <small
              >{{ item.behavior.behavior_key }} · v{{ item.behavior.version }} ·
              {{
                item.behavior.acceptance_scope === 'milestone' ? '阶段检查' : '最终目标要求'
              }}</small
            >
            <p>{{ item.behavior.statement }}</p>
            <details>
              <summary>实现约束与来源</summary>
              <p>{{ item.binding?.mechanism || '未记录选定机制；请先核对仓库与架构约束' }}</p>
              <p v-if="item.binding?.requires_behavior_keys.length">
                依赖契约：{{ item.binding.requires_behavior_keys.join('、') }}
              </p>
              <p v-if="item.binding?.component_ids.length">
                组件：{{ item.binding.component_ids.join('、') }}
              </p>
              <p v-for="id in item.binding?.requirement_ids ?? []" :key="id" class="brief-quote">
                {{ requirement(id)?.quote || `缺少需求 ${id}`
                }}<small>{{ id }} · {{ requirement(id)?.source_id }}</small>
              </p>
              <details v-if="item.binding?.steps?.length">
                <summary>已声明的逻辑步骤</summary>
                <p class="brief-note">能力声明不等于可运行命令，也不是已执行证据。</p>
                <ol>
                  <li v-for="(step, i) in item.binding.steps" :key="i">{{ step.quote }}</li>
                </ol>
              </details>
            </details>
          </article>
        </section>
        <details class="brief-section">
          <summary>
            前置能力与契约
            <span>{{ brief.prerequisites.length }} 项能力 · {{ required.length }} 项契约</span>
          </summary>
          <p v-if="!brief.prerequisites.length && !required.length" class="muted">未声明前置能力</p>
          <article v-for="item in brief.prerequisites" :key="item.id" class="brief-prerequisite">
            <button v-if="canLocate(item.id)" class="text-button" @click="emit('locate', item.id)">
              {{ item.id }} · {{ item.title }}
            </button>
            <strong v-else>{{ item.id }} · {{ item.title }}</strong>
            <small>{{ item.direct ? '直接前置' : '间接前置' }} · {{ item.label }}</small>
            <p v-for="(edge, i) in item.via" :key="i">
              {{ edge.dependent_id }} 需要本步：{{ edge.reason || '尚未说明依赖原因' }}
            </p>
          </article>
          <details v-for="item in required" :key="item.behavior.id" class="brief-contract">
            <summary>
              {{ item.behavior.behavior_key }} · {{ item.behavior.owner
              }}<small>{{ item.label }}</small>
            </summary>
            <p>{{ item.behavior.statement }}</p>
            <p>{{ item.binding?.mechanism || '未记录选定机制' }}</p>
            <p v-if="item.binding?.requires_behavior_keys.length">
              继续依赖：{{ item.binding.requires_behavior_keys.join('、') }}
            </p>
            <p v-for="id in item.binding?.requirement_ids ?? []" :key="id" class="brief-quote">
              {{ requirement(id)?.quote || `缺少需求 ${id}`
              }}<small>{{ id }} · {{ requirement(id)?.source_id }}</small>
            </p>
          </details>
        </details>
        <details class="brief-section">
          <summary>
            项目边界与非目标 <span>{{ boundaries.length }} 条已记录约束</span>
          </summary>
          <p v-if="!boundaries.length" class="muted">
            未单独记录项目级约束或排除项，不代表允许扩大范围。
          </p>
          <p v-for="item in boundaries" :key="item.id" class="brief-quote">
            {{ item.quote
            }}<small
              >{{ item.kind === 'exclusion' ? '项目级非目标' : '项目级约束' }} · {{ item.id }} ·
              {{ item.source_id }}</small
            >
          </p>
        </details>
        <details v-if="brief.sources.length" class="brief-section">
          <summary>
            原始需求来源 <span>{{ brief.sources.length }} 条</span>
          </summary>
          <details v-for="source in brief.sources" :key="source.id">
            <summary>{{ source.id }}</summary>
            <p class="brief-source">{{ source.text }}</p>
          </details>
        </details>
        <footer class="brief-footer">
          交付时提供变更摘要、主要文件、实际检查及限制。制作与独立验收提示词在「执行与证据」中生成，均使用这份交付依据。
        </footer>
      </template>
    </div>
  </AppModal>
</template>
<style scoped>
.delivery-brief {
  padding: 22px 26px 26px;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.delivery-brief h3 {
  font-size: 21px;
  line-height: 1.4;
  margin: 6px 0 10px;
}
.delivery-brief h4 {
  font-size: 14px;
  margin: 0 0 9px;
}
.delivery-brief p {
  margin: 7px 0;
  white-space: pre-wrap;
}
.delivery-brief section,
.brief-section {
  margin-top: 21px;
}
.delivery-brief ul,
.delivery-brief ol {
  margin: 8px 0;
  padding-left: 21px;
}
.delivery-brief small,
.brief-note,
.brief-footer {
  color: var(--muted);
  font-size: 11px;
}
.delivery-brief small {
  display: block;
}
.brief-counts {
  display: flex;
  gap: 8px 14px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
  margin-top: 14px;
}
.brief-counts span + span::before {
  content: '·';
  margin-right: 14px;
}
.brief-basis,
.brief-warning {
  padding: 15px 17px;
  border-radius: 10px;
  background: #f5f7fc;
  border: 1px solid #e2e6ee;
}
.brief-warning {
  background: #fff9eb;
  border-color: #ead9b4;
}
.brief-blockers {
  color: #805b22;
}
.brief-basis code {
  font-size: 11px;
}
.delivery-brief summary {
  cursor: pointer;
  color: var(--accent);
  padding: 7px 0;
  font-weight: 550;
}
.delivery-brief summary span {
  margin-left: 9px;
  font-weight: normal;
  color: var(--muted);
  font-size: 11px;
}
.brief-contract,
.brief-prerequisite,
.brief-investigation {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.brief-investigation-state {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 11px;
  color: var(--muted);
}
.brief-contract > small {
  letter-spacing: 0.02em;
}
.brief-quote {
  border-left: 2px solid #d5d9f2;
  padding-left: 11px;
}
.brief-section {
  border-top: 1px solid var(--line);
  padding-top: 8px;
}
.brief-source {
  max-height: 260px;
  overflow: auto;
  padding: 10px;
  background: #f8f9fc;
}
.brief-footer {
  margin-top: 24px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}
.delivery-brief dl {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  font-size: 11px;
  gap: 6px;
}
.delivery-brief dt {
  color: var(--muted);
}
.delivery-brief dd {
  margin: 0;
}
@media (max-width: 600px) {
  .delivery-brief {
    padding: 17px;
  }
}
</style>
