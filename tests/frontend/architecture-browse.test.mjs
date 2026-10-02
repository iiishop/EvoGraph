import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { computed, createRenderer, effectScope, h, nextTick, reactive, ref } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import {
  moduleUrl,
  source,
  compile,
  vueUrl,
  rolesUrl,
  browseModelUrl,
  browseStoreUrl,
  browseImports,
} from './helpers/architecture-fixtures.mjs';
const require = createRequire(import.meta.url);
const { findArchitectureNodes, architectureVisibleIds, validArchitectureViewport } = await import(
  browseModelUrl
);
const { createArchitectureBrowse, useArchitectureBrowse, architectureBrowse } = await import(
  browseStoreUrl
);
const node = (id, extra = {}) => ({
  id,
  label: `模块 ${id}`,
  description: '用户认证服务',
  role: 'backend',
  source_refs: [`src/${id}.py`],
  ...extra,
});
const diagram = (
  nodes = [node('api'), node('db', { role: 'database' }), node('ui', { role: 'frontend' })],
) => ({
  id: 'architecture',
  title: '架构',
  nodes,
  edges: [
    { source: 'ui', target: 'api', label: '调用' },
    { source: 'api', target: 'db', label: '存储' },
  ],
  groups: [],
});
const project = (id = 'P1', extra = {}) => ({
  id,
  created_at: 'first',
  baselines: [{ id: 'B1' }],
  source_fingerprint: 'fp1',
  source_diagram: diagram([node('source')]),
  source_summary: '',
  research: [],
  uml_diagrams: [],
  attachments: [],
  milestones: [],
  source_milestones: [],
  architectures: [
    { number: 7, diagram: diagram(), technologies: [], decisions: [], research_ids: [] },
  ],
  ...extra,
});
const camera = { x: -340, y: 112, zoom: 0.65 };

test('architecture search ranks actual IDs and searches responsibility, role and source paths without mutating truth', () => {
  const d = diagram([node('api'), node('api2'), node('db', { role: 'database' })]);
  const before = JSON.stringify(d);
  assert.equal(findArchitectureNodes(d.nodes, ' API ')[0].id, 'api');
  assert.deepEqual(
    findArchitectureNodes(d.nodes, '认证 src/db').map((n) => n.id),
    ['db'],
  );
  assert.deepEqual(
    findArchitectureNodes(d.nodes, '数据').map((n) => n.id),
    ['db'],
  );
  assert.deepEqual(
    findArchitectureNodes(d.nodes, '', 'database').map((n) => n.id),
    ['db'],
  );
  assert.deepEqual(findArchitectureNodes(d.nodes, 'does not exist'), []);
  assert.equal(JSON.stringify(d), before);
  assert.equal(findArchitectureNodes(d.nodes, '').length, d.nodes.length);
});

test('relationship highlight uses directed real edges, handles cycles, and intersects search and role', () => {
  const d = diagram();
  assert.deepEqual([...architectureVisibleIds(d, '', 'all', 'api', 'upstream')], ['api', 'ui']);
  assert.deepEqual([...architectureVisibleIds(d, '', 'all', 'api', 'downstream')], ['api', 'db']);
  assert.deepEqual(
    [...architectureVisibleIds(d, 'src/db', 'database', 'api', 'downstream')],
    ['db'],
  );
  d.edges.push({ source: 'db', target: 'api', label: 'cycle' });
  assert.equal(architectureVisibleIds(d, '', 'all', 'api', 'downstream').size, 2);
  assert.equal(d.nodes.length, 3);
  assert.equal(d.edges.length, 3);
});

