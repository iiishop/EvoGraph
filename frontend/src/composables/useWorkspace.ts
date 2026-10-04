import { computed, reactive, shallowReadonly } from 'vue';
import { command, readyTransport } from '../api/client';
import type {
  ArchivedProjectSummary,
  Message,
  Project,
  PlanCandidate,
  ProjectOpenResult,
  ProjectSnapshot,
  ProjectSummary,
  Settings,
} from '../types';
import type { PageId } from '../lib/navigation';
import { useNotifications } from './useNotifications';
import { agentDrafts } from './useAgentDrafts';
import { workflowDrafts } from './useWorkflowDrafts';
import { architectureBrowse } from './useArchitectureBrowse';

export interface WorkspaceFollowBoundary {
  navigationTick: number;
  pulse: number;
  resume: number;
}

const state = reactive({
  projects: [] as ProjectSummary[],
  project: null as Project | null,
  settings: null as Settings | null,
  loading: true,
  busy: false,
  error: '',
  notice: '',
  deletedProject: null as ProjectSummary | null,
  archivedProjects: [] as ArchivedProjectSummary[],
  recoveryLoading: false,
  recoveryLoaded: false,
  recoveryListError: '',
  restoringId: null as string | null,
  recoveryError: null as { id: string; message: string } | null,
  recoveryRestored: null as {
    id: string;
    name: string;
    restored: boolean;
    refreshError: string;
  } | null,
  page: 'projects' as PageId,
  selectedId: null as string | null,
});
// User navigation and background refreshes have different ordering domains.
// A refresh must never become a newer selection intent merely by starting later.
let selectSequence = 0;
let refreshSequence = 0;
// Confirmed settings writes supersede a settings read already in flight.
let settingsSequence = 0;
let settingsOperationOwner: symbol | null = null;
let projectOperationOwner: symbol | null = null;
// Live agent snapshots can advance messages/history without changing the
// project revision. A background read started before one is no longer current.
let snapshotSequence = 0;
// Includes settings navigation, which does not select a different project.
let navigationSequence = 0;
let archivedSequence = 0;
// Owned by the workspace, so dismissing the dialog cannot release a restore.
let recoveryOperationOwner: symbol | null = null;
// Only the outstanding selection needs offscreen freshness metadata. Do not
// retain project snapshots or growing message histories for background tabs.
let pendingSelection: { id: string; sequence: number; activity: number } | null = null;
const maxSelectionReads = 4;

function noteSelectionActivity(id: string) {
  if (pendingSelection?.id === id) pendingSelection.activity++;
}

// The selected workspace tab belongs to this session's accepted project incarnation.
// It stores no Agent navigation, node selection, focus, or graph camera state.
const workspaceTabs = reactive(
  new Map<string, { incarnation: string; key: number; tab: string }>(),
);
let workspaceTabSequence = 0;
function reconcileWorkspaceTab(project: Project) {
  const incarnation = project.created_at ?? '';
  if (workspaceTabs.get(project.id)?.incarnation !== incarnation)
    workspaceTabs.set(project.id, { incarnation, key: ++workspaceTabSequence, tab: 'graph' });
}
function bindWorkspaceTab(project: Project) {
  const id = project.id;
  const incarnation = project.created_at ?? '';
  const saved = workspaceTabs.get(id);
  const key = saved?.incarnation === incarnation ? saved.key : undefined;
  // A lease from before deletion/restore is stale even if id and created_at are reused.
  const current = () => {
    const entry = workspaceTabs.get(id);
    return key !== undefined && entry?.key === key && entry.incarnation === incarnation
      ? entry
      : undefined;
  };
  return {
    key,
    tab: computed({
      get: () => current()?.tab ?? 'graph',
      set: (tab: string) => {
        const entry = current();
        if (
          entry &&
          state.page === 'projects' &&
          state.project?.id === id &&
          (state.project.created_at ?? '') === incarnation
        )
          entry.tab = tab;
      },
    }),
  };
}

function reconcileWorkflowDrafts(project: Project) {
  reconcileWorkspaceTab(project);
  architectureBrowse.reconcile(project);
  workflowDrafts.reconcile(
    project.id,
    [...project.milestones, ...(project.source_milestones ?? [])].map((node) => node.id),
  );
}

