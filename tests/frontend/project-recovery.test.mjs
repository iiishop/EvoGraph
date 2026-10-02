import { markdownContentUrl } from './helpers/markdown-fixtures.mjs';
import {
  composerDocumentUrl,
  composerEditorStubUrl,
  messageContentStubUrl,
} from './helpers/composer-fixtures.mjs';
import { browseModelUrl } from './helpers/architecture-fixtures.mjs';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
for (const key of [
  'window',
  'document',
  'Document',
  'ShadowRoot',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'Event',
])
  globalThis[key] = dom.window[key];
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute('open', '');
};

const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const vueUrl = pathToFileURL(require.resolve('vue')).href;
const { createApp, h, ref, nextTick, effectScope } = await import(vueUrl);
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
  description: `Description ${id}`,
  repository: `/repositories/${id}`,
  is_demo: false,
  milestone_count: 0,
  acceptance: { passed: 0, total: 0, achieved: false },
  updated_at: '2026-10-01T12:00:00Z',
  created_at: '2026-09-01T12:00:00Z',
  source_diagram: {
    nodes: [{ id: 'service', label: 'Service', role: 'backend' }],
    edges: [],
  },
  architectures: [],
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
  const browseUrl = moduleUrl(
    compile('composables/useArchitectureBrowse.ts')
      .replace("from 'vue'", `from ${JSON.stringify(vueUrl)}`)
      .replace("from '../lib/architectureBrowse'", `from ${JSON.stringify(browseModelUrl)}`) +
      `\n// ${id}`,
  );
  const imports = {
    [composerDocumentUrl]: composerDocumentUrl,
    '../../lib/composerDocument': composerDocumentUrl,
    './ComposerEditor.vue': composerEditorStubUrl,
    './MessageContent.vue': messageContentStubUrl,
    './MarkdownContent': markdownContentUrl,
    vue: vueUrl,
    '../api/client': apiUrl,
    './useAgentDrafts': draftsUrl,
    './useWorkflowDrafts': workflowUrl,
    './useArchitectureBrowse': browseUrl,
    './useNotifications': moduleUrl('export const useNotifications = () => ({ push() {} });'),
  };
  const code = compile('composables/useWorkspace.ts').replace(
    /from (['"])([^'"]+)\1/g,
    (_, quote, name) => {
      assert.ok(imports[name], `Unexpected import ${name}`);
      return `from ${JSON.stringify(imports[name])}`;
    },
  );
  const workspaceUrl = moduleUrl(code);
  const { useWorkspace } = await import(workspaceUrl);
  const { agentDrafts } = await import(draftsUrl);
  const { workflowDrafts } = await import(workflowUrl);
  const { architectureBrowse } = await import(browseUrl);
  const { env } = await import(apiUrl);
  const records = new Map(['A', 'B', 'C'].map((id) => [id, project(id)]));
  const archived = new Map(['D', 'E'].map((id) => [id, project(id)]));
  const handlers = {};
  env.handler = async (action, params) => {
    if (handlers[action]) return handlers[action](params);
    if (action === 'projects.get') {
      if (!records.has(params.project_id)) throw new Error('Project missing');
      return structuredClone(records.get(params.project_id));
    }
    if (action === 'projects.list') return [...records.values()];
    if (action === 'projects.list_archived') return [...archived.values()];
    if (action === 'settings.get') return { provider: { config: { model: 'test' } } };
    if (action === 'projects.delete') {
      archived.set(params.project_id, records.get(params.project_id));
      records.delete(params.project_id);
      return { deleted: true };
    }
    if (action === 'projects.restore_archived') {
      if (records.has(params.project_id))
        return { project: structuredClone(records.get(params.project_id)), restored: false };
      const restored = project(params.project_id);
      archived.delete(params.project_id);
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
    workspaceUrl,
    archived,
    drafts: agentDrafts,
    workflows: workflowDrafts,
    browse: architectureBrowse,
    records,
    handlers,
    env,
    saved,
  };
}

test('multiple deleted projects remain discoverable after undo dismissal and workspace reopen', async () => {
  const { workspace, archived } = await harness();
  await workspace.deleteProject(project('A'));
  workspace.dismiss();
  await workspace.deleteProject(project('B'));
  workspace.dismiss();
  await workspace.loadArchivedProjects();
  assert.deepEqual(
    workspace.state.archivedProjects.map((p) => p.id),
    ['D', 'E', 'A', 'B'],
  );
  assert.equal(workspace.state.recoveryLoaded, true);
  await workspace.restoreProject(archived.get('A'));
  assert.equal(workspace.state.deletedProject.id, 'B');
  assert.ok(workspace.state.projects.some((p) => p.id === 'A'));
  assert.ok(!workspace.state.archivedProjects.some((p) => p.id === 'A'));
  assert.equal(workspace.state.project, null, 'restoring through recovery never navigates');
  await workspace.loadArchivedProjects();
  assert.deepEqual(
    workspace.state.archivedProjects.map((p) => p.id),
    ['D', 'E', 'B'],
  );
});

test('archive reads expose loading/error/retry and reject superseded successes and errors', async () => {
  for (const oldFails of [false, true]) {
    const { workspace, handlers } = await harness();
    const old = deferred();
    handlers['projects.list_archived'] = () => old.promise;
    const pending = workspace.loadArchivedProjects();
    assert.equal(workspace.state.recoveryLoading, true);
    handlers['projects.list_archived'] = () => {
      throw new Error('offline');
    };
    await workspace.loadArchivedProjects();
    assert.equal(workspace.state.recoveryLoading, false);
    assert.equal(workspace.state.recoveryListError, 'offline');
    handlers['projects.list_archived'] = () => [project('E')];
    await workspace.loadArchivedProjects();
    if (oldFails) old.reject(new Error('older error'));
    else old.resolve([project('D')]);
    await pending;
    assert.deepEqual(
      workspace.state.archivedProjects.map((p) => p.id),
      ['E'],
    );
    assert.equal(workspace.state.recoveryListError, '');
  }
});

test('confirmed restore survives refresh failure and retry never repeats the mutation', async () => {
  const { workspace, handlers, env } = await harness();
  await workspace.loadArchivedProjects();
  handlers['projects.list'] = () => {
    throw new Error('refresh offline');
  };
  assert.equal(await workspace.restoreProject(project('D')), true);
  assert.equal(workspace.state.recoveryRestored.id, 'D');
  assert.match(workspace.state.recoveryRestored.refreshError, /已恢复.*无需再次恢复/);
  assert.equal(workspace.state.recoveryError, null);
  assert.ok(workspace.state.projects.some((p) => p.id === 'D'));
  assert.ok(!workspace.state.archivedProjects.some((p) => p.id === 'D'));
  await workspace.refreshAfterRestore();
  delete handlers['projects.list'];
  await workspace.refreshAfterRestore();
  assert.equal(workspace.state.recoveryRestored.refreshError, '');
  assert.equal(env.calls.filter(([action]) => action === 'projects.restore_archived').length, 1);
});

test('undo also clears the committed deletion and explains a failed follow-up refresh', async () => {
  const { workspace, handlers, env } = await harness();
  await workspace.deleteProject(project('A'));
  handlers['projects.list'] = () => {
    throw new Error('offline');
  };
  await workspace.undoDelete();
  assert.equal(workspace.state.deletedProject, null);
  assert.equal(workspace.state.project.id, 'A');
  assert.match(workspace.state.notice, /已恢复.*刷新未完成/);
  await workspace.undoDelete();
  assert.equal(env.calls.filter(([action]) => action === 'projects.restore_archived').length, 1);
});

test('restore owns busy through follow-up reads and ignores repeated clicks or unrelated releases', async () => {
  const { workspace, handlers, env, records } = await harness();
  const restore = deferred(),
    refresh = deferred();
  handlers['projects.restore_archived'] = () => restore.promise;
  handlers['projects.list'] = () => refresh.promise;
  const pending = workspace.restoreProject(project('D'));
  assert.equal(workspace.state.restoringId, 'D');
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  assert.equal(await workspace.restoreProject(project('D')), false);
  assert.equal(await workspace.restoreProject(project('E')), false);
  await workspace.perform('unexpected.action');
  restore.resolve({ project: project('D'), restored: true });
  await tick();
  assert.equal(workspace.state.busy, true);
  assert.equal(workspace.state.recoveryRestored.id, 'D');
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  refresh.resolve([...records.values(), project('D')]);
  await pending;
  assert.equal(workspace.state.busy, false);
  assert.equal(workspace.state.restoringId, null);
  assert.equal(env.calls.filter(([a]) => a === 'projects.restore_archived').length, 1);
  assert.ok(!env.calls.some(([a]) => a === 'unexpected.action'));
});

test('restoring an archived project resets old agent, editor and workflow incarnations', async () => {
  const { workspace, drafts, workflows, browse } = await harness();
  const oldProject = project('D');
  browse.activate(oldProject);
  const oldBrowse = browse.viewState(oldProject);
  Object.assign(oldBrowse, {
    query: 'deleted query',
    focusedId: 'service',
    role: 'backend',
    relation: 'downstream',
    viewport: { x: 31, y: 47, zoom: 0.8 },
  });
  const otherBrowse = browse.viewState(project('B'));
  otherBrowse.query = 'keep B';
  const draft = drafts.bind(() => 'D');
  draft.content.value = 'old deleted draft';
  draft.attachmentIds.value = ['old-attachment'];
  const oldAgent = drafts.start('D', { text: 'old request', ids: ['old-attachment'] });
  workflows.entry('D', 'M1').report = 'old report';
  const oldWorkflow = workflows.start('D', 'M1');
  await workspace.restoreProject(project('D'));
  assert.equal(browse.entry(oldProject), undefined, 'confirmed restore discards old browser owner');
  assert.equal(browse.viewState(project('B')), otherBrowse);
  assert.equal(otherBrowse.query, 'keep B');
  await workspace.selectProject('D');
  const restoredBrowse = browse.viewState(workspace.state.project);
  assert.notEqual(restoredBrowse.key, oldBrowse.key);
  assert.equal(restoredBrowse.query, '');
  assert.equal(restoredBrowse.focusedId, '');
  assert.equal(restoredBrowse.role, 'all');
  assert.equal(restoredBrowse.relation, 'all');
  assert.equal(restoredBrowse.viewport, null);
  browse.rememberViewport(oldProject, oldBrowse.key, { x: 999, y: 999, zoom: 0.5 });
  assert.equal(restoredBrowse.viewport, null, 'late old camera cannot write to new owner');
  drafts.settle(oldAgent, false);
  assert.equal(workflows.current(oldWorkflow), false);
  assert.equal(draft.content.value, '');
  assert.deepEqual(draft.attachmentIds.value, []);
  assert.deepEqual(draft.failures.value, []);
  assert.equal(workflows.entry('D', 'M1'), undefined);
});

test('navigation during restore wins, including a settings round trip with no project selection', async () => {
  for (const navigate of ['project', 'settings', 'roundtrip']) {
    const { workspace, handlers, records } = await harness();
    await workspace.deleteProject(project('A'));
    const response = deferred();
    handlers['projects.restore_archived'] = () => response.promise;
    const pending = workspace.undoDelete();
    if (navigate === 'project') await workspace.selectProject('B');
    else {
      workspace.setPage('settings');
      if (navigate === 'roundtrip') workspace.setPage('projects');
    }
    records.set('A', project('A'));
    response.resolve({ project: project('A'), restored: true });
    await pending;
    assert.equal(workspace.state.project?.id ?? null, navigate === 'project' ? 'B' : null);
    assert.equal(workspace.state.page, navigate === 'settings' ? 'settings' : 'projects');
  }
});

test('an archive read started before confirmation cannot reinsert the restored row', async () => {
  const { workspace, handlers } = await harness();
  await workspace.loadArchivedProjects();
  const old = deferred();
  handlers['projects.list_archived'] = () => old.promise;
  const loading = workspace.loadArchivedProjects();
  await workspace.restoreProject(project('D'));
  old.resolve([project('D'), project('E')]);
  await loading;
  assert.deepEqual(
    workspace.state.archivedProjects.map((p) => p.id),
    ['E'],
  );
  assert.equal(workspace.state.recoveryLoading, false);
});

test('a normalized repository conflict keeps the archive row and supports an explicit retry', async () => {
  const { workspace, handlers } = await harness();
  await workspace.loadArchivedProjects();
  handlers['projects.restore_archived'] = () => {
    throw new Error('此仓库已有活跃项目');
  };
  assert.equal(await workspace.restoreProject(project('D')), false);
  assert.equal(workspace.state.recoveryError.id, 'D');
  assert.match(workspace.state.recoveryError.message, /已有活跃项目/);
  assert.equal(workspace.state.restoringId, null);
  assert.equal(workspace.state.busy, false);
  assert.ok(workspace.state.archivedProjects.some((p) => p.id === 'D'));
  assert.ok(!workspace.state.projects.some((p) => p.id === 'D'));
  delete handlers['projects.restore_archived'];
  assert.equal(await workspace.restoreProject(project('D')), true);
  assert.equal(workspace.state.recoveryError, null);
});

function componentUrl(path, imports) {
  const source = readFileSync(
    new URL(`../../frontend/src/components/${path}.vue`, import.meta.url),
    'utf8',
  );
  const { descriptor } = parse(source);
  const js = ts.transpileModule(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
    {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    },
  ).outputText;
  return moduleUrl(
    js.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
      assert.ok(imports[name], `Unexpected component import ${name}`);
      return `from ${JSON.stringify(imports[name])}`;
    }),
  );
}
async function mountRecovery(context) {
  const imports = {
    vue: vueUrl,
    'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
    '../../composables/useWorkspace': context.workspaceUrl,
  };
  imports['../ui/AppModal.vue'] = componentUrl('ui/AppModal', imports);
  const Recovery = (await import(componentUrl('projects/ProjectRecoveryDialog', imports))).default;
  const host = document.createElement('div'),
    trigger = document.createElement('button');
  document.body.append(trigger, host);
  trigger.focus();
  const open = ref(true);
  const app = createApp({
    setup: () => () =>
      open.value
        ? h(Recovery, {
            onClose: () => {
              open.value = false;
            },
          })
        : null,
  });
  app.mount(host);
  await tick();
  await nextTick();
  return {
    host,
    trigger,
    open,
    input: () => host.querySelector('input'),
    button: (text) =>
      [...host.querySelectorAll('button')].find((button) => button.textContent.trim() === text),
    rows: () => [...host.querySelectorAll('.recovery-project')],
    close: async () => {
      host.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true }));
      await nextTick();
    },
    reopen: async () => {
      trigger.focus();
      open.value = true;
      await nextTick();
      await tick();
    },
    dispose() {
      app.unmount();
      host.remove();
      trigger.remove();
    },
  };
}

