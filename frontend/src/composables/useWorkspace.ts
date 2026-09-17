import { computed, reactive, shallowReadonly } from 'vue';
import { command, readyTransport } from '../api/client';
import type { Project, ProjectSummary, Settings } from '../types';
import type { PageId } from '../lib/navigation';
import { useNotifications } from './useNotifications';

const state = reactive({
  projects: [] as ProjectSummary[],
  project: null as Project | null,
  settings: null as Settings | null,
  loading: true,
  busy: false,
  error: '',
  notice: '',
  deletedProject: null as { id: string; name: string } | null,
  page: 'projects' as PageId,
  selectedId: null as string | null,
});
let selectSequence = 0;

async function loadProject(id: string) {
  const sequence = ++selectSequence;
  const project = await command<Project>('projects.get', { project_id: id });
  if (sequence === selectSequence) {
    state.project = project;
    localStorage.setItem('evograph.project', id);
  }
}

async function refresh() {
  const [projects, settings] = await Promise.all([
    command<ProjectSummary[]>('projects.list'),
    command<Settings>('settings.get'),
  ]);
  state.projects = [...new Map(projects.map((p) => [p.id, p])).values()];
  state.settings = settings;
  if (state.project && !state.projects.some((p) => p.id === state.project!.id)) {
    state.project = null;
    state.selectedId = null;
    if (state.projects[0]) await loadProject(state.projects[0].id);
  } else if (state.project) await loadProject(state.project.id);
}

async function perform<T>(
  action: string,
  params: object = {},
  notice = '',
): Promise<T | undefined> {
  if (state.busy) return undefined;
  state.busy = true;
  state.error = '';
  state.notice = '';
  try {
    const result = await command<T>(action, params);
    await refresh();
    useNotifications().push(notice);
    return result;
  } catch (error) {
    state.error = error instanceof Error ? error.message : '操作未完成';
    await refresh().catch(() => {});
    return undefined;
  } finally {
    state.busy = false;
  }
}

async function init() {
  state.loading = true;
  state.error = '';
  try {
    await readyTransport();
    await command('projects.bootstrap');
    await refresh();
    const saved = localStorage.getItem('evograph.project');
    const id = state.projects.find((p) => p.id === saved)?.id ?? state.projects[0]?.id;
    if (id) await loadProject(id);
  } catch (error) {
    state.error = String(error);
  } finally {
    state.loading = false;
  }
}

async function selectProject(id: string) {
  state.page = 'projects';
  state.selectedId = null;
  state.error = '';
  try {
    await loadProject(id);
  } catch (error) {
    state.error = String(error);
  }
}

export function useWorkspace() {
  return {
    state: shallowReadonly(state),
    init,
    refresh,
    perform,
    selectProject,
    applyProject: (project: Project) => {
      if (state.project?.id === project.id) state.project = project;
    },
    setError: (error: string) => {
      state.error = error;
    },
    setBusy: (value: boolean) => {
      state.busy = value;
    },
    deleteProject: async (project: ProjectSummary) => {
      const result = await perform<{ deleted: boolean }>('projects.delete', {
        project_id: project.id,
      });
      if (result?.deleted) {
        state.deletedProject = { id: project.id, name: project.name };
        state.notice = `已删除「${project.name}」，仓库文件保留`;
      }
    },
    undoDelete: async () => {
      if (!state.deletedProject) return;
      const result = await perform<Project>(
        'projects.restore',
        { project_id: state.deletedProject.id },
        '项目已恢复',
      );
      if (result) {
        state.deletedProject = null;
        await selectProject(result.id);
      }
    },
    selected: computed(
      () =>
        [...(state.project?.milestones ?? []), ...(state.project?.source_milestones ?? [])].find(
          (m) => m.id === state.selectedId,
        ) ?? null,
    ),
    selectNode: (id: string | null) => {
      state.selectedId = id;
    },
    setPage: (page: PageId) => {
      state.page = page;
    },
    dismiss: () => {
      state.error = '';
      state.notice = '';
    },
  };
}
