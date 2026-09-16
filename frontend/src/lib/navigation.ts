import { Boxes, Settings2 } from 'lucide-vue-next';
import ProjectWorkspace from '../components/workspace/ProjectWorkspace.vue';
import SettingsView from '../components/settings/SettingsView.vue';

// New workspace pages have one registration point; shell and sidebar consume this list.
export const navigation = [
  { id: 'projects', label: '项目', icon: Boxes, component: ProjectWorkspace, countProjects: true },
  { id: 'settings', label: '设置', icon: Settings2, component: SettingsView, countProjects: false },
] as const;
export type PageId = (typeof navigation)[number]['id'];
