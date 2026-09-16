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

export async function readyTransport() {
  // pywebview injects the API asynchronously; the browser server always uses its fixed port.
  const browserPorts = ['5173', '8765', '4173'];
  if (!browserPorts.includes(location.port) && !window.pywebview?.api) {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 5000);
      window.addEventListener(
        'pywebviewready',
        () => {
          clearTimeout(timer);
          resolve();
        },
        { once: true },
      );
    });
  }
}