test('real recovery dialog exposes truthful metadata, instant search and an accessible empty state', async () => {
  const context = await harness();
  context.archived.set('D', {
    ...project('D'),
    name: 'Account service',
    description: 'Readable description',
  });
  const ui = await mountRecovery(context);
  try {
    assert.equal(ui.host.querySelector('dialog').getAttribute('aria-label'), '已删除项目');
    assert.equal(document.activeElement, ui.input());
    assert.equal(ui.rows().length, 2);
    assert.match(ui.host.textContent, /仓库文件始终保留/);
    assert.match(ui.host.textContent, /记录更新于/);
    assert.doesNotMatch(ui.host.textContent, /删除时间|永久删除|清空/);
    for (const query of ['account', 'READABLE', '/repositories/d']) {
      ui.input().value = query;
      ui.input().dispatchEvent(new Event('input', { bubbles: true }));
      await nextTick();
      assert.equal(ui.rows().length, 1);
      assert.match(ui.rows()[0].textContent, /Account service/);
    }
    ui.input().value = 'missing';
    ui.input().dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
    assert.equal(ui.rows().length, 0);
    assert.match(ui.host.textContent, /没有匹配的已删除项目/);
    ui.button('清除搜索').click();
    await nextTick();
    assert.equal(ui.rows().length, 2);
    assert.equal(document.activeElement, ui.input());
    await ui.close();
    assert.equal(ui.host.querySelector('dialog'), null);
    assert.equal(document.activeElement, ui.trigger);
  } finally {
    ui.dispose();
  }
});

