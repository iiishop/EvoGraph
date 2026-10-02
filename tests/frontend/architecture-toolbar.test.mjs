import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import {
  moduleUrl,
  source,
  compile,
  vueUrl,
  rolesUrl,
  browseImports,
  browseStoreUrl,
} from './helpers/architecture-fixtures.mjs';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
for (const key of [
  'window',
  'document',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'Event',
  'MouseEvent',
  'KeyboardEvent',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.ResizeObserver = class {
  observe() {}
  disconnect() {}
};
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
const { createApp, h, nextTick, ref } = await import('vue');
const require = createRequire(import.meta.url);
const graphUrl = moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
export const graph = { fits: 0, mounts: 0, attrs: null };
export default { inheritAttrs: false, setup(_, { attrs, expose }) {
  graph.mounts++; graph.attrs = attrs;
  expose({ fit() { graph.fits++; }, locate() {} });
  return () => h('button', { class: 'test-graph', type: 'button' }, 'Graph');
} };`);
// This suite exercises presentation only. No API client or extractor is imported or called.
const scopeUrl = moduleUrl(`import { ref } from ${JSON.stringify(vueUrl)};
export const MAX_DETAIL_COMPONENTS = 3, MAX_DETAIL_FILES = 12;
export const matchingScopedDesigns = () => [], classDetailGuidance = () => '';
export let state;
export const useScopedClassDetail = () => {
  const componentIds = ref([]), open = ref(false), loading = ref(false);
  state = { componentIds, open, loading, result: ref(null), error: ref(''), filePaths: ref(null),
    toggle(id) { componentIds.value = componentIds.value.includes(id) ? componentIds.value.filter(x => x !== id) : [...componentIds.value, id]; },
    select(ids) { componentIds.value = ids; return true; }, selectFiles() {}, toggleFile() {},
    show() { open.value = true; loading.value = true; }, close() { open.value = false; loading.value = false; },
    reload() {}, showSaved() { open.value = true; },
  }; return state;
};`);
const stub = moduleUrl('export default { render() { return null; } };');
const passport = moduleUrl(
  `import { h } from ${JSON.stringify(vueUrl)}; export default { setup(_, { slots }) { return () => h('aside', { class: 'test-passport' }, slots['scope-action']?.()); } };`,
);
const imports = {
  ...browseImports,
  vue: vueUrl,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../lib/architectureRoles': rolesUrl,
  '../../composables/useScopedClassDetail': scopeUrl,
  '../../composables/useAgent': moduleUrl(
    'export const useAgent = () => ({ state: { follow: {}, projectId: "", view: "", navigationTick: 0 }, freeView() {} });',
  ),
  './DiagramView.vue': graphUrl,
  './DiagramImage.vue': stub,
  './ArchitectureQuality.vue': stub,
  './ComponentPassport.vue': passport,
  './UmlView.vue': stub,
};
function component(path) {
  const { descriptor } = parse(source(path));
  return moduleUrl(
    compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, q, name) => {
        assert.ok(imports[name], `Unexpected dependency ${name}`);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['./ArchitectureBrowser.vue'] = component('components/design/ArchitectureBrowser.vue');
const Panel = (await import(component('components/design/ArchitecturePanel.vue'))).default;
const { graph } = await import(graphUrl);
const scope = await import(scopeUrl);
const { architectureBrowse } = await import(browseStoreUrl);
const tick = async () => {
  await nextTick();
  await nextTick();
};
const diagram = {
  id: 'architecture',
  title: 'Architecture',
  groups: [],
  nodes: [
    {
      id: 'api',
      label: 'OrdersAPI',
      description: 'Order intake',
      role: 'backend',
      source_refs: ['src/orders.ts'],
    },
    {
      id: 'db',
      label: 'OrdersDB',
      description: 'Order storage',
      role: 'database',
      source_refs: ['src/db.ts'],
    },
  ],
  edges: [{ source: 'api', target: 'db', label: 'Storage' }],
};
let sequence = 0;
async function harness() {
  const id = `toolbar-${++sequence}`;
  const project = ref({
    id,
    created_at: 'first',
    source_fingerprint: 'fp1',
    baselines: [{ id: 'B1' }],
    source_diagram: diagram,
    source_summary: '',
    research: [],
    uml_diagrams: [],
    attachments: [],
    milestones: [],
    source_milestones: [],
    architectures: [1, 2].map((number) => ({
      number,
      summary: `Version ${number}`,
      diagram,
      technologies: [],
      decisions: [],
      research_ids: [],
    })),
  });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(Panel, { project: project.value }) });
  app.mount(root);
  await tick();
  const q = (selector) => root.querySelector(selector);
  const click = async (selector) => {
    const target = typeof selector === 'string' ? q(selector) : selector;
    assert.ok(target, `Missing ${selector}`);
    target.click();
    await tick();
  };
  return {
    root,
    q,
    project,
    click,
    dispose() {
      app.unmount();
      root.remove();
      architectureBrowse.discard(id);
    },
  };
}
const key = (target, value, extra = {}) =>
  target.dispatchEvent(
    new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...extra }),
  );
const change = async (target, value) => {
  target.value = value;
  target.dispatchEvent(new Event('change', { bubbles: true }));
  await tick();
};
const visible = (target) => !target.closest('[style*="display: none"]');

test('default toolbar has two rows, hidden inert disclosures/scope, immediate search and explicit graph actions', async () => {
  const t = await harness();
  try {
    assert.deepEqual(
      [...t.q('.architecture-toolbar').children].filter(visible).map((n) => n.className),
      ['architecture-heading', 'architecture-tools'],
    );
    assert.equal(t.q('.class-scope').style.display, 'none');
    assert.ok(t.q('.class-scope').hasAttribute('inert'));
    assert.ok(t.q('.toolbar-disclosure').hasAttribute('inert'));
    assert.equal(visible(t.q('[aria-label="搜索架构组件"]')), true);
    assert.equal(
      t.q('.architecture-heading .architecture-tabs').textContent.replace(/\s+/g, ' ').trim(),
      '目标架构SRC 源码现状',
    );
    assert.match(t.q('.architecture-browse-status').textContent, /2 组件.*1 关系/);
    const fits = graph.fits;
    await t.click('[aria-label="适应架构画布"]');
    assert.equal(graph.fits, fits + 1);
  } finally {
    t.dispose();
  }
});

test('filter, history, legend and scope disclosures preserve graph instance and camera; active filter identity survives closing', async () => {
  const t = await harness();
  try {
    const mounts = graph.mounts,
      fits = graph.fits;
    await t.click('[data-disclosure-trigger="filters"]');
    assert.equal(document.activeElement, t.q('[aria-label="组件类型"]'));
    assert.equal(t.q('.toolbar-disclosure').hasAttribute('inert'), false);
    assert.equal(t.q('.architecture-filter-fields').hasAttribute('inert'), false);
    await change(t.q('[aria-label="组件类型"]'), 'backend');
    await change(t.q('[aria-label="选择架构组件"]'), 'api');
    await change(t.q('[aria-label="关系范围"]'), 'downstream');
    await t.click('[aria-label="关闭架构工具面板"]');
    assert.equal(document.activeElement, t.q('[data-disclosure-trigger="filters"]'));
    assert.match(t.q('.architecture-selected').textContent, /已定位 OrdersAPI/);
    assert.match(t.q('.filter-summary').textContent, /服务.*下游关系/);
    assert.equal(t.q('.filter-count').textContent, '3');
    assert.equal(graph.attrs['focused-id'], 'api');
    for (const kind of ['legend', 'history']) {
      await t.click(`[data-disclosure-trigger="${kind}"]`);
      await t.click('[aria-label="关闭架构工具面板"]');
    }
    await t.click('.architecture-scope-toggle');
    await t.click('.architecture-scope-toggle');
    assert.equal(graph.fits, fits);
    assert.equal(graph.mounts, mounts);
    await t.click('.architecture-show-all');
    assert.equal(graph.attrs['focused-id'], '');
    assert.equal(graph.attrs.role, 'all');
    assert.equal(graph.attrs.relation, 'all');
    assert.equal(graph.fits, fits + 1);
  } finally {
    t.dispose();
  }
});

test('Escape, Tab focus departure and outside click dismiss accessibly without capturing native select or search keyboard/IME', async () => {
  const t = await harness();
  try {
    const filter = t.q('[data-disclosure-trigger="filters"]');
    await t.click(filter);
    const select = t.q('[aria-label="组件类型"]');
    const nativeHandled = new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    });
    nativeHandled.preventDefault();
    select.dispatchEvent(nativeHandled);
    await tick();
    assert.equal(
      filter.getAttribute('aria-expanded'),
      'true',
      'already-handled native Escape stays local',
    );
    key(select, 'Escape');
    await tick();
    assert.equal(filter.getAttribute('aria-expanded'), 'false', 'closed select Escape dismisses');
    assert.equal(document.activeElement, filter);
    await t.click(filter);
    t.q('[aria-label="关闭架构工具面板"]').focus();
    key(document.activeElement, 'Escape', { isComposing: true });
    await tick();
    assert.equal(filter.getAttribute('aria-expanded'), 'true');
    key(document.activeElement, 'Escape');
    await tick();
    assert.equal(filter.getAttribute('aria-expanded'), 'false');
    assert.equal(document.activeElement, filter);
    await t.click(filter);
    t.q('.test-graph').focus();
    await tick();
    assert.equal(filter.getAttribute('aria-expanded'), 'false');
    assert.equal(document.activeElement, t.q('.test-graph'));
    await t.click(filter);
    t.q('.test-graph').dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await tick();
    assert.equal(filter.getAttribute('aria-expanded'), 'false');
    const search = t.q('[aria-label="搜索架构组件"]');
    search.focus();
    search.value = 'orders';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    await tick();
    key(search, 'Enter', { isComposing: true });
    await tick();
    assert.equal(graph.attrs['focused-id'], '');
    key(search, 'Enter', { keyCode: 229 });
    await tick();
    assert.equal(graph.attrs['focused-id'], '');
    key(search, 'Escape');
    await tick();
    assert.equal(search.value, 'orders');
    assert.equal(search.getAttribute('aria-expanded'), 'false');
    assert.equal(document.activeElement, search);
    key(search, 'ArrowDown');
    key(search, 'Enter');
    await tick();
    assert.equal(graph.attrs['focused-id'], 'api');
  } finally {
    t.dispose();
  }
});

test('queued opening, Escape and Close focus cannot override a newer interaction', async () => {
  const t = await harness();
  try {
    const filter = t.q('[data-disclosure-trigger="filters"]');
    filter.click();
    t.q('[aria-label="选择架构组件"]').focus();
    await tick();
    assert.equal(document.activeElement, t.q('[aria-label="选择架构组件"]'));
    await t.click('[aria-label="关闭架构工具面板"]');
    await t.click(filter);
    key(document.activeElement, 'Escape');
    t.q('.test-graph').focus();
    await tick();
    assert.equal(document.activeElement, t.q('.test-graph'));
    await t.click(filter);
    t.q('[aria-label="关闭架构工具面板"]').click();
    t.q('[aria-label="适应架构画布"]').focus();
    await tick();
    assert.equal(document.activeElement, t.q('[aria-label="适应架构画布"]'));
    await t.click(filter);
    key(document.activeElement, 'Escape');
    t.q('.test-graph').dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await tick();
    assert.notEqual(document.activeElement, filter);
  } finally {
    t.dispose();
  }
});

test('historical identity and return remain visible after version picker closes; SRC identity remains immediate', async () => {
  const t = await harness();
  try {
    await t.click('[data-disclosure-trigger="history"]');
    await t.click(t.q('.version-track button'));
    assert.equal(t.q('.toolbar-disclosure').style.display, 'none');
    assert.match(t.q('.history-identity').textContent, /历史 A1.*回到目标架构/);
    assert.equal(document.activeElement, t.q('[data-disclosure-trigger="history"]'));
    await t.click('.history-identity button');
    assert.equal(t.q('.architecture-revision').textContent, 'A2');
    await t.click([...t.root.querySelectorAll('.architecture-tabs button')][1]);
    assert.equal(
      [...t.root.querySelectorAll('.architecture-tabs button')][1].getAttribute('aria-pressed'),
      'true',
    );
    assert.equal(t.q('.architecture-revision'), null);
  } finally {
    t.dispose();
  }
});

test('scope presentation preserves selections while closed, passport entry reopens it, and return focuses the visible entry', async () => {
  const t = await harness();
  try {
    await t.click('.architecture-scope-toggle');
    await t.click('.class-scope-options input');
    assert.match(t.q('.architecture-scope-toggle').textContent, /1\/3/);
    await t.click('.architecture-scope-toggle');
    assert.equal(visible(t.q('.class-scope')), false);
    assert.ok(t.q('.class-scope').hasAttribute('inert'));
    // Simulate an already-pending detail opening while the strip is hidden. No real detail API runs.
    scope.state.open.value = true;
    scope.state.loading.value = true;
    await tick();
    await t.click('[aria-label="关闭局部类结构"]');
    assert.equal(document.activeElement, t.q('.architecture-scope-toggle'));
    assert.equal(visible(document.activeElement), true);
    await t.click('[data-disclosure-trigger="filters"]');
    await change(t.q('[aria-label="选择架构组件"]'), 'db');
    await t.click('[aria-label="关闭架构工具面板"]');
    await t.click('.passport-scope-action');
    assert.equal(visible(t.q('.class-scope')), true);
    assert.equal(t.q('.class-scope').hasAttribute('inert'), false);
    assert.match(t.q('.architecture-scope-toggle').textContent, /2\/3/);
    await t.click('.class-detail-trigger');
    await t.click('[aria-label="关闭局部类结构"]');
    assert.equal(document.activeElement, t.q('.class-detail-trigger'));
  } finally {
    t.dispose();
  }
});

test('queued disclosure focus cannot jump after project navigation or unmount', async () => {
  const t = await harness();
  const outside = document.createElement('button');
  document.body.append(outside);
  try {
    t.q('[data-disclosure-trigger="filters"]').click();
    t.project.value = { ...t.project.value, id: 'toolbar-new-project' };
    outside.focus();
    await tick();
    assert.equal(document.activeElement, outside);
    t.q('[data-disclosure-trigger="legend"]').click();
    t.dispose();
    outside.focus();
    await tick();
    assert.equal(document.activeElement, outside);
    architectureBrowse.discard('toolbar-new-project');
  } finally {
    outside.remove();
    if (t.root.isConnected) t.dispose();
  }
});
