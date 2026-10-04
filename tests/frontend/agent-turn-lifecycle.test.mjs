import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { browseStoreUrl } from './helpers/architecture-fixtures.mjs';

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
const vueUrl = pathToFileURL(createRequire(import.meta.url).resolve('vue')).href;
const composerUrl = moduleUrl(compile('lib/composerDocument.ts'));
const summariesUrl = moduleUrl(compile('lib/turnSummary.ts'));
const { planAgentRetry } = await import(
  moduleUrl(
    compile('lib/agentRetry.ts').replace(
      /from ['"]\.\/composerDocument['"]/g,
      `from ${JSON.stringify(composerUrl)}`,
    ),
  )
);
const draftsCode = compile('composables/useAgentDrafts.ts')
  .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(vueUrl)}`)
  .replace(/from ['"]\.\.\/lib\/composerDocument['"]/g, `from ${JSON.stringify(composerUrl)}`);
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

async function agentHarness(label, realWorkspace = false) {
  const draftsUrl = moduleUrl(`${draftsCode}\n// ${label}`);
  let workspaceUrl, commands;
  const records = new Map();
  if (realWorkspace) {
    const client = moduleUrl(`
      export const commands = { calls: [], handler: null };
      export const command = (action, params = {}) => { commands.calls.push([action, params]); return commands.handler(action, params); };
      export const readyTransport = async () => {};
      // ${label}
    `);
    const workflow = moduleUrl(
      compile('composables/useWorkflowDrafts.ts').replace(
        /from ['"]vue['"]/g,
        `from ${JSON.stringify(vueUrl)}`,
      ) + `\n// ${label}`,
    );
    const imports = {
      vue: vueUrl,
      '../api/client': client,
      './useAgentDrafts': draftsUrl,
      './useWorkflowDrafts': workflow,
      './useArchitectureBrowse': browseStoreUrl,
      './useNotifications': moduleUrl('export const useNotifications = () => ({ push() {} });'),
    };
    workspaceUrl = moduleUrl(
      compile('composables/useWorkspace.ts').replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
        assert.ok(imports[name], `Unexpected workspace import ${name}`);
        return `from ${JSON.stringify(imports[name])}`;
      }),
    );
    ({ commands } = await import(client));
    for (const id of ['P1', 'P2', 'P3'])
      records.set(id, {
        ...receiptProject(),
        id,
        name: id,
        milestones: [],
        source_milestones: [],
        events: [],
      });
    commands.handler = async (action, params) => {
      if (action === 'projects.bootstrap') return;
      if (action === 'projects.list')
        return [...records.values()].map(({ id, name }) => ({ id, name }));
      if (action === 'projects.get') return structuredClone(records.get(params.project_id));
      if (action === 'settings.get') return { provider: { config: { model: 'test' } } };
      throw new Error(`Unexpected command ${action}`);
    };
    const saved = new Map([['evograph.project', 'P1']]);
    globalThis.localStorage = {
      getItem: (key) => saved.get(key) ?? null,
      setItem: (key, value) => saved.set(key, value),
    };
  }
  const dependency = moduleUrl(`
    import { reactive } from ${JSON.stringify(vueUrl)};
    export { reactive, readonly, watch } from ${JSON.stringify(vueUrl)};
    export { parseTurnSummary } from ${JSON.stringify(summariesUrl)};
    export { agentDrafts } from ${JSON.stringify(draftsUrl)};
    export const env = { run: async () => {}, result: async () => {}, results: 0, refreshes: 0, invalidations: [] };
    ${
      workspaceUrl
        ? `import { useWorkspace as liveWorkspace } from ${JSON.stringify(workspaceUrl)};
    export const workspace = liveWorkspace();`
        : `export const workspace = {
      state: reactive({ project: null, busy: false, error: '' }),
      applyProject(project) { this.state.project = project; },
      appendMessage(projectId, message) { if (this.state.project?.id === projectId) this.state.project.messages.push(message); },
      invalidateProjectRead(projectId) { env.invalidations.push(projectId); },
      setError(error) { this.state.error = error; },
      setBusy(busy) { this.state.busy = busy; },
      async refresh() { env.refreshes++; await env.refreshBody?.(); },
    };`
    }
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
  const { agentDrafts } = await import(draftsUrl);
  if (realWorkspace) await workspace.init();
  return { agent: useAgent(), env, workspace, drafts: agentDrafts, records, commands };
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
  assert.deepEqual(env.invalidations, ['P1']);
});

test('exact terminal receipt reloads a pending offscreen project even when all later stream frames were lost', async () => {
  for (const terminalRoute of ['turn-result', 'summary-only-frame']) {
    for (const destination of ['pending-project', 'new-project', 'settings']) {
      const h = await agentHarness(`offscreen-terminal-${terminalRoute}-${destination}`, true);
      const { agent, env, workspace, commands, records } = h;
      const before = { ...records.get('P1'), revision: 12, messages: [{ id: 'TOOLS-02' }] };
      records.set('P1', before);
      await workspace.selectProject('P1');
      let releaseStream, releaseOldRead;
      const stream = new Promise((resolve) => {
        releaseStream = resolve;
      });
      const oldRead = new Promise((resolve) => {
        releaseOldRead = resolve;
      });
      const final = {
        ...before,
        ...receiptProject('T1', 'stopped', { changed: true, after_revision: 64 }),
        revision: 64,
        milestones: [{ id: 'M64' }],
        messages: [
          { id: 'FINAL-18', project_id: 'P1', role: 'assistant', content: 'saved final narration' },
        ],
      };
      const summary = JSON.parse(final.events[0].detail);
      env.run = async (_, receive) => {
        receive({ type: 'started', turn_id: 'T1' });
        await stream;
        if (terminalRoute === 'summary-only-frame') {
          receive({ type: 'done', turn_id: 'T1', project_id: 'P1', project: null, summary });
          return;
        }
        throw new Error('later graph, message and terminal stream frames were lost');
      };
      env.result = (projectId, turnId) =>
        waitForTurnResult(projectId, turnId, async () => ({
          turn_id: 'T1',
          pending: false,
          summary,
        }));
      const running = agent.send('P1', 'update the project');
      await tick();
      await workspace.selectProject('P2');
      const handle = commands.handler;
      let targetReads = 0;
      commands.handler = (action, params) =>
        action === 'projects.get' && params.project_id === 'P1'
          ? ++targetReads === 1
            ? oldRead
            : structuredClone(records.get('P1'))
          : handle(action, params);
      commands.calls.length = 0;
      const selecting = workspace.selectProject('P1');
      records.set('P1', final);
      releaseStream();
      assert.equal(await running, true);
      assert.equal(agent.state.label, 'stopped');
      assert.equal(workspace.state.project.id, 'P2');
      assert.equal(env.results, terminalRoute === 'turn-result' ? 1 : 0);
      if (destination === 'new-project') await workspace.selectProject('P3');
      if (destination === 'settings') workspace.setPage('settings');
      releaseOldRead(before);
      await selecting;
      assert.equal(targetReads, destination === 'pending-project' ? 2 : 1);
      assert.equal(workspace.state.error, '');
      if (destination === 'pending-project') {
        assert.equal(workspace.state.project.id, 'P1');
        assert.equal(workspace.state.project.revision, 64);
        assert.deepEqual(workspace.state.project.messages, final.messages);
        assert.deepEqual(workspace.state.project.events, final.events);
      } else {
        assert.equal(workspace.state.project.id, destination === 'new-project' ? 'P3' : 'P2');
        assert.equal(workspace.state.page, destination === 'settings' ? 'settings' : 'projects');
      }
      assert.ok(
        commands.calls.every(([action]) =>
          ['projects.get', 'projects.list', 'settings.get'].includes(action),
        ),
      );
    }
  }
});

test('terminal invalidation requires an exact admitted turn and current submission incarnation', async () => {
  for (const invalid of [
    'not-admitted',
    'summary-turn',
    'envelope-turn',
    'envelope-project',
    'retired-owner',
  ]) {
    const { agent, env, drafts } = await agentHarness(`terminal-invalidation-${invalid}`);
    env.run = async (_, receive) => {
      if (invalid !== 'not-admitted') receive({ type: 'started', turn_id: 'T1' });
      if (invalid === 'retired-owner') {
        drafts.discard('P1');
        drafts.activate('P1');
      }
      receive({
        type: 'done',
        turn_id: invalid === 'envelope-turn' ? 'other' : 'T1',
        project_id: invalid === 'envelope-project' ? 'P2' : 'P1',
        summary: {
          turn_id: invalid === 'summary-turn' ? 'other' : 'T1',
          status: 'stopped',
          changed: true,
        },
      });
    };
    await agent.send('P1', 'change');
    assert.deepEqual(env.invalidations, []);
  }
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

test('native stream opts in once on the original request and still accepts a legacy full terminal', async () => {
  const requests = [];
  const target = browser({
    start_agent: async (id, body) => {
      requests.push(body);
      queueMicrotask(() =>
        terminal(target, id, { type: 'done', project: { id: 'P1', messages: [], events: [] } }),
      );
      return { started: true };
    },
  });
  const events = [];
  await agentStream(params, (event) => events.push(event), new AbortController().signal);
  assert.deepEqual(requests, [{ ...params, snapshot_mode: 'compact-v1' }]);
  assert.equal(events[0].project.messages.length, 0);
});

test('HTTP stream opts in once and never replays admission on a negotiation failure', async () => {
  globalThis.window = {};
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (_, request) => {
    requests.push(JSON.parse(request.body));
    return { ok: false, status: 422 };
  };
  try {
    await assert.rejects(
      agentStream(params, () => {}, new AbortController().signal),
      /422/,
    );
    assert.deepEqual(requests, [{ ...params, snapshot_mode: 'compact-v1' }]);
  } finally {
    globalThis.fetch = original;
  }
});

test('canonical saved narration belongs only to the admitted project and turn', async () => {
  const { agent, env, workspace } = await agentHarness('canonical-history');
  const full = { id: 'P1', messages: [], events: [] };
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1', project: full });
    for (const [project_id, turn_id, id] of [
      ['P2', 'T1', 'wrong-project'],
      ['P1', 'T2', 'wrong-turn'],
      ['P1', 'T1', 'saved'],
    ])
      receive({
        type: 'message_saved',
        project_id,
        turn_id,
        saved_message: { id, project_id, role: 'assistant', content: 'full saved text' },
      });
    receive({
      type: 'message_saved',
      project_id: 'P1',
      turn_id: 'T1',
      saved_message: {
        id: 'nested-foreign',
        project_id: 'P2',
        role: 'assistant',
        content: 'must be rejected',
      },
    });
    assert.deepEqual(
      workspace.state.project.messages.map((item) => item.id),
      ['saved'],
    );
    receive({ type: 'done', summary: { turn_id: 'T1', status: 'completed', changed: false } });
  };
  assert.equal(await agent.send('P1', 'change'), true);
});

function receiptProject(turnId = 'T1', status = 'stopped', extra = {}) {
  const summary = {
    version: 1,
    turn_id: turnId,
    status,
    changed: false,
    before_revision: 0,
    after_revision: 0,
    changes: {
      milestones: { added: [], updated: [], removed: [] },
      dependencies: { added: [], updated: [], removed: [] },
      target: null,
      architecture: null,
      other: [],
    },
    ...extra,
  };
  return {
    id: 'P1',
    created_at: '2026-01-01',
    revision: 0,
    messages: [],
    events: [{ id: `E-${turnId}`, kind: 'agent_turn_finished', detail: JSON.stringify(summary) }],
  };
}
function interrupted(h) {
  h.workspace.state.project = { ...receiptProject(), events: [] };
  h.env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    throw new Error('delivery lost');
  };
  h.env.result = async () => {
    throw new Error('receipt read unavailable');
  };
  const draft = h.drafts.bind(() => 'P1');
  draft.content.value = 'original request';
  const attempt = h.drafts.start('P1', { text: draft.content.value, ids: ['old-attachment'] });
  return {
    draft,
    attempt,
    send: () =>
      h.agent.send('P1', attempt.text, undefined, attempt.ids, undefined, undefined, attempt),
  };
}

test('final accepted refresh confirms the exact stopped receipt before composer recovery', async () => {
  const h = await agentHarness('refresh-exact-receipt');
  const { draft, attempt, send } = interrupted(h);
  h.env.refreshBody = async () => {
    h.workspace.state.project = receiptProject('T1', 'stopped', {
      history_warning: '历史记录暂未同步',
    });
  };
  assert.equal(await send(), true);
  assert.equal(h.agent.state.label, 'stopped');
  assert.equal(h.workspace.state.error, '历史记录暂未同步');
  assert.equal(
    h.drafts.settle(attempt, false),
    false,
    'a stale failed callback cannot restore confirmed delivery',
  );
  assert.deepEqual(draft.failures.value, []);
  assert.equal(draft.content.value, '');
});

test('late accepted reopen reconciles only the exact turn and retains restored composer provenance', async () => {
  const h = await agentHarness('reopen-exact-receipt');
  const { draft, attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  assert.equal(h.drafts.settle(attempt, false), true);
  h.workspace.state.project = { ...receiptProject(), id: 'P2' };
  assert.match(h.agent.state.label, /待确认/);
  h.workspace.state.project = receiptProject('older-turn', 'completed');
  assert.match(h.agent.state.label, /待确认/);
  h.workspace.state.project = receiptProject('newer-turn', 'completed');
  assert.match(h.agent.state.label, /待确认/);
  h.workspace.state.project = receiptProject('T1', 'stopped');
  assert.equal(h.agent.state.label, 'stopped');
  assert.equal(draft.failures.value.length, 0);
  assert.equal(draft.content.value, 'original request');
  assert.deepEqual(draft.attachmentIds.value, ['old-attachment']);
  assert.equal(draft.restoredFailure.value.id, attempt.id);
});

test('late receipts preserve edited then reverted text and newer attachments', async () => {
  const h = await agentHarness('receipt-newer-draft');
  const { draft, attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  h.drafts.settle(attempt, false);
  draft.content.value = 'new edit';
  draft.content.value = 'original request';
  draft.attachmentIds.value = ['new-attachment'];
  h.workspace.state.project = receiptProject('T1', 'completed');
  assert.equal(h.agent.state.label, 'completed');
  assert.equal(draft.content.value, 'original request');
  assert.deepEqual(draft.attachmentIds.value, ['new-attachment']);
  assert.equal(draft.failures.value.length, 0);
});

test('late failed receipt resolves uncertainty while keeping recovery and its warning', async () => {
  const h = await agentHarness('receipt-late-failed');
  const { draft, attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  h.drafts.settle(attempt, false);
  h.workspace.state.project = receiptProject('T1', 'failed', { history_warning: '历史保存失败' });
  assert.equal(h.agent.state.label, 'failed');
  assert.equal(h.workspace.state.error, '历史保存失败');
  assert.equal(draft.failures.value[0].id, attempt.id);
  assert.equal(draft.content.value, 'original request');
});

test('a malformed receipt cannot confirm a delivery with a matching turn ID', async () => {
  const h = await agentHarness('receipt-invalid-shape');
  const { attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  h.drafts.settle(attempt, false);
  h.workspace.state.project = receiptProject('T1', 'completed', { version: 99 });
  assert.match(h.agent.state.label, /待确认/);
});

test('deleted and restored entry retires an uncertain receipt even with identical IDs and timestamps', async () => {
  const h = await agentHarness('receipt-retired-incarnation');
  const { attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  h.drafts.settle(attempt, false);
  h.drafts.discard('P1');
  h.drafts.activate('P1');
  const restored = h.drafts.bind(() => 'P1');
  restored.content.value = 'new incarnation draft';
  h.workspace.state.project = receiptProject('T1', 'completed');
  assert.match(h.agent.state.label, /待确认/);
  assert.equal(restored.content.value, 'new incarnation draft');
  assert.deepEqual(restored.failures.value, []);
});

test('a changed project creation timestamp cannot settle an old uncertain turn', async () => {
  const h = await agentHarness('receipt-new-project-created');
  const { send } = interrupted(h);
  assert.equal(await send(), false);
  h.workspace.state.project = { ...receiptProject(), created_at: '2026-09-01' };
  assert.match(h.agent.state.label, /待确认/);
});

test('old exact receipt cannot alter an active newer turn label or outcome', async () => {
  const h = await agentHarness('receipt-newer-run-isolation');
  const { draft, attempt, send } = interrupted(h);
  assert.equal(await send(), false);
  h.drafts.settle(attempt, false);
  let finish;
  h.env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T2' });
    receive({ type: 'thinking' });
    await new Promise((resolve) => {
      finish = resolve;
    });
    receive({ type: 'done', summary: { turn_id: 'T2', status: 'failed', changed: false } });
  };
  const newer = h.agent.send('P1', 'newer request');
  await tick();
  const newerLabel = h.agent.state.label;
  h.workspace.state.project = receiptProject('T1', 'completed');
  assert.equal(h.agent.state.label, newerLabel);
  assert.equal(h.agent.state.running, true);
  assert.equal(draft.failures.value.length, 0);
  finish();
  assert.equal(await newer, false);
  assert.equal(h.agent.state.label, 'failed');
});

test('no admitted turn ID never infers success from a later canonical receipt', async () => {
  const h = await agentHarness('receipt-without-admission');
  h.workspace.state.project = { ...receiptProject(), events: [] };
  h.env.run = async () => {
    throw new Error('admission unavailable');
  };
  assert.equal(await h.agent.send('P1', 'original request'), false);
  h.workspace.state.project = receiptProject('T1', 'completed');
  assert.match(h.agent.state.label, /待确认/);
});

test('thinking events preserve explicit phase labels and use a fallback when unlabeled', async () => {
  const { agent, env } = await agentHarness('thinking-label-precedence');
  const labels = [];
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    for (const event of [
      { type: 'thinking', label: '正在评审架构与交付设计…' },
      { type: 'thinking' },
      { type: 'thinking', label: '调查当前项目' },
      { type: 'thinking', label: '' },
      { type: 'tool_started', label: '读取源文件' },
    ]) {
      receive(event);
      labels.push(agent.state.label);
    }
    receive({ type: 'done', summary: { turn_id: 'T1', status: 'completed', changed: false } });
  };
  assert.equal(await agent.send('P1', 'Investigate the current project'), true);
  assert.deepEqual(labels, [
    '正在评审架构与交付设计…',
    '正在理解目标与当前图…',
    '调查当前项目',
    '正在理解目标与当前图…',
    '读取源文件',
  ]);
});

function planningCandidate(project, extra = {}) {
  return {
    id: 'candidate-1',
    base_revision: project.revision,
    revision: 1,
    status: 'reviewing',
    candidate_hash: 'hash',
    input: 'Plan safely',
    report: { findings: [] },
    metrics: { provider_calls: 1, tokens: 200, elapsed_seconds: 1 },
    project: { ...project, revision: project.revision + 1, milestones: [{ id: 'candidate-only' }] },
    ...extra,
  };
}

test('candidate frames remain isolated through semantic failure and saved reopen', async () => {
  const { agent, env, workspace, records } = await agentHarness('candidate-isolation', true);
  const original = structuredClone(records.get('P1'));
  const candidate = planningCandidate(original);
  const failed = {
    ...candidate,
    status: 'needs_resolution',
    revision: 2,
    report: {
      findings: [
        { code: 'SEMANTIC', subject: 'R1', message: 'Behavior omits the offline constraint' },
      ],
    },
  };
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    receive({
      type: 'candidate_changed',
      project_id: 'P1',
      turn_id: 'T1',
      candidate,
      project: candidate.project,
      label: 'Reviewing candidate',
    });
    assert.equal(workspace.state.project.revision, original.revision);
    assert.deepEqual(workspace.state.project.milestones, original.milestones);
    assert.equal(workspace.state.project.plan_candidate.id, candidate.id);
    assert.equal(agent.state.label, 'Reviewing candidate');
    receive({ type: 'candidate_changed', project_id: 'P1', turn_id: 'T1', candidate: failed });
    records.set('P1', { ...original, plan_candidate: failed });
    receive({
      type: 'done',
      project: { ...original, plan_candidate: failed },
      summary: { turn_id: 'T1', status: 'waiting', changed: false },
    });
  };
  assert.equal(await agent.send('P1', 'Plan safely'), true);
  await workspace.selectProject('P2');
  await workspace.selectProject('P1');
  assert.equal(workspace.state.project.plan_candidate.status, 'needs_resolution');
  assert.equal(workspace.state.project.plan_candidate.report.findings[0].code, 'SEMANTIC');
  assert.equal(workspace.state.project.revision, original.revision);
  assert.deepEqual(workspace.state.project.milestones, original.milestones);
});

test('candidate delivery rejects wrong project, turn, incarnation and regressive revisions', async () => {
  const { agent, env, workspace, records } = await agentHarness('candidate-admission', true);
  const original = structuredClone(records.get('P1'));
  const candidate = planningCandidate(original, { revision: 4 });
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    for (const event of [
      { project_id: 'P2', candidate },
      { turn_id: 'T2', candidate },
      { candidate: { ...candidate, project: { ...candidate.project, id: 'P2' } } },
      { candidate: { ...candidate, project: { ...candidate.project, created_at: 'restored' } } },
    ])
      receive({ type: 'candidate_changed', project_id: 'P1', turn_id: 'T1', ...event });
    assert.equal(workspace.state.project.plan_candidate, undefined);
    receive({ type: 'candidate_changed', candidate });
    receive({
      type: 'candidate_changed',
      candidate: { ...candidate, revision: 3, status: 'generating' },
    });
    assert.equal(workspace.state.project.plan_candidate.revision, 4);
    assert.equal(workspace.state.project.plan_candidate.status, 'reviewing');
    receive({ type: 'done', summary: { turn_id: 'T1', status: 'waiting', changed: false } });
  };
  await agent.send('P1', 'Plan safely');
});

test('candidate updates invalidate stale same-revision reads while canonical commit remains explicit', async () => {
  const { agent, env, workspace, records, commands } = await agentHarness(
    'candidate-read-order',
    true,
  );
  const original = structuredClone(records.get('P1'));
  const candidate = planningCandidate(original);
  let release;
  const oldRead = new Promise((resolve) => {
    release = resolve;
  });
  const prior = commands.handler;
  commands.handler = (action, params) =>
    action === 'projects.get' && params.project_id === 'P1' ? oldRead : prior(action, params);
  const selecting = workspace.selectProject('P1');
  workspace.applyPlanCandidate('P1', candidate);
  release(original);
  await selecting;
  assert.equal(workspace.state.project.plan_candidate.id, candidate.id);
  commands.handler = prior;
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    receive({
      type: 'candidate_changed',
      candidate: { ...candidate, revision: 2, status: 'applied' },
    });
    assert.equal(workspace.state.project.revision, original.revision);
    const committed = {
      ...candidate.project,
      plan_candidate: { ...candidate, status: 'applied', revision: 2 },
    };
    records.set('P1', committed);
    receive({
      type: 'done',
      project: committed,
      summary: { turn_id: 'T1', status: 'completed', changed: true },
    });
  };
  await agent.send('P1', 'Plan safely');
  assert.equal(workspace.state.project.revision, candidate.project.revision);
  assert.equal(workspace.state.project.milestones[0].id, 'candidate-only');
});

test('retired project incarnation cannot receive a candidate even if its ID and creation time are reused', async () => {
  const { agent, env, workspace, drafts, records } = await agentHarness(
    'candidate-retired-incarnation',
    true,
  );
  const candidate = planningCandidate(structuredClone(records.get('P1')));
  env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'T1' });
    drafts.discard('P1');
    drafts.activate('P1');
    receive({ type: 'candidate_changed', candidate });
    assert.equal(workspace.state.project.plan_candidate, undefined);
    receive({ type: 'done', summary: { turn_id: 'T1', status: 'stopped', changed: false } });
  };
  await agent.send('P1', 'Plan safely');
});

test('failed unified submission keeps its admitted turn identity for exact retry messaging', async () => {
  const h = await agentHarness('candidate-failed-retry-identity');
  const attempt = h.drafts.start('P1', { text: 'Original request', ids: [] });
  h.env.run = async (_, receive) => {
    receive({ type: 'started', turn_id: 'candidate-turn' });
    receive({
      type: 'done',
      summary: {
        turn_id: 'candidate-turn',
        status: 'failed',
        changed: false,
        candidate_outcome: {
          id: 'candidate-turn',
          status: 'failed',
          canonical_unchanged: true,
          note: '候选保留',
        },
      },
    });
  };
  const delivered = await h.agent.send(
    'P1',
    attempt.text,
    undefined,
    [],
    undefined,
    undefined,
    attempt,
  );
  assert.equal(delivered, false);
  h.drafts.settle(attempt, delivered);
  const draft = h.drafts.bind(() => 'P1');
  assert.equal(draft.failures.value[0].turnId, 'candidate-turn');
});

test('source analysis is opt-in only and retains composer, verification and submission positions', async () => {
  for (const sourceAnalysis of [false, true]) {
    const h = await agentHarness(`source-analysis-opt-in-${sourceAnalysis}`);
    const document = { version: 1, parts: [{ type: 'text', text: 'Inspect source' }] };
    const attempt = h.drafts.start('P1', { text: 'Inspect source', ids: ['A1'] });
    h.env.run = async (body, receive) => {
      assert.equal(Object.hasOwn(body, 'source_analysis'), sourceAnalysis);
      if (sourceAnalysis) assert.equal(body.source_analysis, true);
      assert.equal(body.project_id, 'P1');
      assert.equal(body.question_id, 'Q1');
      assert.deepEqual(body.attachment_ids, ['A1']);
      assert.deepEqual(body.composer_document, document);
      assert.equal(body.verification_milestone, 'M1');
      receive({ type: 'started', turn_id: 'source-turn' });
      receive({
        type: 'done',
        summary: { turn_id: 'source-turn', status: 'completed', changed: false },
      });
    };
    assert.equal(
      await h.agent.send(
        'P1',
        'Inspect source',
        'Q1',
        ['A1'],
        'M1',
        document,
        attempt,
        sourceAnalysis,
      ),
      true,
    );
    assert.equal(attempt.turnId, 'source-turn');
  }
});

test('native and HTTP agent transports preserve explicit source-analysis intent on the original request', async () => {
  const requests = [];
  const target = browser({
    start_agent: async (id, body) => {
      requests.push(body);
      queueMicrotask(() => terminal(target, id, { type: 'done' }));
      return { started: true };
    },
  });
  const body = { ...params, source_analysis: true };
  await agentStream(body, () => {}, new AbortController().signal);
  globalThis.window = {};
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async (_, request) => {
    requests.push(JSON.parse(request.body));
    return new Response('{"type":"done"}\n', { status: 200 });
  };
  try {
    await agentStream(body, () => {}, new AbortController().signal);
  } finally {
    globalThis.fetch = oldFetch;
  }
  assert.deepEqual(requests, [
    { ...body, snapshot_mode: 'compact-v1' },
    { ...body, snapshot_mode: 'compact-v1' },
  ]);
});

test('a failed source-question answer retains its resolved operation for explicit retries only', async () => {
  const h = await agentHarness('source-answer-retry');
  const attempt = h.drafts.start('P1', {
    text: 'Login',
    ids: [],
    questionId: 'source-question',
    question: { id: 'source-question', prompt: 'Which source behavior matters?', context: '' },
  });
  h.env.run = async (body, receive) => {
    assert.equal(body.source_analysis, undefined, 'the initial answer is routed by its question');
    receive({ type: 'started', turn_id: 'source-answer', source_analysis: true });
    receive({ type: 'error', message: 'Provider failed after consuming the question' });
    receive({
      type: 'done',
      summary: { turn_id: 'source-answer', status: 'failed', changed: false },
    });
  };
  const delivered = await h.agent.send(
    'P1',
    attempt.text,
    attempt.questionId,
    [],
    undefined,
    undefined,
    attempt,
  );
  assert.equal(delivered, false);
  h.drafts.settle(attempt, delivered);
  const failure = h.drafts.bind(() => 'P1').failures.value[0];
  assert.equal(failure.sourceAnalysis, true);
  const plan = planAgentRetry(failure, { question: null, milestones: [] });
  assert.equal(plan.kind, 'ready');
  assert.equal(plan.request.questionId, undefined);
  assert.equal(plan.request.sourceAnalysis, true);
  const preparation = h.drafts.prepareRetry('P1', failure.id);
  const retry = h.drafts.commitRetry(preparation, plan.request);
  assert.equal(retry.sourceAnalysis, true);
  h.env.run = async (body) => {
    assert.equal(body.source_analysis, true);
    throw new DOMException('Aborted before admission', 'AbortError');
  };
  const retryDelivered = await h.agent.send(
    'P1',
    retry.request.text,
    retry.request.questionId,
    [],
    undefined,
    undefined,
    retry,
    retry.request.sourceAnalysis,
  );
  assert.equal(retryDelivered, false);
  h.drafts.settle(retry, retryDelivered);
  assert.equal(h.drafts.bind(() => 'P1').failures.value[0].sourceAnalysis, true);
  h.env.run = async (body, receive) => {
    assert.equal(
      body.source_analysis,
      undefined,
      'ordinary new prompts do not inherit the old operation',
    );
    receive({ type: 'started', turn_id: 'ordinary-turn' });
    receive({
      type: 'done',
      summary: { turn_id: 'ordinary-turn', status: 'completed', changed: false },
    });
  };
  assert.equal(await h.agent.send('P1', 'Plan a new feature'), true);
});
