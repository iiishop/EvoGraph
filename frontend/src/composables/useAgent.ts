import { reactive, readonly } from 'vue';
import { agentStream } from '../api/agentStream';
import { bestEffortRefresh, waitForTurnResult } from '../api/turnResult';
import { turnSummaryStatus } from '../lib/turnSummary';
import { useWorkspace } from './useWorkspace';
import type { AgentEvent } from '../types';
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

function receive(event: AgentEvent) {
  const workspace = useWorkspace();
  if (event.type === 'started') state.turnId = event.turn_id ?? '';
  const previous = workspace.state.project;
  if (event.project) workspace.applyProject(event.project);
  if (event.label) state.label = event.label;
  if (event.type === 'thinking') state.label = '正在理解目标与当前图…';
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
      useNotifications().push(event.message || changeSummary(previous, event.project, event.label));
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
      runFailed = event.summary.status === 'failed';
      if (!runFailed) workspace.setError('');
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
): Promise<boolean> {
  const workspace = useWorkspace();
  if (state.running || workspace.state.busy) return false;
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
  let outcome = false;
  try {
    await agentStream(
      {
        project_id: projectId,
        content,
        attachment_ids: attachmentIds,
        verification_milestone: verificationMilestone,
        ...(questionId ? { question_id: questionId } : {}),
      },
      receive,
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
        if (result.summary) receive({ type: 'done', summary: result.summary });
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
    // Capture this submission's result before releasing the workspace; a
    // newer turn must not change the boolean returned to the older composer.
    outcome = !runFailed;
    state.running = false;
    workspace.setBusy(false);
    controller = null;
    await bestEffortRefresh(() => workspace.refresh());
  }
  // false means the run failed (transport error or an `error` event), so the
  // caller must not treat the submission as delivered.
  return outcome;
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