test('real dialog survives dismissal/reopen while restore owns busy and focuses after a row disappears', async () => {
  const context = await harness();
  const response = deferred();
  context.handlers['projects.restore_archived'] = () => response.promise;
  const ui = await mountRecovery(context);
  try {
    const restoreButton = ui.rows()[0].querySelector('button');
    restoreButton.focus();
    restoreButton.click();
    await nextTick();
    assert.match(ui.host.textContent, /恢复中/);
    assert.ok(ui.rows().every((row) => row.querySelector('button').disabled));
    await ui.close();
    assert.equal(context.workspace.state.busy, true);
    await ui.reopen();
    assert.ok(ui.rows().every((row) => row.querySelector('button').disabled));
    context.records.set('D', project('D'));
    context.archived.delete('D');
    response.resolve({ project: project('D'), restored: true });
    await tick();
    await nextTick();
    assert.match(ui.host.textContent, /已恢复「D」/);
    assert.equal(ui.rows().length, 1);
    assert.equal(context.workspace.state.busy, false);
    assert.equal(context.env.calls.filter(([a]) => a === 'projects.restore_archived').length, 1);
    ui.button('打开项目').click();
    await tick();
    await nextTick();
    assert.equal(ui.host.querySelector('dialog'), null);
    assert.equal(context.workspace.state.project.id, 'D');
  } finally {
    ui.dispose();
  }
});

