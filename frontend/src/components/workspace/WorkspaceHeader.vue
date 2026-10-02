<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  ChevronDown,
  SlidersHorizontal,
  RefreshCw,
  FolderOpen,
  Flag,
  X,
  AlertCircle,
} from 'lucide-vue-next';
import type { Project } from '../../types';
import { acceptanceSummary } from '../../lib/acceptance';
import { useWorkspace } from '../../composables/useWorkspace';
import { useBaseline } from '../../composables/useBaseline';
import BaselineMilestoneStatus from '../graph/BaselineMilestoneStatus.vue';

const props = defineProps<{ project: Project }>();
const emit = defineEmits<{ edit: [] }>();
const { state } = useWorkspace();
const baseline = useBaseline();
const root = ref<HTMLElement>();
const overview = ref<HTMLElement>();
const overviewButton = ref<HTMLButtonElement>();
const overviewOpen = ref(false);
const instantOverview = ref(true);
const overviewId = computed(() => `project-overview-${props.project.id}`);
const latestBaseline = computed(() => props.project.baselines.at(-1));
const latestTarget = computed(() => props.project.targets.at(-1));
const targetStatement = computed(() => latestTarget.value?.statement?.trim() ?? '');
const description = computed(() => props.project.description?.trim() ?? '');
const goalPreview = computed(
  () => targetStatement.value || description.value || '尚未定义最终目标，可在下方描述期望结果',
);
const acceptance = computed(() => acceptanceSummary(props.project));
const baselineAttention = computed(() => {
  const current = latestBaseline.value;
  if (!props.project.repository) return '';
  if (!current) return '待读取基线';
  if (!current.complete) return '扫描不完整';
  if (props.project.source_analysis_baseline_id !== current.id)
    return props.project.source_milestones?.length ? '基线待更新' : '待分析源码';
  return '';
});

