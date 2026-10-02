import { browseImports } from './helpers/architecture-fixtures.mjs';
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
  export const calls = [], fittedBounds = [], centers = [], internals = [], captured = {}, ready = { value: true };
  export const MarkerType = { ArrowClosed: 'arrow' };
  export const useVueFlow = () => ({ fitBounds: (bounds, options) => { fittedBounds.push(bounds); calls.push([options]); return Promise.resolve(ready.value); }, fitView: (...args) => { calls.push(args); return Promise.resolve(ready.value); }, updateNodeInternals(ids) { internals.push(ids); }, setCenter(...args) { centers.push(args); return Promise.resolve(ready.value); }, findNode(id) { return captured.attrs.nodes?.find(n => n.id === id); } });
  export const VueFlow = { inheritAttrs: false, setup(_, { attrs }) { captured.attrs = attrs; return () => h('div'); } };
`);
const stub = url('export default { render() { return null; } };');
const layoutUrl =
  url(`export const layoutState = { run: async diagram => new Map(diagram.nodes.map((n, i) => [n.id, { x: i * 346, y: 0 }])) };
 export const layoutArchitecture = diagram => layoutState.run(diagram);`);
const imports = {
  ...browseImports,
  vue,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '@vue-flow/core': flow,
  '@vue-flow/background': url('export const Background = { render() { return null; } };'),
  '@vue-flow/controls': url('export const Controls = { render() { return null; } };'),
  '../../lib/layoutArchitecture': layoutUrl,
  '../../lib/architectureRouting': url(
    ts.transpileModule(
      readFileSync(
        new URL('../../frontend/src/lib/architectureRouting.ts', import.meta.url),
        'utf8',
      ),
      { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
    ).outputText,
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
  './ArchitectureEdge.vue': stub,
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
const { calls, fittedBounds, centers, internals, captured, ready } = await import(flow);
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

async function harness(initial = {}, initialLayout) {
  calls.length = 0;
  fittedBounds.length = 0;
  ready.value = true;
  centers.length = 0;
  internals.length = 0;
  layoutState.run =
    initialLayout ??
    (async (diagram) => new Map(diagram.nodes.map((n, i) => [n.id, { x: i * 346, y: 0 }])));
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
    ...initial,
  });
  const view = ref();
  const saved = [];
  const app = renderer.createApp({
    render: () => h(DiagramView, { ...props, ref: view, onViewport: (value) => saved.push(value) }),
  });
  app.mount(element());
  return {
    props,
    view,
    saved,
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

test('architecture establishes a one-time entry camera with Agent-follow off and preserves it on resize', async () => {
  const h = await harness();
  try {
    await h.flush();
    assert.ok(calls.length > 0, 'entry must fit without the Agent-follow flag');
    const initialFits = calls.length;
    h.observer.resize(800, 420);
    await h.flush();
    assert.equal(calls.length, initialFits, 'a settled entry camera must not restart on resize');
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

test('a remembered architecture viewport starts in place, survives resizing and does not chase later save echoes', async () => {
  const camera = { x: -250, y: 75, zoom: 0.8 };
  const h = await harness({ browseKey: 'P1:source', initialViewport: camera, focusedId: 'one' });
  try {
    await h.flush();
    assert.deepEqual(captured.attrs['default-viewport'], camera);
    assert.equal(calls.length, 0);
    assert.equal(centers.length, 0);
    h.observer.resize(700, 240);
    await h.flush();
    assert.equal(calls.length, 0);
    const updated = { x: -500, y: 30, zoom: 0.6 };
    captured.attrs.onViewportChange(updated);
    assert.deepEqual(h.saved, [{ key: 'P1:source', viewport: updated }]);
    h.props.initialViewport = updated;
    await h.flush();
    assert.deepEqual(captured.attrs['default-viewport'], camera);
    assert.equal(calls.length, 0);
    h.view.value.locate('one');
    await h.flush();
    assert.equal(centers.length, 1);
    assert.equal(centers[0][2].duration, 0);
    const late = captured.attrs.onViewportChange;
    h.dispose();
    late(camera);
    assert.equal(h.saved.length, 1);
  } finally {
    if (!h.observer.disconnected) h.dispose();
  }
});

test('initial selection without a camera waits for layout; a selection during pending layout is applied only to current nodes', async () => {
  const h = await harness({ focusedId: 'two' });
  try {
    await h.flush();
    assert.equal(centers.length, 1);
    assert.equal(centers[0][0], 464);
    let resolve;
    layoutState.run = () =>
      new Promise((yes) => {
        resolve = yes;
      });
    h.props.diagram = {
      id: 'replacement',
      nodes: [{ id: 'new', label: 'New' }],
      edges: [],
      groups: [],
    };
    await nextTick();
    h.props.focusedId = 'new';
    await h.flush();
    assert.equal(centers.length, 1);
    resolve(new Map([['new', { x: 800, y: 100 }]]));
    await h.flush();
    assert.equal(centers.length, 2);
    assert.equal(centers.at(-1)[0], 918);
    h.props.query = 'no match';
    h.props.role = 'database';
    await h.flush();
    assert.equal(captured.attrs.nodes.length, 1, 'search must not delete graph nodes');
    assert.equal(captured.attrs.nodes[0].data.dimmed, true);
    assert.equal(centers.length, 2, 'search must not move the camera');
  } finally {
    h.dispose();
  }
});

test('manual movement interrupts selection waiting for a new layout', async () => {
  const h = await harness();
  try {
    await h.flush();
    let resolve;
    layoutState.run = () =>
      new Promise((yes) => {
        resolve = yes;
      });
    h.props.diagram = {
      id: 'pending',
      nodes: [{ id: 'new', label: 'New' }],
      edges: [],
      groups: [],
    };
    await nextTick();
    h.props.focusedId = 'new';
    await h.flush();
    captured.attrs.onMoveStart({ event: { type: 'wheel' } });
    resolve(new Map([['new', { x: 800, y: 100 }]]));
    await h.flush();
    assert.equal(centers.length, 0);
  } finally {
    h.dispose();
  }
});

test('large fresh architecture anchors an actual component readably once; explicit Fit still shows all', async () => {
  const h = await harness({
    diagram: {
      id: 'large',
      nodes: Array.from({ length: 12 }, (_, i) => ({ id: `n${i}`, label: `Node ${i}` })),
      edges: [],
      groups: [],
    },
  });
  try {
    await h.flush();
    assert.equal(calls.length, 0);
    assert.deepEqual(centers, [[118, 75, { zoom: 0.85, duration: 0 }]]);
    h.observer.resize(650, 250);
    h.props.diagram = { ...h.props.diagram, title: 'Accepted snapshot' };
    await h.flush();
    assert.equal(centers.length, 1, 'resizing and accepted updates never re-anchor');
    h.view.value.fit();
    assert.deepEqual(calls.at(-1), [{ padding: 0.2, duration: 0 }]);
    assert.equal(centers.length, 1);
  } finally {
    h.dispose();
  }
});

test('manual input before the fresh entry camera applies suppresses the reading anchor', async () => {
  const h = await harness();
  try {
    // The asynchronous route plan mounts the flow before its entry-camera frames.
    await nextTick();
    await nextTick();
    captured.attrs.onMoveStart({ event: { type: 'pointerdown' } });
    await h.flush();
    assert.equal(centers.length, 0);
    assert.equal(calls.length, 0);
  } finally {
    h.dispose();
  }
});

test('an unready viewport does not settle the automatic camera before nodes become ready', async () => {
  for (const count of [2, 12]) {
    const h = await harness({
      diagram: {
        id: 'readiness',
        nodes: Array.from({ length: count }, (_, i) => ({ id: `n${i}`, label: `Node ${i}` })),
        edges: [],
        groups: [],
      },
    });
    try {
      ready.value = false;
      await h.flush();
      const attempts = calls.length + centers.length;
      assert.ok(attempts > 0);
      ready.value = true;
      captured.attrs.onNodesInitialized();
      await h.flush();
      assert.ok(
        calls.length + centers.length > attempts,
        'initialization retries the failed camera',
      );
      const settled = calls.length + centers.length;
      h.observer.resize(700, 260);
      await h.flush();
      assert.equal(calls.length + centers.length, settled);
    } finally {
      h.dispose();
    }
  }
});

test('Fit during the first route plan waits for complete geometry; a newer selection owns the camera', async () => {
  for (const selectAfterFit of [false, true]) {
    let resolve;
    const h = await harness(
      {
        diagram: {
          id: 'first',
          nodes: [
            { id: 'one', label: 'One' },
            { id: 'two', label: 'Two' },
          ],
          edges: [{ source: 'one', target: 'two', label: 'A long first relation' }],
          groups: [],
        },
      },
      () =>
        new Promise((yes) => {
          resolve = yes;
        }),
    );
    try {
      assert.doesNotThrow(() => h.view.value.fit());
      assert.equal(calls.length, 0);
      if (selectAfterFit) h.props.focusedId = 'two';
      resolve(
        new Map([
          ['one', { x: 0, y: 0 }],
          ['two', { x: 386, y: 0 }],
        ]),
      );
      await h.flush();
      assert.equal(calls.length, selectAfterFit ? 0 : 1);
      assert.equal(centers.length, selectAfterFit ? 1 : 0);
      if (!selectAfterFit) assert.ok(fittedBounds[0].width >= 622);
    } finally {
      h.dispose();
    }
  }
});

test('selected incident relations highlight without removing edges or recomputing their routes', async () => {
  const h = await harness({
    diagram: {
      id: 'focus',
      nodes: [
        { id: 'one', label: 'One' },
        { id: 'two', label: 'Two' },
        { id: 'three', label: 'Three' },
      ],
      edges: [
        { source: 'one', target: 'two', label: 'First relation' },
        { source: 'two', target: 'three', label: 'Second relation' },
      ],
      groups: [],
    },
  });
  try {
    await h.flush();
    const before = captured.attrs.edges.map((e) => e.data.route);
    h.props.focusedId = 'one';
    await h.flush();
    assert.equal(captured.attrs.edges.length, 2);
    assert.equal(captured.attrs.edges[0].style.opacity, 1);
    assert.equal(captured.attrs.edges[1].style.opacity, 0.16);
    assert.deepEqual(
      captured.attrs.edges.map((e) => e.data.route),
      before,
    );
    assert.equal(captured.attrs.edges[0].ariaLabel, 'One → Two：First relation');
  } finally {
    h.dispose();
  }
});
