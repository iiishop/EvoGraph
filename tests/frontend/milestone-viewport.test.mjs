import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick, reactive } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const vue = pathToFileURL(require.resolve('vue')).href;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const geometryUrl = url(compile(source('lib/milestoneViewport.ts')));
const { graphBounds, fitMilestoneBounds, keepMilestoneVisible } = await import(geometryUrl);
const boxes = (count) =>
  Array.from({ length: count }, (_, i) => ({
    id: `M${i + 1}`,
    x: (i % 8) * 346,
    y: Math.floor(i / 8) * 350,
    width: 236,
    height: 150,
  }));
function inside(box, viewport, size) {
  assert.ok(box.x * viewport.zoom + viewport.x >= -0.001);
  assert.ok(box.y * viewport.zoom + viewport.y >= -0.001);
  assert.ok((box.x + box.width) * viewport.zoom + viewport.x <= size.width + 0.001);
  assert.ok((box.y + box.height) * viewport.zoom + viewport.y <= size.height + 0.001);
}

test('overview fits all 36 and 64 nodes at wide and narrow sizes below the former 0.25 floor', () => {
  for (const count of [36, 64])
    for (const size of [
      { width: 880, height: 270 },
      { width: 280, height: 210 },
    ]) {
      const nodes = boxes(count);
      const viewport = fitMilestoneBounds(graphBounds(nodes), size);
      assert.ok(viewport.zoom > 0 && viewport.zoom < 0.25);
      nodes.forEach((node) => inside(node, viewport, size));
    }
  assert.equal(fitMilestoneBounds(null, { width: 300, height: 200 }), null);
  assert.equal(fitMilestoneBounds(boxes(1)[0], { width: 0, height: 0 }), null);
});

test('a located node is readable, and resize correction is minimal when it already fits', () => {
  const node = boxes(64).at(-1),
    size = { width: 320, height: 240 };
  const focused = fitMilestoneBounds(node, size, 0.95);
  assert.ok(focused.zoom >= 0.85);
  inside(node, focused, size);
  assert.deepEqual(keepMilestoneVisible(node, size, focused), focused);
  const smaller = { width: 250, height: 200 };
  const corrected = keepMilestoneVisible(node, smaller, focused);
  inside(node, corrected, smaller);
  assert.ok(corrected.zoom <= focused.zoom);
});

