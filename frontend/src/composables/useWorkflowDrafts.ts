import { computed, reactive } from 'vue';

export type WorkflowDraft = {
  report: string;
  revision: number;
  prompt: string;
  error: string;
  notice: string;
  syncError: string;
  copied: boolean;
  pending: number | null;
};
export type WorkflowAttempt = {
  projectId: string;
  milestoneId: string;
  entry: WorkflowDraft;
  token: number;
  report: string;
  revision: number;
};

/** Session drafts outlive inspectors, but never outlive a deleted project incarnation. */
export function createWorkflowDrafts() {
  const projects = reactive(new Map<string, Map<string, WorkflowDraft>>());
  const discarded = reactive(new Set<string>());
  const membership = reactive(new Map<string, Set<string>>());
  let sequence = 0;
  function entry(projectId: string, milestoneId: string) {
    if (
      discarded.has(projectId) ||
      (membership.has(projectId) && !membership.get(projectId)!.has(milestoneId))
    )
      return undefined;
    if (!projects.has(projectId)) projects.set(projectId, new Map());
    const nodes = projects.get(projectId)!;
    if (!nodes.has(milestoneId))
      nodes.set(milestoneId, {
        report: '',
        revision: 0,
        prompt: '',
        error: '',
        notice: '',
        syncError: '',
        copied: false,
        pending: null,
      });
    return nodes.get(milestoneId)!;
  }
  function current(attempt: WorkflowAttempt) {
    return (
      projects.get(attempt.projectId)?.get(attempt.milestoneId) === attempt.entry &&
      attempt.entry.pending === attempt.token
    );
  }
  const discard = (projectId: string) => {
    projects.delete(projectId);
    membership.delete(projectId);
    discarded.add(projectId);
  };
  return {
    entry,
    current,
    reconcile(projectId: string, ids: string[]) {
      const valid = new Set(ids);
      membership.set(projectId, valid);
      const nodes = projects.get(projectId);
      if (nodes) for (const id of nodes.keys()) if (!valid.has(id)) nodes.delete(id);
    },
    activate: (projectId: string) => discarded.delete(projectId),
    discard,
    retain: (ids: string[]) => {
      for (const id of projects.keys()) if (!ids.includes(id)) discard(id);
    },
    start(projectId: string, milestoneId: string): WorkflowAttempt | undefined {
      const draft = entry(projectId, milestoneId);
      if (!draft || draft.pending !== null) return;
      const token = ++sequence;
      Object.assign(draft, { pending: token, error: '', notice: '', syncError: '', copied: false });
      return {
        projectId,
        milestoneId,
        entry: draft,
        token,
        report: draft.report,
        revision: draft.revision,
      };
    },
    bind(projectId: () => string, milestoneId: () => string) {
      const draft = computed(() => entry(projectId(), milestoneId()));
      const report = computed({
        get: () => draft.value?.report ?? '',
        set: (value: string) => {
          if (!draft.value || draft.value.report === value) return;
          draft.value.report = value;
          draft.value.revision++;
        },
      });
      return { draft, report };
    },
  };
}
export const workflowDrafts = createWorkflowDrafts();

/** A confirmed mutation is still successful when the following read fails. */
export async function runWorkflowAction<T>(options: {
  store: ReturnType<typeof createWorkflowDrafts>;
  attempt: WorkflowAttempt;
  mutate: () => Promise<T>;
  refresh: () => Promise<void>;
  confirmed: (result: T, draft: WorkflowDraft) => void;
}) {
  const { store, attempt } = options;
  try {
    let result: T;
    try {
      result = await options.mutate();
    } catch (error) {
      if (store.current(attempt))
        attempt.entry.error = error instanceof Error ? error.message : '操作未完成，请重试。';
      return { confirmed: false as const };
    }
    if (store.current(attempt)) options.confirmed(result, attempt.entry);
    try {
      await options.refresh();
    } catch {
      if (store.current(attempt))
        attempt.entry.syncError = '操作已保存，但最新状态暂未同步。请刷新状态，不要重复提交。';
    }
    return { confirmed: true as const, result, current: store.current(attempt) };
  } finally {
    if (store.current(attempt)) attempt.entry.pending = null;
  }
}
