<script setup lang="ts">
import { ref } from 'vue';
import WorkspaceHeader from './WorkspaceHeader.vue';
import GraphToolbar from '../graph/GraphToolbar.vue';
import MilestoneGraph from '../graph/MilestoneGraph.vue';
import MilestoneInspector from '../graph/MilestoneInspector.vue';
import EvidencePanel from './EvidencePanel.vue';
import ActivityPanel from './ActivityPanel.vue';
import AgentDock from '../agent/AgentDock.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';
defineProps<{ project: Project }>();
defineEmits<{ edit: [] }>();
const { selected } = useWorkspace();
const tab = ref('graph'),
  graph = ref<InstanceType<typeof MilestoneGraph>>();
</script>
<template>
  <main class="project-workspace">
    <WorkspaceHeader :project="project" @edit="$emit('edit')" />
    <section class="workspace-body">
      <div class="planning-region">
        <GraphToolbar
          :tab="tab"
          :count="project.milestones.length"
          @tab="tab = $event"
          @fit="graph?.fit()"
          @reset="graph?.reset()"
        />
        <div class="planning-content">
          <MilestoneGraph v-if="tab === 'graph'" ref="graph" :project="project" /><EvidencePanel
            v-else-if="tab === 'evidence'"
            :project="project"
          /><ActivityPanel v-else :project="project" /><MilestoneInspector
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
