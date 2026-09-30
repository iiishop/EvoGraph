<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { useAgent } from '../../composables/useAgent';
import WorkspaceHeader from './WorkspaceHeader.vue';
import GraphToolbar from '../graph/GraphToolbar.vue';
import MilestoneGraph from '../graph/MilestoneGraph.vue';
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
const { selected } = useWorkspace();
const root = ref<HTMLElement>();
useEntrance(root);
const activeView = computed(() => workspaceViews.find((v) => v.id === tab.value)!);
const tab = ref('graph'),
  graph = ref<InstanceType<typeof MilestoneGraph>>();
</script>
<template>
  <main ref="root" class="project-workspace">
    <WorkspaceHeader :project="project" @edit="$emit('edit')" />
    <section class="workspace-body" :class="{ 'architecture-active': tab === 'architecture' }">
      <div class="planning-region">
        <GraphToolbar
          :tab="tab"
          :count="project.milestones.length + (project.source_milestones?.length ?? 0)"
          @tab="
            tab = $event;
            agent.freeView(project.id);
          "
          @fit="graph?.fit()"
          @reset="graph?.reset()"
        />
        <div class="planning-content">
          <component
            :is="activeView.component"
            :key="project.id + tab"
            ref="graph"
            :project="project"
          />
          <component
            :is="selected?.origin === 'source' ? SourceInspector : MilestoneInspector"
            v-if="selected && tab === 'graph'"
            :key="selected.id"
            :milestone="selected"
            :project="project"
          />
        </div>
      </div>
      <AgentDock :project="project" @resume="resume" />
    </section>
  </main>
</template>
