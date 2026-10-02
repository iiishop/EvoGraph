import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick, reactive, h, ref } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const vue = pathToFileURL(require.resolve('vue')).href;
const flow = url(`import { h } from ${JSON.stringify(vue)};
  export const calls = [], centers = [], internals = [], captured = {};
  export const MarkerType = { ArrowClosed: 'arrow' };
  export const useVueFlow = () => ({ fitView: (...args) => { calls.push(args); }, updateNodeInternals(ids) { internals.push(ids); }, setCenter(...args) { centers.push(args); }, findNode(id) { return captured.attrs.nodes?.find(n => n.id === id); } });
  export const VueFlow = { inheritAttrs: false, setup(_, { attrs }) { captured.attrs = attrs; return () => h('div'); } };
`);
const stub = url('export default { render() { return null; } };');
const layoutUrl =
  url(`export const layoutState = { run: async diagram => new Map(diagram.nodes.map((n, i) => [n.id, { x: i * 346, y: 0 }])) };
 export const layoutArchitecture = diagram => layoutState.run(diagram);`);
const imports = {
  vue,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '@vue-flow/core': flow,
  '@vue-flow/background': url('export const Background = { render() { return null; } };'),
  '@vue-flow/controls': url('export const Controls = { render() { return null; } };'),
  '../../lib/layoutArchitecture': layoutUrl,
  '../../lib/graphGeometry': url(
    'export const routeArchitectureEdge = () => []; export const architectureLabels = () => [];',
  ),
  '../../lib/architectureLayout': url('export const architectureGroupBounds = () => null;'),
  '../../lib/architectureRoles': url('export const architectureRole = () => ({ color: "blue" });'),
  '../../composables/useAgent': url(
    'export const useAgent = () => ({ state: { follow: { P1: false }, pulse: 0 }, freeView() {} });',
  ),
  '../../composables/useWorkspace': url(
    'export const useWorkspace = () => ({ state: { project: { id: "P1" } } });',
  ),
  './ArchitectureNode.vue': stub,
  './ArchitectureGroup.vue': stub,
  '../graph/GraphEdge.vue': stub,
};
const code = readFileSync(
  new URL('../../frontend/src/components/design/DiagramView.vue', import.meta.url),
  'utf8',
);
const { descriptor } = parse(code);
const compiled = ts
  .transpileModule(compileScript(descriptor, { id: 'viewport', inlineTemplate: true }).content, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  .outputText.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected dependency ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
const DiagramView = (await import(url(compiled))).default;
const { calls, centers, internals, captured } = await import(flow);
const { layoutState } = await import(layoutUrl);
const element = (tag = 'root') => ({
  tag,
  children: [],
  parent: null,
  clientWidth: 800,
  clientHeight: 300,
});
const renderer = createRenderer({
  createElement: element,
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText(node, text) {
    node.text = text;
  },
  setElementText(node, text) {
    node.text = text;
  },
  patchProp(node, key, previous, value) {
    node[key] = value;
  },
  insert(node, parent) {
    node.parent = parent;
    parent.children.push(node);
  },
  remove(node) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
  },
  parentNode: (node) => node.parent,
  nextSibling: () => null,
});

