import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc';
import ts from 'typescript';
import { surfaceMotionUrl } from './helpers/surface-motion-fixtures.mjs';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
  url: 'http://localhost/',
});
for (const name of [
  'window',
  'document',
  'Element',
  'HTMLElement',
  'SVGElement',
  'Node',
  'localStorage',
])
  Object.defineProperty(globalThis, name, { configurable: true, value: dom.window[name] });
const vue = import.meta.resolve('vue');
const { createApp, nextTick, h, ref } = await import('vue');
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const listeners = new Set();
const media = {
  matches: false,
  addEventListener(type, fn) {
    listeners.add(fn);
  },
  removeEventListener(type, fn) {
    listeners.delete(fn);
  },
};
window.matchMedia = () => media;
const animations = [];
HTMLElement.prototype.animate = function (frames, options) {
  const animation = {
    element: this,
    frames,
    options,
    cancelled: false,
    onfinish: null,
    cancel() {
      this.cancelled = true;
    },
    finish() {
      this.onfinish?.();
    },
  };
  animations.push(animation);
  return animation;
};
const flush = async () => {
  await nextTick();
  await nextTick();
};
function pointer() {
  document.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true }));
}
function keyboard() {
  document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}
function mount(view) {
  const host = document.createElement('div');
  host.className = 'spatial-app';
  document.body.append(host);
  animations.length = 0;
  media.matches = false;
  const app = createApp(view);
  app.mount(host);
  return {
    host,
    dispose() {
      app.unmount();
      host.remove();
      assert.equal(listeners.size, 0);
    },
  };
}
const envUrl =
  url(`import { reactive, computed, ref, h, onBeforeUnmount } from ${JSON.stringify(vue)};
export const env = { unmounted: 0, tab: ref('graph'), state: reactive({ page: 'projects', loading: false, project: { id: 'P', created_at: 'one', milestones: [{ id: 'A' }, { id: 'B' }], source_milestones: [], messages: [] }, selectedId: null, projects: [] }) };
export const useWorkspace = () => ({ state: env.state, init() {}, dismiss() {}, undoDelete() {}, reserveSettingsOperation() {}, setPage(page) { env.state.page = page; }, selectProject() {}, selected: computed(() => env.state.project.milestones.find(node => node.id === env.state.selectedId)), selectNode(id) { env.state.selectedId = id; }, bindWorkspaceTab: () => ({ key: 'P', tab: env.tab }) });
export const useAgent = () => ({ state: reactive({ follow: {}, navigationTick: 0, pulse: 0 }), freeView() {}, resumeFollow() {} });
export const Page = { setup() { onBeforeUnmount(() => env.unmounted++); return () => h('main', { class: 'test-page' }, env.state.page); } };
export const navigation = [{ id: 'projects', label: 'Projects', countProjects: true, component: Page }, { id: 'settings', label: 'Settings', component: { render() { return h('main', { class: 'test-settings' }); } } }];
export const workspaceViews = [{ id: 'graph', component: { render() { return h('div', { class: 'test-graph' }); } } }, { id: 'architecture', component: { render() { return h('div', { class: 'test-architecture' }); } } }];
export const Inspector = { props: ['milestone'], setup(props) { return () => h('aside', { class: 'inspector' }, props.milestone.id); } };`);
const { env } = await import(envUrl);
const empty = url('export default { render() { return null; } };');
const alias = (name) => url(`export { ${name} as default } from ${JSON.stringify(envUrl)};`);
const imports = {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  './composables/useSurfaceMotion': surfaceMotionUrl,
  '../../composables/useSurfaceMotion': surfaceMotionUrl,
  './composables/useWorkspace': envUrl,
  '../../composables/useWorkspace': envUrl,
  '../../composables/useAgent': envUrl,
  './lib/navigation': envUrl,
  '../../lib/navigation': envUrl,
  '../../lib/workspaceViews': envUrl,
  '../../api/client': url('export const command = () => {};'),
  '../../composables/useSettingsForms': url(
    'export const settingsCommandKey = Symbol(); export const queuedSettingsCommand = () => {};',
  ),
  './ProviderForm.vue': url(
    `import { h, ref } from ${JSON.stringify(vue)}; export default { setup() { const draft = ref(''); return () => h('input', { class: 'model-draft', value: draft.value, onInput: event => draft.value = event.target.value }); } };`,
  ),
  './ResearchForm.vue': url(
    `import { h } from ${JSON.stringify(vue)}; export default { render() { return h('input', { class: 'tool-draft' }); } };`,
  ),
  '../graph/MilestoneInspector.vue': alias('Inspector'),
  '../graph/SourceInspector.vue': alias('Inspector'),
};
for (const name of [
  './components/sidebar/AppSidebar.vue',
  './components/projects/ProjectDialog.vue',
  './components/projects/ProjectWelcome.vue',
  './components/projects/ProjectRecoveryDialog.vue',
  './components/ui/NotificationStack.vue',
  './WorkspaceHeader.vue',
  '../graph/GraphToolbar.vue',
  '../graph/MilestoneGraph.vue',
  '../graph/MilestoneFinder.vue',
  '../agent/AgentDock.vue',
  './ProjectItem.vue',
  './SidebarNavItem.vue',
])
  imports[name] = empty;
