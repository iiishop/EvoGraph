<script setup lang="ts">
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue';
import { useAgent } from '../../composables/useAgent';
import WorkspaceHeader from './WorkspaceHeader.vue';
import UnifiedPlanBar from './UnifiedPlanBar.vue';
import PlanCandidatePanel from './PlanCandidatePanel.vue';
import GraphToolbar from '../graph/GraphToolbar.vue';
import MilestoneGraph from '../graph/MilestoneGraph.vue';
import MilestoneFinder from '../graph/MilestoneFinder.vue';
import MilestoneInspector from '../graph/MilestoneInspector.vue';
import SourceInspector from '../graph/SourceInspector.vue';
import { useSurfaceMotion } from '../../composables/useSurfaceMotion';
import { workspaceViews } from '../../lib/workspaceViews';
import AgentDock from '../agent/AgentDock.vue';
import { useWorkspace, type WorkspaceFollowBoundary } from '../../composables/useWorkspace';
import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const agent = useAgent();
const candidatePreview = ref(false);
const candidate = computed(() => props.project.plan_candidate);
watch(
  () => `${props.project.id}:${props.project.created_at}:${candidate.value?.id ?? ''}`,
  () => {
    candidatePreview.value = Boolean(
      candidate.value && !['applied', 'discarded'].includes(candidate.value.status),
    );
  },
  { immediate: true },
);
watch(
  () => [candidate.value?.status, props.project.revision],
  () => {
    if (
      candidate.value?.status === 'applied' &&
      props.project.revision > candidate.value.base_revision
    )
      candidatePreview.value = false;
  },
);
const showingCandidate = computed(() => candidatePreview.value && Boolean(candidate.value));
function followPage() {
  if (
    mounted &&
    agent.state.projectId === props.project.id &&
    agent.state.follow[props.project.id] !== false &&
    workspaceViews.some((v) => v.id === agent.state.view)
  )
    tab.value = agent.state.view;
}
function resume() {
  if (!mounted) return;
  followBoundary.value.resume++;
  agent.resumeFollow(props.project.id);
  followPage();
}
watch(() => agent.state.navigationTick, followPage);
defineEmits<{ edit: [] }>();
const { state, selected, selectNode, bindWorkspaceTab } = useWorkspace();
const tabSession = computed(() => bindWorkspaceTab(props.project));
const followBoundary = ref<WorkspaceFollowBoundary>({ navigationTick: 0, pulse: 0, resume: 0 });
watch(
  () => tabSession.value.key,
  () => {
    // An accepted incarnation/remount owns only live events after this point.
    followBoundary.value = {
      navigationTick: agent.state.navigationTick,
      pulse: agent.state.pulse,
      resume: 0,
    };
  },
  { immediate: true, flush: 'sync' },
);
const tab = computed({
  get: () => tabSession.value.tab.value,
  set: (value) => {
    if (mounted && workspaceViews.some((view) => view.id === value))
      tabSession.value.tab.value = value;
  },
});
let mounted = true;
// Child events captured before replacement must not act on a restored/recreated project.
const viewActions = computed(() => {
  const key = tabSession.value.key;
  const projectId = props.project.id;
  const incarnation = props.project.created_at;
  const current = () =>
    mounted &&
    state.page === 'projects' &&
    state.project?.id === projectId &&
    state.project.created_at === incarnation &&
    tabSession.value.key === key;
  return {
    select: (value: string) => {
      if (!current()) return;
      tab.value = value;
      agent.freeView(props.project.id);
    },
    resume: () => {
      if (current()) resume();
    },
    locate: (id: string) => {
      if (current()) void locate(id);
    },
    receipt: (eventId: string, trigger: HTMLElement) => {
      if (current()) void composer.value?.openReceipt(eventId, trigger);
    },
  };
});
const viewMotion = useSurfaceMotion();
const inspectorMotion = useSurfaceMotion('right');
const inspectorSurface = ref<HTMLElement>();
watch(
  () => selected.value?.id,
  (id, previous) => {
    if (id && previous && id !== previous) inspectorMotion.finish(inspectorSurface.value);
  },
  { flush: 'post' },
);
const inspectorLeaving = ref(false);
const inspectorOpen = computed(() =>
  Boolean(!showingCandidate.value && selected.value && tab.value === 'graph'),
);
watch(tab, () => viewMotion.reveal(planningContent.value), { flush: 'post' });
const planningContent = ref<HTMLElement>();
const composer = ref<InstanceType<typeof AgentDock>>();
const finderOpen = ref(false);
function openFinder() {
  agent.freeView(props.project.id);
  finderOpen.value = true;
}
const milestones = computed(() => [
  ...(props.project.source_milestones ?? []),
  ...props.project.milestones,
]);
let locateSequence = 0;
onBeforeUnmount(() => {
  mounted = false;
  locateSequence++;
});
async function locate(id: string) {
  if (!mounted || !milestones.value.some((item) => item.id === id)) return;
  candidatePreview.value = false;
  const sequence = ++locateSequence;
  const projectId = props.project.id;
  const tabKey = tabSession.value.key;
  finderOpen.value = false;
  tab.value = 'graph';
  selectNode(id);
  agent.freeView(props.project.id);
  await nextTick();
  if (
    sequence !== locateSequence ||
    tabSession.value.key !== tabKey ||
    props.project.id !== projectId ||
    state.project?.id !== projectId ||
    state.selectedId !== id ||
    tab.value !== 'graph'
  )
    return;
  if (planningContent.value) planningContent.value.scrollTop = 0;
  graph.value?.locate(id);
}
const activeView = computed(() => workspaceViews.find((v) => v.id === tab.value)!);
const graph = ref<InstanceType<typeof MilestoneGraph>>();
watch(tab, (value) => {
  if (value !== 'graph') finderOpen.value = false;
});
</script>
<template>
  <main class="project-workspace">
    <div class="workspace-chrome">
      <WorkspaceHeader :project="project" @edit="$emit('edit')" />
      <UnifiedPlanBar
        :project="project"
        :preview="showingCandidate"
        @preview="candidatePreview = $event"
      />
      <GraphToolbar
        v-show="!showingCandidate"
        :tab="tab"
        :count="project.milestones.length + (project.source_milestones?.length ?? 0)"
        @tab="viewActions.select"
        @find="openFinder"
        @fit="graph?.fit()"
        @reset="graph?.reset()"
      />
    </div>
    <section
      class="workspace-body"
      :class="{
        'architecture-active': !showingCandidate && tab === 'architecture',
        'workspace-detail-open': inspectorOpen || inspectorLeaving,
        'question-active': Boolean(project.question),
      }"
    >
      <div class="planning-region">
        <div
          ref="planningContent"
          class="planning-content"
          :class="{
            'graph-detail-open': !showingCandidate && selected && tab === 'graph',
            'candidate-preview-active': showingCandidate,
          }"
        >
          <PlanCandidatePanel
            v-if="showingCandidate && candidate"
            :candidate="candidate"
            :canonical="project"
          />
          <component
            v-show="!showingCandidate"
            :is="activeView.component"
            :key="`${project.id}:${tabSession.key}:${tab}`"
            ref="graph"
            :project="project"
            v-bind="tab === 'graph' || tab === 'architecture' ? { followBoundary } : {}"
            @compose="composer?.focus()"
            @receipt="viewActions.receipt"
          />
        </div>
      </div>
      <!-- One surface owns open/close motion. A node change only replaces its
           inner inspector, so frequent selections never animate reading text. -->
      <Transition
        :css="false"
        @enter="inspectorMotion.enter"
        @leave="inspectorMotion.leave"
        @enter-cancelled="inspectorMotion.cancel"
        @leave-cancelled="inspectorMotion.cancel"
        @before-enter="inspectorLeaving = false"
        @before-leave="inspectorLeaving = true"
        @after-leave="inspectorLeaving = false"
      >
        <div
          v-if="!showingCandidate && selected && tab === 'graph'"
          ref="inspectorSurface"
          class="inspector-surface"
        >
          <component
            :is="selected.origin === 'source' ? SourceInspector : MilestoneInspector"
            :key="selected.id"
            :milestone="selected"
            :project="project"
            @locate="viewActions.locate"
          />
        </div>
      </Transition>
      <AgentDock
        ref="composer"
        :project="project"
        :review-owner="tabSession.key"
        :view="tab"
        :compact="
          showingCandidate || tab === 'architecture' || (tab === 'graph' && Boolean(selected))
        "
        @resume="viewActions.resume"
        @locate="viewActions.locate"
      />
    </section>
    <MilestoneFinder
      v-if="!showingCandidate && finderOpen && tab === 'graph'"
      :milestones="milestones"
      @select="viewActions.locate"
      @close="finderOpen = false"
    />
  </main>