test('session browsing remembers separate projects and source/current/history views across remounts and baseline refresh', () => {
  const store = createArchitectureBrowse(),
    p = project(),
    q = project('P2');
  const current = store.viewState(p);
  Object.assign(current, {
    query: 'api',
    focusedId: 'api',
    role: 'backend',
    relation: 'downstream',
  });
  store.rememberViewport(p, current.key, camera);
  store.entry(p).view = 'source';
  const sourceView = store.viewState(p);
  sourceView.query = 'source';
  sourceView.focusedId = 'source';
  store.rememberViewport(p, sourceView.key, { x: 10, y: 20, zoom: 1.2 });
  store.entry(p).view = 'revision:7';
  store.viewState(p).query = 'db';
  assert.equal(store.viewState(q).query, '');
  store.entry(p).view = 'current';
  const refreshed = structuredClone({ ...p, baselines: [{ id: 'B2' }], source_fingerprint: 'fp2' });
  store.reconcile(refreshed);
  assert.equal(store.viewState(refreshed), current);
  assert.equal(current.query, 'api');
  assert.equal(current.focusedId, 'api');
  assert.equal(current.relation, 'downstream');
  assert.deepEqual(current.viewport, camera);
  store.entry(refreshed).view = 'source';
  assert.equal(store.viewState(refreshed).query, 'source');
  assert.equal(store.viewState(refreshed).viewport.zoom, 1.2);
  store.entry(refreshed).view = 'revision:7';
  assert.equal(store.viewState(refreshed).query, 'db');
});

test('removed components reconcile selection and role; entirely replaced graph invalidates camera and identity', () => {
  const store = createArchitectureBrowse(),
    p = project();
  const state = store.viewState(p);
  Object.assign(state, { focusedId: 'db', role: 'database', query: 'src', relation: 'upstream' });
  store.rememberViewport(p, state.key, camera);
  p.architectures[0].diagram.nodes = [node('api')];
  store.reconcile(p);
  assert.equal(state.focusedId, '');
  assert.equal(state.role, 'all');
  assert.equal(state.relation, 'all');
  assert.equal(state.query, 'src');
  assert.deepEqual(state.viewport, camera);
  const oldKey = state.key;
  p.architectures[0].diagram.nodes = [node('replacement')];
  store.reconcile(p);
  assert.equal(state.viewport, null);
  assert.notEqual(state.key, oldKey);
  store.rememberViewport(p, oldKey, camera);
  assert.equal(state.viewport, null);
});

test('history identity follows revision numbers rather than shifting array indexes', () => {
  const store = createArchitectureBrowse(),
    p = project();
  p.architectures.push({ ...p.architectures[0], number: 12 });
  store.entry(p).view = 'revision:12';
  store.viewState(p).query = 'remember';
  p.architectures.shift();
  store.reconcile(p);
  assert.equal(store.entry(p).view, 'revision:12');
  assert.equal(store.viewState(p).query, 'remember');
  p.architectures = [];
  store.reconcile(p);
  assert.equal(store.entry(p).view, 'source');
});

test('deleted, restored and recreated project incarnations never inherit or accept late old navigation state', () => {
  const store = createArchitectureBrowse(),
    p = project();
  const old = store.viewState(p);
  old.query = 'old secret context';
  store.discard(p.id);
  assert.equal(store.viewState(p), undefined);
  store.rememberViewport(p, old.key, camera);
  store.activate(p);
  const restored = store.viewState(p);
  assert.equal(restored.query, '');
  assert.notEqual(restored.key, old.key);
  store.rememberViewport(p, old.key, camera);
  assert.equal(restored.viewport, null);
  restored.query = 'restored';
  const recreated = { ...p, created_at: 'second' };
  const next = store.viewState(recreated);
  assert.equal(next.query, '');
  assert.equal(
    store.viewState(p),
    undefined,
    'an old mounted incarnation cannot replace its successor',
  );
  store.rememberViewport(p, restored.key, camera);
  assert.equal(next.viewport, null);
  store.retain([]);
  assert.equal(store.viewState(recreated), undefined);
  assert.equal(validArchitectureViewport({ ...camera, zoom: NaN }), false);
  assert.equal(validArchitectureViewport({ ...camera, zoom: 4 }), false);
});

