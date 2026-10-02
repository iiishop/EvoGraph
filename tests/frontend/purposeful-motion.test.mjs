import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
});
for (const key of [
  'window',
  'document',
  'Document',
  'navigator',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'KeyboardEvent',
  'WheelEvent',
  'MutationObserver',
  'DOMRect',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
const mediaListeners = new Set();
const media = {
  matches: false,
  addEventListener(type, fn) {
    if (type === 'change') mediaListeners.add(fn);
  },
  removeEventListener(type, fn) {
    if (type === 'change') mediaListeners.delete(fn);
  },
  change(matches) {
    this.matches = matches;
    for (const fn of mediaListeners) fn({ matches });
  },
};
let finePointer = true;
globalThis.matchMedia = (query) => (query.includes('pointer') ? { matches: finePointer } : media);
dom.window.matchMedia = globalThis.matchMedia;
const size = (node) =>
  node.classList?.contains('vue-flow__node')
    ? { width: 236, height: 150 }
    : { width: 880, height: 310 };
for (const [property, dimension] of [
  ['clientWidth', 'width'],
  ['clientHeight', 'height'],
  ['offsetWidth', 'width'],
  ['offsetHeight', 'height'],
])
  Object.defineProperty(dom.window.HTMLElement.prototype, property, {
    configurable: true,
    get() {
      return size(this)[dimension];
    },
  });
dom.window.HTMLElement.prototype.getBoundingClientRect = function () {
  const { width, height } = size(this);
  return new DOMRect(0, 0, width, height);
};
// jsdom does not implement DOMMatrix; Vue Flow only reads the viewport scale.
dom.window.DOMMatrixReadOnly = class {
  constructor(transform) {
    this.m22 = Number(transform?.match(/scale\(([^)]+)\)/)?.[1] ?? 1);
  }
};
const ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
  }
  observe(node) {
    this.callback([{ target: node, contentRect: node.getBoundingClientRect() }]);
  }
  unobserve() {}
  disconnect() {}
};
globalThis.ResizeObserver = ResizeObserver;
dom.window.ResizeObserver = ResizeObserver;
const { createApp, h, nextTick, reactive } = await import('vue');
const vue = import.meta.resolve('vue');
const flow = import.meta.resolve('@vue-flow/core');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const empty = url('export default { render() { return null; } };');
const icons = import.meta.resolve('lucide-vue-next');
async function component(path, imports) {
  const { descriptor } = parse(source(path));
  const compiled = compile(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected import ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
  return (await import(url(compiled))).default;
}
const nodeUrl = url(
  compile(
    compileScript(parse(source('components/graph/MilestoneNode.vue')).descriptor, {
      id: 'motion-node',
      inlineTemplate: true,
    }).content,
  ).replace(
    /from (['"])([^'"]+)\1/g,
    (_, quote, name) =>
      `from ${JSON.stringify(
        {
          vue,
          '@vue-flow/core': flow,
          'lucide-vue-next': icons,
          '../ui/StatusBadge.vue': empty,
        }[name],
      )}`,
  ),
);
const envUrl = url(`import { reactive } from ${JSON.stringify(vue)};
export const env = { calls: [], flow: null };
export const workspace = { state: reactive({ selectedId: null }), selectNode(id) { workspace.state.selectedId = id; }, perform: async () => {} };
export const useWorkspace = () => workspace;
export const agent = { state: reactive({ follow: { P: false }, projectId: '', focusId: '', running: false, updates: {}, pulse: 0 }), freeView(id) { agent.state.follow[id] = false; } };
export const useAgent = () => agent;`);
const instrumentedFlow = url(`import { useVueFlow as useRealVueFlow } from ${JSON.stringify(flow)};
import { env } from ${JSON.stringify(envUrl)};
export { VueFlow, MarkerType } from ${JSON.stringify(flow)};
export function useVueFlow(id) {
 const store = useRealVueFlow(id); env.flow = store;
 return { ...store, setViewport(value, options) { env.calls.push([value, options]); return store.setViewport(value, options); } };
}`);
const Graph = await component('components/graph/MilestoneGraph.vue', {
  vue,
  '@vue-flow/core': instrumentedFlow,
  '@vue-flow/background': import.meta.resolve('@vue-flow/background'),
  '@vue-flow/controls': import.meta.resolve('@vue-flow/controls'),
  'lucide-vue-next': icons,
  '../../composables/useWorkspace': envUrl,
  '../../composables/useAgent': envUrl,
  '../../composables/useGraphLayout': url(`export const edgeId = (a, b) => a + ':' + b;
    export const layout = async milestones => ({ direction: 'RIGHT', positions: new Map(milestones.map((m,i) => [m.id, { x: (i % 8) * 346, y: Math.floor(i / 8) * 350 }])), routes: new Map() });`),
  '../../lib/milestoneInteraction': url(compile(source('lib/milestoneInteraction.ts'))),
  '../../lib/milestoneViewport': url(compile(source('lib/milestoneViewport.ts'))),
  '../../lib/graphGeometry': url(
    'export const separateBoxes = boxes => boxes; export const routeAroundBoxes = () => [];',
  ),
  '../../lib/edgeKinds': url(
    'export const edgeKinds = []; export const edgeKind = () => ({ color: "blue" });',
  ),
  './MilestoneNode.vue': nodeUrl,
  './GraphEdge.vue': empty,
  './BaselineMilestoneStatus.vue': empty,
  './GoalMarker.vue': empty,
});
const { env, workspace, agent } = await import(envUrl);
const settle = async (ms = 60) => {
  await nextTick();
  await new Promise((resolve) => setTimeout(resolve, ms));
  await nextTick();
};
const project = () => ({
  id: 'P',
  readiness: {},
  source_milestones: [],
  milestones: Array.from({ length: 64 }, (_, i) => ({
    id: `M${i + 1}`,
    title: `Milestone ${i + 1}`,
    dependencies: [],
    dependency_reasons: {},
    scope: ['src'],
    behavior_revision_ids: [],
    status: 'PLANNED',
    position: null,
  })),
});
function mount(view, props) {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(view, props),
    vm = app.mount(root);
  return {
    root,
    vm,
    dispose() {
      app.unmount();
      root.remove();
    },
  };
}

test('production graph and installed Vue Flow make twenty keyboard activations and Fit immediate', async () => {
  workspace.state.selectedId = null;
  env.calls.length = 0;
  media.matches = false;
  const view = mount(Graph, { project: project() });
  try {
    await settle(100);
    assert.ok(
      env.flow.viewportHelper.value.viewportInitialized,
      'the actual D3 viewport must be initialized',
    );
    env.calls.length = 0;
    for (let i = 1; i <= 20; i++) {
      const node = view.root.querySelector(`.vue-flow__node[data-id="M${i}"]`);
      assert.ok(node);
      node.focus();
      const event = new KeyboardEvent('keydown', {
        key: i % 2 ? 'Enter' : ' ',
        bubbles: true,
        cancelable: true,
      });
      node.dispatchEvent(event);
      await settle();
      assert.equal(event.defaultPrevented, true);
      assert.equal(workspace.state.selectedId, `M${i}`);
      assert.ok(node.classList.contains('selected'));
    }
    assert.ok(env.calls.length >= 20);
    assert.ok(env.calls.every(([, options]) => options.duration === 0));
    view.root.querySelector('[aria-label="适应画布"]').click();
    await settle();
    assert.equal(env.calls.at(-1)[1].duration, 0);
    assert.ok(env.flow.getViewport().zoom < 0.25);
  } finally {
    view.dispose();
  }
  assert.equal(mediaListeners.size, 0);
});

test('installed D3 Agent motion stops on a live reduced-motion change without finishing an old target', async () => {
  workspace.state.selectedId = null;
  agent.state.follow.P = false;
  env.calls.length = 0;
  media.matches = false;
  const view = mount(Graph, { project: project() });
  try {
    await settle(100);
    Object.assign(agent.state, {
      projectId: 'P',
      focusId: 'M64',
      running: true,
      follow: { P: true },
      pulse: agent.state.pulse + 1,
    });
    await settle(65);
    assert.equal(env.calls.at(-1)[1].duration, 200);
    const current = env.flow.getViewport();
    media.change(true);
    assert.deepEqual(env.calls.at(-1), [current, { duration: 0 }]);
    await settle(260);
    assert.deepEqual(
      env.flow.getViewport(),
      current,
      'actual D3 transform cannot resume after cancellation',
    );
    view.vm.locate('M2');
    await settle();
    assert.equal(env.calls.at(-1)[1].duration, 0);
  } finally {
    view.dispose();
    agent.state.follow.P = false;
    agent.state.running = false;
    media.matches = false;
  }
});

test('64-node update bursts retain the same factual markers and reset only at the next turn boundary', async () => {
  workspace.state.selectedId = null;
  Object.assign(agent.state, {
    projectId: 'P',
    focusId: 'M1',
    running: false,
    updates: {},
    follow: { P: false },
  });
  const view = mount(Graph, { project: project() });
  try {
    await settle(100);
    assert.equal(view.root.querySelectorAll('.node-update-marker').length, 0);
    assert.match(view.root.querySelector('.node-agent-label').textContent, /最近定位/);
    assert.doesNotMatch(view.root.textContent, /刚刚更新/);
    for (let i = 1; i <= 64; i++) agent.state.updates[`M${i}`] = 1;
    await settle();
    const markers = [...view.root.querySelectorAll('.node-update-marker')];
    assert.equal(markers.length, 64);
    for (let tick = 2; tick <= 10; tick++) {
      for (let i = 1; i <= 64; i++) agent.state.updates[`M${i}`] = tick;
      await nextTick();
    }
    assert.deepEqual([...view.root.querySelectorAll('.node-update-marker')], markers);
    assert.ok(
      markers.every(
        (marker) => marker.textContent === '本轮已改' && marker.title.includes('不代表已通过验收'),
      ),
    );
    assert.equal(view.root.querySelectorAll('.node-update-flash').length, 0);
    agent.state.running = true;
    await nextTick();
    assert.match(view.root.querySelector('.node-agent-label').textContent, /正在操作/);
    agent.state.updates = {};
    await settle();
    assert.equal(view.root.querySelectorAll('.node-update-marker').length, 0);
  } finally {
    view.dispose();
    agent.state.running = false;
    agent.state.projectId = '';
  }
});

test('frequent workspace/inspector reading and Send contain no recurring entrance or hover movement', () => {
  assert.doesNotMatch(source('components/workspace/ProjectWorkspace.vue'), /useEntrance/);
  const css = ['styles/agent.css', 'styles/studio.css', 'styles/spatial.css']
    .map(source)
    .join('\n');
  assert.doesNotMatch(css, /node-update-flash|node-updated|spatial-update|inspector-reveal/);
  const send = source('styles/studio.css').match(
    /\.send-button \{[\s\S]*?\.send-button:disabled/,
  )[0];
  assert.doesNotMatch(send, /transition:|translate|scale\(/);
});

const Picker = await component('components/attachments/AttachmentPicker.vue', {
  vue,
  'lucide-vue-next': icons,
  '../../composables/useWorkspace': envUrl,
  '../../composables/useAttachments': url(`import { ref } from ${JSON.stringify(vue)};
    export const useAttachments = () => ({ formats: ref({ extensions: ['.txt'] }), formatError: ref(''), loadFormats: async () => true, uploadBatch: async () => null });`),
  '../../composables/useAgentDrafts': url(`import { ref } from ${JSON.stringify(vue)};
    export const agentDrafts = {}; export const useAgentDraft = () => ({ confirmedAttachments: ref([]), attachmentTransfer: ref() });`),
  '../../composables/useNotifications': url(
    'export const useNotifications = () => ({ push() {} });',
  ),
});
function pickerHarness() {
  const animations = [];
  const original = dom.window.HTMLElement.prototype.animate;
  dom.window.HTMLElement.prototype.animate = function (frames, options) {
    const animation = {
      element: this,
      frames,
      options,
      cancelled: false,
      cancel() {
        this.cancelled = true;
      },
    };
    animations.push(animation);
    return animation;
  };
  media.matches = false;
  finePointer = true;
  const view = mount(Picker, { project: { id: 'P', name: 'Project', attachments: [] } });
  const outside = document.createElement('button');
  document.body.append(outside);
  const add = view.root.querySelector('.attachment-add');
  const pointer = () =>
    add.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, detail: 1 }));
  const escape = () =>
    view.root
      .querySelector('.attachment-tools-anchor')
      .dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      );
  const flush = async () => {
    await nextTick();
    await nextTick();
  };
  return {
    ...view,
    add,
    pointer,
    escape,
    outside,
    animations,
    flush,
    dispose() {
      view.dispose();
      outside.remove();
      if (original) dom.window.HTMLElement.prototype.animate = original;
      else delete dom.window.HTMLElement.prototype.animate;
      media.matches = false;
      finePointer = true;
    },
  };
}

test('pointer tools entrance stays anchored, cancellable and immediate for keyboard or coarse pointers', async () => {
  const p = pickerHarness();
  try {
    p.pointer();
    await p.flush();
    assert.equal(p.animations.length, 1);
    const first = p.animations[0];
    assert.deepEqual(first.frames, [
      { opacity: 0, transform: 'scale(0.97)' },
      { opacity: 1, transform: 'scale(1)' },
    ]);
    assert.deepEqual(first.options, { duration: 180, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' });
    assert.ok(p.root.querySelector('.attachment-tools').contains(document.activeElement));
    media.change(true);
    assert.equal(first.cancelled, true);
    assert.ok(p.root.querySelector('.attachment-tools'));
    p.escape();
    await p.flush();
    assert.equal(p.root.querySelector('.attachment-tools'), null);
    assert.equal(document.activeElement, p.add);
    media.change(false);
    p.add.click();
    await p.flush();
    assert.equal(p.animations.length, 1, 'detail=0 keyboard/assistive click must be instant');
    p.escape();
    await p.flush();
    finePointer = false;
    p.pointer();
    await p.flush();
    assert.equal(p.animations.length, 1, 'coarse pointer must be instant');
    p.escape();
    await p.flush();
    finePointer = true;
    media.change(true);
    p.pointer();
    await p.flush();
    assert.equal(p.animations.length, 1, 'reduced motion must not start a positional entrance');
  } finally {
    p.dispose();
  }
  assert.equal(mediaListeners.size, 0);
});

test('ten rapid tools dismiss/reopen cycles keep only the latest menu and focus; outside focus wins', async () => {
  const p = pickerHarness();
  try {
    for (let i = 0; i < 10; i++) {
      p.pointer();
      await p.flush();
      const entry = p.animations.at(-1);
      p.escape();
      p.pointer();
      await p.flush();
      assert.equal(entry.cancelled, true);
      assert.equal(p.root.querySelectorAll('.attachment-tools').length, 1);
      assert.ok(p.root.querySelector('.attachment-tools').contains(document.activeElement));
      p.escape();
      await p.flush();
      assert.equal(p.root.querySelectorAll('.attachment-tools button').length, 0);
      assert.equal(document.activeElement, p.add);
    }
    p.pointer();
    p.escape();
    await p.flush();
    assert.equal(
      p.root.querySelector('.attachment-tools'),
      null,
      'Escape can dismiss before entry starts',
    );
    p.pointer();
    await p.flush();
    p.escape();
    p.outside.focus();
    await p.flush();
    assert.equal(document.activeElement, p.outside, 'a stale close must not reclaim outside focus');
    p.pointer();
    p.outside.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true }));
    await p.flush();
    assert.equal(p.root.querySelector('.attachment-tools'), null);
    assert.equal(document.activeElement, p.outside);
  } finally {
    p.dispose();
  }
});

