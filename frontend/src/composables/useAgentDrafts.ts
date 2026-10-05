import { computed, reactive } from 'vue';
import type { Attachment, ComposerDocument, PendingQuestion } from '../types';
import {
  cloneComposerDocument,
  normalizeComposerDocument,
  renderComposerDocument,
  documentAttachmentIds,
  composerFreeText,
} from '../lib/composerDocument';
import type { AttachmentUploadResult } from '../lib/attachmentUpload';

type DraftContent = {
  text: string;
  composerDocument?: ComposerDocument;
  ids: string[];
  questionId?: string;
  question?: Pick<PendingQuestion, 'id' | 'prompt' | 'context' | 'verification_milestone'>;
  verificationMilestone?: string;
  sourceAnalysis?: boolean;
};
export type DraftRequest = {
  text: string;
  composerDocument?: ComposerDocument;
  questionId?: string;
  verificationMilestone?: string;
  sourceAnalysis?: boolean;
};
export type FailedDraft = DraftContent & { id: number; restoredRevision?: number; turnId?: string };
type DraftEntry = {
  text: string;
  composerDocument?: ComposerDocument;
  ids: string[];
  revision: number;
  failures: FailedDraft[];
  pending: Set<number>;
  recoveryError: string;
  composerOrigin: FailedDraft | null;
  composerAnchor: FailedDraft | null;
  confirmedAttachments: Attachment[];
  attachmentTransfer: {
    id: number;
    total: number;
    completed: number;
    phase: 'preparing' | 'uploading' | 'refreshing' | 'done';
    report: AttachmentUploadResult | null;
  } | null;
};
export type DraftAttempt = DraftContent & {
  turnId?: string;
  id: number;
  projectId: string;
  entry: DraftEntry;
  restoreRevision: number | null;
  request?: DraftRequest;
};
export type RetryPreparation = {
  id: number;
  projectId: string;
  entry: DraftEntry;
  failure: FailedDraft;
  restoreRevision?: number;
};
export type AttachmentTransfer = { id: number; projectId: string; entry: DraftEntry };

