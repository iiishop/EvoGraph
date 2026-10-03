import { reactive, readonly, watch } from 'vue';
import { agentStream } from '../api/agentStream';
import { bestEffortRefresh, waitForTurnResult } from '../api/turnResult';
import { parseTurnSummary, turnSummaryStatus } from '../lib/turnSummary';
import { useWorkspace } from './useWorkspace';
import type { ComposerDocument, AgentEvent, Project } from '../types';
import { agentDrafts, type DraftAttempt } from './useAgentDrafts';
import { useNotifications } from './useNotifications';
import { changeSummary } from '../lib/changeSummary';

const state = reactive({
  running: false,
  projectId: '',
  turnId: '',
  label: '',
  focusId: '',
  view: 'graph',
  diagramId: '',
  diagramKind: '',
  navigationTick: 0,
  effect: '',
  pulse: 0,
  follow: {} as Record<string, boolean>,
  updates: {} as Record<string, number>,
});
let controller: AbortController | null = null;
// The composer needs to know whether a run failed so it can give the user's
// text back instead of silently dropping it.
let runFailed = false;
let receivedDone = false;
let receivedSummary = false;
let receiptWatcherStarted = false;
let latestRun: symbol | null = null;
type PendingReceipt = {
  owner: symbol;
  projectId: string;
  createdAt: string;
  turnId: string;
  outcome: boolean;
  error: string;
  isCurrent: () => boolean;
  submission?: DraftAttempt;
};
const pendingReceipts = new Set<PendingReceipt>();

function reconcileReceipts(project: Project | null) {
  const workspace = useWorkspace();
  for (const receipt of pendingReceipts) {
    // Entry identity retires a deleted/restored incarnation even when its ID
    // and creation timestamp are unchanged. Navigation alone does not retire it.
    if (!receipt.isCurrent()) {
      pendingReceipts.delete(receipt);
      continue;
    }
    if (!project || project.id !== receipt.projectId) continue;
    if (!receipt.createdAt || project.created_at !== receipt.createdAt) {
      pendingReceipts.delete(receipt);
      continue;
    }
    const summary = (project.events ?? [])
      .filter((event) => event.kind === 'agent_turn_finished')
      .map((event) => parseTurnSummary(event.detail))
      .find((summary) => summary?.turn_id === receipt.turnId);
    if (!summary) continue;
    pendingReceipts.delete(receipt);
    receipt.outcome = summary.status !== 'failed';
    if (receipt.outcome && receipt.submission) agentDrafts.confirmDelivered(receipt.submission);
    // An older receipt may settle its own draft, never a newer run's status.
    if (latestRun !== receipt.owner) continue;
    state.label = turnSummaryStatus(summary);
    if (!workspace.state.error || workspace.state.error === receipt.error)
      workspace.setError(
        summary.history_warning ||
          (summary.status === 'failed' ? '本轮未完成，已提交的修改保留' : ''),
      );
  }
}

function receive(event: AgentEvent) {
  const workspace = useWorkspace();
  if (event.type === 'started') state.turnId = event.turn_id ?? '';
  const previous = workspace.state.project;
  if (event.project) workspace.applyProject(event.project);
  if (
    event.type === 'message_saved' &&
    event.saved_message &&
    event.project_id === state.projectId &&
    event.saved_message.project_id === event.project_id &&
    event.turn_id === state.turnId
  )
    workspace.appendMessage(event.project_id, event.saved_message);
  if (event.label) state.label = event.label;
  if (event.type === 'thinking') state.label = event.label || '正在理解目标与当前图…';
  if (event.type === 'focus') {
    state.focusId = event.node_id ?? '';
    state.effect = event.effect ?? '';
    state.pulse++;
  }
  if (event.type === 'graph_changed') {
    if (event.view) {
      state.view = event.diagram_kind === 'class' ? 'architecture' : event.view;
      state.diagramId = event.diagram_id ?? '';
      state.diagramKind = event.diagram_kind ?? '';
      state.navigationTick++;
    }
    if (event.project)
      useNotifications().push(
        event.message || changeSummary(previous, event.project, event.label),
        `agent:${state.projectId}`,
      );
    const ids = event.node_ids ?? [];
    ids.forEach((id) => {
      state.updates[id] = (state.updates[id] ?? 0) + 1;
    });
    if (ids[0]) {
      state.focusId = ids[0];
      state.effect = event.effect ?? '';
      state.pulse++;
    }
  }
  if (event.type === 'question') {
    if (workspace.state.project?.id === state.projectId && event.question) {
      workspace.applyProject({ ...workspace.state.project, question: event.question });
    }
    state.label = '等待你的回答';
  }
  if (event.type === 'error') {
    runFailed = true;
    workspace.setError(event.message ?? 'Agent 操作未完成');
    state.label = '本轮已停止';
  }
  if (event.type === 'tool_failed') state.label = `${event.label}受阻，正在修正`;
  if (event.type === 'done') {
    receivedDone = true;
    if (event.summary) {
      receivedSummary = true;
      runFailed = event.summary.status === 'failed';
      if (!runFailed) workspace.setError(event.summary.history_warning || '');
      state.label = turnSummaryStatus(event.summary);
    } else if (event.cancelled) {
      runFailed = true;
      state.label = '已停止，保存结果待确认';
      workspace.setError('停止后的保存结果尚未确认，请重新打开项目检查已保存的更改');
    } else {
      state.label = event.project?.question
        ? '等待你的回答'
        : runFailed
          ? '本轮未完成，已提交的修改保留'
          : event.changed
            ? '图已更新'
            : '本轮结束';
    }
  }
}