async function loadProject(id: string, navigate = false): Promise<ProjectOpenResult> {
  const sequence = ++selectSequence;
  const navigation = navigationSequence;
  const snapshot = snapshotSequence;
  const current = () => sequence === selectSequence && navigation === navigationSequence;
  // Already-rendered live state still wins without a redundant read. Offscreen
  // activity instead requires a fresh read to fulfill the user's selection.
  const liveSnapshotWon = () => state.project?.id === id && snapshot !== snapshotSequence;
  const selection = { id, sequence, activity: 0 };
  pendingSelection = selection;
  if (navigate) {
    state.page = 'projects';
    state.selectedId = null;
  }
  state.error = '';
  try {
    // Retry reads only, sequentially under the original navigation owner. A
    // continuously active stream must not produce an unbounded request loop.
    for (let attempt = 0; attempt < maxSelectionReads; attempt++) {
      const activity = selection.activity;
      try {
        const project = await command<Project & { archived?: boolean }>('projects.get', {
          project_id: id,
        });
        if (!current()) return 'superseded';
        // The intended project is already open with newer canonical live data.
        // Keep that state and its tab/draft owners rather than reopening it.
        if (liveSnapshotWon()) return 'accepted';
        if (activity !== selection.activity) continue;
        if (!project || project.id !== id) throw new Error('未收到所选项目的完整内容，请重试打开');
        if (project.archived) throw new Error('该项目已删除，请从「已删除项目」查看并按需恢复');
        if (
          state.project?.id === id &&
          project.created_at === state.project.created_at &&
          project.revision < state.project.revision
        )
          return 'accepted';
        architectureBrowse.activate(project);
        agentDrafts.activate(id);
        workflowDrafts.activate(id);
        reconcileWorkflowDrafts(project);
        state.project = project;
        localStorage.setItem('evograph.project', id);
        return 'accepted';
      } catch (error) {
        if (!current()) return 'superseded';
        if (liveSnapshotWon()) return 'accepted';
        if (activity !== selection.activity) continue;
        state.error = String(error);
        return 'failed';
      }
    }
    if (!current()) return 'superseded';
    if (liveSnapshotWon()) return 'accepted';
    state.error = '项目仍在更新，最新内容暂未读取完成。请重新打开项目重试。';
    return 'failed';
  } finally {
    if (pendingSelection === selection) pendingSelection = null;
  }
}