test('real dialog keeps errors inline and retries refresh rather than restoring a committed record', async () => {
  const context = await harness();
  context.handlers['projects.list_archived'] = () => {
    throw new Error('offline archive');
  };
  const ui = await mountRecovery(context);
  try {
    assert.match(ui.host.querySelector('[role="alert"]').textContent, /offline archive/);
    assert.doesNotMatch(ui.host.textContent, /没有待恢复的项目/);
    delete context.handlers['projects.list_archived'];
    ui.button('重试读取').click();
    await tick();
    await nextTick();
    context.handlers['projects.restore_archived'] = () => {
      throw new Error('此仓库已有活跃项目');
    };
    ui.rows()[0].querySelector('button').click();
    await tick();
    await nextTick();
    assert.match(ui.rows()[0].querySelector('[role="alert"]').textContent, /已有活跃项目/);
    assert.equal(ui.rows().length, 2);
    delete context.handlers['projects.restore_archived'];
    context.handlers['projects.list'] = () => {
      throw new Error('workspace offline');
    };
    ui.rows()[0].querySelector('button').focus();
    ui.rows()[0].querySelector('button').click();
    await tick();
    await nextTick();
    assert.match(ui.host.textContent, /已恢复「D」/);
    assert.match(ui.host.textContent, /无需再次恢复/);
    assert.equal(document.activeElement, ui.input());
    const calls = context.env.calls.filter(([a]) => a === 'projects.restore_archived').length;
    delete context.handlers['projects.list'];
    ui.button('重试刷新工作空间').click();
    await tick();
    await nextTick();
    assert.equal(
      context.env.calls.filter(([a]) => a === 'projects.restore_archived').length,
      calls,
    );
    assert.doesNotMatch(ui.host.textContent, /无需再次恢复/);
    ui.rows()[0].querySelector('button').click();
    await tick();
    await nextTick();
    assert.match(ui.host.textContent, /没有待恢复的项目/);
  } finally {
    ui.dispose();
  }
});