async function send(
  projectId: string,
  content: string,
  questionId?: string,
  attachmentIds: string[] = [],
  verificationMilestone?: string,
  composerDocument?: ComposerDocument,
  submission?: DraftAttempt,
): Promise<boolean> {
  const workspace = useWorkspace();
  if (state.running || workspace.state.busy) return false;
  // This session-level watcher outlives a dock unmount and observes only project
  // snapshots the workspace has accepted through its existing ordering guards.
  if (!receiptWatcherStarted) {
    receiptWatcherStarted = true;
    watch(() => workspace.state.project, reconcileReceipts, { flush: 'sync' });
  }
  const receipt: PendingReceipt = {
    owner: Symbol('agent submission'),
    projectId,
    createdAt: workspace.state.project?.id === projectId ? workspace.state.project.created_at : '',
    turnId: '',
    outcome: false,
    error: '',
    isCurrent: agentDrafts.captureOwner(projectId),
    submission,
  };
  latestRun = receipt.owner;
  receivedSummary = false;
  runFailed = false;
  receivedDone = false;
  state.turnId = '';
  state.running = true;
  state.projectId = projectId;
  state.label = '连接 Agent…';
  state.focusId = '';
  state.updates = {};
  workspace.setError('');
  workspace.setBusy(true);
  controller = new AbortController();
  const receiveForRun = (event: AgentEvent) => {
    if (event.type === 'started') receipt.turnId = event.turn_id ?? '';
    // A factual terminal may lack a project snapshot after a stream loss or
    // failed snapshot lookup. Invalidate only this exact admitted incarnation.
    if (
      event.type === 'done' &&
      receipt.turnId &&
      event.summary?.turn_id === receipt.turnId &&
      (!event.turn_id || event.turn_id === receipt.turnId) &&
      (!event.project_id || event.project_id === projectId) &&
      latestRun === receipt.owner &&
      receipt.isCurrent()
    )
      workspace.invalidateProjectRead(projectId);
    receive(event);
  };
  try {
    await agentStream(
      {
        project_id: projectId,
        content,
        attachment_ids: attachmentIds,
        ...(composerDocument ? { composer_document: composerDocument } : {}),
        verification_milestone: verificationMilestone,
        ...(questionId ? { question_id: questionId } : {}),
      },
      receiveForRun,
      controller.signal,
    );
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      runFailed = true;
      workspace.setError(String(error));
    }
  } finally {
    if (!state.turnId && !receivedDone) {
      runFailed = true;
      state.label = '本轮结果待确认，请重新打开项目查看';
      workspace.setError('尚未收到可确认的轮次结果，请重新打开项目检查已保存的更改');
    }
    if (state.turnId && !receivedDone) {
      state.label = '正在确认本轮已保存的结果…';
      try {
        const result = await waitForTurnResult(projectId, state.turnId);
        if (result.summary) receiveForRun({ type: 'done', summary: result.summary });
        else {
          runFailed = true;
          state.label = '本轮结果待确认，请重新打开项目查看';
          workspace.setError('本轮保存结果尚未确认，请重新打开项目检查已保存的更改');
        }
      } catch {
        runFailed = true;
        state.label = '本轮结果待确认，请重新打开项目查看';
        workspace.setError('本轮保存结果尚未确认，请稍后重新打开项目检查已保存的更改');
      }
    }
    // Keep each submission's outcome separate before releasing the workspace.
    // Only its own exact canonical receipt may subsequently settle uncertainty.
    receipt.outcome = !runFailed;
    receipt.turnId = state.turnId;
    receipt.error = workspace.state.error;
    if (receipt.turnId && !receivedSummary && runFailed) pendingReceipts.add(receipt);
    reconcileReceipts(workspace.state.project);
    state.running = false;
    workspace.setBusy(false);
    controller = null;
    await bestEffortRefresh(() => workspace.refresh());
  }
  // false means the run failed (transport error or an `error` event), so the
  // caller must not treat the submission as delivered.
  return receipt.outcome;
}

export function useAgent() {
  return {
    state: readonly(state),
    send,
    stop: () => {
      controller?.abort();
      state.label = '正在停止并保存已提交的修改…';
    },
    freeView: (projectId: string) => {
      state.follow[projectId] = false;
    },
    resumeFollow: (projectId: string) => {
      state.follow[projectId] = true;
      state.pulse++;
    },
  };
}