function discardProject(id: string) {
  workspaceTabs.delete(id);
  architectureBrowse.discard(id);
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
  const settingsVersion = settingsSequence;
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
    if (settingsVersion === settingsSequence) state.settings = settings;
    // A list fetched across a selection may predate that project (for example,
    // creation or restore). It is not authority to discard the newer draft.
    if (!current() || selecting || pendingSelection) return;
    for (const id of workspaceTabs.keys())
      if (!state.projects.some((project) => project.id === id)) workspaceTabs.delete(id);
    architectureBrowse.retain(state.projects.map((project) => project.id));
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

async function loadArchivedProjects() {
  const sequence = ++archivedSequence;
  state.recoveryLoading = true;
  state.recoveryListError = '';
  try {
    const projects = await command<ArchivedProjectSummary[]>('projects.list_archived');
    if (sequence !== archivedSequence) return;
    state.archivedProjects = [
      ...new Map(projects.map((project) => [project.id, project])).values(),
    ];
    state.recoveryLoaded = true;
  } catch (error) {
    if (sequence === archivedSequence)
      state.recoveryListError = error instanceof Error ? error.message : '无法读取已删除项目';
  } finally {
    if (sequence === archivedSequence) state.recoveryLoading = false;
  }
}

async function refreshAfterRestore() {
  const restored = state.recoveryRestored;
  if (!restored) return;
  try {
    await refresh();
    if (state.recoveryRestored === restored) restored.refreshError = '';
  } catch {
    if (state.recoveryRestored === restored)
      restored.refreshError = `${restored.restored ? '项目已恢复' : '项目已在工作空间中'}，但工作空间刷新未完成。可以重试刷新，无需再次恢复。`;
  }
}

type ProjectRestoreOutcome = { project: Project & { archived: boolean }; restored: boolean };

async function restoreProject(project: ProjectSummary, navigate = false) {
  if (state.busy || recoveryOperationOwner) return false;
  const owner = Symbol('restore');
  recoveryOperationOwner = owner;
  const selection = selectSequence;
  const navigation = navigationSequence;
  state.busy = true;
  state.restoringId = project.id;
  state.recoveryError = null;
  state.error = '';
  let outcome: ProjectRestoreOutcome;
  try {
    outcome = await command<ProjectRestoreOutcome>('projects.restore_archived', {
      project_id: project.id,
    });
    const acceptance = outcome?.project?.acceptance;
    if (
      typeof outcome?.restored !== 'boolean' ||
      outcome.project?.id !== project.id ||
      outcome.project.archived !== false ||
      typeof outcome.project.name !== 'string' ||
      typeof outcome.project.description !== 'string' ||
      typeof outcome.project.repository !== 'string' ||
      typeof outcome.project.is_demo !== 'boolean' ||
      !Array.isArray(outcome.project.milestones) ||
      !Number.isInteger(acceptance?.passed) ||
      !Number.isInteger(acceptance?.total) ||
      acceptance.passed < 0 ||
      acceptance.total < acceptance.passed ||
      typeof acceptance.achieved !== 'boolean'
    )
      throw new Error('恢复结果无法确认，请刷新已删除项目后重试');
  } catch (error) {
    const message = error instanceof Error ? error.message : '恢复未完成，请重试';
    state.recoveryError = { id: project.id, message };
    if (navigate) state.error = message;
    if (recoveryOperationOwner === owner) {
      recoveryOperationOwner = null;
      state.restoringId = null;
      state.busy = false;
    }
    return false;
  }
  const restored = outcome.project;
  try {
    // Commit the UI outcome before any fallible read. Old reads and draft attempts
    // belong to the deleted incarnation and cannot resurrect it after this point.
    snapshotSequence++;
    archivedSequence++;
    state.recoveryLoading = false;
    state.archivedProjects = state.archivedProjects.filter((item) => item.id !== restored.id);
    // A stale recovery row can refer to an already-active project with new
    // unsent work. Only an authoritative archived→active transition starts a
    // new incarnation. A successful no-op must retain that live ownership.
    if (outcome.restored) {
      workspaceTabs.delete(restored.id);
      architectureBrowse.discard(restored.id);
      agentDrafts.discard(restored.id);
      workflowDrafts.discard(restored.id);
      if (pendingSelection?.id === restored.id) {
        selectSequence++;
        pendingSelection = null;
      }
      if (state.project?.id === restored.id) {
        state.project = null;
        state.selectedId = null;
      }
    }
    // Archived rows may predate a rename, repository change or restored work.
    // The confirmed response, never the stale row, supplies immediate metadata.
    const summary = {
      id: restored.id,
      name: restored.name,
      description: restored.description,
      repository: restored.repository,
      is_demo: restored.is_demo,
      milestone_count: restored.milestones.length,
      acceptance: restored.acceptance,
    };
    state.projects = [...state.projects.filter((item) => item.id !== restored.id), summary];
    if (state.deletedProject?.id === restored.id) state.deletedProject = null;
    state.notice = '';
    state.recoveryRestored = {
      id: restored.id,
      name: restored.name,
      restored: outcome.restored,
      refreshError: '',
    };
    const confirmed = outcome.restored
      ? `已恢复「${restored.name}」`
      : `「${restored.name}」已在工作空间中`;
    useNotifications().push(confirmed);
    await refreshAfterRestore();
    if (navigate && state.recoveryRestored?.refreshError)
      state.notice = `${confirmed}，工作空间刷新未完成。可在已删除项目中重试刷新。`;
    if (navigate && selection === selectSequence && navigation === navigationSequence)
      await selectProject(restored.id);
    return true;
  } finally {
    if (recoveryOperationOwner === owner) {
      recoveryOperationOwner = null;
      state.restoringId = null;
      state.busy = false;
    }
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
  navigationSequence++;
  return loadProject(id, true);
}

export function useWorkspace() {
  return {
    state: shallowReadonly(state),
    init,
    refresh,
    perform,
    selectProject,
    bindWorkspaceTab,
    navigationToken: () => navigationSequence,
    navigationIsCurrent: (token: number) => token === navigationSequence,
    invalidateProjectReads: () => {
      refreshSequence++;
    },
    reserveProjectOperation: () => {
      if (state.busy) return null;
      const owner = Symbol('project save');
      projectOperationOwner = owner;
      state.busy = true;
      return () => {
        if (projectOperationOwner !== owner) return;
        projectOperationOwner = null;
        state.busy = false;
      };
    },
    loadArchivedProjects,
    restoreProject,
    refreshAfterRestore,
    // A verified terminal receipt may arrive after the stream's project and
    // message frames were lost. Its pending selection must reread saved state.
    invalidateProjectRead: noteSelectionActivity,
    // A confirmed settings write does not depend on a later project refresh.
    applySettings: (settings: Settings) => {
      settingsSequence++;
      state.settings = settings;
    },
    applyProject: (snapshot: ProjectSnapshot) => {
      if (!Number.isInteger(snapshot.revision) || snapshot.revision < 0) return;
      if (state.project?.id !== snapshot.id) {
        noteSelectionActivity(snapshot.id);
        return;
      }
      if (state.project?.id === snapshot.id && snapshot.revision >= state.project.revision) {
        // Omission is meaningful only with the negotiated discriminator. Keep
        // saved messages/events intact until the next authoritative full view.
        let project: Project;
        if ('snapshot_mode' in snapshot && snapshot.snapshot_mode === 'compact-v1') {
          const { snapshot_mode: _mode, ...planning } = snapshot;
          project = { ...planning, messages: state.project.messages, events: state.project.events };
        } else project = snapshot as Project;
        snapshotSequence++;
        reconcileWorkflowDrafts(project);
        state.project = project;
      }
    },
    // Candidate payloads carry planning snapshots, but never become canonical
    // workspace data. They only replace this isolated preview metadata.
    applyPlanCandidate: (projectId: string, candidate: PlanCandidate) => {
      if (
        !candidate ||
        !candidate.id ||
        candidate.project?.id !== projectId ||
        !Number.isInteger(candidate.revision) ||
        candidate.revision < 0 ||
        !Number.isInteger(candidate.base_revision) ||
        candidate.base_revision < 0
      )
        return;
      if (state.project?.id !== projectId) {
        noteSelectionActivity(projectId);
        return;
      }
      if (candidate.project.created_at !== state.project.created_at) return;
      const current = state.project.plan_candidate;
      if (current?.id === candidate.id && current.revision > candidate.revision) return;
      if (current && current.base_revision > candidate.base_revision) return;
      snapshotSequence++;
      state.project = { ...state.project, plan_candidate: candidate };
    },
    appendMessage: (projectId: string, message: Message) => {
      if (
        message.project_id !== projectId ||
        typeof message.id !== 'string' ||
        !message.id.trim() ||
        typeof message.content !== 'string' ||
        !['user', 'assistant'].includes(message.role)
      )
        return;
      if (state.project?.id !== projectId) {
        noteSelectionActivity(projectId);
        return;
      }
      if (state.project.messages.some((item) => item.id === message.id)) return;
      snapshotSequence++;
      state.project = { ...state.project, messages: [...state.project.messages, message] };
    },
    setError: (error: string) => {
      state.error = error;
    },
    setBusy: (value: boolean) => {
      // Workspace-owned requests retain their busy lease after view disposal.
      if (!settingsOperationOwner && !recoveryOperationOwner && !projectOperationOwner)
        state.busy = value;
    },
    reserveSettingsOperation: () => {
      if (state.busy) return null;
      const owner = Symbol('settings operation');
      settingsOperationOwner = owner;
      state.busy = true;
      return () => {
        if (settingsOperationOwner !== owner) return;
        settingsOperationOwner = null;
        state.busy = false;
      };
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
          snapshotSequence++;
          archivedSequence++;
          state.recoveryLoading = false;
          state.recoveryLoaded = false;
          state.recoveryRestored = null;
          state.deletedProject = { ...project };
          state.notice = `已删除「${project.name}」，仓库文件保留`;
        },
      );
    },
    undoDelete: async () => {
      if (!state.deletedProject) return;
      await restoreProject(state.deletedProject, true);
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
      navigationSequence++;
      state.page = page;
    },
    dismiss: () => {
      state.error = '';
      state.notice = '';
    },
  };
}