test('stale archive no-op preserves the open project, typed editor/workflow drafts and authoritative metadata', async () => {
  const context = await harness();
  const { workspace, records, handlers, drafts, workflows, browse } = context;
  await workspace.loadArchivedProjects();
  const current = {
    ...project('D', 7),
    name: 'Already restored and renamed',
    repository: '/repositories/new-active-path',
    is_demo: true,
    milestones: [{ id: 'M1' }],
    acceptance: { passed: 1, total: 2, achieved: false },
  };
  records.set('D', current);
  await workspace.selectProject('D');
  workspace.selectNode('M1');
  const live = workspace.state.project;
  const liveBrowse = browse.viewState(live);
  Object.assign(liveBrowse, {
    query: 'live browser query',
    focusedId: 'service',
    role: 'backend',
    relation: 'downstream',
    viewport: { x: 37, y: 53, zoom: 0.7 },
  });
  const liveBrowserFields = JSON.parse(JSON.stringify(liveBrowse));
  const draft = drafts.bind(() => 'D');
  draft.content.value = 'new active unsent draft';
  draft.attachmentIds.value = ['new-active-attachment'];
  const workflow = workflows.entry('D', 'M1');
  workflow.report = 'new active workflow report';
  const oldArchiveRead = deferred();
  handlers['projects.list_archived'] = () => oldArchiveRead.promise;
  handlers['projects.list'] = () => {
    throw new Error('follow-up refresh unavailable');
  };
  const ui = await mountRecovery(context);
  try {
    assert.equal(workspace.state.recoveryLoading, true);
    ui.rows()
      .find((row) => row.textContent.includes('Description D'))
      .querySelector('button')
      .click();
    await tick();
    await nextTick();
    assert.equal(workspace.state.project, live);
    assert.equal(workspace.state.selectedId, 'M1');
    assert.equal(draft.content.value, 'new active unsent draft');
    assert.deepEqual(draft.attachmentIds.value, ['new-active-attachment']);
    assert.equal(workflows.entry('D', 'M1'), workflow);
    assert.equal(workflow.report, 'new active workflow report');
    assert.equal(workspace.state.recoveryRestored.restored, false);
    assert.equal(browse.viewState(live), liveBrowse);
    assert.deepEqual(JSON.parse(JSON.stringify(liveBrowse)), liveBrowserFields);
    assert.match(ui.host.textContent, /Already restored and renamed.*已在工作空间中/);
    assert.match(ui.host.textContent, /未发送的草稿已保留/);
    assert.doesNotMatch(ui.host.textContent, /已恢复「/);
    const summary = workspace.state.projects.find((item) => item.id === 'D');
    assert.equal(summary.name, current.name);
    assert.equal(summary.repository, current.repository);
    assert.equal(summary.is_demo, true);
    assert.equal(summary.milestone_count, 1);
    assert.deepEqual(summary.acceptance, current.acceptance);
    oldArchiveRead.resolve([project('D'), project('E')]);
    await tick();
    await nextTick();
    assert.ok(!workspace.state.archivedProjects.some((item) => item.id === 'D'));
    assert.equal(workspace.state.project, live);
    assert.equal(draft.content.value, 'new active unsent draft');
    const mutations = context.env.calls.filter(
      ([action]) => action === 'projects.restore_archived',
    ).length;
    ui.button('重试刷新工作空间').click();
    await tick();
    await nextTick();
    assert.equal(
      context.env.calls.filter(([action]) => action === 'projects.restore_archived').length,
      mutations,
    );
    assert.equal(draft.content.value, 'new active unsent draft');
    assert.equal(browse.viewState(live), liveBrowse);
    assert.deepEqual(JSON.parse(JSON.stringify(liveBrowse)), liveBrowserFields);
  } finally {
    ui.dispose();
  }
});

