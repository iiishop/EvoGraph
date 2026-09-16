import type { AgentEvent } from '../types';

export async function agentStream(
  params: { project_id: string; content: string; question_id?: string },
  receive: (event: AgentEvent) => void,
  signal: AbortSignal,
) {
  if (window.pywebview?.api) {
    const api = window.pywebview.api;
    const requestId = crypto.randomUUID();
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        window.removeEventListener('evograph:agent', listener);
        signal.removeEventListener('abort', abort);
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
      const abort = () => {
        api.cancel_agent(requestId);
        cleanup();
        resolve();
      };
      window.addEventListener('evograph:agent', listener);
      signal.addEventListener('abort', abort, { once: true });
      api.start_agent(requestId, params).catch((error) => {
        cleanup();
        reject(error);
      });
    });
    return;
  }
  const response = await fetch('/api/agent/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
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
