import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { posix } from 'node:path';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';
import { composerEditorStubUrl } from './helpers/composer-fixtures.mjs';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const name of [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'Element',
  'Node',
  'SVGElement',
  'Event',
  'Document',
  'ShadowRoot',
])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.localStorage = dom.window.localStorage;
// DOM behavior only: native focus trap and actual pixels are separate QA.
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
  this.querySelector('[autofocus]')?.focus();
};
const vue = await import('vue');
const { createApp, nextTick } = vue;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const copy = (value) => JSON.parse(JSON.stringify(value));
const tick = async () => {
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const record = (id = 'A', fields = {}) => ({
  id,
  name: id,
  description: '',
  repository: '',
  revision: 0,
  created_at: `created-${id}`,
  archived: false,
  baselines: [],
  milestones: [],
  source_milestones: [],
  architectures: [],
  messages: [],
  events: [],
  attachments: [],
  targets: [],
  behaviors: [],
  plans: [],
  evidence: [],
  is_demo: false,
  ...fields,
});
const rejected = (code, message) => ({ rejected: { code, message } });
let generation = 0;

async function harness(t, { initial = [], editing } = {}) {
  const identity = ++generation;
  const modules = new Map();
  function module(path) {
    if (modules.has(path)) return modules.get(path);
    let content = source(path);
    if (path.endsWith('.vue'))
      content = compileScript(parse(content).descriptor, {
        id: path,
        inlineTemplate: true,
      }).content;
    const compiled = transpile(content).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
      let dependency;
      if (!name.startsWith('.')) dependency = import.meta.resolve(name);
      else {
        const resolved = posix.normalize(posix.join(posix.dirname(path), name));
        dependency = module(resolved.endsWith('.vue') ? resolved : `${resolved}.ts`);
      }
      return `from ${JSON.stringify(dependency)}`;
    });
    const result = url(`${compiled}\n// session ${identity}`);
    modules.set(path, result);
    return result;
  }
  const workspace = (await import(module('composables/useWorkspace.ts'))).useWorkspace();
  const forms = await import(module('composables/useProjectForm.ts'));
  const drafts = (await import(module('composables/useAgentDrafts.ts'))).agentDrafts;
  const calls = [],
    records = new Map(initial.map((item) => [item.id, copy(item)])),
    handlers = {};
  let created = 0;
  const create = (params) => {
    const request = [...records.values()].find((item) => item.creation_key === params.request_id);
    if (request) return { outcome: 'reused_request', project: copy(request) };
    const existing = [...records.values()].find(
      (item) => !item.archived && params.repository && item.repository === params.repository,
    );
    if (existing) return { outcome: 'existing_repository', project: copy(existing) };
    const item = record(`new-${++created}`, { ...params, creation_key: params.request_id });
    delete item.request_id;
    records.set(item.id, item);
    return { outcome: 'created', project: copy(item) };
  };
  window.pywebview = {
    api: {
      command: async (action, params) => {
        calls.push([action, copy(params)]);
        let value;
        if (handlers[action]) value = await handlers[action](params);
        else if (action === 'projects.bootstrap') value = null;
        else if (action === 'projects.list')
          value = [...records.values()]
            .filter((item) => !item.archived)
            .map((item) => ({
              ...item,
              milestone_count: item.milestones.length,
              acceptance: { passed: 0, total: 0, achieved: false },
            }));
        else if (action === 'settings.get') value = { provider: null };
        else if (action === 'projects.get') {
          const item = records.get(params.project_id);
          if (!item)
            return { ok: false, error: { code: 'INVALID_OPERATION', message: 'Missing project' } };
          value = {
            ...copy(item),
            messages: copy(item.messages),
            events: copy(item.events),
            acceptance: { passed: 0, total: 0, achieved: false },
          };
        } else if (action === 'projects.create_with_outcome') value = create(params);
        else if (action === 'projects.update') {
          const item = records.get(params.project_id);
          Object.assign(item, params, { revision: item.revision + 1 });
          value = copy(item);
        } else throw Error(`Unexpected command ${action}`);
        return value?.rejected ? { ok: false, error: value.rejected } : { ok: true, data: value };
      },
    },
  };
  localStorage.clear();
  await workspace.init();
  const ProjectDialog = (await import(module('components/projects/ProjectDialog.vue'))).default;
  let app,
    host,
    closes = 0;
  const opener = document.createElement('button');
  opener.textContent = 'Open form';
  document.body.append(opener);
  const unmount = () => {
    app?.unmount();
    app = undefined;
    host?.remove();
  };
  function mount() {
    opener.focus();
    host = document.createElement('div');
    document.body.append(host);
    app = createApp(ProjectDialog, {
      project: editing,
      onClose() {
        closes++;
        unmount();
      },
    });
    app.mount(host);
    return host;
  }
  mount();
  t.after(() => {
    unmount();
    opener.remove();
    delete window.pywebview;
  });
  const session = forms.useProjectForm(editing);
  const input = (element, text) => {
    element.value = text;
    element.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const fill = (
    name = 'Reading workspace',
    description = 'Read, search and export books',
    repository = '',
  ) => {
    input(host.querySelector('input'), name);
    input(host.querySelector('textarea'), description);
    input(host.querySelectorAll('input')[1], repository);
  };
  const submit = () =>
    host
      .querySelector('form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  const click = (text) =>
    [...host.querySelectorAll('button')]
      .find((button) => button.textContent.trim() === text)
      ?.click();
  return {
    workspace,
    session,
    drafts,
    calls,
    records,
    handlers,
    create,
    fill,
    submit,
    click,
    mount,
    unmount,
    opener,
    module,
    override: (path, moduleUrl) => modules.set(path, moduleUrl),
    get host() {
      return host;
    },
    get closes() {
      return closes;
    },
  };
}

test('real command rejection stays editable and a double click admits one write', async (t) => {
  const h = await harness(t),
    gate = deferred();
  h.handlers['projects.create_with_outcome'] = () => gate.promise;
  h.fill();
  h.submit();
  h.submit();
  await tick();
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  assert.equal(h.host.querySelector('button[aria-label="关闭"]').disabled, true);
  h.host.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true }));
  assert.equal(h.closes, 0);
  gate.resolve(rejected('INVALID_OPERATION', 'Directory does not exist'));
  await tick();
  assert.equal(h.session.form.phase, 'editing');
  assert.match(h.host.textContent, /Directory does not exist/);
  assert.equal(h.host.querySelector('input').disabled, false);
  assert.equal(h.host.querySelector('textarea').value, 'Read, search and export books');
});