test('browse binding excludes unrelated history/evidence traversal while reconciling nested source and target edits', async () => {
  const guarded = (field) =>
    Object.defineProperty({}, field, {
      enumerable: true,
      get() {
        throw new Error(`Browsing must not traverse unrelated ${field}`);
      },
    });
  const p = ref(
    project('narrow-browse-watch', {
      messages: [guarded('content')],
      evidence: [guarded('output')],
      revision: 1,
    }),
  );
  const scope = effectScope();
  const original = architectureBrowse.reconcile;
  let reconciliations = 0;
  architectureBrowse.reconcile = (value) => {
    reconciliations++;
    return original(value);
  };
  try {
    const binding = scope.run(() => useArchitectureBrowse(() => p.value));
    binding.focusedId.value = 'db';
    binding.role.value = 'database';
    binding.relation.value = 'upstream';
    assert.equal(reconciliations, 1);
    p.value.messages.push(guarded('content'));
    p.value.evidence.push(guarded('output'));
    p.value.revision++;
    await nextTick();
    assert.equal(
      reconciliations,
      1,
      'message, evidence and revision updates must not schedule browse reconciliation',
    );
    // Whole snapshots still reconcile, but their unrelated histories are never traversed.
    p.value = { ...p.value, messages: [guarded('content')], evidence: [guarded('output')] };
    assert.equal(reconciliations, 2);
    assert.equal(binding.focusedId.value, 'db');
    p.value.architectures[0].diagram.nodes.splice(1, 1);
    assert.equal(binding.focusedId.value, '');
    assert.equal(binding.role.value, 'all');
    assert.equal(binding.relation.value, 'all');
    binding.view.value = 'source';
    binding.focusedId.value = 'source';
    binding.rememberViewport({ key: binding.key.value, viewport: camera });
    const previousKey = binding.key.value;
    p.value.source_diagram.nodes[0].id = 'replacement-source';
    assert.equal(binding.focusedId.value, '');
    assert.equal(binding.viewport.value, null);
    assert.notEqual(binding.key.value, previousKey);
  } finally {
    scope.stop();
    architectureBrowse.reconcile = original;
    architectureBrowse.discard(p.value.id);
  }
});

test('Vue binding persists across component scopes and keeps project/view writes isolated', () => {
  const p = ref(project('binding')),
    scope = effectScope();
  const first = scope.run(() => useArchitectureBrowse(() => p.value));
  first.query.value = 'api';
  first.focusedId.value = 'api';
  first.rememberViewport({ key: first.key.value, viewport: camera });
  first.view.value = 'source';
  first.query.value = 'source';
  scope.stop();
  const nextScope = effectScope(),
    second = nextScope.run(() => useArchitectureBrowse(() => p.value));
  assert.equal(second.view.value, 'source');
  assert.equal(second.query.value, 'source');
  second.view.value = 'current';
  assert.equal(second.query.value, 'api');
  assert.deepEqual(second.viewport.value, camera);
  p.value = { ...p.value, source_fingerprint: 'next', baselines: [{ id: 'B2' }] };
  assert.equal(second.query.value, 'api');
  assert.equal(second.focusedId.value, 'api');
  p.value = project('binding-other');
  assert.equal(second.query.value, '');
  nextScope.stop();
});

