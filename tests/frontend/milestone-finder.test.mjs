import { surfaceMotionUrl } from './helpers/surface-motion-fixtures.mjs';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, h, nextTick, ref } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const vue = pathToFileURL(require.resolve('vue')).href;
const searchUrl = url(compile(source('lib/milestoneFinder.ts')));
const { findMilestones } = await import(searchUrl);
const milestone = (id, extra = {}) => ({
  id,
  title: `交付 ${id}`,
  scope: [`src/${id.toLowerCase()}.ts`],
  origin: 'plan',
  ...extra,
});
const entries = () =>
  Array.from({ length: 64 }, (_, i) => milestone(`M${i + 1}`)).concat(
    milestone('SRC_AUTH', { title: '已实现登录', origin: 'source', scope: ['src/auth/login.py'] }),
  );

test('finding covers IDs, titles, scope, case-insensitive multi-word terms and SRC with stable exact-ID ranking', () => {
  const items = entries();
  assert.equal(findMilestones(items, '').length, 65);
  assert.equal(findMilestones(items, 'm6')[0].id, 'M6');
  assert.deepEqual(
    findMilestones(items, '交付 M64').map((x) => x.id),
    ['M64'],
  );
  assert.deepEqual(
    findMilestones(items, 'src/auth').map((x) => x.id),
    ['SRC_AUTH'],
  );
  assert.deepEqual(
    findMilestones(items, 'SRC 源码').map((x) => x.id),
    ['SRC_AUTH'],
  );
  assert.deepEqual(
    findMilestones(items, '登录').map((x) => x.id),
    ['SRC_AUTH'],
  );
  assert.deepEqual(findMilestones(items, 'unmatched feature'), []);
});

