<script setup lang="ts">
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue';
import { useAgent } from '../../composables/useAgent';
import WorkspaceHeader from './WorkspaceHeader.vue';
import GraphToolbar from '../graph/GraphToolbar.vue';
import MilestoneGraph from '../graph/MilestoneGraph.vue';
import MilestoneFinder from '../graph/MilestoneFinder.vue';
import MilestoneInspector from '../graph/MilestoneInspector.vue';
import SourceInspector from '../graph/SourceInspector.vue';
import { workspaceViews } from '../../lib/workspaceViews';
import { useEntrance } from '../../composables/useEntrance';
import AgentDock from '../agent/AgentDock.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const agent = useAgent();
function followPage() {
  if (
    agent.state.projectId === props.project.id &&
    agent.state.follow[props.project.id] !== false &&
    workspaceViews.some((v) => v.id === agent.state.view)
  )
    tab.value = agent.state.view;
}
function resume() {
  agent.resumeFollow(props.project.id);
  followPage();
}
watch(() => agent.state.navigationTick, followPage);
defineEmits<{ edit: [] }>();
const { state, selected, selectNode } = useWorkspace();
const planningContent = ref<HTMLElement>();
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
  locateSequence++;
});
async function locate(id: string) {
  if (!milestones.value.some((item) => item.id === id)) return;
  const sequence = ++locateSequence;
  const projectId = props.project.id;
  finderOpen.value = false;
  tab.value = 'graph';
  selectNode(id);
  agent.freeView(props.project.id);
  await nextTick();
  if (
    sequence !== locateSequence ||
    props.project.id !== projectId ||
    state.project?.id !== projectId ||
    state.selectedId !== id ||
    tab.value !== 'graph'
  )
    return;
  if (planningContent.value) planningContent.value.scrollTop = 0;
  graph.value?.locate(id);
}
const root = ref<HTMLElement>();
useEntrance(root);
const activeView = computed(() => workspaceViews.find((v) => v.id === tab.value)!);
const tab = ref('graph'),
  graph = ref<InstanceType<typeof MilestoneGraph>>();
watch(tab, (value) => {
  if (value !== 'graph') finderOpen.value = false;
});
</script>
<template>
  <main ref="root" class="project-workspace">
    <GraphToolbar
      :tab="tab"
      :count="project.milestones.length + (project.source_milestones?.length ?? 0)"
      @tab="
        tab = $event;
        agent.freeView(project.id);
      "
      @find="openFinder"
      @fit="graph?.fit()"
      @reset="graph?.reset()"
    />
    <WorkspaceHeader :project="project" @edit="$emit('edit')" />
    <section
      class="workspace-body"
      :class="{
        'architecture-active': tab === 'architecture',
        'workspace-detail-open': selected && tab === 'graph',
        'question-active': Boolean(project.question),
      }"
    >
      <div class="planning-region">
        <div
          ref="planningContent"
          class="planning-content"
          :class="{ 'graph-detail-open': selected && tab === 'graph' }"
        >
          <component
            :is="activeView.component"
            :key="project.id + tab"
            ref="graph"
            :project="project"
          />
        </div>
      </div>
      <component
        :is="selected?.origin === 'source' ? SourceInspector : MilestoneInspector"
        v-if="selected && tab === 'graph'"
        :key="selected.id"
        :milestone="selected"
        :project="project"
        @locate="locate"
      />
      <AgentDock
        :project="project"
        :view="tab"
        :compact="tab === 'architecture' || (tab === 'graph' && Boolean(selected))"
        @resume="resume"
        @locate="locate"
      />
    </section>
    <MilestoneFinder
      v-if="finderOpen && tab === 'graph'"
      :milestones="milestones"
      @select="locate"
      @close="finderOpen = false"
    />
  </main>
</template>

<style scoped>
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
  .workspace-detail-open > :deep(.inspector) {
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