function toggleOverview(event: MouseEvent) {
  // Keyboard and assistive activation must never wait for decorative motion.
  instantOverview.value = event.detail === 0;
  overviewOpen.value = !overviewOpen.value;
}
function closeOverview(restoreFocus = false, instant = true) {
  instantOverview.value = instant;
  overviewOpen.value = false;
  if (restoreFocus) overviewButton.value?.focus();
}
function editProject() {
  // The settings dialog must return focus to a visible control, not a hidden
  // repository action inside the disclosure when the dialog later closes.
  closeOverview(true);
  emit('edit');
}
function escapeOverview(event: KeyboardEvent) {
  if (!overviewOpen.value || event.key !== 'Escape') return;
  event.preventDefault();
  event.stopPropagation();
  closeOverview(true);
}
function outsidePointer(event: PointerEvent) {
  if (overviewOpen.value && event.target instanceof Node && !root.value?.contains(event.target))
    closeOverview(false, false);
}
function leaveOverview(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && !root.value?.contains(event.relatedTarget))
    closeOverview();
}
watch(
  () => props.project.id,
  () => {
    const focusInside = Boolean(overview.value?.contains(document.activeElement));
    closeOverview(focusInside);
    if (overview.value) overview.value.scrollTop = 0;
  },
);
onMounted(() => document.addEventListener('pointerdown', outsidePointer, true));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outsidePointer, true));
</script>
<template>
  <header
    ref="root"
    class="workspace-header compact-projectbar"
    @keydown="escapeOverview"
    @focusout="leaveOverview"
  >
    <div class="projectbar-identity">
      <div class="projectbar-title">
        <h1 :title="project.name">{{ project.name }}</h1>
        <span v-if="project.is_demo" class="projectbar-demo">示例 · 未执行</span>
      </div>
      <p class="projectbar-goal" :title="goalPreview">
        <Flag :size="12" aria-hidden="true" />
        <span class="projectbar-goal-label">{{
          targetStatement ? '最终目标' : description ? '项目简介' : '目标待定义'
        }}</span>
        <span class="projectbar-goal-text">{{ goalPreview }}</span>
      </p>
    </div>
    <div class="projectbar-actions">
      <span v-if="state.busy" class="projectbar-busy" role="status">
        <RefreshCw :size="13" class="spinning" aria-hidden="true" />处理中
      </span>
      <button
        v-if="!project.repository"
        class="button secondary projectbar-connect"
        type="button"
        :disabled="state.busy"
        title="尚未连接仓库；打开项目设置，连接本地仓库"
        @click="editProject"
      >
        <FolderOpen :size="14" aria-hidden="true" />连接仓库
      </button>
      <button
        ref="overviewButton"
        class="project-overview-trigger"
        type="button"
        :aria-expanded="overviewOpen"
        :aria-controls="overviewId"
        :title="baselineAttention ? `项目概览 · ${baselineAttention}` : '查看目标、仓库与验证进度'"
        @click="toggleOverview"
      >
        项目概览
        <span v-if="baselineAttention" class="projectbar-attention">
          <AlertCircle :size="13" aria-hidden="true" /><span>{{ baselineAttention }}</span>
        </span>
        <ChevronDown :size="14" :class="{ expanded: overviewOpen }" aria-hidden="true" />
      </button>
      <Transition name="project-overview" :css="!instantOverview">
        <section
          v-show="overviewOpen"
          :id="overviewId"
          ref="overview"
          class="project-overview"
          :aria-hidden="!overviewOpen"
          :inert="overviewOpen ? undefined : true"
          role="region"
          :aria-labelledby="`${overviewId}-title`"
        >
          <div class="project-overview-heading">
            <h2 :id="`${overviewId}-title`">项目概览</h2>
            <button
              class="icon-button"
              type="button"
              aria-label="收起项目概览"
              @click="closeOverview(true, $event.detail === 0)"
            >
              <X :size="17" />
            </button>
          </div>
          <div class="project-overview-section project-overview-intro">
            <h3>{{ project.name }}</h3>
            <p>{{ description || '尚未填写项目描述' }}</p>
            <span class="project-overview-local">
              <span v-if="project.is_demo">示例 · 未执行 · </span>SQLite 本地存储
            </span>
          </div>
          <section class="project-overview-section">
            <h3>最终目标 <span class="project-overview-caption">项目结果 · 非 PR 节点</span></h3>
            <p>{{ targetStatement || '尚未定义最终目标，可在下方对话中描述期望结果。' }}</p>
            <div class="project-overview-acceptance" :class="{ achieved: acceptance.achieved }">
              <span class="project-overview-progress-label">
                <strong>{{ acceptance.passed }} / {{ acceptance.total }}</strong>
                {{ acceptance.total ? '最终目标已验证' : '尚未定义最终目标标准' }}
              </span>
              <span class="progress-track" aria-hidden="true"
                ><span
                  :style="{
                    width: `${acceptance.total ? (acceptance.passed / acceptance.total) * 100 : 0}%`,
                  }"
                ></span
              ></span>
            </div>
            <p class="project-overview-footnote">
              步骤专属
              {{ acceptance.stepOnlyTotal }} 项，仍须通过所在里程碑的验收，不计入最终目标进度。
            </p>
          </section>
          <dl class="project-overview-versions">
            <div>
              <dt>最终目标版本</dt>
              <dd>V{{ latestTarget?.number ?? 0 }}</dd>
            </div>
            <div>
              <dt>规划版本</dt>
              <dd>P{{ project.plans.at(-1)?.number ?? 0 }}</dd>
            </div>
            <div>
              <dt>仓库基线</dt>
              <dd>
                {{
                  latestBaseline
                    ? `B${latestBaseline.number}${latestBaseline.complete ? '' : ' · 扫描不完整'}`
                    : project.repository
                      ? '尚未读取'
                      : '未连接仓库'
                }}
              </dd>
            </div>
          </dl>
          <section class="project-overview-section project-overview-repository">
            <h3>本地仓库</h3>
            <p class="project-overview-path">
              {{ project.repository || '尚未连接本地仓库，可先规划项目' }}
            </p>
            <p v-if="project.repository && !latestBaseline" class="project-overview-footnote">
              已关联 · 等待读取基线
            </p>
            <p
              v-if="project.repository && state.settings?.provider"
              :id="`${overviewId}-transmission`"
              class="source-analysis-note"
            >
              读取或倒推时，会按需将源码上下文发送给已配置的模型，分析已实现能力。
            </p>
            <button
              class="button secondary"
              type="button"
              :disabled="state.busy"
              :aria-describedby="
                project.repository && state.settings?.provider
                  ? `${overviewId}-transmission`
                  : undefined
              "
              :title="
                project.repository
                  ? state.settings?.provider
                    ? '读取仓库基线，按需将源码上下文发送给已配置模型分析'
                    : '只读扫描仓库文件；配置模型后可继续分析源码'
                  : '打开项目设置，连接本地仓库'
              "
              @click="project.repository ? baseline.refresh(project) : editProject()"
            >
              <RefreshCw
                v-if="project.repository"
                :size="14"
                :class="{ spinning: state.busy }"
                aria-hidden="true"
              />
              <FolderOpen v-else :size="14" aria-hidden="true" />
              {{ !project.repository ? '连接仓库' : latestBaseline ? '刷新基线' : '读取基线' }}
            </button>
            <BaselineMilestoneStatus :project="project" />
            <p v-if="project.source_analysis_summary" class="project-overview-source-summary">
              {{ project.source_analysis_summary }}
            </p>
          </section>
        </section>
      </Transition>
      <button
        class="icon-button bordered projectbar-settings"
        type="button"
        aria-label="项目设置"
        @click="editProject"
      >
        <SlidersHorizontal :size="16" />
      </button>
    </div>
  </header>
</template>