async function component(path) {
  const { descriptor } = parse(source(path));
  const code = compile(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], name);
    return `from ${JSON.stringify(imports[name])}`;
  });
  const compiledUrl = url(code);
  const view = (await import(compiledUrl)).default;
  view.fixtureUrl = compiledUrl;
  return view;
}
const Sidebar = await component('components/sidebar/AppSidebar.vue');
imports['./components/sidebar/AppSidebar.vue'] = Sidebar.fixtureUrl;
const App = await component('App.vue');
const Settings = await component('components/settings/SettingsView.vue');
const Workspace = await component('components/workspace/ProjectWorkspace.vue');

function reset() {
  Object.assign(env.state, { page: 'projects', selectedId: null });
  env.tab.value = 'graph';
}

test('page replacement immediately retires the old owner and only the newest surface animates', async () => {
  reset();
  env.unmounted = 0;
  const view = mount(App);
  try {
    pointer();
    env.state.page = 'settings';
    await flush();
    assert.equal(env.unmounted, 1);
    assert.equal(view.host.querySelector('.test-page'), null);
    const first = animations.at(-1);
    assert.equal(first.options.duration, 220);
    assert.deepEqual(first.frames[0], { opacity: 0, transform: 'translateY(14px)' });
    env.state.page = 'projects';
    await flush();
    assert.equal(first.cancelled, true);
    assert.equal(view.host.querySelectorAll('.test-page').length, 1);
    keyboard();
    assert.equal(animations.at(-1).cancelled, true);
    const count = animations.length;
    env.state.page = 'settings';
    await flush();
    assert.equal(animations.length, count, 'keyboard navigation stays immediate');
  } finally {
    view.dispose();
  }
});

test('category motion preserves the same input and unsaved draft over rapid reversals', async () => {
  reset();
  const view = mount(Settings);
  try {
    const input = view.host.querySelector('.model-draft');
    input.value = 'unsaved model';
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    const buttons = view.host.querySelectorAll('.settings-category-nav button');
    pointer();
    for (let i = 0; i < 10; i++) {
      buttons[(i + 1) % 2].click();
      await flush();
    }
    assert.equal(view.host.querySelector('.model-draft'), input);
    assert.equal(input.value, 'unsaved model');
    assert.equal(animations.length, 10);
    assert.ok(animations.slice(0, -1).every((animation) => animation.cancelled));
    media.matches = true;
    for (const listener of listeners) listener({ matches: true });
    assert.equal(animations.at(-1).cancelled, true);
    buttons[1].click();
    await flush();
    assert.equal(animations.at(-1).options.duration, 100);
    assert.ok(animations.at(-1).frames.every((frame) => !('transform' in frame)));
  } finally {
    view.dispose();
  }
});