test('malformed recovery success cannot clear a current project or any draft incarnation', async () => {
  for (const response of [
    undefined,
    { project: project('D') },
    { project: project('D'), restored: 'false' },
    { project: project('E'), restored: true },
    { project: { ...project('D'), milestones: null }, restored: true },
    { project: { ...project('D'), archived: true }, restored: true },
    { project: { ...project('D'), archived: undefined }, restored: false },
    { project: { ...project('D'), acceptance: 'invalid' }, restored: true },
    {
      project: { ...project('D'), acceptance: { passed: -1, total: 2, achieved: false } },
      restored: true,
    },
    {
      project: { ...project('D'), acceptance: { passed: 1, total: Number.NaN, achieved: false } },
      restored: true,
    },
    {
      project: { ...project('D'), acceptance: { passed: 1, total: 2, achieved: 'false' } },
      restored: false,
    },
  ]) {
    const { workspace, records, handlers, drafts } = await harness();
    await workspace.loadArchivedProjects();
    records.set('D', project('D'));
    await workspace.selectProject('D');
    const live = workspace.state.project;
    drafts.bind(() => 'D').content.value = 'preserve D';
    drafts.bind(() => 'E').content.value = 'preserve E';
    handlers['projects.restore_archived'] = () => response;
    assert.equal(await workspace.restoreProject(project('D')), false);
    assert.equal(workspace.state.project, live);
    assert.equal(drafts.bind(() => 'D').content.value, 'preserve D');
    assert.equal(drafts.bind(() => 'E').content.value, 'preserve E');
    assert.match(workspace.state.recoveryError.message, /结果无法确认/);
    assert.equal(workspace.state.recoveryRestored, null);
    assert.equal(workspace.state.busy, false);
    assert.ok(workspace.state.archivedProjects.some((item) => item.id === 'D'));
  }
});

test('Undo final project read cannot activate after later Settings navigation or a round trip', async () => {
  for (const roundtrip of [false, true]) {
    for (const rejects of [false, true]) {
      const { workspace, handlers, drafts, saved, env } = await harness();
      await workspace.deleteProject(project('A'));
      saved.set('evograph.project', 'B');
      const read = deferred();
      handlers['projects.get'] = () => read.promise;
      const before = env.calls.filter(([action]) => action === 'projects.get').length;
      const undo = workspace.undoDelete();
      await tick();
      assert.equal(env.calls.filter(([action]) => action === 'projects.get').length, before + 1);
      workspace.setPage('settings');
      if (roundtrip) workspace.setPage('projects');
      if (rejects) read.reject(new Error('obsolete final read'));
      else read.resolve(project('A'));
      await undo;
      assert.equal(workspace.state.project, null);
      assert.equal(workspace.state.page, roundtrip ? 'projects' : 'settings');
      assert.equal(workspace.state.error, '');
      assert.equal(saved.get('evograph.project'), 'B');
      drafts.bind(() => 'A').content.value = 'obsolete read did not activate this draft';
      assert.equal(drafts.bind(() => 'A').content.value, '');
      assert.equal(workspace.state.recoveryRestored.restored, true);
      assert.equal(workspace.state.busy, false);
    }
  }
});

const settingsFormsUrl = moduleUrl(
  compile('composables/useSettingsForms.ts').replace(
    "from 'vue'",
    `from ${JSON.stringify(vueUrl)}`,
  ),
);
const { useModelSettings, queuedSettingsCommand } = await import(settingsFormsUrl);
const settingsRecord = (model) => ({
  adapters: [
    {
      id: 'synthetic',
      name: 'Synthetic local adapter',
      fields: [{ key: 'model', label: 'Model', default: 'old', required: true }],
      secret_label: '',
    },
  ],
  provider: { adapter: 'synthetic', config: { model }, has_key: false },
});