for (const failure of ['projects.list', 'settings.get', 'current-project']) {
  test(`confirmed creation survives ${failure} read failure and retry never writes again`, async (t) => {
    const h = await harness(t, { initial: failure === 'current-project' ? [record()] : [] });
    const action = failure === 'current-project' ? 'projects.get' : failure;
    h.handlers[action] = () => {
      throw Error('Read temporarily unavailable');
    };
    h.fill();
    h.submit();
    await tick();
    assert.equal(h.session.form.phase, 'confirmed');
    assert.equal(h.closes, 0);
    assert.match(h.host.textContent, /项目已保存/);
    assert.equal(h.host.querySelector('input').disabled, true);
    assert.equal(h.session.form.saved.project.id, 'new-1');
    delete h.handlers[action];
    h.click('打开已保存项目');
    await tick();
    assert.equal(h.workspace.state.project.id, 'new-1');
    assert.equal(h.closes, 1);
    assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  });
}

test('failed final open preserves confirmed identity and a read-only open recovers', async (t) => {
  const h = await harness(t);
  h.handlers['projects.get'] = () => {
    throw Error('Final read failed');
  };
  h.fill();
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'confirmed');
  assert.equal(h.workspace.state.project, null);
  assert.equal(h.closes, 0);
  assert.match(h.host.textContent, /重试只会读取/);
  delete h.handlers['projects.get'];
  h.click('打开已保存项目');
  await tick();
  assert.equal(h.workspace.state.project.id, 'new-1');
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  assert.ok(Array.isArray(h.workspace.state.project.messages));
});

