import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const compile = (path) =>
  ts.transpileModule(readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const { agentStream } = await import(moduleUrl(compile('api/agentStream.ts')));
const { waitForTurnResult, bestEffortRefresh } = await import(
  moduleUrl(
    compile('api/turnResult.ts').replace(
      /import .* from ['"]\.\/client['"];?/,
      'const command = () => { throw new Error("Unexpected default command"); };',
    ),
  )
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const params = { project_id: 'P1', content: 'A scoped edit' };

function browser(api) {
  const target = new EventTarget();
  target.pywebview = { api };
  globalThis.window = target;
  return target;
}
function terminal(target, requestId, event) {
  const message = new Event('evograph:agent');
  message.detail = { request_id: requestId, event };
  target.dispatchEvent(message);
}

test('native stop waits for admission and the finalized terminal event', async () => {
  let admitted;
  let requestId;
  let cancels = 0;
  const target = browser({
    start_agent: (id) => {
      requestId = id;
      return new Promise((resolve) => {
        admitted = resolve;
      });
    },
    cancel_agent: async () => {
      cancels++;
      return { cancelled: true };
    },
  });
  const controller = new AbortController();
  const events = [];
  let settled = false;
  const running = agentStream(params, (event) => events.push(event), controller.signal).then(() => {
    settled = true;
  });
  controller.abort();
  await tick();
  assert.equal(cancels, 0, 'stop cannot race ahead of native admission');
  admitted({ started: true });
  await tick();
  assert.equal(cancels, 1);
  assert.equal(settled, false, 'an acknowledged cancel is not a saved terminal result');
  terminal(target, requestId, { type: 'done', cancelled: true, summary: { status: 'stopped' } });
  await running;
  assert.equal(events.at(-1).summary.status, 'stopped');
  terminal(target, requestId, { type: 'graph_changed' });
  assert.equal(events.length, 1, 'terminal delivery removes the stream listener');
});

test('already-aborted native request still waits for admission before cancelling', async () => {
  let requestId;
  const target = browser({
    start_agent: async (id) => {
      requestId = id;
      return { started: true };
    },
    cancel_agent: async () => {
      terminal(target, requestId, { type: 'done', cancelled: true });
      return { cancelled: true };
    },
  });
  const controller = new AbortController();
  controller.abort();
  await agentStream(params, () => {}, controller.signal);
});

test('native cancel failure rejects instead of claiming stopped or discarding the error', async () => {
  browser({
    start_agent: async () => ({ started: true }),
    cancel_agent: async () => {
      throw new Error('native cancellation unavailable');
    },
  });
  const controller = new AbortController();
  const running = agentStream(params, () => {}, controller.signal);
  controller.abort();
  await assert.rejects(running, /native cancellation unavailable/);
});

test('turn settlement waits for the exact admitted turn without inventing a rollback', async () => {
  const summary = { version: 1, turn_id: 'T1', status: 'stopped', changed: true };
  let reads = 0;
  let pauses = 0;
  const result = await waitForTurnResult(
    'P1',
    'T1',
    async () => {
      reads++;
      return reads < 3
        ? { turn_id: 'T1', pending: true, summary: null }
        : { turn_id: 'T1', pending: false, summary };
    },
    async () => {
      pauses++;
    },
  );
  assert.equal(reads, 3);
  assert.equal(pauses, 2);
  assert.equal(result.summary, summary);
});

test('unavailable or missing terminal outcomes are never synthesized as success', async () => {
  await assert.rejects(
    waitForTurnResult('P1', 'T1', async () => {
      throw new Error('offline');
    }),
    /offline/,
  );
  assert.deepEqual(
    await waitForTurnResult('P1', 'T1', async () => ({
      turn_id: 'T1',
      pending: false,
      summary: null,
    })),
    { turn_id: 'T1', pending: false, summary: null },
  );
});

test('turn settlement rejects a mismatched outcome instead of showing another turn', async () => {
  await assert.rejects(
    waitForTurnResult('P1', 'T1', async () => ({
      turn_id: 'T2',
      pending: false,
      summary: null,
    })),
    /其他轮次/,
  );
  await assert.rejects(
    waitForTurnResult('P1', 'T1', async () => ({
      turn_id: 'T1',
      pending: false,
      summary: { turn_id: 'T2' },
    })),
    /其他轮次/,
  );
});

test('lost native cancellation result releases transport for persisted outcome recovery', async () => {
  browser({
    start_agent: async () => ({ started: true }),
    cancel_agent: async () => ({ cancelled: true }),
  });
  const controller = new AbortController();
  const running = agentStream(params, () => {}, controller.signal, 5);
  controller.abort();
  await assert.rejects(running, /停止结果尚未送达/);
});

test('pending and hung result reads have bounded unknown outcomes', async () => {
  await assert.rejects(
    waitForTurnResult(
      'P1',
      'T1',
      async () => ({ turn_id: 'T1', pending: true, summary: null }),
      tick,
      5,
    ),
    /尚未确认/,
  );
  await assert.rejects(
    waitForTurnResult('P1', 'T1', () => new Promise(() => {}), tick, 5),
    /尚未确认/,
  );
});

async function agentHarness(label) {
  const dependency = moduleUrl(`
    export const reactive = (value) => value;
    export const readonly = (value) => value;
    export const env = { run: async () => {}, result: async () => {}, results: 0, refreshes: 0 };
    export const workspace = {
      state: { project: null, busy: false, error: '' },
      applyProject(project) { this.state.project = project; },
      setError(error) { this.state.error = error; },
      setBusy(busy) { this.state.busy = busy; },
      async refresh() { env.refreshes++; await env.refreshBody?.(); },
    };
    export const useWorkspace = () => workspace;
    export const agentStream = (...args) => env.run(...args);
    export const waitForTurnResult = (...args) => { env.results++; return env.result(...args); };
    export const bestEffortRefresh = (refresh) => refresh().catch(() => {});
    export const useNotifications = () => ({ push() {} });
    export const changeSummary = () => '';
    export const turnSummaryStatus = (summary) => summary.status;
    // ${label}
  `);
  const source = compile('composables/useAgent.ts').replace(
    /from (['"])([^'"]+)\1/g,
    () => `from ${JSON.stringify(dependency)}`,
  );
  const { useAgent } = await import(moduleUrl(source));
  const { env, workspace } = await import(dependency);
  return { agent: useAgent(), env, workspace };
}

test('cancellation before started is unresolved rather than successful delivery', async () => {
  const { agent, env, workspace } = await agentHarness('before-start');
  env.run = async () => {
    throw new DOMException('Aborted', 'AbortError');
  };
  assert.equal(await agent.send('P1', 'change'), false);
  assert.match(agent.state.label, /待确认/);
  assert.match(workspace.state.error, /可确认/);
  assert.equal(workspace.state.busy, false);
  assert.equal(env.results, 0);
});

test('native terminal without persisted cancellation receipt remains unresolved', async () => {
  const { agent, env, workspace } = await agentHarness('missing-receipt');
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    receive({ type: 'done', turn_id: 'T1', cancelled: true, summary: null });
  };
  assert.equal(await agent.send('P1', 'change'), false);
  assert.match(agent.state.label, /待确认/);
  assert.match(workspace.state.error, /尚未确认/);
});

test('workspace stays busy until matching interrupted result is finalized', async () => {
  const { agent, env, workspace } = await agentHarness('pending-receipt');
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    throw new DOMException('Aborted', 'AbortError');
  };
  let finish;
  env.result = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const running = agent.send('P1', 'change');
  await tick();
  assert.equal(agent.state.running, true);
  assert.equal(workspace.state.busy, true);
  assert.equal(env.refreshes, 0);
  finish({
    turn_id: 'T1',
    pending: false,
    summary: { turn_id: 'T1', status: 'stopped', changed: true },
  });
  assert.equal(await running, true);
  assert.equal(agent.state.label, 'stopped');
  assert.equal(workspace.state.busy, false);
  assert.equal(env.refreshes, 1);
});

test('authoritative completed receipt clears a transient transport failure', async () => {
  const { agent, env, workspace } = await agentHarness('recovered-receipt');
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    throw new Error('temporary connection loss');
  };
  env.result = async () => ({
    turn_id: 'T1',
    pending: false,
    summary: { turn_id: 'T1', status: 'completed', changed: false },
  });
  assert.equal(await agent.send('P1', 'change'), true);
  assert.equal(agent.state.label, 'completed');
  assert.equal(workspace.state.error, '');
});

test('best-effort final refresh cannot indefinitely delay restoring the submission', async () => {
  await bestEffortRefresh(() => new Promise(() => {}), 5);
  await bestEffortRefresh(async () => {
    throw new Error('offline');
  }, 5);
});

test('a newer turn cannot rewrite the result of a submission awaiting refresh', async () => {
  const { agent, env } = await agentHarness('isolated-send-result');
  let releaseRefresh;
  let firstRefresh = true;
  env.refreshBody = () => {
    if (!firstRefresh) return;
    firstRefresh = false;
    return new Promise((resolve) => {
      releaseRefresh = resolve;
    });
  };
  env.run = async (_, receive) => {
    receive({ type: 'done', summary: { turn_id: 'T1', status: 'failed', changed: true } });
  };
  const oldSend = agent.send('P1', 'first change');
  await tick();
  env.run = async (_, receive) => {
    receive({ type: 'done', summary: { turn_id: 'T2', status: 'completed', changed: false } });
  };
  assert.equal(await agent.send('P1', 'second change'), true);
  releaseRefresh();
  assert.equal(await oldSend, false);
});