test('disposed settings save excludes recovery and stale settings reads cannot overwrite its confirmation', async () => {
  const { workspace, handlers, env } = await harness();
  const oldSettings = deferred(),
    saveResponse = deferred();
  handlers['settings.get'] = () => oldSettings.promise;
  handlers['settings.save'] = () => saveResponse.promise;
  const oldRefresh = workspace.refresh();
  const command = queuedSettingsCommand(env.handler, workspace.reserveSettingsOperation);
  const scope = effectScope();
  const form = scope.run(() =>
    useModelSettings(settingsRecord('old'), command, workspace.applySettings),
  );
  form.fields.value.model = 'new';
  const saving = form.save();
  await tick();
  form.dispose();
  scope.stop();
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  assert.equal(await workspace.restoreProject(project('D')), false);
  assert.equal(workspace.reserveSettingsOperation(), null);
  assert.equal(env.calls.filter(([action]) => action === 'projects.restore_archived').length, 0);
  saveResponse.resolve(settingsRecord('new'));
  await saving;
  assert.equal(workspace.state.busy, false);
  assert.equal(workspace.state.settings.provider.config.model, 'new');
  oldSettings.resolve(settingsRecord('old'));
  await oldRefresh;
  assert.equal(workspace.state.settings.provider.config.model, 'new');
  delete handlers['settings.get'];
  assert.equal(await workspace.restoreProject(project('D')), true);
});

test('recovery excludes settings mutations through the confirmed follow-up read and releases exact ownership', async () => {
  const { workspace, handlers, records } = await harness();
  const restoreResponse = deferred(),
    refreshResponse = deferred();
  handlers['projects.restore_archived'] = () => restoreResponse.promise;
  handlers['projects.list'] = () => refreshResponse.promise;
  const settingsCalls = [];
  const command = queuedSettingsCommand(async (action) => {
    settingsCalls.push(action);
    return settingsRecord('new');
  }, workspace.reserveSettingsOperation);
  const staleRelease = workspace.reserveSettingsOperation();
  staleRelease();
  const restoring = workspace.restoreProject(project('D'));
  staleRelease();
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  await assert.rejects(command('settings.save'), /工作空间操作进行中/);
  records.set('D', project('D'));
  restoreResponse.resolve({ project: project('D'), restored: true });
  await tick();
  assert.equal(workspace.state.recoveryRestored.restored, true);
  assert.equal(workspace.state.busy, true);
  workspace.setBusy(false);
  staleRelease();
  await assert.rejects(command('research.configure'), /工作空间操作进行中/);
  assert.deepEqual(settingsCalls, []);
  refreshResponse.resolve([...records.values()]);
  assert.equal(await restoring, true);
  assert.equal(workspace.state.busy, false);
  await command('settings.save');
  assert.deepEqual(settingsCalls, ['settings.save']);
  assert.equal(workspace.state.busy, false);
});

test('already-active recovery keeps browser identity, filters and camera across successful follow-up refresh', async () => {
  const { workspace, records, browse, drafts, workflows } = await harness();
  const active = { ...project('D', 7), milestones: [{ id: 'M1' }] };
  records.set('D', active);
  await workspace.selectProject('D');
  workspace.selectNode('M1');
  const state = browse.viewState(workspace.state.project);
  Object.assign(state, {
    query: 'live query',
    focusedId: 'service',
    role: 'backend',
    relation: 'upstream',
    viewport: { x: 17, y: 23, zoom: 1.2 },
  });
  const before = JSON.parse(JSON.stringify(state));
  drafts.bind(() => 'D').content.value = 'live draft';
  const workflow = workflows.entry('D', 'M1');
  workflow.report = 'live report';
  assert.equal(await workspace.restoreProject(project('D')), true);
  assert.equal(workspace.state.recoveryRestored.restored, false);
  assert.equal(workspace.state.project.id, 'D');
  assert.equal(workspace.state.selectedId, 'M1');
  assert.equal(browse.viewState(workspace.state.project), state);
  assert.deepEqual(JSON.parse(JSON.stringify(state)), before);
  assert.equal(drafts.bind(() => 'D').content.value, 'live draft');
  assert.equal(workflows.entry('D', 'M1'), workflow);
  assert.equal(workflow.report, 'live report');
});

