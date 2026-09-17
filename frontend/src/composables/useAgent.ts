import { reactive, readonly } from 'vue';
import { agentStream } from '../api/agentStream';
import { useWorkspace } from './useWorkspace';
import type { AgentEvent } from '../types';
import { useNotifications } from './useNotifications';
import { changeSummary } from '../lib/changeSummary';

const state = reactive({
  running: false,
  projectId: '',
  label: '',
  focusId: '',
  effect: '',
  pulse: 0,
  follow: {} as Record<string, boolean>,
  updates: {} as Record<string, number>,
});
let controller: AbortController | null = null;
// The composer needs to know whether a run failed so it can give the user's
// text back instead of silently dropping it.
let runFailed = false;

function receive(event: AgentEvent) {
  const workspace = useWorkspace();
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
  if (event.type === 'done')
    state.label = event.project?.question
      ? '等待你的回答'
      : event.changed
        ? '图已更新'
        : '本轮结束';
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
  state.running = true;
  state.projectId = projectId;
  state.label = '连接 Agent…';
  state.focusId = '';
  state.updates = {};
  workspace.setError('');
  workspace.setBusy(true);
  controller = new AbortController();
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
    state.running = false;
    workspace.setBusy(false);
    controller = null;
    await workspace.refresh().catch(() => {});
  }
  // false means the run failed (transport error or an `error` event), so the
  // caller must not treat the submission as delivered.
  return !runFailed;
}

export function useAgent() {
  return {
    state: readonly(state),
    send,
    stop: () => {
      controller?.abort();
      state.label = '已停止，已完成的图修改保留';
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
