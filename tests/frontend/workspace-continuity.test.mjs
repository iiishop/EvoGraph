import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const vueUrl = pathToFileURL(require.resolve('vue')).href;
const compile = (path) =>
  ts.transpileModule(readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
const project = (id, version = 0) => ({
  id,
  name: id,
  milestones: [],
  source_milestones: [],
  version,
});
let sequence = 0;
async function harness(initialize = true) {
  const id = ++sequence;
  const apiUrl = moduleUrl(`export const env = { calls: [], handler: null };
    export const command = (action, params = {}) => { env.calls.push([action, params]); return env.handler(action, params); };
    export const readyTransport = async () => {}; // ${id}`);
  const draftsUrl = moduleUrl(
    compile('composables/useAgentDrafts.ts').replace(
      "from 'vue'",
      `from ${JSON.stringify(vueUrl)}`,
    ) + `\n// ${id}`,
  );
  const imports = {
    vue: vueUrl,
    '../api/client': apiUrl,
    './useAgentDrafts': draftsUrl,
    './useNotifications': moduleUrl('export const useNotifications = () => ({ push() {} });'),
  };
  const code = compile('composables/useWorkspace.ts').replace(
    /from (['"])([^'"]+)\1/g,
    (_, quote, name) => {
      assert.ok(imports[name], `Unexpected import ${name}`);
      return `from ${JSON.stringify(imports[name])}`;
    },
  );
  const { useWorkspace } = await import(moduleUrl(code));
  const { agentDrafts } = await import(draftsUrl);
  const { env } = await import(apiUrl);
  const records = new Map(['A', 'B', 'C'].map((id) => [id, project(id)]));
  const handlers = {};
  env.handler = async (action, params) => {
    if (handlers[action]) return handlers[action](params);
    if (action === 'projects.get') {
      if (!records.has(params.project_id)) throw new Error('Project missing');
      return structuredClone(records.get(params.project_id));
    }
    if (action === 'projects.list')
      return [...records.values()].map(({ id, name }) => ({ id, name }));
    if (action === 'settings.get') return { provider: { config: { model: 'test' } } };
    if (action === 'projects.delete') {
      records.delete(params.project_id);
      return { deleted: true };
    }
    if (action === 'projects.restore') {
      const restored = project(params.project_id);
      records.set(restored.id, restored);
      return restored;
    }
    if (action === 'projects.bootstrap') return undefined;
    throw new Error(`Unexpected action ${action}`);
  };
  const saved = new Map([['evograph.project', 'A']]);
  globalThis.localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
  };
  const workspace = useWorkspace();
  if (initialize) await workspace.init();
  return { workspace, drafts: agentDrafts, records, handlers, env, saved };
}

test('a pending user selection beats an earlier A refresh in either response order', async () => {
  for (const order of ['refresh-first', 'selection-first']) {
    const { workspace, handlers, saved } = await harness();
    const a = deferred(),
      b = deferred();
    handlers['projects.get'] = ({ project_id }) => (project_id === 'A' ? a.promise : b.promise);
    const refresh = workspace.refresh();
    await tick();
    const select = workspace.selectProject('B');
    if (order === 'refresh-first') {
      a.resolve(project('A', 1));
      await refresh;
      b.resolve(project('B', 2));
    } else {
      b.resolve(project('B', 2));
      await select;
      a.resolve(project('A', 1));
    }
    await Promise.all([refresh, select]);
    assert.equal(workspace.state.project.id, 'B');
    assert.equal(workspace.state.project.version, 2);
    assert.equal(saved.get('evograph.project'), 'B');
  }
});

test('a refresh started while B is pending does not promote the rendered A to a new selection', async () => {
  const { workspace, handlers, env } = await harness();
  const b = deferred();
  handlers['projects.get'] = ({ project_id }) =>
    project_id === 'B' ? b.promise : project('A', 99);
  const select = workspace.selectProject('B');
  const count = env.calls.filter(([action]) => action === 'projects.get').length;
  await workspace.refresh();
  assert.equal(env.calls.filter(([action]) => action === 'projects.get').length, count);
  b.resolve(project('B', 1));
  await select;
  assert.equal(workspace.state.project.id, 'B');
});

test('a list response spanning navigation cannot discard the new project draft or load old detail', async () => {
  const { workspace, handlers, drafts } = await harness();
  const list = deferred();
  handlers['projects.list'] = () => list.promise;
  const refreshing = workspace.refresh();
  await workspace.selectProject('B');
  drafts.bind(() => 'B').content.value = 'new B draft';
  list.resolve([project('A')]);
  await refreshing;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(drafts.bind(() => 'B').content.value, 'new B draft');
});

test('rapid selections ignore both older successes and older failures', async () => {
  const { workspace, handlers, saved } = await harness();
  const a = deferred(),
    b = deferred(),
    c = deferred();
  handlers['projects.get'] = ({ project_id }) => ({ A: a, B: b, C: c })[project_id].promise;
  const selectingA = workspace.selectProject('A');
  const selectingB = workspace.selectProject('B');
  const selectingC = workspace.selectProject('C');
  c.resolve(project('C', 3));
  await selectingC;
  b.reject(new Error('older B failure'));
  a.resolve(project('A', 1));
  await Promise.all([selectingA, selectingB]);
  assert.equal(workspace.state.project.id, 'C');
  assert.equal(workspace.state.error, '');
  assert.equal(saved.get('evograph.project'), 'C');
});

test('overlapping same-project refreshes keep the latest request, with no late error replacing it', async () => {
  for (const olderFails of [false, true]) {
    const { workspace, handlers } = await harness();
    const first = deferred(),
      second = deferred();
    let calls = 0;
    handlers['projects.get'] = () => (++calls === 1 ? first : second).promise;
    const old = workspace.refresh();
    await tick();
    const current = workspace.refresh();
    await tick();
    second.resolve(project('A', 2));
    await current;
    if (olderFails) first.reject(new Error('stale refresh'));
    else first.resolve(project('A', 1));
    await old;
    assert.equal(workspace.state.project.version, 2);
    assert.equal(workspace.state.error, '');
  }
});

test('a superseded refresh error is ignored but a current selection failure remains useful and retryable', async () => {
  const { workspace, handlers } = await harness();
  const a = deferred();
  handlers['projects.get'] = ({ project_id }) => (project_id === 'A' ? a.promise : project('B'));
  const refresh = workspace.refresh();
  await tick();
  await workspace.selectProject('B');
  a.reject(new Error('old A refresh failed'));
  await refresh;
  assert.equal(workspace.state.error, '');
  handlers['projects.get'] = () => {
    throw new Error('current selection unavailable');
  };
  await workspace.selectProject('C');
  assert.equal(workspace.state.project.id, 'B');
  assert.match(workspace.state.error, /current selection unavailable/);
  handlers['projects.get'] = () => project('C', 4);
  await workspace.selectProject('C');
  assert.equal(workspace.state.project.id, 'C');
  assert.equal(workspace.state.error, '');
});

test('confirmed A deletion preserves a pending B selection and clears only A draft state', async () => {
  const { workspace, handlers, drafts } = await harness();
  drafts.bind(() => 'A').content.value = 'delete me';
  drafts.bind(() => 'B').content.value = 'keep me';
  const b = deferred();
  handlers['projects.get'] = () => b.promise;
  const selecting = workspace.selectProject('B');
  await workspace.deleteProject({ id: 'A', name: 'A' });
  assert.equal(workspace.state.project, null);
  b.resolve(project('B'));
  await selecting;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(drafts.bind(() => 'A').content.value, '');
  assert.equal(drafts.bind(() => 'B').content.value, 'keep me');
});

test('deleting the pending selection invalidates its late response, and undo has a clean draft', async () => {
  const { workspace, handlers, drafts } = await harness();
  drafts.bind(() => 'A').content.value = 'old request';
  const attempt = drafts.start('A', { text: 'old request', ids: ['old-file'] });
  const a = deferred();
  handlers['projects.get'] = () => a.promise;
  const selecting = workspace.selectProject('A');
  await workspace.deleteProject({ id: 'A', name: 'A' });
  a.resolve(project('A'));
  await selecting;
  assert.equal(workspace.state.project, null);
  drafts.settle(attempt, false);
  assert.equal(drafts.bind(() => 'A').content.value, '');
  delete handlers['projects.get'];
  await workspace.undoDelete();
  assert.equal(workspace.state.project.id, 'A');
  assert.equal(drafts.bind(() => 'A').content.value, '');
  assert.deepEqual(drafts.bind(() => 'A').failures.value, []);
});

test('confirmed deletion clears immediately even when subsequent refresh fails; failed deletion retains drafts', async () => {
  for (const deleted of [false, true]) {
    const { workspace, handlers, drafts } = await harness();
    drafts.bind(() => 'A').content.value = 'draft';
    if (!deleted)
      handlers['projects.delete'] = () => {
        throw new Error('delete refused');
      };
    else
      handlers['projects.list'] = () => {
        throw new Error('refresh offline');
      };
    await workspace.deleteProject({ id: 'A', name: 'A' });
    assert.equal(drafts.bind(() => 'A').content.value, deleted ? '' : 'draft');
    if (deleted) {
      assert.equal(workspace.state.project, null);
      assert.equal(workspace.state.deletedProject.id, 'A');
    }
  }
});

test('accepted external-deletion refresh prunes removed projects without switching to another project', async () => {
  const { workspace, records, drafts } = await harness();
  drafts.bind(() => 'A').content.value = 'removed';
  drafts.bind(() => 'B').content.value = 'retained';
  records.delete('A');
  await workspace.refresh();
  assert.equal(workspace.state.project, null);
  assert.equal(drafts.bind(() => 'A').content.value, '');
  assert.equal(drafts.bind(() => 'B').content.value, 'retained');
});

test('initial saved-project restore does not override a newer explicit user selection', async () => {
  const { workspace, handlers } = await harness(false);
  const bootstrap = deferred();
  handlers['projects.bootstrap'] = () => bootstrap.promise;
  const initializing = workspace.init();
  await tick();
  await workspace.selectProject('B');
  bootstrap.resolve();
  await initializing;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(workspace.state.loading, false);
});

test('undo restores the project without overriding a newer explicit selection', async () => {
  const { workspace, handlers, records } = await harness();
  await workspace.deleteProject({ id: 'A', name: 'A' });
  const restored = deferred();
  handlers['projects.restore'] = () => restored.promise;
  const undo = workspace.undoDelete();
  await workspace.selectProject('B');
  records.set('A', project('A'));
  restored.resolve(project('A'));
  await undo;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(workspace.state.deletedProject, null);
  assert.ok(workspace.state.projects.some((item) => item.id === 'A'));
});