test('inspector opens once, node reading remains immediate, and retained exits are inert', async () => {
  reset();
  const view = mount({ setup: () => () => h(Workspace, { project: env.state.project }) });
  try {
    pointer();
    env.state.selectedId = 'A';
    await flush();
    const surface = view.host.querySelector('.inspector-surface');
    const opening = animations.at(-1);
    assert.equal(opening.options.duration, 220);
    assert.deepEqual(opening.frames[0], { opacity: 0, transform: 'translateX(22px)' });
    env.state.selectedId = 'B';
    await flush();
    assert.equal(view.host.querySelector('.inspector-surface'), surface);
    assert.equal(surface.textContent, 'B');
    assert.equal(opening.cancelled, true, 'a newer reading selection settles the current entrance');
    assert.equal(animations.length, 1);
    env.state.selectedId = null;
    await flush();
    const closing = animations.at(-1);
    assert.equal(closing.options.duration, 180);
    assert.equal(surface.inert, true);
    assert.ok(
      view.host.querySelector('.workspace-detail-open'),
      'detail column stays stable during exit',
    );
    env.state.selectedId = 'A';
    await flush();
    assert.equal(view.host.querySelectorAll('.inspector-surface').length, 1);
    assert.equal(view.host.querySelector('.inspector-surface').inert, false);
    assert.equal(view.host.querySelector('.inspector-surface').textContent, 'A');
    closing.finish();
    await flush();
    assert.equal(
      view.host.querySelectorAll('.inspector-surface').length,
      1,
      'old completion cannot remove new detail',
    );
    keyboard();
    env.state.selectedId = null;
    await flush();
    const count = animations.length;
    env.state.selectedId = 'B';
    await flush();
    assert.equal(animations.length, count);
  } finally {
    view.dispose();
  }
});

test('sidebar uses retained opposing surfaces and survives ten open/close reversals', async () => {
  reset();
  localStorage.setItem('evograph.sidebar', 'collapsed');
  const view = mount(Sidebar);
  try {
    pointer();
    for (let i = 0; i < 10; i++) {
      view.host.querySelector('[aria-label="展开侧边栏"]').click();
      await flush();
      assert.equal(view.host.querySelector('.sidebar').inert, false);
      view.host.querySelector('[aria-label="收起侧边栏"]').click();
      await flush();
      assert.equal(view.host.querySelector('.sidebar').inert, true);
      assert.equal(view.host.querySelector('.sidebar-rail').inert, false);
    }
    for (const animation of animations) animation.finish();
    await flush();
    assert.equal(view.host.querySelectorAll('.sidebar').length, 1);
    assert.equal(view.host.querySelector('.sidebar').style.display, 'none');
    assert.notEqual(view.host.querySelector('.sidebar-rail').style.display, 'none');
  } finally {
    view.dispose();
  }
});