for (const [label, result] of [
  ['wrong identity', record('another-project')],
  ['archived record', record('new-1', { archived: true })],
]) {
  test(`opening a confirmed result rejects ${label} without false navigation`, async (t) => {
    const h = await harness(t);
    h.handlers['projects.get'] = () => copy(result);
    h.fill();
    h.submit();
    await tick();
    assert.equal(h.session.form.phase, 'confirmed');
    assert.equal(h.workspace.state.project, null);
    assert.equal(h.closes, 0);
    delete h.handlers['projects.get'];
    h.click('打开已保存项目');
    await tick();
    assert.equal(h.workspace.state.project.id, 'new-1');
    assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  });
}

test('a lost response locks exact original payload and key across close and remount', async (t) => {
  const h = await harness(t);
  h.handlers['projects.create_with_outcome'] = (params) => {
    h.create(params);
    throw Error('Lost response');
  };
  h.fill('Original name', 'Original description');
  h.submit();
  await tick();
  const original = h.calls.find(([action]) => action === 'projects.create_with_outcome')[1];
  assert.equal(h.records.size, 1);
  assert.equal(h.session.form.phase, 'uncertain');
  assert.equal(h.host.querySelector('input').disabled, true);
  // Even programmatic later draft mutation cannot change the submitted retry.
  h.session.form.fields.name = 'Unapplied draft';
  h.click('稍后确认');
  assert.equal(h.closes, 1);
  h.mount();
  await tick();
  assert.equal(h.session.form.phase, 'uncertain');
  assert.equal(h.host.querySelector('input').disabled, true);
  delete h.handlers['projects.create_with_outcome'];
  h.click('重试原创建请求');
  await tick();
  const writes = h.calls.filter(([action]) => action === 'projects.create_with_outcome');
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[1][1], original);
  assert.equal(h.records.size, 1);
  assert.equal(h.workspace.state.project.name, 'Original name');
});

test('a rejected recovery attempt cannot clear an earlier ambiguous creation identity', async (t) => {
  const h = await harness(t);
  h.handlers['projects.create_with_outcome'] = (params) => {
    h.create(params);
    throw Error('First response was lost');
  };
  h.fill('Original name', 'Frozen contents');
  h.submit();
  await tick();
  const original = h.calls.find(([action]) => action === 'projects.create_with_outcome')[1];
  h.handlers['projects.create_with_outcome'] = () =>
    rejected('BUSY', 'Another operation is running');
  h.click('重试原创建请求');
  await tick();
  assert.equal(h.session.form.phase, 'uncertain');
  assert.equal(h.host.querySelector('input').disabled, true);
  h.unmount();
  h.mount();
  delete h.handlers['projects.create_with_outcome'];
  h.click('重试原创建请求');
  await tick();
  const writes = h.calls.filter(([action]) => action === 'projects.create_with_outcome');
  assert.equal(writes.length, 3);
  assert.deepEqual(
    writes.map(([, params]) => params),
    [original, original, original],
  );
  assert.equal(h.records.size, 1);
  assert.equal(h.workspace.state.project.name, 'Original name');
});

for (const [label, corrupt] of [
  ['null', () => null],
  ['missing data', () => undefined],
  ['unknown outcome', (result) => ({ ...result, outcome: 'unknown' })],
  ['non-string outcome', (result) => ({ ...result, outcome: ['created'] })],
  ['missing project ID', (result) => ({ ...result, project: { ...result.project, id: '' } })],
  [
    'wrong request identity',
    (result) => ({ ...result, project: { ...result.project, creation_key: 'another-request' } }),
  ],
]) {
  test(`malformed creation confirmation (${label}) retains a retryable frozen request`, async (t) => {
    const h = await harness(t);
    h.handlers['projects.create_with_outcome'] = (params) => corrupt(h.create(params));
    h.fill();
    h.submit();
    await tick();
    assert.equal(h.session.form.phase, 'uncertain');
    assert.equal(h.session.form.saved, null);
    assert.equal(h.workspace.state.busy, false);
    assert.equal(h.host.querySelector('input').disabled, true);
    assert.match(h.host.textContent, /未收到完整的创建确认/);
    h.unmount();
    h.mount();
    delete h.handlers['projects.create_with_outcome'];
    h.click('重试原创建请求');
    await tick();
    assert.equal(h.records.size, 1);
    assert.equal(h.workspace.state.project.id, 'new-1');
    const writes = h.calls.filter(([action]) => action === 'projects.create_with_outcome');
    assert.equal(writes.length, 2);
    assert.deepEqual(writes[0][1], writes[1][1]);
  });
}

