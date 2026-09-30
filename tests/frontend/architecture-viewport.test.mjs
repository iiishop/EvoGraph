import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const vue = pathToFileURL(require.resolve('vue')).href;
const flow = url(`import { h } from ${JSON.stringify(vue)};
  export const calls = [], captured = {};
  export const MarkerType = { ArrowClosed: 'arrow' };
  export const useVueFlow = () => ({ fitView: (...args) => { calls.push(args); }, updateNodeInternals() {}, setCenter() {}, findNode() {} });
  export const VueFlow = { inheritAttrs: false, setup(_, { attrs }) { captured.attrs = attrs; return () => h('div'); } };
`);
const stub = url('export default { render() { return null; } };');
const imports = {
  vue,
  '@vue-flow/core': flow,
  '@vue-flow/background': url('export const Background = { render() { return null; } };'),
  '@vue-flow/controls': url('export const Controls = { render() { return null; } };'),
  '../../lib/layoutArchitecture': url(
    'export const layoutArchitecture = async (diagram) => new Map(diagram.nodes.map((n) => [n.id, { x: 0, y: 0 }]));',
  ),
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
const { calls, captured } = await import(flow);
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

test('architecture fits on entry with Agent-follow off, adapts to resizing, and preserves a user camera', async () => {
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
  const app = renderer.createApp(DiagramView, {
    diagram: { id: 'a', nodes: [{ id: 'one', label: 'One' }], edges: [], groups: [] },
  });
  try {
    app.mount(element());
    await flush();
    assert.ok(calls.length > 0, 'entry must fit without the Agent-follow flag');
    const initialFits = calls.length;
    observer.resize(800, 420);
    await flush();
    assert.ok(
      calls.length > initialFits,
      'composer/viewport resize should fit the untouched overview',
    );
    const fitted = calls.length;
    observer.resize(0, 0);
    observer.resize(800, 420);
    await flush();
    assert.equal(calls.length, fitted, 'Back to the same size should preserve the camera');
    captured.attrs.onMoveStart({ event: { type: 'wheel' } });
    observer.resize(700, 280);
    await flush();
    assert.equal(calls.length, fitted, 'manual camera should survive later resizes');
    app.unmount();
    assert.equal(observer.disconnected, true);
    assert.equal(frames.size, 0);
  } finally {
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