test('rail navigation and expansion animate separate page/layout surfaces without width animation', async () => {
  reset();
  env.state.page = 'settings';
  localStorage.setItem('evograph.sidebar', 'collapsed');
  const view = mount(App);
  try {
    const main = view.host.querySelector('.app-main');
    main.getBoundingClientRect = () => ({
      left: view.host.querySelector('.sidebar-slot').classList.contains('is-collapsed') ? 64 : 220,
    });
    pointer();
    view.host.querySelector('[aria-label="选择项目"]').click();
    await flush();
    const page = animations.find((animation) =>
      animation.element.classList.contains('app-page-surface'),
    );
    const layout = animations.find((animation) => animation.element === main);
    assert.ok(page);
    assert.ok(layout);
    assert.notEqual(page.element, layout.element);
    assert.deepEqual(layout.frames, [{ transform: 'translateX(-156px)' }, { transform: 'none' }]);
    assert.ok(
      animations.every((animation) =>
        animation.frames.every((frame) =>
          Object.keys(frame).every((key) => ['opacity', 'transform'].includes(key)),
        ),
      ),
    );
    const sidebarSource = source('components/sidebar/AppSidebar.vue');
    assert.match(sidebarSource, /@media \(max-width: 1200px\)\s*\{[\s\S]*?flex-basis: 190px/);
    assert.match(sidebarSource, /@media \(max-width: 760px\)\s*\{[\s\S]*?flex-basis: 52px/);
  } finally {
    view.dispose();
  }
});

test('a cancelled transition reverses from its visible frame and unmount cancels the final animation', async () => {
  const { useSurfaceMotion } = await import(surfaceMotionUrl);
  let motion;
  const view = mount({
    setup() {
      motion = useSurfaceMotion('left');
      return () => h('div', { class: 'surface' });
    },
  });
  const el = view.host.querySelector('.surface');
  const original = window.getComputedStyle;
  let done = 0;
  try {
    pointer();
    motion.enter(el, () => done++);
    const first = animations.at(-1);
    window.getComputedStyle = () => ({ opacity: '0.42', transform: 'matrix(1, 0, 0, 1, -9, 0)' });
    motion.cancel(el);
    motion.leave(el, () => done++);
    assert.equal(first.cancelled, true);
    assert.deepEqual(animations.at(-1).frames[0], {
      opacity: '0.42',
      transform: 'matrix(1, 0, 0, 1, -9, 0)',
    });
    assert.equal(done, 0);
  } finally {
    window.getComputedStyle = original;
    view.dispose();
  }
  assert.equal(animations.at(-1).cancelled, true);
  assert.equal(done, 1);
});

test('the page surface fills its normal block parent in the production cascade', () => {
  const { descriptor } = parse(source('App.vue'));
  const style = document.createElement('style');
  style.textContent =
    ['base', 'studio', 'spatial'].map((name) => source(`styles/${name}.css`)).join('\n') +
    compileStyle({
      source: descriptor.styles[0].content,
      filename: 'App.vue',
      id: 'data-v-page-motion',
      scoped: true,
    }).code;
  document.head.append(style);
  const host = document.createElement('div');
  host.className = 'spatial-app';
  host.innerHTML =
    '<div class="app-main" style="width:1116px;height:744px"><div class="app-page-surface" data-v-page-motion><main class="project-workspace"></main></div></div>';
  document.body.append(host);
  try {
    const page = host.querySelector('.app-page-surface');
    const css = window.getComputedStyle(page);
    assert.equal(css.width, '100%');
    assert.equal(window.getComputedStyle(page.parentElement).display, 'block');
    assert.equal(parseFloat(css.minWidth), 0);
    assert.equal(css.height, '100%');
  } finally {
    host.remove();
    style.remove();
  }
});

test('compiled sidebar visibility override stays scoped and cannot turn html into a flex container', () => {
  const { descriptor } = parse(source('components/sidebar/AppSidebar.vue'));
  const compiled = compileStyle({
    source: descriptor.styles[0].content,
    filename: 'AppSidebar.vue',
    id: 'data-v-sidebar-motion',
    scoped: true,
  });
  assert.deepEqual(compiled.errors, []);
  assert.doesNotMatch(compiled.code, /(?:^|})\s*html\b/);
  assert.match(
    compiled.code,
    /\.sidebar-slot > \.sidebar\[data-v-sidebar-motion\]\s*\{\s*display: flex;/,
  );
  const style = document.createElement('style');
  style.textContent = "html[data-sidebar='collapsed'] .sidebar { display:none }\n" + compiled.code;
  const previous = document.documentElement.dataset.sidebar;
  document.documentElement.dataset.sidebar = 'collapsed';
  document.head.append(style);
  const slot = document.createElement('div');
  slot.className = 'sidebar-slot is-collapsed';
  slot.innerHTML = '<aside class="sidebar" data-v-sidebar-motion></aside>';
  document.body.append(slot);
  try {
    assert.notEqual(window.getComputedStyle(document.documentElement).display, 'flex');
    assert.equal(
      window.getComputedStyle(slot.firstElementChild).display,
      'flex',
      'retained close frame remains visible until v-show hides it',
    );
    slot.firstElementChild.style.display = 'none';
    assert.equal(window.getComputedStyle(slot.firstElementChild).display, 'none');
  } finally {
    slot.remove();
    style.remove();
    if (previous === undefined) delete document.documentElement.dataset.sidebar;
    else document.documentElement.dataset.sidebar = previous;
  }
});

test('sidebar focus moves to the matching visible control and rail project navigation hands off to search', async () => {
  reset();
  localStorage.setItem('evograph.sidebar', 'collapsed');
  const view = mount(Sidebar);
  try {
    const rail = view.host.querySelector('.sidebar-rail');
    const sidebar = view.host.querySelector('.sidebar');
    const expand = view.host.querySelector('.rail-brand');
    const collapse = view.host.querySelector('.sidebar-toggle');
    assert.equal(rail.hasAttribute('aria-hidden'), false);
    assert.equal(sidebar.hasAttribute('aria-hidden'), false);
    for (let i = 0; i < 5; i++) {
      keyboard();
      expand.focus();
      expand.click();
      await flush();
      assert.equal(document.activeElement, collapse);
      assert.equal(sidebar.inert, false);
      collapse.click();
      await flush();
      assert.equal(document.activeElement, expand);
      assert.equal(rail.inert, false);
    }
    pointer();
    const chooseProject = view.host.querySelector('[aria-label="选择项目"]');
    chooseProject.focus();
    chooseProject.click();
    await flush();
    assert.equal(document.activeElement, view.host.querySelector('.project-search input'));
    assert.equal(sidebar.inert, false);
    assert.equal(rail.inert, true);
    assert.notEqual(document.activeElement, document.body);
  } finally {
    view.dispose();
  }
});

test('sidebar focus handoff never reclaims a newer outside focus or an unrelated existing focus', async () => {
  reset();
  localStorage.setItem('evograph.sidebar', 'collapsed');
  const outside = document.createElement('button');
  document.body.append(outside);
  const view = mount(Sidebar);
  try {
    const expand = view.host.querySelector('.rail-brand');
    const collapse = view.host.querySelector('.sidebar-toggle');
    pointer();
    expand.focus();
    expand.click();
    outside.focus();
    await flush();
    assert.equal(document.activeElement, outside);
    collapse.click();
    await flush();
    assert.equal(document.activeElement, outside);
    expand.click();
    await flush();
    assert.equal(document.activeElement, outside);
    for (const animation of animations) animation.finish();
    await flush();
    assert.equal(document.activeElement, outside);
  } finally {
    view.dispose();
    outside.remove();
  }
});

test('deferred sidebar handoff retires on a newer toggle, unmount, or a newer outside focus', async () => {
  reset();
  localStorage.setItem('evograph.sidebar', 'collapsed');
  const outside = document.createElement('button');
  document.body.append(outside);
  const view = mount(Sidebar);
  let disposed = false;
  try {
    pointer();
    const expand = view.host.querySelector('.rail-brand');
    const collapse = view.host.querySelector('.sidebar-toggle');
    expand.focus();
    expand.click();
    await nextTick();
    outside.focus();
    await flush();
    assert.equal(
      document.activeElement,
      outside,
      'outside focus after the render flush wins over deferred handoff',
    );
    collapse.focus();
    collapse.click();
    await nextTick();
    expand.click();
    await flush();
    assert.equal(view.host.querySelector('.sidebar').inert, false);
    assert.equal(view.host.querySelector('.sidebar-rail').inert, true);
    assert.ok(
      !view.host.querySelector('.sidebar-rail').contains(document.activeElement),
      'cancelled collapse handoff cannot focus the hidden rail',
    );
    collapse.focus();
    collapse.click();
    await nextTick();
    view.dispose();
    disposed = true;
    outside.focus();
    await flush();
    assert.equal(document.activeElement, outside);
  } finally {
    if (!disposed) view.dispose();
    outside.remove();
  }
});