test('malformed update confirmation uses read-only recovery and never another update', async (t) => {
  const original = record('A');
  const h = await harness(t, { initial: [original], editing: original });
  h.handlers['projects.update'] = (params) => {
    Object.assign(h.records.get('A'), params);
    return null;
  };
  h.fill('Saved update', 'Updated metadata');
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'uncertain');
  assert.equal(h.session.form.saved, null);
  h.click('读取并核对结果');
  await tick();
  assert.equal(h.session.form.phase, 'confirmed');
  assert.equal(h.calls.filter(([action]) => action === 'projects.update').length, 1);
});

test('INTERNAL envelopes are ambiguous and must not unlock edited resubmission', async (t) => {
  const h = await harness(t);
  h.handlers['projects.create_with_outcome'] = () => rejected('INTERNAL', 'Unknown server result');
  h.fill();
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'uncertain');
  assert.equal(h.host.querySelector('input').disabled, true);
});

test('occupied repository explicitly preserves original metadata and all local draft fields', async (t) => {
  const old = record('old', {
    name: 'Existing identity',
    description: 'Original description',
    repository: '/synthetic/repo',
  });
  const h = await harness(t, { initial: [old] });
  h.fill('Proposed replacement', 'Proposed description', '/synthetic/repo');
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'existing');
  assert.equal(h.closes, 0);
  assert.match(h.host.textContent, /该仓库已有项目/);
  assert.match(h.host.textContent, /Existing identity/);
  assert.match(h.host.textContent, /没有应用/);
  assert.deepEqual(h.records.get('old'), old);
  h.click('继续编辑');
  await tick();
  assert.equal(h.session.form.phase, 'editing');
  assert.equal(h.host.querySelector('input').value, 'Proposed replacement');
  assert.equal(h.host.querySelector('textarea').value, 'Proposed description');
  assert.equal(h.host.querySelectorAll('input')[1].value, '/synthetic/repo');
  h.submit();
  await tick();
  h.click('打开现有项目');
  await tick();
  assert.equal(h.closes, 1);
  assert.equal(h.workspace.state.project.name, 'Existing identity');
  assert.deepEqual(h.records.get('old'), old);
});

test('archived request replay stays confirmed without resurrection or an open action', async (t) => {
  const h = await harness(t);
  h.handlers['projects.create_with_outcome'] = (params) => ({
    outcome: 'reused_request',
    project: record('archived', { archived: true, creation_key: params.request_id }),
  });
  h.fill();
  h.submit();
  await tick();
  assert.match(h.host.textContent, /项目目前已删除/);
  assert.match(h.host.textContent, /不会重新创建或自动恢复/);
  assert.equal(h.calls.filter(([action]) => action === 'projects.get').length, 0);
  assert.equal(
    [...h.host.querySelectorAll('button')].some((button) =>
      button.textContent.includes('打开已保存'),
    ),
    false,
  );
});

for (const navigate of ['project', 'settings']) {
  test(`delayed confirmed write never overrides newer ${navigate} navigation`, async (t) => {
    const h = await harness(t, { initial: [record('A'), record('B')] }),
      gate = deferred();
    h.handlers['projects.create_with_outcome'] = () => gate.promise;
    h.fill();
    h.submit();
    if (navigate === 'project') await h.workspace.selectProject('B');
    else {
      h.workspace.setPage('settings');
      h.workspace.setPage('projects');
      h.workspace.setPage('settings');
    }
    gate.resolve(
      h.create(h.calls.find(([action]) => action === 'projects.create_with_outcome')[1]),
    );
    await tick();
    assert.equal(h.session.form.phase, 'confirmed');
    assert.equal(h.closes, 0);
    assert.equal(h.workspace.state.project.id, navigate === 'project' ? 'B' : 'A');
    if (navigate === 'settings') assert.equal(h.workspace.state.page, 'settings');
    assert.match(h.host.textContent, /未自动跳转/);
  });
}

