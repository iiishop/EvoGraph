type Envelope<T> = { ok: true; data: T } | { ok: false; error: { code: string; message: string } };
declare global {
  interface Window {
    pywebview?: {
      api: {
        command: (action: string, params: object) => Promise<Envelope<unknown>>;
        start_agent: (request_id: string, params: object) => Promise<{ started: boolean }>;
        cancel_agent: (request_id: string) => Promise<{ cancelled: boolean }>;
      };
    };
  }
}

// The transport is the only place that knows whether it is running in a browser or desktop.
export async function command<T>(action: string, params: object = {}): Promise<T> {
  let response: Envelope<T>;
  if (window.pywebview?.api) {
    response = (await window.pywebview.api.command(action, params)) as Envelope<T>;
  } else {
    const result = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, params }),
    });
    if (!result.ok) throw new Error(`连接失败（${result.status}），请确认 Python 后端已启动`);
    response = await result.json();
  }
  if (!response.ok) throw new Error(response.error.message);
  return response.data;
}

// pywebview creates window.pywebview.api as an empty object a few milliseconds
// before it attaches the method stubs, so the api object alone is not ready.
function bridgeReady() {
  return typeof window.pywebview?.api?.command === 'function';
}

export async function readyTransport() {
  // pywebview may inject its bridge before Vue mounts. Poll as well as listening so
  // desktop startup does not fall through to fetch('/api/command') from file://.
  // Wait for the real method, not the placeholder object, or the first call lands
  // in that gap and throws "api.command is not a function".
  if (bridgeReady()) return;
  // Browser mode supports arbitrary configured ports. A pywebview asset server
  // has no API health route, so it still waits for its injected bridge.
  if (location.protocol === 'http:' || location.protocol === 'https:') {
    try {
      const response = await fetch('/api/health', { signal: AbortSignal.timeout(1500) });
      if (response.ok && (await response.json()).status === 'ok') return;
    } catch {
      /* Continue waiting for the desktop bridge. */
    }
  }
  await new Promise<void>((resolve, reject) => {
    const started = Date.now();
    let timer = 0;
    const settle = () => {
      window.clearInterval(timer);
      window.removeEventListener('pywebviewready', listener);
      resolve();
    };
    const listener = () => {
      if (bridgeReady()) settle();
    };
    window.addEventListener('pywebviewready', listener, { once: true });
    timer = window.setInterval(() => {
      if (bridgeReady()) {
        settle();
      } else if (Date.now() - started > 15000) {
        window.clearInterval(timer);
        reject(
          new Error(
            '桌面桥接未就绪，请确认已使用 uv run evograph 启动应用，而不是直接打开 dist/index.html',
          ),
        );
      }
    }, 50);
  });
}