test('unmount cancels tools motion and pending open focus without leaking media listeners', async () => {
  const p = pickerHarness();
  p.pointer();
  await p.flush();
  const entry = p.animations.at(-1);
  p.escape();
  p.pointer();
  p.dispose();
  await p.flush();
  assert.equal(entry.cancelled, true);
  assert.equal(mediaListeners.size, 0);
  assert.equal(document.querySelector('.attachment-tools'), null);
});

test('a changed node with a long stable ID reserves its marker and retains the complete ID', async () => {
  const longId = 'PR2-architecture-component-with-a-very-long-stable-reference-0123456789';
  const fixture = project();
  fixture.milestones[0].id = longId;
  workspace.state.selectedId = longId;
  Object.assign(agent.state, {
    projectId: 'P',
    focusId: longId,
    running: false,
    updates: { [longId]: 3 },
    follow: { P: false },
  });
  const view = mount(Graph, { project: fixture });
  try {
    await settle(100);
    const card = view.root.querySelector(`.vue-flow__node[data-id="${longId}"] .milestone-node`);
    const id = card.querySelector('.node-id'),
      value = card.querySelector('.node-id-value');
    assert.equal(id.title, longId);
    assert.equal(value.textContent, longId);
    assert.equal(card.querySelector('.node-update-marker').textContent, '本轮已改');
    assert.equal(value.parentElement, id);
    // jsdom cannot measure flex overflow; assert the production containment
    // contract here, and leave actual narrow-node clipping to native QA.
    const css = source('styles/agent.css');
    assert.match(css, /\.milestone-node \.node-id\s*\{\s*min-width: 0;/);
    assert.match(
      css,
      /\.node-id-value\s*\{[^}]*min-width: 0;[^}]*overflow: hidden;[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/,
    );
    assert.match(css, /\.node-update-marker\s*\{\s*flex-shrink: 0;/);
  } finally {
    view.dispose();
    workspace.state.selectedId = null;
    agent.state.projectId = '';
  }
});