test('delayed open returns superseded after settings navigation and does not activate stale drafts', async (t) => {
  const h = await harness(t, { initial: [record('A')] }),
    gate = deferred();
  h.handlers['projects.get'] = ({ project_id }) =>
    project_id === 'new-1' ? gate.promise : copy(h.records.get(project_id));
  h.fill();
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'opening');
  h.workspace.setPage('settings');
  gate.resolve(copy(h.records.get('new-1')));
  await tick();
  assert.equal(h.workspace.state.project.id, 'A');
  assert.equal(h.workspace.state.page, 'settings');
  assert.equal(h.closes, 0);
  assert.equal(h.session.form.phase, 'confirmed');
});

test('a confirmed save open cannot overwrite newer stream messages or its crossview tab lease', async (t) => {
  const initial = record('A');
  const h = await harness(t, { initial: [initial], editing: initial });
  const lease = h.workspace.bindWorkspaceTab(h.workspace.state.project);
  lease.tab.value = 'architecture';
  const gate = deferred();
  let reads = 0;
  h.handlers['projects.get'] = () => {
    reads++;
    return reads === 1 ? copy(h.records.get('A')) : gate.promise;
  };
  h.fill('Updated', 'Confirmed save');
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'opening');
  assert.equal(h.workspace.state.busy, true);
  h.workspace.appendMessage('A', {
    id: 'canonical-after-save',
    project_id: 'A',
    role: 'assistant',
    content: 'A newer canonical message',
  });
  h.workspace.setBusy(false);
  assert.equal(h.workspace.reserveSettingsOperation(), null);
  assert.equal(await h.workspace.restoreProject(record('deleted')), false);
  gate.resolve(copy(h.records.get('A')));
  await tick();
  assert.equal(h.session.form.phase, 'confirmed');
  assert.equal(h.session.form.opened, true);
  assert.equal(h.closes, 1);
  assert.equal(h.workspace.state.busy, false);
  assert.equal(h.workspace.state.project.messages.at(-1).id, 'canonical-after-save');
  const current = h.workspace.bindWorkspaceTab(h.workspace.state.project);
  assert.equal(current.key, lease.key);
  assert.equal(current.tab.value, 'architecture');
  assert.equal(h.calls.filter(([action]) => action === 'projects.update').length, 1);
  assert.equal(h.calls.filter(([action]) => action === 'projects.restore_archived').length, 0);
});

test('save lease survives forced remount and unrelated release; settings/save exclusion is bidirectional', async (t) => {
  const h = await harness(t),
    gate = deferred();
  h.handlers['projects.create_with_outcome'] = () => gate.promise;
  h.fill();
  h.submit();
  await tick();
  h.unmount();
  h.workspace.setBusy(false);
  assert.equal(h.workspace.state.busy, true);
  assert.equal(h.workspace.reserveSettingsOperation(), null);
  assert.equal(await h.workspace.perform('unrelated.write'), undefined);
  h.mount();
  await tick();
  assert.equal(h.host.querySelector('button[aria-label="关闭"]').disabled, true);
  h.submit();
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  gate.resolve(rejected('BUSY', 'Try later'));
  await tick();
  assert.equal(h.workspace.state.busy, false);
  const release = h.workspace.reserveSettingsOperation();
  assert.ok(release);
  h.submit();
  await tick();
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  release();
});