test('canonical stream history supersedes a no-op recovery refresh without releasing recovery ownership', async () => {
  const { workspace, records, handlers, browse, drafts, workflows } = await harness();
  const active = { ...project('A', 7), milestones: [{ id: 'M1' }], messages: [], events: [] };
  records.set('A', active);
  workspace.applyProject(active);
  const browser = browse.viewState(workspace.state.project);
  Object.assign(browser, {
    query: 'keep query',
    focusedId: 'service',
    viewport: { x: 17, y: 23, zoom: 1.2 },
  });
  const before = JSON.parse(JSON.stringify(browser));
  drafts.bind(() => 'A').content.value = 'keep typed composer';
  const workflow = workflows.entry('A', 'M1');
  workflow.report = 'keep workflow report';
  const read = deferred();
  handlers['projects.get'] = () => read.promise;
  const restoring = workspace.restoreProject(project('A'));
  await tick();
  assert.equal(workspace.state.recoveryRestored.restored, false);
  assert.equal(workspace.state.busy, true);
  const narration = {
    id: 'canonical',
    project_id: 'A',
    role: 'assistant',
    content: 'received while recovery refresh was pending',
  };
  workspace.appendMessage('A', narration);
  const { messages: _messages, events: _events, ...planning } = active;
  workspace.applyProject({ ...planning, revision: 8, snapshot_mode: 'compact-v1' });
  workspace.setBusy(false);
  assert.equal(workspace.reserveSettingsOperation(), null);
  assert.equal(workspace.state.busy, true);
  read.resolve(active);
  assert.equal(await restoring, true);
  assert.equal(workspace.state.busy, false);
  assert.equal(workspace.state.project.revision, 8);
  assert.deepEqual(workspace.state.project.messages, [narration]);
  assert.equal('snapshot_mode' in workspace.state.project, false);
  assert.equal(browse.viewState(workspace.state.project), browser);
  assert.deepEqual(JSON.parse(JSON.stringify(browser)), before);
  assert.equal(drafts.bind(() => 'A').content.value, 'keep typed composer');
  assert.equal(workflows.entry('A', 'M1'), workflow);
  assert.equal(workflow.report, 'keep workflow report');
});

test('compact snapshots and saved messages preserve settings ownership and confirmed settings', async () => {
  const { workspace, handlers, env } = await harness();
  workspace.applyProject({ ...project('A', 7), messages: [], events: [] });
  const oldSettings = deferred();
  handlers['settings.get'] = () => oldSettings.promise;
  const refreshing = workspace.refresh();
  const release = workspace.reserveSettingsOperation();
  workspace.applySettings(settingsRecord('confirmed'));
  workspace.appendMessage('A', {
    id: 'canonical',
    project_id: 'A',
    role: 'assistant',
    content: 'saved',
  });
  workspace.applyProject({ ...project('A', 8), snapshot_mode: 'compact-v1' });
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  assert.equal(await workspace.restoreProject(project('D')), false);
  assert.equal(env.calls.filter(([action]) => action === 'projects.restore_archived').length, 0);
  oldSettings.resolve(settingsRecord('stale'));
  await refreshing;
  assert.equal(workspace.state.settings.provider.config.model, 'confirmed');
  assert.equal(workspace.state.project.messages[0].id, 'canonical');
  assert.equal(workspace.state.busy, true);
  release();
  assert.equal(workspace.state.busy, false);
});

test('confirmed restored incarnation rejects late stream frames before explicit reactivation', async () => {
  const { workspace, records, handlers, browse, drafts, workflows } = await harness();
  const old = { ...project('A', 7), milestones: [{ id: 'M1' }], messages: [], events: [] };
  workspace.applyProject(old);
  const browser = browse.viewState(workspace.state.project);
  drafts.bind(() => 'A').content.value = 'deleted draft';
  workflows.entry('A', 'M1').report = 'deleted report';
  const read = deferred();
  handlers['projects.list'] = () => read.promise;
  handlers['projects.restore_archived'] = () => ({ project: project('A', 8), restored: true });
  const restoring = workspace.restoreProject(project('A'));
  await tick();
  assert.equal(workspace.state.project, null);
  workspace.appendMessage('A', {
    id: 'late',
    project_id: 'A',
    role: 'assistant',
    content: 'obsolete',
  });
  workspace.applyProject({ ...old, revision: 9, snapshot_mode: 'compact-v1' });
  assert.equal(workspace.state.project, null);
  assert.equal(browse.viewState(old), undefined);
  assert.equal(drafts.bind(() => 'A').content.value, '');
  read.resolve([...records.values()]);
  assert.equal(await restoring, true);
  records.set('A', { ...project('A', 8), messages: [], events: [] });
  await workspace.selectProject('A');
  assert.deepEqual(workspace.state.project.messages, []);
  assert.notEqual(browse.viewState(workspace.state.project), browser);
  assert.equal(workflows.entry('A', 'M1'), undefined);
});
