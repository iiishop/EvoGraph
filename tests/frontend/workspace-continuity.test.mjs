import { browseStoreUrl } from './helpers/architecture-fixtures.mjs';
import {
  composerDocumentUrl,
  composerEditorStubUrl,
  messageContentStubUrl,
} from './helpers/composer-fixtures.mjs';
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
  ts
    .transpileModule(readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace(
      /(['"])(?:\.\.\/lib\/|\.\/|\.\.\/\.\.\/lib\/)composerDocument\1/g,
      JSON.stringify(composerDocumentUrl),
    );
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
  archived: false,
  description: '',
  repository: '',
  is_demo: false,
  acceptance: { passed: 0, total: 0, achieved: false },
  milestones: [],
  source_milestones: [],
  version,
  revision: version,
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
  const workflowUrl = moduleUrl(
    compile('composables/useWorkflowDrafts.ts').replace(
      "from 'vue'",
      `from ${JSON.stringify(vueUrl)}`,
    ) + `\n// ${id}`,
  );
  const imports = {
    [composerDocumentUrl]: composerDocumentUrl,
    '../../lib/composerDocument': composerDocumentUrl,
    './ComposerEditor.vue': composerEditorStubUrl,
    './MessageContent.vue': messageContentStubUrl,
    vue: vueUrl,
    '../api/client': apiUrl,
    './useAgentDrafts': draftsUrl,
    './useWorkflowDrafts': workflowUrl,
    './useArchitectureBrowse': browseStoreUrl,
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
  const { workflowDrafts } = await import(workflowUrl);
  const { architectureBrowse } = await import(browseStoreUrl);
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
    if (action === 'projects.restore_archived') {
      const restored = project(params.project_id);
      records.set(restored.id, restored);
      return { project: restored, restored: true };
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
  return {
    workspace,
    drafts: agentDrafts,
    workflows: workflowDrafts,
    browsing: architectureBrowse,
    records,
    handlers,
    env,
    saved,
  };
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
  handlers['projects.restore_archived'] = () => restored.promise;
  const undo = workspace.undoDelete();
  await workspace.selectProject('B');
  records.set('A', project('A'));
  restored.resolve({ project: project('A'), restored: true });
  await undo;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(workspace.state.deletedProject, null);
  assert.ok(workspace.state.projects.some((item) => item.id === 'A'));
});

test('a late background read cannot roll back an authoritative streamed revision or same-revision history', async () => {
  for (const sameRevision of [false, true]) {
    const { workspace, handlers } = await harness();
    const read = deferred();
    handlers['projects.get'] = () => read.promise;
    const pending = workspace.refresh();
    await tick();
    const current = {
      ...project('A', 20),
      attachments: [{ id: 'confirmed-asset' }],
      messages: [{ content: 'new saved receipt' }],
    };
    workspace.applyProject(current);
    read.resolve({ ...project('A', sameRevision ? 20 : 19), attachments: [], messages: [] });
    await pending;
    assert.equal(workspace.state.project.revision, 20);
    assert.deepEqual(workspace.state.project.attachments, current.attachments);
    assert.deepEqual(workspace.state.project.messages, current.messages);
  }
});

test('older same-project snapshots are ignored but fresh equal-revision details remain refreshable', async () => {
  const { workspace, handlers } = await harness();
  workspace.applyProject({ ...project('A', 20), messages: ['current'] });
  workspace.applyProject({ ...project('A', 19), messages: ['old stream'] });
  assert.deepEqual(workspace.state.project.messages, ['current']);
  handlers['projects.get'] = () => ({ ...project('A', 19), messages: ['old read'] });
  await workspace.refresh();
  assert.deepEqual(workspace.state.project.messages, ['current']);
  handlers['projects.get'] = () => ({ ...project('A', 20), messages: ['fresh equal revision'] });
  await workspace.refresh();
  assert.deepEqual(workspace.state.project.messages, ['fresh equal revision']);
  handlers['projects.get'] = () => ({ ...project('A', 21), attachments: ['next saved file'] });
  await workspace.refresh();
  assert.equal(workspace.state.project.revision, 21);
  assert.deepEqual(workspace.state.project.attachments, ['next saved file']);
});

test('a late refresh failure is obsolete after a live snapshot, without cancelling newer navigation', async () => {
  const { workspace, handlers } = await harness();
  const read = deferred();
  handlers['projects.get'] = ({ project_id }) =>
    project_id === 'A' ? read.promise : project('B', 1);
  const pending = workspace.refresh();
  await tick();
  workspace.applyProject(project('A', 2));
  await workspace.selectProject('B');
  read.reject(new Error('late failed read'));
  await pending;
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(workspace.state.error, '');
});

test('a delayed read from before deletion cannot overwrite a restored project incarnation', async () => {
  const { workspace, handlers, records, drafts } = await harness();
  const oldRead = deferred();
  let calls = 0;
  handlers['projects.get'] = ({ project_id }) =>
    ++calls === 1 ? oldRead.promise : structuredClone(records.get(project_id));
  const pending = workspace.refresh();
  await tick();
  await workspace.deleteProject({ id: 'A', name: 'A' });
  await workspace.undoDelete();
  drafts.bind(() => 'A').content.value = 'restored project draft';
  workspace.applyProject({ ...project('A', 1), messages: ['restored'] });
  oldRead.resolve({ ...project('A', 50), messages: ['deleted incarnation'] });
  await pending;
  assert.equal(workspace.state.project.revision, 1);
  assert.deepEqual(workspace.state.project.messages, ['restored']);
  assert.equal(drafts.bind(() => 'A').content.value, 'restored project draft');
});

test('only accepted complete snapshots invalidate removed-node drafts and reintroduced IDs get clean entries', async () => {
  const { workspace, records, workflows } = await harness();
  const withNodes = (revision, ids) => ({
    ...project('A', revision),
    milestones: ids.map((id) => ({ id })),
  });
  workspace.applyProject(withNodes(1, ['M1', 'M2']));
  const first = workflows.bind(
      () => 'A',
      () => 'M1',
    ),
    second = workflows.bind(
      () => 'A',
      () => 'M2',
    );
  first.report.value = 'A M1';
  second.report.value = 'A M2';
  workspace.applyProject(withNodes(0, []));
  assert.equal(first.report.value, 'A M1', 'rejected stale snapshot cannot delete drafts');
  workspace.applyProject(withNodes(2, ['M2']));
  assert.equal(first.draft.value, undefined);
  assert.equal(second.report.value, 'A M2');
  records.set('A', withNodes(3, ['M1', 'M2']));
  await workspace.refresh();
  assert.equal(first.report.value, '');
  first.report.value = 'fresh';
  await workspace.selectProject('B');
  await workspace.selectProject('A');
  assert.equal(first.report.value, 'fresh');
  records.set('A', withNodes(4, ['M2']));
  await workspace.selectProject('A');
  assert.equal(first.draft.value, undefined);
});

test('accepted workspace lifecycle retains architecture navigation across selections and discards it on deletion or list removal', async () => {
  const { workspace, browsing, records } = await harness();
  const a = browsing.viewState(workspace.state.project);
  a.query = 'architecture browse state';
  await workspace.selectProject('B');
  assert.equal(browsing.viewState(workspace.state.project).query, '');
  await workspace.selectProject('A');
  assert.equal(browsing.viewState(workspace.state.project).query, 'architecture browse state');
  await workspace.deleteProject({ id: 'A', name: 'A' });
  assert.equal(browsing.viewState(project('A')), undefined);
  await workspace.undoDelete();
  assert.equal(browsing.viewState(workspace.state.project).query, '');
  assert.notEqual(browsing.viewState(workspace.state.project).key, a.key);
  browsing.viewState(workspace.state.project).query = 'restored';
  await workspace.selectProject('B');
  records.delete('A');
  await workspace.refresh();
  assert.equal(browsing.viewState(project('A')), undefined);
});

test('a delayed project selection cannot activate drafts or browsing after a Settings round-trip', async () => {
  for (const returnToProjects of [false, true]) {
    const { workspace, handlers, drafts, workflows, browsing, saved } = await harness();
    drafts.bind(() => 'A').content.value = 'keep the current draft';
    const a = browsing.viewState(workspace.state.project);
    a.query = 'keep query';
    const pending = deferred();
    handlers['projects.get'] = () => pending.promise;
    const selecting = workspace.selectProject('B');
    workspace.setPage('settings');
    if (returnToProjects) workspace.setPage('projects');
    pending.resolve(project('B', 5));
    await selecting;
    assert.equal(workspace.state.page, returnToProjects ? 'projects' : 'settings');
    assert.equal(workspace.state.project.id, 'A');
    assert.equal(saved.get('evograph.project'), 'A');
    assert.equal(drafts.bind(() => 'A').content.value, 'keep the current draft');
    assert.equal(browsing.viewState(workspace.state.project).query, 'keep query');
    assert.equal(workspace.state.error, '');
  }
});

test('same-project delayed reads and errors cannot replace newer equal-revision history or browsing', async () => {
  for (const failed of [false, true]) {
    const { workspace, handlers, browsing } = await harness();
    workspace.applyProject({ ...project('A', 10), messages: [], events: [] });
    browsing.viewState(workspace.state.project).query = 'api';
    const pending = deferred();
    handlers['projects.get'] = () => pending.promise;
    const selecting = workspace.selectProject('A');
    workspace.applyProject({
      ...project('A', 10),
      messages: [{ id: 'new canonical history' }],
      events: [{ id: 'new event' }],
    });
    if (failed) pending.reject(new Error('obsolete read failure'));
    else pending.resolve({ ...project('A', 10), messages: [], events: [] });
    await selecting;
    assert.equal(workspace.state.project.messages[0].id, 'new canonical history');
    assert.equal(workspace.state.project.events[0].id, 'new event');
    assert.equal(browsing.viewState(workspace.state.project).query, 'api');
    assert.equal(workspace.state.error, '');
  }
});

test('same-project selection rejects an older revision but accepts a newly created incarnation', async () => {
  const { workspace, handlers, browsing } = await harness();
  workspace.applyProject({ ...project('A', 20), created_at: 'old', messages: [{ id: 'current' }] });
  browsing.viewState(workspace.state.project).query = 'old incarnation';
  handlers['projects.get'] = () => ({ ...project('A', 19), created_at: 'old', messages: [] });
  await workspace.selectProject('A');
  assert.equal(workspace.state.project.revision, 20);
  assert.equal(workspace.state.project.messages[0].id, 'current');
  const previousKey = browsing.viewState(workspace.state.project).key;
  handlers['projects.get'] = () => ({ ...project('A', 0), created_at: 'new', messages: [] });
  await workspace.selectProject('A');
  assert.equal(workspace.state.project.created_at, 'new');
  assert.equal(browsing.viewState(workspace.state.project).query, '');
  assert.notEqual(browsing.viewState(workspace.state.project).key, previousKey);
});

test('compact stream merges only planning state and canonical messages deduplicate by saved ID', async () => {
  const { workspace } = await harness();
  const message = {
    id: 'saved-user',
    content: '#original @label',
    composer_document: { version: 1, parts: [{ type: 'text', text: '#original @label' }] },
  };
  workspace.applyProject({ ...project('A', 10), messages: [message], events: [{ id: 'receipt' }] });
  workspace.applyProject({
    ...project('A', 11),
    snapshot_mode: 'compact-v1',
    question: { id: 'Q1' },
  });
  assert.equal(workspace.state.project.revision, 11);
  assert.deepEqual(workspace.state.project.messages, [message]);
  assert.deepEqual(workspace.state.project.events, [{ id: 'receipt' }]);
  const narration = {
    id: 'saved-assistant',
    project_id: 'A',
    role: 'assistant',
    content: 'full narration'.repeat(2000),
  };
  workspace.appendMessage('A', narration);
  workspace.appendMessage('A', narration);
  workspace.appendMessage('B', { id: 'other', content: 'not A' });
  assert.deepEqual(workspace.state.project.messages, [message, narration]);
  workspace.applyProject({
    ...project('A', 12),
    messages: [message, narration],
    events: [{ id: 'terminal' }],
  });
  assert.equal(workspace.state.project.messages.length, 2);
  assert.deepEqual(workspace.state.project.events, [{ id: 'terminal' }]);
});

test('same-project selection reads and errors cannot replace newer equal-revision stream history', async () => {
  for (const failed of [false, true]) {
    const { workspace, handlers } = await harness();
    workspace.applyProject({ ...project('A', 10), messages: [], events: [] });
    const pending = deferred();
    handlers['projects.get'] = () => pending.promise;
    const selecting = workspace.selectProject('A');
    workspace.appendMessage('A', {
      id: 'canonical',
      project_id: 'A',
      role: 'assistant',
      content: 'new at same revision',
    });
    if (failed) pending.reject(new Error('obsolete failure'));
    else pending.resolve({ ...project('A', 10), messages: [], events: [] });
    await selecting;
    assert.equal(workspace.state.project.messages[0].id, 'canonical');
    assert.equal(workspace.state.error, '');
  }
});

test('same-project selection refuses an older revision even without an intervening stream event', async () => {
  const { workspace, handlers } = await harness();
  workspace.applyProject({ ...project('A', 20), messages: [{ id: 'new' }], events: [] });
  handlers['projects.get'] = () => ({ ...project('A', 19), messages: [], events: [] });
  await workspace.selectProject('A');
  assert.equal(workspace.state.project.revision, 20);
  assert.equal(workspace.state.project.messages[0].id, 'new');
});

test('same-revision saved-message event also invalidates background refresh', async () => {
  const { workspace, handlers } = await harness();
  workspace.applyProject({ ...project('A', 20), messages: [], events: [] });
  const read = deferred();
  handlers['projects.get'] = () => read.promise;
  const refreshing = workspace.refresh();
  await tick();
  workspace.appendMessage('A', {
    id: 'latest',
    project_id: 'A',
    role: 'assistant',
    content: 'canonical',
  });
  read.resolve({ ...project('A', 20), messages: [], events: [] });
  await refreshing;
  assert.equal(workspace.state.project.messages[0].id, 'latest');
});

test('canonical append rejects mismatched record identity and malformed saved-message shapes', async () => {
  const { workspace } = await harness();
  workspace.applyProject({ ...project('A', 20), messages: [], events: [] });
  const message = { id: 'saved', project_id: 'A', role: 'assistant', content: 'exact text' };
  for (const change of [
    { project_id: 'B' },
    { project_id: undefined },
    { id: '' },
    { id: 4 },
    { content: null },
    { role: 'system' },
  ])
    workspace.appendMessage('A', { ...message, ...change });
  assert.equal(workspace.state.project.messages.length, 0);
  workspace.appendMessage('A', message);
  assert.deepEqual(workspace.state.project.messages, [message]);
});

test('offscreen stream completion invalidates the held selection and rereads canonical final history', async () => {
  const { workspace, handlers, records, env, saved, drafts, browsing } = await harness();
  const snapshot = (revision) => ({
    ...project('A', revision),
    created_at: 'atlas-incarnation',
    milestones: [revision - 2, revision - 1, revision].map((id) => ({ id: `M${id}` })),
    messages: [{ id: `TOOLS-${revision}`, content: `saved at ${revision}` }],
    events: [],
  });
  records.set('A', snapshot(12));
  await workspace.selectProject('A');
  drafts.bind(() => 'A').content.value = 'keep unsent Atlas draft';
  browsing.viewState(workspace.state.project).query = 'Atlas query';
  await workspace.selectProject('B');
  const oldRead = deferred(),
    freshRead = deferred();
  let reads = 0;
  handlers['projects.get'] = ({ project_id }) => {
    assert.equal(project_id, 'A');
    return ++reads === 1 ? oldRead.promise : freshRead.promise;
  };
  env.calls.length = 0;
  const selecting = workspace.selectProject('A');
  for (let revision = 13; revision <= 64; revision++) {
    const { messages, events, ...planning } = snapshot(revision);
    workspace.applyProject({ ...planning, snapshot_mode: 'compact-v1' });
  }
  const finalMessage = {
    id: 'FINAL-18',
    project_id: 'A',
    role: 'assistant',
    content: 'final narration',
  };
  workspace.appendMessage('A', finalMessage);
  const final = {
    ...snapshot(64),
    messages: [finalMessage],
    events: [{ id: 'completed-receipt' }],
  };
  records.set('A', final);
  workspace.applyProject(final);
  await workspace.refresh(); // Native terminal refresh occurs before the held selection returns.
  assert.equal(workspace.state.project.id, 'B');
  oldRead.resolve(snapshot(12));
  await tick();
  assert.equal(reads, 2);
  assert.equal(workspace.state.project.id, 'B', 'stale intermediate data is never rendered');
  freshRead.resolve(structuredClone(final));
  await selecting;
  assert.equal(workspace.state.project.id, 'A');
  assert.equal(workspace.state.project.revision, 64);
  assert.deepEqual(
    workspace.state.project.milestones.map(({ id }) => id),
    ['M62', 'M63', 'M64'],
  );
  assert.deepEqual(workspace.state.project.messages, [finalMessage]);
  assert.deepEqual(workspace.state.project.events, [{ id: 'completed-receipt' }]);
  assert.equal(drafts.bind(() => 'A').content.value, 'keep unsent Atlas draft');
  assert.equal(browsing.viewState(workspace.state.project).query, 'Atlas query');
  assert.equal(saved.get('evograph.project'), 'A');
  assert.ok(
    env.calls.every(([action]) =>
      ['projects.get', 'projects.list', 'settings.get'].includes(action),
    ),
  );
});

test('offscreen equal-revision canonical messages invalidate stale selection success and failure', async () => {
  for (const failed of [false, true]) {
    const { workspace, handlers, env } = await harness();
    await workspace.selectProject('B');
    const oldRead = deferred();
    const message = {
      id: 'canonical',
      project_id: 'A',
      role: 'assistant',
      content: 'saved without a revision change',
    };
    const final = { ...project('A', 20), messages: [message], events: [{ id: 'receipt' }] };
    let reads = 0;
    handlers['projects.get'] = () => (++reads === 1 ? oldRead.promise : structuredClone(final));
    env.calls.length = 0;
    const selecting = workspace.selectProject('A');
    workspace.appendMessage('A', message);
    if (failed) oldRead.reject(new Error('obsolete read failure'));
    else oldRead.resolve({ ...project('A', 20), messages: [], events: [] });
    await selecting;
    assert.equal(reads, 2);
    assert.deepEqual(workspace.state.project.messages, [message]);
    assert.equal(workspace.state.error, '');
    assert.deepEqual(
      env.calls.map(([action]) => action),
      ['projects.get', 'projects.get'],
    );
  }
});

test('activity racing a selection retry requires another owned read, never stale canonical history', async () => {
  const { workspace, handlers, env } = await harness();
  await workspace.selectProject('B');
  const reads = [deferred(), deferred(), deferred()];
  let count = 0;
  handlers['projects.get'] = () => reads[count++].promise;
  env.calls.length = 0;
  const selecting = workspace.selectProject('A');
  workspace.applyProject({ ...project('A', 10), snapshot_mode: 'compact-v1' });
  reads[0].resolve({ ...project('A', 9), messages: [], events: [] });
  await tick();
  const message = {
    id: 'latest',
    project_id: 'A',
    role: 'assistant',
    content: 'saved during retry',
  };
  workspace.appendMessage('A', message);
  reads[1].resolve({ ...project('A', 10), messages: [], events: [] });
  await tick();
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(count, 3);
  reads[2].resolve({ ...project('A', 10), messages: [message], events: [{ id: 'final' }] });
  await selecting;
  assert.deepEqual(workspace.state.project.messages, [message]);
  assert.deepEqual(workspace.state.project.events, [{ id: 'final' }]);
  assert.deepEqual(
    env.calls.map(([action]) => action),
    ['projects.get', 'projects.get', 'projects.get'],
  );
});

test('new project or Settings navigation cancels an offscreen read or its retry without reactivation', async () => {
  for (const stage of ['initial', 'retry']) {
    for (const destination of ['C', 'settings', 'settings-round-trip']) {
      const { workspace, handlers, saved, browsing } = await harness();
      await workspace.selectProject('B');
      const first = deferred(),
        second = deferred();
      let reads = 0;
      handlers['projects.get'] = ({ project_id }) =>
        project_id === 'C' ? project('C', 3) : (++reads === 1 ? first : second).promise;
      const selecting = workspace.selectProject('A');
      workspace.applyProject({ ...project('A', 10), snapshot_mode: 'compact-v1' });
      if (stage === 'retry') {
        first.resolve(project('A', 9));
        await tick();
        assert.equal(reads, 2);
      }
      if (destination === 'C') await workspace.selectProject('C');
      else {
        workspace.setPage('settings');
        if (destination === 'settings-round-trip') workspace.setPage('projects');
      }
      const winner = workspace.state.project;
      const browser = browsing.viewState(winner);
      browser.query = 'new intent';
      if (stage === 'initial') first.resolve(project('A', 9));
      else second.resolve(project('A', 10));
      await selecting;
      assert.equal(reads, stage === 'initial' ? 1 : 2);
      assert.equal(workspace.state.project, winner);
      assert.equal(browsing.viewState(winner).query, 'new intent');
      assert.equal(saved.get('evograph.project'), destination === 'C' ? 'C' : 'B');
      assert.equal(workspace.state.page, destination === 'settings' ? 'settings' : 'projects');
      assert.equal(workspace.state.error, '');
    }
  }
});

test('offscreen selection retry preserves confirmed settings and surfaces only its current failure', async () => {
  const { workspace, handlers } = await harness();
  await workspace.selectProject('B');
  const oldRead = deferred();
  let reads = 0;
  handlers['projects.get'] = () => {
    if (++reads === 1) return oldRead.promise;
    throw new Error('current reread unavailable');
  };
  const selecting = workspace.selectProject('A');
  workspace.applyProject({ ...project('A', 10), snapshot_mode: 'compact-v1' });
  const release = workspace.reserveSettingsOperation();
  const confirmed = { provider: { config: { model: 'confirmed' } } };
  workspace.applySettings(confirmed);
  oldRead.reject(new Error('obsolete read failure'));
  await selecting;
  assert.equal(reads, 2);
  assert.equal(workspace.state.project.id, 'B');
  assert.match(workspace.state.error, /current reread unavailable/);
  assert.deepEqual(workspace.state.settings, confirmed);
  assert.equal(workspace.state.busy, true);
  release();
});

test('continuous offscreen activity has bounded reads and an honest retryable error', async () => {
  const { workspace, handlers, env } = await harness();
  await workspace.selectProject('B');
  let reads = 0;
  handlers['projects.get'] = () => {
    reads++;
    assert.ok(reads <= 4, 'selection retries must not become an unbounded read loop');
    workspace.applyProject({ ...project('A', reads + 1), snapshot_mode: 'compact-v1' });
    return { ...project('A', reads), messages: [], events: [] };
  };
  env.calls.length = 0;
  await workspace.selectProject('A');
  assert.equal(reads, 4);
  assert.equal(workspace.state.project.id, 'B');
  assert.match(workspace.state.error, /更新.*重试/);
  assert.ok(env.calls.every(([action]) => action === 'projects.get'));
  handlers['projects.get'] = () => ({ ...project('A', 5), messages: [], events: [] });
  await workspace.selectProject('A');
  assert.equal(workspace.state.project.id, 'A');
  assert.equal(workspace.state.error, '');
});

test('unrelated or malformed offscreen stream events do not cause selection rereads', async () => {
  const { workspace, handlers } = await harness();
  await workspace.selectProject('B');
  const pending = deferred();
  let reads = 0;
  handlers['projects.get'] = () => {
    reads++;
    return pending.promise;
  };
  const selecting = workspace.selectProject('A');
  workspace.applyProject({ ...project('C', 20), snapshot_mode: 'compact-v1' });
  for (const revision of [NaN, undefined, -1, '20'])
    workspace.applyProject({ ...project('A'), revision, snapshot_mode: 'compact-v1' });
  const valid = { id: 'saved', project_id: 'A', role: 'assistant', content: 'exact text' };
  workspace.appendMessage('C', { ...valid, project_id: 'C' });
  workspace.appendMessage('C', valid);
  for (const change of [
    { project_id: 'B' },
    { project_id: undefined },
    { id: '' },
    { id: 4 },
    { content: null },
    { role: 'system' },
  ])
    workspace.appendMessage('A', { ...valid, ...change });
  pending.resolve({ ...project('A', 20), messages: [], events: [] });
  await selecting;
  assert.equal(reads, 1);
  assert.equal(workspace.state.project.id, 'A');
});

test('delete and restore retire the pending retry even when the project ID and timestamp are reused', async () => {
  const { workspace, handlers, records, drafts, saved } = await harness();
  records.set('A', { ...project('A', 10), created_at: 'same', messages: [], events: [] });
  await workspace.selectProject('B');
  const first = deferred(),
    retry = deferred();
  let reads = 0;
  handlers['projects.get'] = ({ project_id }) =>
    project_id === 'A'
      ? (++reads === 1 ? first : retry).promise
      : structuredClone(records.get(project_id));
  const selecting = workspace.selectProject('A');
  workspace.applyProject({ ...project('A', 11), created_at: 'same', snapshot_mode: 'compact-v1' });
  first.resolve({ ...project('A', 10), created_at: 'same', messages: [], events: [] });
  await tick();
  assert.equal(reads, 2);
  await workspace.deleteProject({ id: 'A', name: 'A' });
  handlers['projects.restore_archived'] = () => {
    const restored = { ...project('A', 0), created_at: 'same', messages: [], events: [] };
    records.set('A', restored);
    return { project: restored, restored: true };
  };
  await workspace.restoreProject({ id: 'A', name: 'A' });
  workspace.appendMessage('A', {
    id: 'late-old',
    project_id: 'A',
    role: 'assistant',
    content: 'old incarnation',
  });
  retry.resolve({ ...project('A', 99), created_at: 'same', messages: [{ id: 'old' }], events: [] });
  await selecting;
  assert.equal(reads, 2);
  assert.equal(workspace.state.project.id, 'B');
  assert.equal(saved.get('evograph.project'), 'B');
  delete handlers['projects.get'];
  await workspace.selectProject('A');
  assert.equal(workspace.state.project.revision, 0);
  assert.deepEqual(workspace.state.project.messages, []);
  assert.equal(drafts.bind(() => 'A').content.value, '');
});