test('project save and integrated recovery operations share one exclusion boundary', async (t) => {
  const h = await harness(t),
    gate = deferred();
  assert.equal(typeof h.workspace.restoreProject, 'function');
  h.handlers['projects.create_with_outcome'] = () => gate.promise;
  h.fill();
  h.submit();
  await tick();
  assert.equal(await h.workspace.restoreProject(record('deleted')), false);
  assert.equal(h.calls.filter(([action]) => action === 'projects.restore_archived').length, 0);
  gate.resolve(rejected('BUSY', 'Try later'));
  await tick();
  const restoring = deferred();
  h.handlers['projects.restore_archived'] = () => restoring.promise;
  const restored = h.workspace.restoreProject(record('deleted'));
  await tick();
  h.workspace.setBusy(false);
  assert.equal(h.workspace.state.busy, true);
  assert.equal(h.workspace.reserveSettingsOperation(), null);
  h.submit();
  await tick();
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 1);
  const refreshing = deferred();
  h.handlers['projects.list'] = () => refreshing.promise;
  restoring.resolve({
    project: record('deleted', { acceptance: { passed: 0, total: 0, achieved: false } }),
    restored: true,
  });
  await tick();
  assert.equal(h.workspace.state.recoveryRestored.id, 'deleted');
  assert.equal(h.workspace.state.busy, true);
  h.workspace.setBusy(false);
  assert.equal(h.workspace.reserveProjectOperation(), null);
  assert.equal(h.workspace.reserveSettingsOperation(), null);
  assert.equal(await h.workspace.perform('unrelated.write'), undefined);
  refreshing.resolve([]);
  assert.equal(await restored, true);
  assert.equal(h.workspace.state.busy, false);
  const release = h.workspace.reserveProjectOperation();
  assert.ok(release);
  release();
  const settingsRelease = h.workspace.reserveSettingsOperation();
  assert.ok(settingsRelease);
  release();
  h.workspace.setBusy(false);
  assert.equal(h.workspace.state.busy, true);
  assert.equal(await h.workspace.restoreProject(record('deleted')), false);
  assert.equal(h.workspace.reserveProjectOperation(), null);
  settingsRelease();
  assert.equal(h.workspace.state.busy, false);
});

test('confirmed update survives failed reads and never resends the update', async (t) => {
  const old = record('A', {
    name: 'Original',
    repository: '/synthetic/repo',
    baselines: [{ number: 2 }],
  });
  const h = await harness(t, { initial: [old], editing: old });
  assert.equal(h.host.querySelectorAll('input')[1].readOnly, true);
  assert.match(h.host.textContent, /名称与描述仍可修改/);
  h.handlers['settings.get'] = () => {
    throw Error('Settings read failed');
  };
  h.fill('Updated', 'Updated description', '/unapplied/path');
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'confirmed');
  assert.equal(h.records.get('A').repository, '/synthetic/repo');
  delete h.handlers['settings.get'];
  h.click('打开已保存项目');
  await tick();
  assert.equal(h.workspace.state.project.name, 'Updated');
  assert.equal(h.calls.filter(([action]) => action === 'projects.update').length, 1);
});

test('lost update response resolves through read-only comparison instead of duplicate mutation', async (t) => {
  const old = record('A', { name: 'Original' });
  const h = await harness(t, { initial: [old], editing: old });
  h.handlers['projects.update'] = (params) => {
    Object.assign(h.records.get('A'), params);
    throw Error('Lost update');
  };
  h.fill('Updated', 'Saved description');
  h.submit();
  await tick();
  assert.equal(h.session.form.phase, 'uncertain');
  h.click('读取并核对结果');
  await tick();
  assert.equal(h.session.form.phase, 'confirmed');
  assert.match(h.host.textContent, /当前内容已核对/);
  assert.equal(h.calls.filter(([action]) => action === 'projects.update').length, 1);
});

test('dialog has a stable accessible name, validates name, preserves optional repo, and returns focus', async (t) => {
  const h = await harness(t);
  const dialog = h.host.querySelector('dialog'),
    heading = h.host.querySelector('h2');
  assert.equal(dialog.getAttribute('aria-labelledby'), heading.id);
  assert.equal(heading.textContent, '创建新项目');
  assert.equal(document.activeElement, h.host.querySelector('input'));
  h.fill('   ');
  h.submit();
  await tick();
  assert.equal(h.calls.filter(([action]) => action === 'projects.create_with_outcome').length, 0);
  assert.match(h.host.textContent, /没有仓库也可以先规划/);
  assert.equal(h.host.querySelectorAll('input')[1].required, false);
  h.click('取消');
  assert.equal(document.activeElement, h.opener);
});

