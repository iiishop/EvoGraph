import { markRaw } from 'vue';
import { Network, ListChecks, History, Boxes, Images } from 'lucide-vue-next';
import MilestoneGraph from '../components/graph/MilestoneGraph.vue';
import EvidencePanel from '../components/workspace/EvidencePanel.vue';
import ActivityPanel from '../components/workspace/ActivityPanel.vue';
import ArchitecturePanel from '../components/design/ArchitecturePanel.vue';
import DesignLibrary from '../components/design/DesignLibrary.vue';

// One entry controls both the tab and its content.
export const workspaceViews = [
  { id: 'graph', label: '里程碑图', icon: Network, component: markRaw(MilestoneGraph) },
  { id: 'architecture', label: '架构设计', icon: Boxes, component: markRaw(ArchitecturePanel) },
  { id: 'design', label: '项目资料', icon: Images, component: markRaw(DesignLibrary) },
  { id: 'evidence', label: '验证证据', icon: ListChecks, component: markRaw(EvidencePanel) },
  { id: 'activity', label: '演化记录', icon: History, component: markRaw(ActivityPanel) },
];
