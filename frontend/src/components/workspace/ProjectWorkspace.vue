<script setup lang="ts">
import { ref, computed } from 'vue';
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
defineProps<{ project: Project }>();
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
    <section class="workspace-body">
      <div class="planning-region">
        <GraphToolbar
          :tab="tab"
          :count="project.milestones.length + (project.source_milestones?.length ?? 0)"
          @tab="tab = $event"
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
      <AgentDock :project="project" />
    </section>
  </main>
</template>