// Deliberately session-only: unsent prompts and attachment references should not
// acquire a new plaintext browser-persistence lifetime just to survive a view.
export function createAgentDraftStore() {
  const entries = reactive(new Map<string, DraftEntry>());
  const deleted = reactive(new Set<string>());
  const preparations = new Map<number, RetryPreparation>();
  let sequence = 0;

  function entry(projectId: string) {
    if (deleted.has(projectId)) return undefined;
    if (!entries.has(projectId)) {
      entries.set(projectId, {
        text: '',
        ids: [],
        revision: 0,
        failures: [],
        pending: new Set(),
        recoveryError: '',
        composerOrigin: null,
        composerAnchor: null,
        confirmedAttachments: [],
        attachmentTransfer: null,
      });
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
    draft.recoveryError = '';
    const id = ++sequence;
    if (clearDraft) {
      // A normal resubmission of an unchanged restored draft consumes its
      // recovery item too. Other saved requests must remain recoverable.
      draft.failures = draft.failures.filter((item) => item.restoredRevision !== draft.revision);
      draft.text = '';
      draft.composerDocument = undefined;
      draft.ids = [];
      draft.composerOrigin = null;
      draft.composerAnchor = null;
      draft.revision++;
    }
    draft.pending.add(id);
    // A retry is a new admission. Never attach its outcome to the failed turn
    // if transport stops before the new started frame arrives.
    const { turnId: _previousTurn, ...freshContent } = content as DraftContent & {
      turnId?: string;
    };
    return {
      ...freshContent,
      ...(content.composerDocument
        ? { composerDocument: cloneComposerDocument(content.composerDocument) }
        : {}),
      ids: [...content.ids],
      question: content.question ? { ...content.question } : undefined,
      verificationMilestone:
        content.verificationMilestone ?? content.question?.verification_milestone ?? undefined,
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
      ...(attempt.turnId ? { turnId: attempt.turnId } : {}),
      text: attempt.text,
      ...(attempt.composerDocument
        ? { composerDocument: cloneComposerDocument(attempt.composerDocument) }
        : {}),
      ids: [...attempt.ids],
      questionId: attempt.questionId,
      question: attempt.question ? { ...attempt.question } : undefined,
      verificationMilestone: attempt.verificationMilestone,
      ...(attempt.sourceAnalysis ? { sourceAnalysis: true } : {}),
    };
    const restored = attempt.restoreRevision === draft.revision;
    if (restored) {
      draft.text = attempt.text;
      draft.composerDocument = cloneComposerDocument(attempt.composerDocument);
      draft.ids = [...attempt.ids];
      failure.restoredRevision = ++draft.revision;
      draft.composerOrigin = failure;
      draft.composerAnchor = failure;
    }
    draft.failures.push(failure);
    return restored;
  }

  function recoverComposer(projectId: string) {
    const draft = entries.get(projectId);
    const origin = draft?.composerOrigin;
    if (!draft || !origin) return;
    if (!draft.failures.some((item) => item.id === origin.id))
      draft.failures.push({
        ...origin,
        ...(origin.composerDocument
          ? { composerDocument: cloneComposerDocument(origin.composerDocument) }
          : {}),
        ids: [...origin.ids],
      });
    return origin;
  }

  function prepareRetry(projectId: string, failureId: number, fromComposer = false) {
    const draft = entries.get(projectId);
    const failure = draft?.failures.find((item) => item.id === failureId);
    if (!draft || !failure || draft.pending.size) return;
    draft.recoveryError = '';
    const useComposer = fromComposer && draft.composerOrigin?.id === failureId;
    const preparation: RetryPreparation = {
      id: ++sequence,
      projectId,
      entry: draft,
      restoreRevision: useComposer ? draft.revision : failure.restoredRevision,
      failure: {
        ...failure,
        text: useComposer ? draft.text : failure.text,
        ...((useComposer ? draft.composerDocument : failure.composerDocument)
          ? {
              composerDocument: cloneComposerDocument(
                useComposer ? draft.composerDocument : failure.composerDocument,
              ),
            }
          : {}),
        ids: [...(useComposer ? draft.ids : failure.ids)],
        question: failure.question ? { ...failure.question } : undefined,
      },
    };
    if (useComposer && !draft.composerDocument) delete preparation.failure.composerDocument;
    draft.pending.add(preparation.id);
    preparations.set(preparation.id, preparation);
    return preparation;
  }

  function retryIsCurrent(preparation: RetryPreparation) {
    const draft = entries.get(preparation.projectId);
    return Boolean(
      draft === preparation.entry &&
      draft.pending.has(preparation.id) &&
      draft.failures.some((item) => item.id === preparation.failure.id),
    );
  }

  function cancelRetry(preparation: RetryPreparation) {
    preparations.delete(preparation.id);
    if (entries.get(preparation.projectId) === preparation.entry)
      preparation.entry.pending.delete(preparation.id);
  }

  function detachRestored(projectId: string, failureId: number) {
    const draft = entries.get(projectId);
    const failure = draft?.failures.find((item) => item.id === failureId);
    if (!draft || !failure || draft.composerOrigin?.id !== failureId) return;
    // The old answer remains in recovery, rather than becoming an implicit
    // answer to a different current question. Never clear a newer edit.
    draft.text = '';
    draft.composerDocument = undefined;
    if (failure.restoredRevision === draft.revision) draft.ids = [];
    draft.composerOrigin = null;
    draft.composerAnchor = null;
    draft.revision++;
    failure.restoredRevision = undefined;
  }

  function discard(projectId: string) {
    for (const preparation of preparations.values())
      if (preparation.projectId === projectId) cancelRetry(preparation);
    deleted.add(projectId);
    entries.delete(projectId);
  }

  function attachmentTransferIsCurrent(transfer: AttachmentTransfer) {
    const draft = entries.get(transfer.projectId);
    return Boolean(
      draft === transfer.entry &&
      draft.attachmentTransfer?.id === transfer.id &&
      draft.attachmentTransfer.phase !== 'done',
    );
  }

  return {
    start,
    retry,
    prepareRetry,
    retryIsCurrent,
    cancelRetry,
    detachRestored,
    recoverComposer,
    beginAttachmentTransfer: (
      projectId: string,
      total: number,
      phase: 'preparing' | 'uploading' = 'uploading',
    ): AttachmentTransfer | undefined => {
      const draft = entry(projectId);
      if (!draft || (draft.attachmentTransfer && draft.attachmentTransfer.phase !== 'done')) return;
      const id = ++sequence;
      draft.attachmentTransfer = { id, total, completed: 0, phase, report: null };
      draft.pending.add(id);
      return { id, projectId, entry: draft };
    },
    attachmentTransferIsCurrent,
    confirmAttachment: (transfer: AttachmentTransfer, asset: Attachment, limit = 6) => {
      if (!attachmentTransferIsCurrent(transfer)) return false;
      const draft = transfer.entry;
      draft.confirmedAttachments = [
        ...draft.confirmedAttachments.filter((item) => item.id !== asset.id),
        { ...asset, excerpt: '' },
      ];
      const referenced = new Set([...draft.ids, ...documentAttachmentIds(draft.composerDocument)]);
      if (!draft.ids.includes(asset.id) && (referenced.has(asset.id) || referenced.size < limit)) {
        draft.ids = [...draft.ids, asset.id];
        draft.revision++;
      }
      draft.attachmentTransfer!.completed++;
      return true;
    },
    attachmentPhase: (
      transfer: AttachmentTransfer,
      phase: 'uploading' | 'refreshing',
      completed: number,
    ) => {
      if (!attachmentTransferIsCurrent(transfer)) return;
      Object.assign(transfer.entry.attachmentTransfer!, { phase, completed });
    },
    finishAttachmentTransfer: (
      transfer: AttachmentTransfer,
      report: AttachmentUploadResult | null,
    ) => {
      if (!attachmentTransferIsCurrent(transfer)) return;
      transfer.entry.attachmentTransfer!.phase = 'done';
      transfer.entry.attachmentTransfer!.report = report;
      transfer.entry.pending.delete(transfer.id);
    },
    setRecoveryError: (projectId: string, message: string) => {
      const draft = entries.get(projectId);
      if (draft) draft.recoveryError = message;
    },
    commitRetry: (preparation: RetryPreparation, request: DraftRequest) => {
      if (!retryIsCurrent(preparation)) return;
      cancelRetry(preparation);
      const attempt = start(
        preparation.projectId,
        preparation.failure,
        preparation.restoreRevision === preparation.entry.revision,
      );
      if (attempt) {
        preparation.entry.failures = preparation.entry.failures.filter(
          (item) => item.id !== preparation.failure.id,
        );
        attempt.request = {
          ...request,
          ...(request.composerDocument
            ? { composerDocument: cloneComposerDocument(request.composerDocument) }
            : {}),
        };
      }
      return attempt;
    },
    settle,
    // Keep this owner even while its project is offscreen. Deletion/restore
    // replaces the entry, so a late receipt cannot touch the new incarnation.
    captureOwner: (projectId: string) => {
      const draft = entry(projectId);
      return () => Boolean(draft && entries.get(projectId) === draft);
    },
    confirmDelivered: (attempt: DraftAttempt) => {
      const draft = entries.get(attempt.projectId);
      if (draft !== attempt.entry) return;
      const pending = draft.pending.delete(attempt.id);
      const recovered = draft.failures.some((item) => item.id === attempt.id);
      if (!pending && !recovered) return;
      draft.failures = draft.failures.filter((item) => item.id !== attempt.id);
      if (recovered && !draft.failures.length) draft.recoveryError = '';
      // Retain every current composer edit, including restored text, and its
      // question provenance. Untouched Send still passes through retry checks;
      // removing that anchor could silently bind an old answer to a new question.
    },
    discard,
    activate: (projectId: string) => {
      deleted.delete(projectId);
    },
    retain: (projectIds: string[]) => {
      const present = new Set(projectIds);
      for (const id of entries.keys()) if (!present.has(id)) discard(id);
    },
    dismissFailure: (projectId: string, failureId: number) => {
      for (const preparation of preparations.values())
        if (preparation.projectId === projectId && preparation.failure.id === failureId)
          cancelRetry(preparation);
      const draft = entries.get(projectId);
      if (draft) {
        draft.failures = draft.failures.filter((item) => item.id !== failureId);
        if (!draft.failures.length) draft.recoveryError = '';
      }
    },
    bind: (projectId: () => string) => ({
      content: computed({
        get: () => entry(projectId())?.text ?? '',
        set: (text: string) => {
          const draft = entry(projectId());
          if (!draft || draft.text === text) return;
          draft.text = text;
          draft.composerDocument = undefined;
          const anchor = draft.composerAnchor;
          draft.composerOrigin =
            anchor &&
            composerFreeText(undefined, text) ===
              composerFreeText(anchor.composerDocument, anchor.text)
              ? anchor
              : null;
          draft.revision++;
        },
      }),
      composerDocument: computed({
        get: () => cloneComposerDocument(entry(projectId())?.composerDocument),
        set: (value: ComposerDocument | undefined) => {
          const draft = entry(projectId());
          if (!draft) return;
          const next = value ? normalizeComposerDocument(value) : undefined;
          if (JSON.stringify(next) === JSON.stringify(draft.composerDocument)) return;
          draft.composerDocument = cloneComposerDocument(next);
          draft.text = next ? renderComposerDocument(next) : draft.text;
          const anchor = draft.composerAnchor;
          draft.composerOrigin =
            anchor &&
            composerFreeText(next, draft.text) ===
              composerFreeText(anchor.composerDocument, anchor.text)
              ? anchor
              : null;
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
      recoveryError: computed(() => entry(projectId())?.recoveryError ?? ''),
      confirmedAttachments: computed(() => entry(projectId())?.confirmedAttachments ?? []),
      attachmentTransfer: computed(() => entry(projectId())?.attachmentTransfer ?? null),
      // Closing a hint or editing attachments never reinterprets the answer
      // text. Only a text edit or an explicit new-question answer releases it.
      restoredFailure: computed(() => entry(projectId())?.composerOrigin ?? undefined),
      failureRestored: computed(() => {
        const draft = entry(projectId());
        return Boolean(draft && draft.failures.at(-1)?.restoredRevision === draft.revision);
      }),
    }),
  };
}

export const agentDrafts = createAgentDraftStore();
export const useAgentDraft = (projectId: () => string) => agentDrafts.bind(projectId);