</template>

<style scoped>
.candidate-preview-active {
  display: block;
  overflow-y: auto;
}

.inspector-surface {
  grid-area: detail;
  min-width: 0;
  min-height: 0;
  display: flex;
}
.inspector-surface > :deep(.inspector) {
  position: static;
  width: 100%;
  flex: 1;
  min-height: 0;
}
.workspace-body {
  overflow: visible;
}
/* A fixed graph-canvas minimum must not overflow a shorter flex viewport. */
.workspace-body .planning-content :deep(.milestone-stage > .graph-canvas) {
  min-height: 0;
}
/* On narrow windows the detail remains in flow so it cannot cover the node
   that was just located. Both the canvas and existing inspector stay usable. */
@media (max-width: 760px) {
  .workspace-detail-open {
    overflow-y: auto;
  }
  .graph-detail-open :deep(.milestone-stage) {
    flex: 1 0 auto;
    min-height: 0;
  }
  .graph-detail-open :deep(.milestone-stage > .graph-canvas) {
    flex: 1 0 auto;
  }
  .graph-detail-open :deep(.milestone-stage > .graph-canvas > .vue-flow) {
    min-height: 220px;
  }
  .inspector-surface {
    max-height: min(38vh, 300px);
    min-height: 160px;
  }
  .workspace-detail-open .inspector-surface > :deep(.inspector) {
    position: static;
    width: 100%;
    flex: 1 1 220px;
    max-height: min(38vh, 300px);
    min-height: 160px;
    border-left: 0;
    border-top: 1px solid var(--line);
  }
}
</style>
