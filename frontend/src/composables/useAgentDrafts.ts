import { computed, reactive } from 'vue';

type DraftContent = { text: string; ids: string[]; questionId?: string };
export type FailedDraft = DraftContent & { id: number; restoredRevision?: number };
type DraftEntry = {
  text: string;
  ids: string[];
  revision: number;
  failures: FailedDraft[];
  pending: Set<number>;
};
export type DraftAttempt = DraftContent & {
  id: number;
  projectId: string;
  entry: DraftEntry;
  restoreRevision: number | null;
};

// Deliberately session-only: unsent prompts and attachment references should not
// acquire a new plaintext browser-persistence lifetime just to survive a view.
export function createAgentDraftStore() {
  const entries = reactive(new Map<string, DraftEntry>());
  const deleted = reactive(new Set<string>());
  let sequence = 0;

  function entry(projectId: string) {
    if (deleted.has(projectId)) return undefined;
    if (!entries.has(projectId)) {
      entries.set(projectId, { text: '', ids: [], revision: 0, failures: [], pending: new Set() });
    }
    return entries.get(projectId)!;
  }

  function start(
    projectId: string,
    content: DraftContent,
    clearDraft = true,
  ): DraftAttempt | undefined {
    const draft = entry(projectId);
    if (!draft || draft.pending.size || !content.text.trim()) return;
    const id = ++sequence;
    if (clearDraft) {
      // A normal resubmission of an unchanged restored draft consumes its
      // recovery item too. Other saved requests must remain recoverable.
      draft.failures = draft.failures.filter((item) => item.restoredRevision !== draft.revision);
      draft.text = '';
      draft.ids = [];
      draft.revision++;
    }
    draft.pending.add(id);
    return {
      ...content,
      ids: [...content.ids],
      id,
      projectId,
      entry: draft,
      restoreRevision: clearDraft ? draft.revision : null,
    };
  }

  function retry(projectId: string, failureId: number) {
    const draft = entries.get(projectId);
    const failure = draft?.failures.find((item) => item.id === failureId);
    if (!draft || !failure) return;
    const attempt = start(projectId, failure, failure.restoredRevision === draft.revision);
    if (attempt) draft.failures = draft.failures.filter((item) => item.id !== failureId);
    return attempt;
  }

  function settle(attempt: DraftAttempt, delivered: boolean) {
    const draft = entries.get(attempt.projectId);
    // Entry identity also distinguishes a restored project from its deleted
    // incarnation. Late callbacks must never recreate deleted drafts.
    if (draft !== attempt.entry || !draft.pending.delete(attempt.id)) return false;
    if (delivered) return false;
    const failure: FailedDraft = {
      id: attempt.id,
      text: attempt.text,
      ids: [...attempt.ids],
      questionId: attempt.questionId,
    };
    const restored = attempt.restoreRevision === draft.revision;
    if (restored) {
      draft.text = attempt.text;
      draft.ids = [...attempt.ids];
      failure.restoredRevision = ++draft.revision;
    }
    draft.failures.push(failure);
    return restored;
  }

  function discard(projectId: string) {
    deleted.add(projectId);
    entries.delete(projectId);
  }

  return {
    start,
    retry,
    settle,
    discard,
    activate: (projectId: string) => {
      deleted.delete(projectId);
    },
    retain: (projectIds: string[]) => {
      const present = new Set(projectIds);
      for (const id of entries.keys()) if (!present.has(id)) discard(id);
    },
    dismissFailure: (projectId: string, failureId: number) => {
      const draft = entries.get(projectId);
      if (draft) draft.failures = draft.failures.filter((item) => item.id !== failureId);
    },
    bind: (projectId: () => string) => ({
      content: computed({
        get: () => entry(projectId())?.text ?? '',
        set: (text: string) => {
          const draft = entry(projectId());
          if (!draft || draft.text === text) return;
          draft.text = text;
          draft.revision++;
        },
      }),
      attachmentIds: computed({
        get: () => [...(entry(projectId())?.ids ?? [])],
        set: (ids: string[]) => {
          const draft = entry(projectId());
          if (
            !draft ||
            (ids.length === draft.ids.length && ids.every((id, i) => id === draft.ids[i]))
          )
            return;
          draft.ids = [...ids];
          draft.revision++;
        },
      }),
      failures: computed(() => entry(projectId())?.failures ?? []),
      pending: computed(() => Boolean(entry(projectId())?.pending.size)),
      failureRestored: computed(() => {
        const draft = entry(projectId());
        return Boolean(draft && draft.failures[0]?.restoredRevision === draft.revision);
      }),
    }),
  };
}

export const agentDrafts = createAgentDraftStore();
export const useAgentDraft = (projectId: () => string) => agentDrafts.bind(projectId);