const element = (tag = 'root') => ({ tag, children: [], parent: null });
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
let harnessId = 0;
async function harness(count = 64, reduced = false, initial = {}) {
  const id = ++harnessId;
  const flowUrl = url(`import { ref, h } from ${JSON.stringify(vue)};
    export const env = { attrs: null, calls: [], dimensions: ref({ width: 880, height: 270 }), viewport: { x: 0, y: 0, zoom: 1 }, apply: async value => { env.viewport = value; return true; } };
    export const MarkerType = { ArrowClosed: 'arrow' };
    export const useVueFlow = () => ({ dimensions: env.dimensions, getViewport: () => ({ ...env.viewport }), setViewport: (value, options) => { env.calls.push([value, options]); return env.apply(value, options); } });
    export const VueFlow = { inheritAttrs: false, setup(_, { attrs, slots }) { env.attrs = attrs; return () => h('flow', {}, slots.default?.()); } }; // ${id}`);
  const workspaceUrl = url(`import { reactive } from ${JSON.stringify(vue)};
    export const workspace = { state: reactive({ selectedId: null }), selectNode(id) { workspace.state.selectedId = id; }, perform: async () => {} }; export const useWorkspace = () => workspace; // ${id}`);
  const agentUrl = url(`import { reactive } from ${JSON.stringify(vue)};
    export const agent = { state: reactive({ follow: { P1: false }, projectId: '', focusId: '', running: false, updates: {}, pulse: 0 }), freeView(id) { agent.state.follow[id] = false; } }; export const useAgent = () => agent; // ${id}`);
  const layoutUrl =
    url(`export const pending = { load: async (milestones) => ({ direction: 'RIGHT', positions: new Map(milestones.map((m, i) => [m.id, { x: (i % 8) * 346, y: Math.floor(i / 8) * 350 }])), routes: new Map() }) };
    export const edgeId = (a, b) => a + ':' + b; export const layout = (milestones) => pending.load(milestones); // ${id}`);
  const stub = url('export default { render() { return null; } };');
  const imports = {
    vue,
    '@vue-flow/core': flowUrl,
    '@vue-flow/background': url('export const Background = { render() { return null; } };'),
    '@vue-flow/controls': url(
      'export const Controls = { inheritAttrs: false, emits: ["zoomIn", "zoomOut"], setup(_, { slots }) { return () => slots["control-fit-view"]?.(); } };',
    ),
    'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
    '../../composables/useGraphLayout': layoutUrl,
    '../../composables/useWorkspace': workspaceUrl,
    '../../composables/useAgent': agentUrl,
    '../../lib/milestoneViewport': geometryUrl,
    '../../lib/graphGeometry': url(
      'export const separateBoxes = boxes => boxes; export const routeAroundBoxes = () => [];',
    ),
    '../../lib/edgeKinds': url(
      'export const edgeKinds = []; export const edgeKind = () => ({ color: "blue" });',
    ),
    './MilestoneNode.vue': stub,
    './GraphEdge.vue': stub,
    './BaselineMilestoneStatus.vue': stub,
    './GoalMarker.vue': stub,
  };
  const { descriptor } = parse(source('components/graph/MilestoneGraph.vue'));
  const code = compile(
    compileScript(descriptor, { id: 'milestone-viewport', inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, q, name) => {
    assert.ok(imports[name], name);
    return `from ${JSON.stringify(imports[name])}`;
  });
  const Graph = (await import(url(code))).default;
  const { env } = await import(flowUrl),
    { workspace } = await import(workspaceUrl),
    { agent } = await import(agentUrl),
    { pending } = await import(layoutUrl);
  const project = reactive({
    id: 'P1',
    readiness: {},
    source_milestones: [],
    milestones: boxes(count).map((b) => ({
      id: b.id,
      dependencies: [],
      dependency_reasons: {},
      scope: [],
      title: b.id,
      position: null,
    })),
  });
  const originals = Object.fromEntries(
    ['requestAnimationFrame', 'cancelAnimationFrame', 'matchMedia'].map((key) => [
      key,
      globalThis[key],
    ]),
  );
  const frames = new Map();
  let frameId = 0;
  globalThis.requestAnimationFrame = (callback) => {
    const id = ++frameId;
    frames.set(id, callback);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  globalThis.matchMedia = () => ({ matches: reduced });
  if (initial.selectedId) workspace.state.selectedId = initial.selectedId;
  if (initial.agent) Object.assign(agent.state, initial.agent);
  const app = renderer.createApp(Graph, { project });
  const root = element();
  const vm = app.mount(root);
  const flush = async () => {
    for (let i = 0; i < 8; i++) {
      await nextTick();
      const callbacks = [...frames.values()];
      frames.clear();
      callbacks.forEach((fn) => fn());
    }
  };
  return {
    env,
    workspace,
    agent,
    project,
    pending,
    vm,
    root,
    flush,
    frames,
    dispose: () => {
      app.unmount();
      for (const [key, value] of Object.entries(originals)) {
        if (value === undefined) delete globalThis[key];
        else globalThis[key] = value;
      }
    },
  };
}

test('real milestone graph fits all nodes and explicit selection centers after inspector/composer resize', async () => {
  const h = await harness();
  try {
    await h.flush();
    assert.ok(h.env.calls.length);
    assert.ok(h.env.attrs['min-zoom'] < 0.25);
    boxes(64).forEach((box) => inside(box, h.env.viewport, h.env.dimensions.value));
    h.workspace.selectNode('M64');
    h.env.dimensions.value = { width: 480, height: 310 };
    await h.flush();
    assert.ok(h.env.viewport.zoom >= 0.9);
    inside(boxes(64).at(-1), h.env.viewport, h.env.dimensions.value);
    h.env.dimensions.value = { width: 290, height: 210 };
    await h.flush();
    inside(boxes(64).at(-1), h.env.viewport, h.env.dimensions.value);
    const count = h.env.calls.length;
    h.env.dimensions.value = { width: 290, height: 210 };
    await h.flush();
    assert.equal(h.env.calls.length, count);
  } finally {
    h.dispose();
  }
});

test('manual pan cancels queued selection and later auto movement until an explicit same-node locate or Fit', async () => {
  const h = await harness();
  try {
    await h.flush();
    h.workspace.selectNode('M64');
    h.env.attrs.onMoveStart({ event: { type: 'pointerdown' } });
    const count = h.env.calls.length;
    h.env.dimensions.value = { width: 320, height: 210 };
    h.project.milestones[0].position = { x: 6000, y: 3000 };
    await h.flush();
    assert.equal(h.env.calls.length, count);
    assert.equal(h.vm.locate('M64'), true);
    await h.flush();
    assert.ok(h.env.calls.length > count);
    const focused = { ...h.env.viewport };
    h.env.attrs.onMoveStart({ event: { type: 'wheel' } });
    h.env.dimensions.value = { width: 400, height: 280 };
    await h.flush();
    assert.deepEqual(h.env.viewport, focused);
    h.vm.fit();
    await h.flush();
    assert.ok(h.env.viewport.zoom < 0.25);
  } finally {
    h.dispose();
  }
});

test('layout changes keep the selected node visible; removing it clears stale selection', async () => {
  const h = await harness();
  try {
    await h.flush();
    h.workspace.selectNode('M2');
    await h.flush();
    h.project.milestones[1].position = { x: 9000, y: 7000 };
    await h.flush();
    inside({ x: 9000, y: 7000, width: 236, height: 150 }, h.env.viewport, h.env.dimensions.value);
    h.project.milestones.splice(1, 1);
    await h.flush();
    assert.equal(h.workspace.state.selectedId, null);
    assert.equal(h.vm.locate('M2'), false);
  } finally {
    h.dispose();
  }
});

test('reduced motion makes every explicit camera transition immediate and unmount cancels pending frames/layout', async () => {
  const h = await harness(36, true);
  let resolve;
  try {
    await h.flush();
    h.workspace.selectNode('M30');
    await h.flush();
    h.vm.fit();
    await h.flush();
    Object.assign(h.agent.state, {
      projectId: 'P1',
      focusId: 'M2',
      follow: { P1: true },
      pulse: 1,
    });
    await h.flush();
    assert.ok(h.env.calls.every(([, options]) => options.duration === 0));
    h.pending.load = () =>
      new Promise((done) => {
        resolve = done;
      });
    h.project.milestones.push({
      id: 'new',
      dependencies: [],
      dependency_reasons: {},
      position: null,
    });
    await nextTick();
    h.vm.fit();
    const count = h.env.calls.length;
    h.dispose();
    assert.equal(h.frames.size, 0);
    resolve({ direction: 'RIGHT', positions: new Map(), routes: new Map() });
    await nextTick();
    assert.equal(h.env.calls.length, count);
  } catch (error) {
    h.dispose();
    throw error;
  }
});

test('graph remount honors an already-issued Resume Follow pulse ahead of an older selected node', async () => {
  const h = await harness(64, false, {
    selectedId: 'M2',
    agent: { projectId: 'P1', focusId: 'M60', running: true, follow: { P1: true }, pulse: 7 },
  });
  try {
    await h.flush();
    assert.equal(h.agent.state.follow.P1, true);
    assert.equal(h.workspace.state.selectedId, 'M2');
    assert.ok(h.env.viewport.zoom >= 0.9);
    inside(boxes(64)[59], h.env.viewport, h.env.dimensions.value);
  } finally {
    h.dispose();
  }
});

test('a focus issued before its node exists is honored after the corresponding layout arrives', async () => {
  const h = await harness(36);
  try {
    await h.flush();
    Object.assign(h.agent.state, {
      projectId: 'P1',
      focusId: 'new-focus',
      running: true,
      follow: { P1: true },
      pulse: 1,
    });
    await h.flush();
    h.project.milestones.push({
      id: 'new-focus',
      dependencies: [],
      dependency_reasons: {},
      position: null,
    });
    await h.flush();
    assert.equal(h.agent.state.follow.P1, true);
    assert.ok(h.env.viewport.zoom >= 0.9);
    inside(boxes(37)[36], h.env.viewport, h.env.dimensions.value);
  } finally {
    h.dispose();
  }
});

test('the narrow selected-node case uses the actual 220px inner flow, not the taller stage with its strips', () => {
  const node = boxes(64).at(-1);
  const innerFlow = { width: 280, height: 220 };
  const focused = fitMilestoneBounds(node, innerFlow, 0.95);
  assert.ok(focused.zoom >= 0.9);
  inside(node, focused, innerFlow);
  const workspace = source('components/workspace/ProjectWorkspace.vue');
  assert.match(
    workspace,
    /\.milestone-stage > \.graph-canvas > \.vue-flow\)\s*\{\s*min-height: 220px/,
  );
  assert.match(workspace, /min-height: 160px/);
  assert.match(workspace, /planningContent\.value\.scrollTop = 0/);
});

function deferredAnimations(env) {
  const animations = [];
  env.apply = (value, options) => {
    if (!options.duration) {
      env.viewport = value;
      return Promise.resolve(true);
    }
    return new Promise((resolve) => animations.push({ value, resolve }));
  };
  return animations;
}

test('resize during focus animation reaches readable zoom and stale completions cannot clear a newer focus intent', async () => {
  const h = await harness();
  try {
    await h.flush();
    const animations = deferredAnimations(h.env);
    h.workspace.selectNode('M64');
    await h.flush();
    assert.equal(animations.length, 1);
    h.env.viewport = { ...h.env.viewport, zoom: 0.12 };
    h.env.dimensions.value = { width: 480, height: 300 };
    await h.flush();
    assert.ok(h.env.viewport.zoom >= 0.9);
    inside(boxes(64)[63], h.env.viewport, h.env.dimensions.value);
    h.workspace.selectNode('M2');
    await h.flush();
    assert.equal(animations.length, 2);
    h.env.viewport = { ...h.env.viewport, zoom: 0.18 };
    animations[0].resolve(true);
    await nextTick();
    h.env.dimensions.value = { width: 400, height: 280 };
    await h.flush();
    assert.ok(h.env.viewport.zoom >= 0.9);
    inside(boxes(64)[1], h.env.viewport, h.env.dimensions.value);
    animations[1].resolve(true);
    await h.flush();
    inside(boxes(64)[1], h.env.viewport, h.env.dimensions.value);
  } finally {
    h.dispose();
  }
});

test('a real manual gesture interrupts an in-flight animation and owns later resize/layout changes', async () => {
  const h = await harness();
  try {
    await h.flush();
    const animations = deferredAnimations(h.env);
    h.workspace.selectNode('M64');
    await h.flush();
    h.env.viewport = { x: -340, y: -160, zoom: 0.2 };
    h.env.attrs.onMoveStart({ event: { type: 'pointerdown' } });
    const manualViewport = { ...h.env.viewport };
    const count = h.env.calls.length;
    assert.deepEqual(h.env.calls.at(-1), [manualViewport, { duration: 0 }]);
    animations[0].resolve(true);
    await nextTick();
    h.env.dimensions.value = { width: 400, height: 250 };
    h.project.milestones[0].position = { x: 8000, y: 5000 };
    await h.flush();
    assert.equal(h.env.calls.length, count);
    assert.deepEqual(h.env.viewport, manualViewport);
    assert.equal(h.agent.state.follow.P1, false);
  } finally {
    h.dispose();
  }
});

for (const mode of ['selected', 'agent']) {
  test(`${mode} readable focus recovers after temporary question/composer shrink`, async () => {
    const h = await harness();
    try {
      await h.flush();
      if (mode === 'selected') h.workspace.selectNode('M64');
      else {
        Object.assign(h.agent.state, { projectId: 'P1', focusId: 'M64', pulse: 1 });
        h.agent.state.follow.P1 = true;
      }
      await h.flush();
      assert.ok(h.env.viewport.zoom >= 0.9);
      h.env.dimensions.value = { width: 320, height: 110 };
      await h.flush();
      assert.ok(h.env.viewport.zoom < 0.5);
      inside(boxes(64)[63], h.env.viewport, h.env.dimensions.value);
      const limited = h.env.viewport.zoom;
      h.env.dimensions.value = { width: 320, height: 90 };
      await h.flush();
      assert.ok(
        h.env.viewport.zoom <= limited,
        'still-small viewport must keep fitting rather than force 0.95',
      );
      h.env.dimensions.value = { width: 440, height: 300 };
      await h.flush();
      assert.equal(h.env.viewport.zoom, 0.95);
      inside(boxes(64)[63], h.env.viewport, h.env.dimensions.value);
      assert.equal(h.env.calls.at(-1)[1].duration, 0);
    } finally {
      h.dispose();
    }
  });
}

test('manual zoom after a shrink stays owned by the user when the question closes', async () => {
  const h = await harness();
  try {
    await h.flush();
    h.workspace.selectNode('M64');
    await h.flush();
    h.env.dimensions.value = { width: 320, height: 110 };
    await h.flush();
    h.env.attrs.onMoveStart({ event: { type: 'wheel' } });
    const viewport = { ...h.env.viewport },
      calls = h.env.calls.length;
    h.env.dimensions.value = { width: 440, height: 300 };
    await h.flush();
    assert.deepEqual(h.env.viewport, viewport);
    assert.equal(h.env.calls.length, calls);
    h.vm.locate('M64');
    await h.flush();
    assert.equal(h.env.viewport.zoom, 0.95);
  } finally {
    h.dispose();
  }
});

test('late focus-animation completion cannot suppress later shrink-to-grow recovery', async () => {
  const h = await harness();
  try {
    await h.flush();
    const animations = deferredAnimations(h.env);
    h.workspace.selectNode('M64');
    await h.flush();
    assert.equal(animations.length, 1);
    h.env.dimensions.value = { width: 320, height: 110 };
    await h.flush();
    assert.ok(h.env.viewport.zoom < 0.5);
    h.env.dimensions.value = { width: 440, height: 300 };
    await h.flush();
    assert.equal(h.env.viewport.zoom, 0.95);
    animations[0].resolve(true);
    await h.flush();
    h.env.dimensions.value = { width: 320, height: 90 };
    await h.flush();
    assert.ok(h.env.viewport.zoom < 0.5);
    h.env.dimensions.value = { width: 440, height: 300 };
    await h.flush();
    assert.equal(h.env.viewport.zoom, 0.95);
    inside(boxes(64)[63], h.env.viewport, h.env.dimensions.value);
  } finally {
    h.dispose();
  }
});
