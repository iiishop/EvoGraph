import { surfaceMotionUrl } from './helpers/surface-motion-fixtures.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of [
  'window',
  'document',
  'Document',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, nextTick } = await import('vue');
const vue = import.meta.resolve('vue');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const read = (path) => readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const envUrl = url(`import { reactive, computed, h } from ${JSON.stringify(vue)};
export const env = { state: reactive({ page: 'projects', project: { id: 'P', milestones: [{ id: 'A', origin: 'plan' }, { id: 'B', origin: 'plan' }], source_milestones: [] }, selectedId: null }), locates: [], free: [], receipts: [], receiptCallback: null, target: 'A' };
export const Graph = { emits: ['receipt'], setup(_, { expose, emit }) { expose({ locate: id => env.locates.push(id), fit() {}, reset() {} }); env.receiptCallback = (id, trigger) => emit('receipt', id, trigger); return () => h('div', { class: 'test-graph' }); } };
export const workspaceViews = [{ id: 'graph', component: Graph }, { id: 'architecture', component: { render() { return h('div', { class: 'test-architecture' }); } } }];
const tab = reactive({ value: 'graph' });
export const useWorkspace = () => ({ bindWorkspaceTab: () => ({ key: 'P', tab: computed({ get: () => tab.value, set: value => { tab.value = value; } }) }), state: env.state, selected: computed(() => env.state.project.milestones.find(m => m.id === env.state.selectedId)), selectNode: id => env.state.selectedId = id });
export const useAgent = () => ({ state: reactive({ projectId: '', follow: {}, navigationTick: 0 }), freeView: id => env.free.push(id), resumeFollow() {} });
export const useEntrance = () => {};
export const Toolbar = { emits: ['tab'], setup(_, { emit }) { return () => h('button', { class: 'tab-other', onClick: () => emit('tab', 'architecture') }, 'architecture'); } };
export const Dock = { emits: ['locate'], setup(_, { emit, expose }) { expose({ openReceipt: (id, trigger) => env.receipts.push({ id, trigger }) }); return () => h('button', { class: 'receipt-link', onClick: () => emit('locate', env.target) }, 'locate'); } };
export const Inspector = { emits: ['locate'], setup(_, { emit }) { return () => h('button', { class: 'dependency-link', onClick: () => emit('locate', 'B') }, 'dependency'); } };`);
const { env } = await import(envUrl);
const stub = url('export default { render() { return null; } };');
const alias = (name) => url(`export { ${name} as default } from ${JSON.stringify(envUrl)};`);
const imports = {
  '../../composables/useSurfaceMotion': surfaceMotionUrl,
  vue,
  '../../composables/useAgent': envUrl,
  '../../composables/useWorkspace': envUrl,
  '../../composables/useEntrance': envUrl,
  '../../lib/workspaceViews': envUrl,
  './WorkspaceHeader.vue': stub,
  './UnifiedPlanBar.vue': stub,
  './PlanCandidatePanel.vue': stub,
  '../graph/GraphToolbar.vue': alias('Toolbar'),
  '../graph/MilestoneGraph.vue': alias('Graph'),
  '../graph/MilestoneFinder.vue': stub,
  '../graph/MilestoneInspector.vue': alias('Inspector'),
  '../graph/SourceInspector.vue': alias('Inspector'),
  '../agent/AgentDock.vue': alias('Dock'),
};
function component(path, map) {
  const { descriptor } = parse(read(path));
  return import(
    url(
      compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
        /from (['"])([^'"]+)\1/g,
        (_, q, name) => {
          assert.ok(map[name], name);
          return `from ${JSON.stringify(map[name])}`;
        },
      ),
    )
  ).then((module) => module.default);
}
const Workspace = await component('components/workspace/ProjectWorkspace.vue', imports);
const summaryUrl = url(compile(read('lib/turnSummary.ts')));
const Receipt = await component('components/agent/AgentTurnSummary.vue', {
  vue,
  '../../lib/turnSummary': summaryUrl,
});
const flush = async () => {
  await nextTick();
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
const mount = (view, props) => {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(view, props);
  app.mount(root);
  return {
    root,
    dispose() {
      app.unmount();
      root.remove();
    },
  };
};

test('real receipt emits the actual node ID; absent and removed milestones stay plain text', async () => {
  const selected = [];
  const summary = {
    version: 1,
    turn_id: 'T',
    status: 'completed',
    changed: true,
    before_revision: 1,
    after_revision: 2,
    changes: {
      milestones: {
        added: [{ id: 'A', title: 'Current A', fields: [] }],
        updated: [{ id: 'missing', title: 'Missing', fields: ['scope'] }],
        removed: [{ id: 'removed', title: 'Removed', fields: [] }],
      },
      dependencies: {
        added: [{ source: 'A', target: 'B', reason: 'Actual stored reason', type: 'verification' }],
        updated: [],
        removed: [],
      },
      target: null,
      architecture: null,
      other: [],
    },
  };
  const view = mount(Receipt, {
    summary,
    milestones: [
      { id: 'A', title: 'A' },
      { id: 'B', title: 'B' },
    ],
    onLocate: (id) => selected.push(id),
  });
  view.root.querySelector('details').open = true;
  for (const button of view.root.querySelectorAll('.turn-node-link')) button.click();
  assert.deepEqual(selected, ['A', 'A', 'B']);
  assert.ok(view.root.textContent.includes('Missing'));
  assert.ok(view.root.textContent.includes('Removed'));
  view.dispose();
});

test('receipt navigation switches to graph, centers repeated same-node requests and rejects missing nodes', async () => {
  env.state.selectedId = null;
  env.locates = [];
  env.free = [];
  env.target = 'A';
  const view = mount(Workspace, { project: env.state.project });
  view.root.querySelector('.tab-other').click();
  await flush();
  assert.ok(view.root.querySelector('.test-architecture'));
  view.root.querySelector('.receipt-link').click();
  await flush();
  assert.ok(view.root.querySelector('.test-graph'));
  assert.equal(env.state.selectedId, 'A');
  assert.deepEqual(env.locates, ['A']);
  view.root.querySelector('.receipt-link').click();
  await flush();
  assert.deepEqual(env.locates, ['A', 'A']);
  view.root.querySelector('.dependency-link').click();
  await flush();
  assert.equal(env.state.selectedId, 'B');
  assert.deepEqual(env.locates, ['A', 'A', 'B']);
  env.target = 'gone';
  view.root.querySelector('.receipt-link').click();
  await flush();
  assert.equal(env.state.selectedId, 'B');
  assert.deepEqual(env.locates, ['A', 'A', 'B']);
  assert.ok(env.free.every((id) => id === 'P'));
  view.dispose();
});

test('a newer receipt or node selection supersedes an awaiting locate tick', async () => {
  env.state.selectedId = null;
  env.locates = [];
  env.target = 'A';
  const view = mount(Workspace, { project: env.state.project });
  view.root.querySelector('.receipt-link').click();
  env.target = 'B';
  view.root.querySelector('.receipt-link').click();
  await flush();
  assert.deepEqual(env.locates, ['B']);
  env.target = 'A';
  view.root.querySelector('.receipt-link').click();
  env.state.selectedId = null;
  await flush();
  assert.deepEqual(env.locates, ['B']);
  view.dispose();
});

test('workspace routes historical event identity and origin to its existing dock, rejecting stale project actions', async () => {
  env.state.selectedId = null;
  env.state.page = 'projects';
  env.receipts = [];
  const originalProject = env.state.project;
  const view = mount(Workspace, { project: originalProject });
  try {
    const origin = view.root.querySelector('.test-graph');
    const callback = env.receiptCallback;
    callback('chosen-terminal-event', origin);
    await flush();
    assert.deepEqual(env.receipts, [{ id: 'chosen-terminal-event', trigger: origin }]);
    env.state.project = { ...originalProject, id: 'other' };
    callback('late-foreign-event', origin);
    await flush();
    assert.equal(env.receipts.length, 1);
    env.state.project = { ...originalProject, created_at: 'recreated' };
    callback('late-incarnation-event', origin);
    await flush();
    assert.equal(env.receipts.length, 1);
    env.state.project = originalProject;
    env.state.page = 'settings';
    callback('late-settings-event', origin);
    await flush();
    assert.equal(env.receipts.length, 1);
  } finally {
    view.dispose();
    env.state.project = originalProject;
    env.state.page = 'projects';
  }
});
