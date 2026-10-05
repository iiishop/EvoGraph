import type { AgentEvent, ComposerDocument, RepairFrom, ReviewRecheck } from '../types';

export async function agentStream(
  params: {
    project_id: string;
    content: string;
    composer_document?: ComposerDocument;
    question_id?: string;
    attachment_ids?: string[];
    verification_milestone?: string;
    source_analysis?: boolean;
    review_recheck?: ReviewRecheck;
    repair_from?: RepairFrom;
  },
  receive: (event: AgentEvent) => void,
  signal: AbortSignal,
  cancellationTimeoutMs = 5000,
) {
  // Opt in on the original request in both transports. Old servers may ignore
  // this field and return full views; never resend a turn to negotiate.
  const request = { ...params, snapshot_mode: 'compact-v1' as const };
  if (window.pywebview?.api) {
    const api = window.pywebview.api;
    const requestId = crypto.randomUUID();
    await new Promise<void>((resolve, reject) => {
      let cancellationTimer: ReturnType<typeof setTimeout> | undefined;
      const cleanup = () => {
        window.removeEventListener('evograph:agent', listener);
        signal.removeEventListener('abort', abort);
        if (cancellationTimer !== undefined) clearTimeout(cancellationTimer);
      };
      const listener = (raw: Event) => {
        const detail = (raw as CustomEvent).detail;
        if (detail.request_id !== requestId) return;
        receive(detail.event);
        if (detail.event.type === 'done') {
          cleanup();
          resolve();
        }
      };
      let started: Promise<{ started: boolean }>;
      const abort = () => {
        cancellationTimer = setTimeout(() => {
          cleanup();
          reject(new Error('停止结果尚未送达，正在核对已保存的状态'));
        }, cancellationTimeoutMs);
        // Wait for admission, then keep the listener until the backend has
        // finalized partial edits and sends its terminal project snapshot.
        void started
          .then(() => api.cancel_agent(requestId))
          .catch((error) => {
            cleanup();
            reject(error);
          });
      };
      window.addEventListener('evograph:agent', listener);
      signal.addEventListener('abort', abort, { once: true });
      started = api.start_agent(requestId, request);
      started.catch((error) => {
        cleanup();
        reject(error);
      });
      if (signal.aborted) abort();
    });
    return;
  }
  const response = await fetch('/api/agent/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok || !response.body) throw new Error(`Agent 连接失败（${response.status}）`);
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary: number;
      while ((boundary = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 1);
        if (line) receive(JSON.parse(line));
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) receive(JSON.parse(buffer));
  } finally {
    reader.releaseLock();
  }
}