test('empty project handoff targets the one existing composer, without sending or requiring a provider', async (t) => {
  const h = await harness(t);
  const Handoff = (await import(h.module('components/projects/EmptyPlanningHandoff.vue'))).default;
  h.fill('New', 'Stored project context');
  h.submit();
  await tick();
  assert.equal(h.workspace.state.project.description, 'Stored project context');
  assert.equal(h.workspace.state.project.repository, '');
  const draft = h.drafts.bind(() => h.workspace.state.project.id);
  assert.equal(draft.content.value, '');
  for (const providerAvailable of [false, true]) {
    let focus = 0;
    const element = document.createElement('div');
    document.body.append(element);
    const app = createApp(Handoff, {
      description: 'Stored project context',
      providerAvailable,
      onCompose() {
        focus++;
      },
    });
    app.mount(element);
    assert.match(element.textContent, /项目描述已保存/);
    assert.match(element.textContent, /同一个输入框/);
    if (!providerAvailable) assert.match(element.textContent, /发送前在设置中配置 Provider/);
    element.querySelector('button').click();
    assert.equal(focus, 1);
    assert.equal(draft.content.value, '');
    draft.content.value = 'First meaningful idea';
    assert.equal(draft.content.value, 'First meaningful idea');
    draft.content.value = '';
    app.unmount();
    element.remove();
  }
  assert.equal(
    h.calls.some(([action]) => action.startsWith('agent.')),
    false,
  );
  const workspaceSource = source('components/workspace/ProjectWorkspace.vue');
  assert.equal((workspaceSource.match(/<AgentDock/g) ?? []).length, 1);
  assert.match(workspaceSource, /@compose="composer\?\.focus\(\)"/);
  const dock = source('components/agent/AgentDock.vue');
  assert.match(dock, /defineExpose\(\{/);
  assert.match(dock, /message\.value\?\.focus\(\)/);
});

test('entry focus reaches the real AgentDock editor; the first idea sends only after an explicit submit', async (t) => {
  const h = await harness(t);
  h.fill('New workspace', 'Saved background');
  h.submit();
  await tick();
  const Handoff = (await import(h.module('components/projects/EmptyPlanningHandoff.vue'))).default;
  const vueUrl = import.meta.resolve('vue');
  const agentUrl = url(`import { reactive } from ${JSON.stringify(vueUrl)};
    export const calls = [];
    export const agent = { state: reactive({ running: false, projectId: '', label: '', follow: {} }),
      send: async (...args) => { calls.push(args); return true; }, stop() {} };
    export const useAgent = () => agent; // mounted-handoff-${generation}`);
  h.override('composables/useAgent.ts', agentUrl);
  h.override('components/agent/ComposerEditor.vue', composerEditorStubUrl);
  const stub = url('export default { inheritAttrs: false, render() { return null; } };');
  for (const name of [
    'agent/AgentQuestion',
    'agent/AgentTurnSummary',
    'agent/AgentReviewTray',
    'graph/FollowAgentButton',
    'attachments/AttachmentPicker',
    'attachments/AttachmentReceipt',
    'agent/MessageContent',
  ])
    h.override(`components/${name}.vue`, stub);
  const AgentDock = (await import(h.module('components/agent/AgentDock.vue'))).default;
  const { calls } = await import(agentUrl);
  const dock = vue.ref();
  const element = document.createElement('div');
  document.body.append(element);
  const app = createApp({
    setup() {
      return () =>
        vue.h('div', [
          vue.h(Handoff, {
            description: 'Saved background',
            providerAvailable: Boolean(h.workspace.state.settings?.provider),
            onCompose: () => dock.value?.focus(),
          }),
          vue.h(AgentDock, { ref: dock, project: h.workspace.state.project }),
        ]);
    },
  });
  app.mount(element);
  t.after(() => {
    app.unmount();
    element.remove();
  });
  const editor = element.querySelector('textarea');
  assert.ok(editor);
  assert.equal(editor.value, '');
  element.querySelector('.empty-planning-handoff button').click();
  await tick();
  assert.equal(document.activeElement, editor);
  assert.equal(calls.length, 0);
  editor.value = 'Make a searchable reading catalog first';
  editor.dispatchEvent(new Event('input', { bubbles: true }));
  await tick();
  assert.equal(h.drafts.bind(() => h.workspace.state.project.id).content.value, editor.value);
  assert.equal(calls.length, 0);
  assert.equal(element.querySelector('.send-button').disabled, true);
  h.workspace.applySettings({
    provider: { config: { model: 'deterministic test provider' } },
    adapters: [],
  });
  await tick();
  assert.equal(calls.length, 0);
  element.querySelector('.send-button').click();
  await tick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'new-1');
  assert.equal(calls[0][1], 'Make a searchable reading catalog first');
});

for (const stale of ['success', 'failure']) {
  test(`a still-owned open accepts newer live same-project state after a stale read ${stale}`, async (t) => {
    const h = await harness(t, { initial: [record('A')] });
    const lease = h.workspace.bindWorkspaceTab(h.workspace.state.project);
    lease.tab.value = 'activity';
    const gate = deferred();
    h.handlers['projects.get'] = () => gate.promise;
    const opening = h.workspace.selectProject('A');
    h.workspace.appendMessage('A', {
      id: 'live-during-open',
      project_id: 'A',
      role: 'assistant',
      content: 'Canonical live reply',
    });
    const current = h.workspace.state.project;
    if (stale === 'failure') gate.reject(new Error('obsolete read failed'));
    else gate.resolve(copy(h.records.get('A')));
    assert.equal(await opening, 'accepted');
    assert.equal(h.workspace.state.project, current);
    assert.equal(h.workspace.state.error, '');
    assert.equal(h.workspace.bindWorkspaceTab(current).key, lease.key);
    assert.equal(lease.tab.value, 'activity');
  });
}

test('offscreen canonical activity resolves a pending open only after its owned reread', async (t) => {
  const h = await harness(t, { initial: [record('A'), record('B')] });
  await h.workspace.selectProject('B');
  const gate = deferred();
  const message = {
    id: 'offscreen-final',
    project_id: 'A',
    role: 'assistant',
    content: 'Saved final reply',
  };
  let reads = 0;
  h.handlers['projects.get'] = () =>
    ++reads === 1 ? gate.promise : { ...copy(h.records.get('A')), messages: [message] };
  const opening = h.workspace.selectProject('A');
  h.workspace.appendMessage('A', message);
  gate.resolve(copy(h.records.get('A')));
  assert.equal(await opening, 'accepted');
  assert.equal(reads, 2);
  assert.deepEqual(h.workspace.state.project.messages, [message]);
});

test('an open result distinguishes newer navigation from an unavailable or continuously changing read', async (t) => {
  const h = await harness(t, { initial: [record('A'), record('B')] });
  const gate = deferred();
  h.handlers['projects.get'] = () => gate.promise;
  const opening = h.workspace.selectProject('B');
  h.workspace.setPage('settings');
  gate.reject(new Error('obsolete read'));
  assert.equal(await opening, 'superseded');
  assert.equal(h.workspace.state.page, 'settings');
  assert.equal(h.workspace.state.error, '');
  h.handlers['projects.get'] = () => {
    throw new Error('current read unavailable');
  };
  assert.equal(await h.workspace.selectProject('B'), 'failed');
  assert.match(h.workspace.state.error, /current read unavailable/);
  let reads = 0;
  h.handlers['projects.get'] = () => {
    reads++;
    h.workspace.invalidateProjectRead('B');
    return copy(h.records.get('B'));
  };
  assert.equal(await h.workspace.selectProject('B'), 'failed');
  assert.equal(reads, 4);
  assert.equal(h.workspace.state.project.id, 'A');
  assert.match(h.workspace.state.error, /更新.*重试/);
});

test('an older same-incarnation read accepts the intended already-open snapshot without replacing its owners', async (t) => {
  const h = await harness(t, { initial: [record('A', { revision: 10 })] });
  const current = h.workspace.state.project;
  const lease = h.workspace.bindWorkspaceTab(current);
  lease.tab.value = 'architecture';
  const draft = h.drafts.bind(() => 'A');
  draft.content.value = 'Keep this unsent draft';
  h.handlers['projects.get'] = () => record('A', { revision: 9 });
  assert.equal(await h.workspace.selectProject('A'), 'accepted');
  assert.equal(h.workspace.state.project, current);
  assert.equal(h.workspace.state.project.revision, 10);
  assert.equal(h.workspace.bindWorkspaceTab(current).key, lease.key);
  assert.equal(lease.tab.value, 'architecture');
  assert.equal(h.workspace.state.error, '');
  assert.equal(draft.content.value, 'Keep this unsent draft');
});