const all = (n) => [n, ...(n.children ?? []).flatMap(all)];
const textOf = (n) => `${n.text ?? ''}${(n.children ?? []).map(textOf).join('')}`;
let activeElement;
class Element {
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
      clientWidth: 900,
      clientHeight: 600,
      scrollTop: 0,
    });
  }
  get options() {
    return this.children.filter((n) => n.tag === 'option');
  }
  addEventListener(name, callback) {
    (this.listeners ??= {})[name] = callback;
  }
  removeEventListener() {}
  setAttribute(name, value) {
    this[name] = value;
  }
  focus() {
    activeElement = this;
  }
  contains(node) {
    return all(this).includes(node);
  }
  scrollIntoView(options) {
    this.scrolled = options;
  }
  querySelector() {
    return all(this).find((node) => node['aria-selected'] === true);
  }
  getBoundingClientRect() {
    return { top: 0 };
  }
}
const renderer = createRenderer({
  createElement: (tag) => new Element(tag),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText(n, text) {
    n.text = text;
  },
  setElementText(n, text) {
    n.text = text;
    n.children = [];
  },
  patchProp(n, key, prev, value) {
    n[key] = value;
  },
  insert(n, parent, anchor) {
    if (n.parent) n.parent.children = n.parent.children.filter((x) => x !== n);
    n.parent = parent;
    const i = parent.children.indexOf(anchor);
    if (i < 0) parent.children.push(n);
    else parent.children.splice(i, 0, n);
  },
  remove(n) {
    if (n.parent) n.parent.children = n.parent.children.filter((x) => x !== n);
  },
  parentNode: (n) => n.parent,
  nextSibling: (n) => n.parent?.children[n.parent.children.indexOf(n) + 1] ?? null,
});
const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
const graphUrl = moduleUrl(
  `import { h } from ${JSON.stringify(vueUrl)}; export const captured = { fits: 0, located: [], instances: [] }; export default { inheritAttrs: false, setup(_, { attrs, expose }) { captured.instances.push(attrs); expose({ fit() { captured.fits++; }, locate(id) { captured.located.push(id); } }); return () => h('graph', attrs); } };`,
);
const detailUrl = moduleUrl(`import { ref } from ${JSON.stringify(vueUrl)};
export const MAX_DETAIL_COMPONENTS = 3, MAX_DETAIL_FILES = 12; export const matchingScopedDesigns = () => []; export const classDetailGuidance = () => ''; export const contexts = [];
export const useScopedClassDetail = context => { contexts.push(context); return { componentIds: ref([]), filePaths: ref(null), open: ref(false), loading: ref(false), result: ref(null), error: ref(''), select() {}, toggle() {}, selectFiles() {}, toggleFile() {}, close() {}, show() {}, reload() {}, showSaved() {} }; };`);
const imports = {
  ...browseImports,
  vue: vueUrl,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../lib/architectureRoles': rolesUrl,
  '../../composables/useAgent': moduleUrl(
    `export const useAgent = () => ({ state: { follow: {}, projectId: '', view: '', navigationTick: 0 }, freeView() {} });`,
  ),
  '../../composables/useScopedClassDetail': detailUrl,
  './DiagramView.vue': graphUrl,
  './DiagramImage.vue': stub,
  './ArchitectureQuality.vue': stub,
  './ComponentPassport.vue': stub,
  './UmlView.vue': stub,
};
function component(path) {
  const { descriptor } = parse(source(path));
  return moduleUrl(
    compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, q, name) => {
        assert.ok(imports[name], name);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['./ArchitectureBrowser.vue'] = component('components/design/ArchitectureBrowser.vue');
const Browser = (await import(imports['./ArchitectureBrowser.vue'])).default;
const Panel = (await import(component('components/design/ArchitecturePanel.vue'))).default;
const tick = async () => {
  await nextTick();
  await nextTick();
};
async function browserHarness() {
  const props = reactive({ nodes: diagram().nodes, query: '', role: 'all', focusedId: '' }),
    selected = [];
  const root = new Element();
  const app = renderer.createApp({
    render: () =>
      h(Browser, {
        ...props,
        'onUpdate:query': (q) => (props.query = q),
        onSelect: (id) => selected.push(id),
      }),
  });
  app.mount(root);
  await tick();
  const input = () => all(root).find((n) => n.tag === 'input');
  return {
    props,
    root,
    selected,
    input,
    options: () => all(root).filter((n) => n.role === 'option'),
    type: async (value) => {
      input().onInput({ target: { value } });
      await tick();
    },
    key: async (key, extra = {}) => {
      input().onKeydown({ key, preventDefault() {}, stopPropagation() {}, ...extra });
      await tick();
    },
    dispose: () => app.unmount(),
  };
}

test('real architecture browser exposes keyboard results, instant scroll, Enter locate and Escape close without erasing the query', async () => {
  const b = await browserHarness();
  try {
    assert.equal(b.input().role, 'combobox');
    assert.equal(b.input()['aria-expanded'], false);
    await b.key('ArrowDown');
    assert.equal(b.options().length, 3);
    await b.key('ArrowDown');
    assert.equal(b.options()[1]['aria-selected'], true);
    assert.equal(b.options()[1].scrolled.behavior, 'instant');
    await b.key('Enter');
    assert.deepEqual(b.selected, ['db']);
    assert.equal(activeElement, b.input());
    assert.equal(b.input()['aria-expanded'], false);
    await b.type('src/api');
    assert.equal(b.options().length, 1);
    await b.key('Escape');
    assert.equal(b.props.query, 'src/api');
    assert.equal(b.input()['aria-expanded'], false);
  } finally {
    b.dispose();
  }
});

test('IME, no matches and deleted stale options cannot cause navigation; clear search restores real results', async () => {
  const b = await browserHarness();
  try {
    await b.type('api');
    const old = b.options()[0];
    await b.key('Enter', { isComposing: true });
    await b.key('Enter', { keyCode: 229 });
    assert.deepEqual(b.selected, []);
    b.props.nodes = b.props.nodes.filter((n) => n.id !== 'api');
    await tick();
    old.onClick();
    await b.key('Enter');
    assert.deepEqual(b.selected, []);
    assert.match(textOf(b.root), /没有匹配组件/);
    all(b.root)
      .find((n) => n['aria-label'] === '清空组件搜索')
      .onClick();
    await tick();
    assert.equal(b.props.query, '');
    assert.equal(b.options().length, 2);
  } finally {
    b.dispose();
  }
});

test('real panel wires per-view/remount camera state, explicit re-locate, show-all and independent evidence invalidation', async () => {
  const originals = Object.fromEntries(
    ['requestAnimationFrame', 'cancelAnimationFrame', 'ResizeObserver'].map((k) => [
      k,
      globalThis[k],
    ]),
  );
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  const p = ref(project('panel-integration')),
    visible = ref(true),
    root = new Element();
  const app = renderer.createApp({
    render: () => (visible.value ? h(Panel, { project: p.value }) : null),
  });
  const { captured } = await import(graphUrl),
    { contexts } = await import(detailUrl);
  const graph = () => all(root).find((n) => n.tag === 'graph');
  const button = (label) => all(root).find((n) => n.tag === 'button' && textOf(n).trim() === label);
  app.mount(root);
  await tick();
  try {
    const oldEvidence = contexts.at(-1)().key;
    all(root)
      .find((n) => n['aria-label'] === '选择架构组件')
      .onChange({ target: { value: 'api' } });
    await tick();
    graph().onViewport({ key: graph()['browse-key'], viewport: camera });
    all(root)
      .find((n) => n.tag === 'input' && n.role === 'combobox')
      .onInput({ target: { value: 'api' } });
    await tick();
    const key = graph()['browse-key'];
    button('SRC 源码现状').onClick();
    await tick();
    assert.equal(graph().query, '');
    assert.notEqual(graph()['browse-key'], key);
    button('目标架构').onClick();
    await tick();
    assert.equal(graph().query, 'api');
    assert.equal(graph()['focused-id'], 'api');
    assert.deepEqual(graph()['initial-viewport'], camera);
    visible.value = false;
    await tick();
    visible.value = true;
    await tick();
    assert.equal(graph()['browse-key'], key);
    assert.deepEqual(graph()['initial-viewport'], camera);
    p.value = { ...p.value, source_fingerprint: 'fp2', baselines: [{ id: 'B2' }] };
    await tick();
    assert.notEqual(contexts.at(-1)().key, oldEvidence);
    assert.equal(graph().query, 'api');
    assert.equal(graph()['focused-id'], 'api');
    const located = captured.located.length;
    all(root)
      .find((n) => n['aria-label'] === '选择架构组件')
      .onChange({ target: { value: 'api' } });
    await tick();
    assert.equal(captured.located.length, located + 1);
    button('清除筛选 · 显示全部').onClick();
    await tick();
    assert.equal(graph().query, '');
    assert.equal(graph()['focused-id'], '');
    assert.equal(graph().role, 'all');
    assert.equal(graph().relation, 'all');
    assert.ok(captured.fits > 0);
  } finally {
    app.unmount();
    architectureBrowse.discard(p.value.id);
    for (const [key, value] of Object.entries(originals))
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
  }
});

test('explicit full-fit overview zoom remains valid across session restoration', () => {
  const store = createArchitectureBrowse();
  const p = project();
  const state = store.viewState(p);
  const overview = { x: 84, y: -12, zoom: 0.025 };
  assert.equal(validArchitectureViewport(overview), true);
  assert.equal(validArchitectureViewport({ ...overview, zoom: 0.0001 }), false);
  assert.equal(validArchitectureViewport({ ...overview, zoom: 0 }), false);
  store.rememberViewport(p, state.key, overview);
  assert.deepEqual(store.viewState(p).viewport, overview);
});