const all = (node) => [node, ...(node.children ?? []).flatMap(all)];
const textOf = (node) => `${node.text ?? ''}${(node.children ?? []).map(textOf).join('')}`;
class HostElement {
  get [Symbol.toStringTag]() {
    return 'HTMLElement';
  }
  constructor(tag = 'root') {
    Object.assign(this, {
      tag,
      tagName: tag.toUpperCase(),
      children: [],
      parent: null,
      style: {},
      listeners: {},
      value: '',
    });
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  removeEventListener(name) {
    delete this.listeners[name];
  }
  setAttribute(name, value) {
    this[name] = value;
  }
  removeAttribute(name) {
    delete this[name];
  }
  getRootNode() {
    return globalThis.document;
  }
  showModal() {
    this.open = true;
  }
  focus() {
    globalThis.document.activeElement = this;
  }
  scrollIntoView() {
    this.scrolled = true;
  }
  querySelector(selector) {
    return selector === '[aria-selected="true"]'
      ? all(this).find((node) => node['aria-selected'] === true)
      : null;
  }
}
const renderer = createRenderer({
  createElement: (tag) => new HostElement(tag),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText(node, text) {
    node.text = text;
  },
  setElementText(node, text) {
    node.text = text;
    node.children = [];
  },
  patchProp(node, key, previous, value) {
    node[key] = value;
  },
  insert(node, parent, anchor) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
    node.parent = parent;
    const i = anchor ? parent.children.indexOf(anchor) : -1;
    if (i < 0) parent.children.push(node);
    else parent.children.splice(i, 0, node);
  },
  remove(node) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null,
});
const imports = {
  vue,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../lib/milestoneFinder': searchUrl,
};
function component(path) {
  const { descriptor } = parse(source(path));
  return url(
    compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => {
        assert.ok(imports[name], name);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['../ui/AppModal.vue'] = component('components/ui/AppModal.vue');
const Finder = (await import(component('components/graph/MilestoneFinder.vue'))).default;
const tick = async () => {
  await nextTick();
  await nextTick();
};
async function harness() {
  const originals = Object.fromEntries(
    ['document', 'Document', 'ShadowRoot', 'HTMLElement'].map((key) => [key, globalThis[key]]),
  );
  globalThis.Document = class {};
  globalThis.ShadowRoot = class {};
  globalThis.HTMLElement = HostElement;
  globalThis.document = Object.assign(new Document(), { activeElement: null });
  const trigger = new HostElement('button');
  trigger.focus();
  const milestones = ref(entries()),
    open = ref(true),
    selected = [];
  const app = renderer.createApp({
    setup: () => () =>
      open.value
        ? h(Finder, {
            milestones: milestones.value,
            onSelect: (id) => {
              selected.push(id);
              open.value = false;
            },
            onClose: () => {
              open.value = false;
            },
          })
        : null,
  });
  const root = new HostElement();
  app.mount(root);
  await tick();
  const input = () => all(root).find((node) => node.tag === 'input');
  return {
    root,
    trigger,
    milestones,
    open,
    selected,
    input,
    options: () => all(root).filter((node) => node.role === 'option'),
    type: async (query) => {
      input().value = query;
      input().listeners.input({ target: input() });
      await tick();
    },
    key: async (key, extra = {}) => {
      input().onKeydown({ key, preventDefault() {}, ...extra });
      await tick();
    },
    dispose: () => {
      app.unmount();
      for (const [key, value] of Object.entries(originals)) {
        if (value === undefined) delete globalThis[key];
        else globalThis[key] = value;
      }
    },
  };
}

test('the real finder and AppModal provide keyboard navigation, Enter selection and focus return', async () => {
  const h = await harness();
  try {
    assert.equal(document.activeElement, h.input());
    assert.equal(h.input().role, 'combobox');
    assert.equal(h.options().length, 65);
    assert.match(textOf(h.root), /SRC · 源码观察/);
    await h.type('M6');
    assert.equal(textOf(h.options()[0]).includes('M6'), true);
    await h.key('ArrowDown');
    assert.equal(h.options()[1]['aria-selected'], true);
    assert.equal(h.options()[1].scrolled, true);
    const next = findMilestones(entries(), 'M6')[1].id;
    await h.key('Enter');
    assert.deepEqual(h.selected, [next]);
    assert.equal(document.activeElement, h.trigger);
  } finally {
    h.dispose();
  }
});

test('Escape returns focus without selection; IME Enter never selects a partial search', async () => {
  const h = await harness();
  try {
    await h.key('Enter', { isComposing: true });
    await h.key('Enter', { keyCode: 229 });
    assert.deepEqual(h.selected, []);
    assert.equal(h.open.value, true);
    await h.key('Escape');
    assert.deepEqual(h.selected, []);
    assert.equal(document.activeElement, h.trigger);
  } finally {
    h.dispose();
  }
});

test('no-match guidance stays usable and a stale deleted result cannot be chosen', async () => {
  const h = await harness();
  try {
    await h.type('M64');
    const stale = h.options()[0];
    h.milestones.value = h.milestones.value.filter((item) => item.id !== 'M64');
    await tick();
    assert.equal(h.options().length, 0);
    assert.match(textOf(h.root), /没有匹配的里程碑/);
    assert.match(textOf(h.root), /更短的 ID/);
    stale.onClick();
    await h.key('Enter');
    assert.deepEqual(h.selected, []);
    await h.type('src/auth');
    assert.equal(h.options().length, 1);
    await h.key('Enter');
    assert.deepEqual(h.selected, ['SRC_AUTH']);
  } finally {
    h.dispose();
  }
});

test('live title and scope edits update matching without losing a still-valid active result', async () => {
  const h = await harness();
  try {
    await h.type('交付');
    await h.key('ArrowDown');
    const selectedId = h.input()['aria-activedescendant'];
    h.milestones.value[1].scope = ['src/changed.ts'];
    await tick();
    assert.equal(h.input()['aria-activedescendant'], selectedId);
    await h.type('src/changed');
    assert.equal(h.options().length, 1);
    h.milestones.value[1].title = '更新后的交付';
    await tick();
    assert.match(textOf(h.options()[0]), /更新后的交付/);
  } finally {
    h.dispose();
  }
});

test('narrow graph details stay in flow and reduced-motion dialogs retain the existing motion safeguard', () => {
  const workspace = source('components/workspace/ProjectWorkspace.vue');
  assert.match(workspace, /@media \(max-width: 760px\)/);
  assert.match(
    workspace,
    /\.workspace-detail-open \.inspector-surface > :deep\(\.inspector\)\s*\{\s*position: static/,
  );
  assert.match(
    workspace,
    /:compact="\s*showingCandidate \|\| tab === 'architecture' \|\| \(tab === 'graph' && Boolean\(selected\)\)\s*"/,
  );
  assert.match(
    source('styles/dialogs.css'),
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation: none/,
  );
});

test('workspace finder selection opens the existing SRC inspector and re-locates an already selected node', async () => {
  const originals = Object.fromEntries(
    ['document', 'Document', 'ShadowRoot', 'HTMLElement'].map((key) => [key, globalThis[key]]),
  );
  globalThis.Document = class {};
  globalThis.ShadowRoot = class {};
  globalThis.HTMLElement = HostElement;
  globalThis.document = Object.assign(new Document(), { activeElement: null });
  const workspaceUrl = url(`import { reactive, computed } from ${JSON.stringify(vue)};
    export const data = reactive({ page: 'projects', selectedId: null, milestones: [], project: null });
    const tab = reactive({ value: 'graph' });
    export const useWorkspace = () => ({ bindWorkspaceTab: () => ({ key: 'P1', tab: computed({ get: () => tab.value, set: value => { tab.value = value; } }) }), state: data, selected: computed(() => data.milestones.find(item => item.id === data.selectedId)), selectNode: id => { data.selectedId = id; } });`);
  const viewsUrl = url(`import { h } from ${JSON.stringify(vue)};
    export const calls = []; export const graph = { inheritAttrs: false, setup(_, { expose }) { expose({ locate: id => calls.push(id) }); return () => h('graph'); } };
    export const workspaceViews = [{ id: 'graph', component: graph }]; export default graph;`);
  const agentUrl = url(
    `import { reactive } from ${JSON.stringify(vue)}; export const agent = { state: reactive({ follow: {}, navigationTick: 0 }), freeView() {}, resumeFollow() {} }; export const useAgent = () => agent;`,
  );
  const stub = url('export default { inheritAttrs: false, render() { return null; } };');
  Object.assign(imports, {
    '../../composables/useSurfaceMotion': surfaceMotionUrl,
    '../../composables/useWorkspace': workspaceUrl,
    '../../composables/useAgent': agentUrl,
    '../../composables/useEntrance': url('export const useEntrance = () => {};'),
    '../../lib/workspaceViews': viewsUrl,
    './WorkspaceHeader.vue': stub,
    './UnifiedPlanBar.vue': stub,
    './PlanCandidatePanel.vue': stub,
    '../agent/AgentDock.vue': url(
      `import { h } from ${JSON.stringify(vue)}; export default { inheritAttrs: false, render() { return h('agent-dock'); } };`,
    ),
    '../graph/MilestoneGraph.vue': viewsUrl,
    '../graph/MilestoneFinder.vue': component('components/graph/MilestoneFinder.vue'),
    '../graph/GraphToolbar.vue': url(
      `import { h } from ${JSON.stringify(vue)}; export default { inheritAttrs: false, emits: ['find'], setup(_, { emit }) { return () => h('button', { 'aria-label': '查找里程碑', onClick: () => emit('find') }, '查找里程碑'); } };`,
    ),
    '../graph/MilestoneInspector.vue': url(
      `import { h } from ${JSON.stringify(vue)}; export default { props: ['milestone'], setup(props) { return () => h('plan-inspector', { id: props.milestone.id }); } };`,
    ),
    '../graph/SourceInspector.vue': url(
      `import { h } from ${JSON.stringify(vue)}; export default { props: ['milestone'], setup(props) { return () => h('source-inspector', { id: props.milestone.id }); } };`,
    ),
  });
  const Workspace = (await import(component('components/workspace/ProjectWorkspace.vue'))).default;
  const { data } = await import(workspaceUrl),
    { calls } = await import(viewsUrl);
  data.milestones = entries();
  const project = {
    id: 'P1',
    source_milestones: data.milestones.filter((item) => item.origin === 'source'),
    milestones: data.milestones.filter((item) => item.origin !== 'source'),
  };
  data.project = project;
  const app = renderer.createApp(Workspace, { project }),
    root = new HostElement();
  app.mount(root);
  await tick();
  try {
    for (let iteration = 0; iteration < 2; iteration++) {
      const button = all(root).find(
        (node) => node.tag === 'button' && node['aria-label'] === '查找里程碑',
      );
      button.focus();
      button.onClick();
      await tick();
      const input = all(root).find((node) => node.role === 'combobox');
      input.value = 'src/auth';
      input.listeners.input({ target: input });
      await tick();
      input.onKeydown({ key: 'Enter', preventDefault() {} });
      await tick();
      assert.equal(data.selectedId, 'SRC_AUTH');
      assert.equal(all(root).find((node) => node.tag === 'source-inspector')?.id, 'SRC_AUTH');
      const inspector = all(root).find((node) => node.tag === 'source-inspector');
      const dock = all(root).find((node) => node.tag === 'agent-dock');
      assert.equal(
        inspector.parent.parent,
        dock.parent,
        'inspector and stable composer share the workspace grid',
      );
      assert.match(String(inspector.parent.parent.class), /workspace-body/);
      assert.match(String(inspector.parent.parent.class), /workspace-detail-open/);
      assert.notEqual(
        inspector.parent,
        all(root).find((node) => node.tag === 'graph').parent,
        'inspector is outside the measured graph viewport',
      );
      assert.equal(
        all(root).some((node) => node.tag === 'plan-inspector'),
        false,
      );
      assert.equal(document.activeElement, button);
    }
    assert.deepEqual(calls, ['SRC_AUTH', 'SRC_AUTH']);
  } finally {
    app.unmount();
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});

test('finder and toolbar remain bounded in small windows without hiding the search action', () => {
  assert.match(source('components/graph/MilestoneFinder.vue'), /max-height: min\(46vh, 420px\)/);
  assert.match(source('styles/dialogs.css'), /max-width: calc\(100vw - 40px\)/);
  assert.match(source('styles/dialogs.css'), /max-height: 86vh/);
  const toolbar = source('components/graph/GraphToolbar.vue');
  assert.match(toolbar, /aria-label="查找里程碑"/);
  assert.match(toolbar, /overflow-x: auto/);
  assert.match(toolbar, /\.graph-actions\s*\{\s*flex-shrink: 0/);
});
