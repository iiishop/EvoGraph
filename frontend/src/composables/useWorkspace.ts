import { computed, reactive, shallowReadonly } from 'vue';
import { command, readyTransport } from '../api/client';
import type { Project, ProjectSummary, Settings } from '../types';
import type { PageId } from '../lib/navigation';
import { useNotifications } from './useNotifications';
import { agentDrafts } from './useAgentDrafts';
import { workflowDrafts } from './useWorkflowDrafts';

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
// User navigation and background refreshes have different ordering domains.
// A refresh must never become a newer selection intent merely by starting later.
let selectSequence = 0;
let refreshSequence = 0;
// Live agent snapshots can advance messages/history without changing the
// project revision. A background read started before one is no longer current.
let snapshotSequence = 0;
let pendingSelection: { id: string; sequence: number } | null = null;

function reconcileWorkflowDrafts(project: Project) {
  workflowDrafts.reconcile(
    project.id,
    [...project.milestones, ...(project.source_milestones ?? [])].map((node) => node.id),
  );
}

async function loadProject(id: string, navigate = false) {
  const sequence = ++selectSequence;
  pendingSelection = { id, sequence };
  if (navigate) {
    state.page = 'projects';
    state.selectedId = null;
  }
  state.error = '';
  try {
    const project = await command<Project>('projects.get', { project_id: id });
    if (sequence !== selectSequence) return;
    agentDrafts.activate(id);
    workflowDrafts.activate(id);
    reconcileWorkflowDrafts(project);
    state.project = project;
    localStorage.setItem('evograph.project', id);
  } catch (error) {
    if (sequence === selectSequence) state.error = String(error);
  } finally {
    if (pendingSelection?.sequence === sequence) pendingSelection = null;
  }
}

function discardProject(id: string) {
  agentDrafts.discard(id);
  workflowDrafts.discard(id);
  // Deleting the rendered A must not cancel an in-flight user selection of B.
  if (pendingSelection?.id === id || (!pendingSelection && state.project?.id === id)) {
    selectSequence++;
    pendingSelection = null;
  }
  if (state.project?.id === id) {
    state.project = null;
    state.selectedId = null;
  }
  state.projects = state.projects.filter((project) => project.id !== id);
}

async function refresh() {
  const sequence = ++refreshSequence;
  const selection = selectSequence;
  const snapshot = snapshotSequence;
  const projectId = state.project?.id;
  const selecting = Boolean(pendingSelection);
  const current = () =>
    sequence === refreshSequence && selection === selectSequence && snapshot === snapshotSequence;
  try {
    const [projects, settings] = await Promise.all([
      command<ProjectSummary[]>('projects.list'),
      command<Settings>('settings.get'),
    ]);
    if (sequence !== refreshSequence || snapshot !== snapshotSequence) return;
    state.projects = [...new Map(projects.map((p) => [p.id, p])).values()];
    state.settings = settings;
    // A list fetched across a selection may predate that project (for example,
    // creation or restore). It is not authority to discard the newer draft.
    if (!current() || selecting || pendingSelection) return;
    agentDrafts.retain(state.projects.map((project) => project.id));
    workflowDrafts.retain(state.projects.map((project) => project.id));
    if (state.project && !state.projects.some((p) => p.id === state.project!.id)) {
      discardProject(state.project.id);
      return;
    }
    if (!projectId || state.project?.id !== projectId) return;
    const project = await command<Project>('projects.get', { project_id: projectId });
    if (
      current() &&
      !pendingSelection &&
      state.project?.id === projectId &&
      project.revision >= state.project.revision
    ) {
      reconcileWorkflowDrafts(project);
      state.project = project;
    }
  } catch (error) {
    // Superseded refresh failures are as stale as superseded project data.
    if (current()) throw error;
  }
}

async function perform<T>(
  action: string,
  params: object = {},
  notice = '',
  onSuccess?: (result: T) => void,
): Promise<T | undefined> {
  if (state.busy) return undefined;
  state.busy = true;
  state.error = '';
  state.notice = '';
  try {
    const result = await command<T>(action, params);
    onSuccess?.(result);
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
  const selection = selectSequence;
  state.loading = true;
  state.error = '';
  try {
    await readyTransport();
    await command('projects.bootstrap');
    await refresh();
    const saved = localStorage.getItem('evograph.project');
    const id = state.projects.find((p) => p.id === saved)?.id ?? state.projects[0]?.id;
    if (id && selection === selectSequence) await loadProject(id);
  } catch (error) {
    if (selection === selectSequence) state.error = String(error);
  } finally {
    state.loading = false;
  }
}

async function selectProject(id: string) {
  await loadProject(id, true);
}

export function useWorkspace() {
  return {
    state: shallowReadonly(state),
    init,
    refresh,
    perform,
    selectProject,
    applyProject: (project: Project) => {
      if (state.project?.id === project.id && project.revision >= state.project.revision) {
        snapshotSequence++;
        reconcileWorkflowDrafts(project);
        state.project = project;
      }
    },
    setError: (error: string) => {
      state.error = error;
    },
    setBusy: (value: boolean) => {
      state.busy = value;
    },
    deleteProject: async (project: ProjectSummary) => {
      await perform<{ deleted: boolean }>(
        'projects.delete',
        { project_id: project.id },
        '',
        (result) => {
          if (!result.deleted) return;
          // Clear as soon as deletion is confirmed, even if refresh fails.
          discardProject(project.id);
          state.deletedProject = { id: project.id, name: project.name };
          state.notice = `已删除「${project.name}」，仓库文件保留`;
        },
      );
    },
    undoDelete: async () => {
      if (!state.deletedProject) return;
      const selection = selectSequence;
      const result = await perform<Project>(
        'projects.restore',
        { project_id: state.deletedProject.id },
        '项目已恢复',
      );
      if (result) {
        state.deletedProject = null;
        if (selection === selectSequence) await selectProject(result.id);
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