async function harness() {
  calls.length = 0;
  centers.length = 0;
  internals.length = 0;
  layoutState.run = async (diagram) =>
    new Map(diagram.nodes.map((n, i) => [n.id, { x: i * 346, y: 0 }]));
  const originals = Object.fromEntries(
    ['requestAnimationFrame', 'cancelAnimationFrame', 'ResizeObserver'].map((key) => [
      key,
      globalThis[key],
    ]),
  );
  const frames = new Map();
  let frameId = 0,
    observer;
  globalThis.requestAnimationFrame = (callback) => {
    const id = ++frameId;
    frames.set(id, callback);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  globalThis.ResizeObserver = class {
    constructor(callback) {
      this.callback = callback;
      observer = this;
    }
    observe(node) {
      this.node = node;
      this.callback([{ contentRect: { width: node.clientWidth, height: node.clientHeight } }]);
    }
    disconnect() {
      this.disconnected = true;
    }
    resize(width, height) {
      this.node.clientWidth = width;
      this.node.clientHeight = height;
      this.callback([{ contentRect: { width, height } }]);
    }
  };
  const flush = async () => {
    for (let i = 0; i < 8; i++) {
      await nextTick();
      const callbacks = [...frames.values()];
      frames.clear();
      callbacks.forEach((callback) => callback());
    }
  };
  const props = reactive({
    focusedId: '',
    diagram: {
      id: 'a',
      nodes: [
        { id: 'one', label: 'One' },
        { id: 'two', label: 'Two' },
      ],
      edges: [],
      groups: [],
    },
  });
  const view = ref();
  const app = renderer.createApp({ render: () => h(DiagramView, { ...props, ref: view }) });
  app.mount(element());
  return {
    props,
    view,
    flush,
    frames,
    get observer() {
      return observer;
    },
    dispose() {
      app.unmount();
      for (const [key, value] of Object.entries(originals)) {
        if (value === undefined) delete globalThis[key];
        else globalThis[key] = value;
      }
    },
  };
}

test('architecture fits on entry with Agent-follow off, adapts to resizing, and preserves a user camera', async () => {
  const h = await harness();
  try {
    await h.flush();
    assert.ok(calls.length > 0, 'entry must fit without the Agent-follow flag');
    const initialFits = calls.length;
    h.observer.resize(800, 420);
    await h.flush();
    assert.ok(
      calls.length > initialFits,
      'composer/viewport resize should fit the untouched overview',
    );
    const fitted = calls.length;
    h.observer.resize(0, 0);
    h.observer.resize(800, 420);
    await h.flush();
    assert.equal(calls.length, fitted, 'Back to the same size should preserve the camera');
    captured.attrs.onMoveStart({ event: { type: 'wheel' } });
    h.observer.resize(700, 280);
    await h.flush();
    assert.equal(calls.length, fitted, 'manual camera should survive later resizes');
  } finally {
    h.dispose();
  }
  assert.equal(h.observer.disconnected, true);
  assert.equal(h.frames.size, 0);
});

test('rapid architecture selection is instant; manual input and explicit Fit supersede awaiting focus', async () => {
  const h = await harness();
  try {
    await h.flush();
    for (let i = 0; i < 20; i++) {
      h.props.focusedId = i % 2 ? 'one' : 'two';
      await h.flush();
    }
    assert.equal(centers.length, 20);
    assert.ok(centers.every(([, , options]) => options.duration === 0));
    assert.equal(centers.at(-1)[0], 118);
    h.props.focusedId = 'two';
    await nextTick();
    captured.attrs.onMoveStart({ event: { type: 'wheel' } });
    await h.flush();
    assert.equal(centers.length, 20);
    h.props.focusedId = 'one';
    await nextTick();
    h.view.value.fit();
    await h.flush();
    assert.equal(centers.length, 20);
    assert.deepEqual(calls.at(-1), [{ padding: 0.2, duration: 0 }]);
    h.props.focusedId = 'two';
    h.props.focusedId = '';
    await h.flush();
    assert.equal(centers.length, 20);
  } finally {
    h.dispose();
  }
});

test('unmounted architecture ignores delayed focus, layout success and layout failure', async () => {
  for (const rejectLayout of [false, true]) {
    const h = await harness();
    await h.flush();
    let resolve, reject;
    layoutState.run = () =>
      new Promise((yes, no) => {
        resolve = yes;
        reject = no;
      });
    h.props.diagram = { id: 'new', nodes: [{ id: 'new', label: 'New' }], edges: [], groups: [] };
    h.props.focusedId = 'two';
    await nextTick();
    h.dispose();
    const count = calls.length,
      focused = centers.length,
      updated = internals.length;
    if (rejectLayout) reject(new Error('obsolete failure'));
    else resolve(new Map([['new', { x: 1000, y: 0 }]]));
    await nextTick();
    await nextTick();
    assert.equal(calls.length, count);
    assert.equal(centers.length, focused);
    assert.equal(internals.length, updated);
    assert.equal(h.frames.size, 0);
  }
});
