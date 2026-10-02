import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

// Use Vue's real DOM renderer and Transition, including cancellation callbacks.
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
  pretendToBeVisual: true,
});
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement', 'Event'])
  globalThis[key] = dom.window[key];
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
const require = createRequire(import.meta.url);
const vueUrl = pathToFileURL(require.resolve('vue')).href;
const { createApp, h, nextTick, ref } = await import(vueUrl);
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const workspaceUrl = moduleUrl(`import { reactive } from ${JSON.stringify(vueUrl)};
export const state = reactive({ busy: false, settings: null });
export const useWorkspace = () => ({ state });`);
const baselineUrl = moduleUrl(`export const calls = [];
export const useBaseline = () => ({ refresh(project) { calls.push(['refresh', project.id]); }, reconstruct(project) { calls.push(['reconstruct', project.id]); } });`);
const imports = {
  vue: vueUrl,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../composables/useWorkspace': workspaceUrl,
  '../../composables/useBaseline': baselineUrl,
  '../../lib/acceptance': moduleUrl(transpile(source('lib/acceptance.ts'))),
};
function component(path) {
  const { descriptor } = parse(source(`components/${path}.vue`));
  return moduleUrl(
    transpile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => {
        assert.ok(imports[name], `Unexpected dependency ${name}`);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['../graph/BaselineMilestoneStatus.vue'] = component('graph/BaselineMilestoneStatus');
const Header = (await import(component('workspace/WorkspaceHeader'))).default;
const { state } = await import(workspaceUrl);
const { calls } = await import(baselineUrl);
const style = document.createElement('style');
style.textContent =
  source('styles/compact-workspace.css') +
  `
/* jsdom does not expand transition shorthands into their longhands. */
.project-overview-enter-active { transition-duration: 0.18s; transition-delay: 0s; transition-property: opacity, transform; }
.project-overview-leave-active { transition-duration: 0.125s; transition-delay: 0s; transition-property: opacity, transform; }
`;
document.head.append(style);
const tick = async () => {
  await nextTick();
  await nextTick();
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const project = (extra = {}) => ({
  id: 'A',
  name: '库存演化',
  description: '这是项目背景描述',
  repository: '/local/project/repository',
  baselines: [{ id: 'B1', number: 1, complete: true }],
  targets: [{ number: 2, statement: '保留数据并完成迁移', required_behavior_ids: ['T1'] }],
  plans: [{ number: 7 }],
  milestones: [{ id: 'M1', behavior_revision_ids: ['T1', 'S1'] }],
  behaviors: [{ id: 'T1' }, { id: 'S1', acceptance_scope: 'milestone' }],
  verified_behaviors: ['S1'],
  source_milestones: [],
  source_analysis_baseline_id: 'B1',
  source_analysis_summary: '已检查源码；这不代表正式验收通过。',
  ...extra,
});
async function harness(extra) {
  state.busy = false;
  state.settings = null;
  calls.length = 0;
  const item = ref(project(extra)),
    edits = [];
  const host = document.createElement('div');
  document.body.append(host);
  const outside = document.createElement('button');
  outside.textContent = '画布操作';
  document.body.append(outside);
  const app = createApp({
    setup: () => () => h(Header, { project: item.value, onEdit: () => edits.push(item.value.id) }),
  });
  app.mount(host);
  await tick();
  const trigger = () => host.querySelector('.project-overview-trigger');
  const panel = () => host.querySelector('.project-overview');
  const click = async (element, detail = 0) => {
    element.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, detail }));
    await tick();
  };
  return {
    host,
    item,
    edits,
    outside,
    trigger,
    panel,
    click,
    open: async (detail = 0) => {
      trigger().focus();
      await click(trigger(), detail);
    },
    button: (label) =>
      [...host.querySelectorAll('button')].find((button) => button.textContent.trim() === label),
    dispose() {
      app.unmount();
      host.remove();
      outside.remove();
    },
  };
}

test('compact default shows one honest target line while all metadata is disclosed on demand', async () => {
  const h = await harness({
    name: '很长的项目名称'.repeat(20),
    is_demo: true,
    repository: '/long/path/'.repeat(30),
  });
  try {
    assert.equal(h.host.querySelectorAll('h1').length, 1);
    assert.equal(h.host.querySelector('.projectbar-goal-text').textContent, '保留数据并完成迁移');
    assert.equal(h.panel().style.display, 'none');
    assert.equal(h.panel().getAttribute('aria-hidden'), 'true');
    assert.ok(h.panel().hasAttribute('inert'));
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'false');
    assert.equal(h.trigger().getAttribute('aria-controls'), h.panel().id);
    assert.ok(h.host.querySelector(`#${h.panel().getAttribute('aria-labelledby')}`));
    assert.equal(h.host.querySelector('.breadcrumb, .title-row, .version-strip'), null);
    await h.open();
    for (const text of [
      '项目背景描述',
      '/long/path/'.repeat(30),
      'V2',
      'P7',
      'B1',
      '0 / 1',
      '步骤专属 1 项',
      '非 PR 节点',
      'SQLite 本地存储',
      '示例 · 未执行',
      '已检查源码',
    ])
      assert.ok(h.panel().textContent.includes(text), text);
    assert.equal(h.panel().getAttribute('aria-hidden'), 'false');
    assert.equal(h.panel().hasAttribute('inert'), false);
    assert.equal(h.panel().style.display, '');
  } finally {
    h.dispose();
  }
});

test('empty targets never masquerade as the project name or description', async () => {
  const h = await harness({ targets: [], description: '项目简介不是验收目标' });
  try {
    assert.equal(h.host.querySelector('.projectbar-goal-label').textContent, '项目简介');
    assert.equal(h.host.querySelector('.projectbar-goal-text').textContent, '项目简介不是验收目标');
    await h.open();
    assert.match(h.panel().textContent, /尚未定义最终目标标准/);
    assert.match(h.panel().textContent, /0 \/ 0/);
    assert.equal(h.panel().querySelector('.achieved'), null);
    h.item.value = project({ targets: [], description: '' });
    await tick();
    assert.equal(h.host.querySelector('.projectbar-goal-label').textContent, '目标待定义');
    assert.match(h.host.querySelector('.projectbar-goal-text').textContent, /尚未定义最终目标/);
  } finally {
    h.dispose();
  }
});

test('Escape and the explicit close restore focus without a modal trap', async () => {
  const h = await harness();
  try {
    await h.open();
    const close = h.host.querySelector('[aria-label="收起项目概览"]');
    close.focus();
    const event = new dom.window.KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    });
    close.dispatchEvent(event);
    await tick();
    assert.equal(event.defaultPrevented, true);
    assert.equal(document.activeElement, h.trigger());
    assert.equal(h.panel().style.display, 'none');
    await h.open();
    await h.click(close);
    assert.equal(document.activeElement, h.trigger());
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'false');
    await h.open();
    h.outside.focus();
    await tick();
    assert.equal(document.activeElement, h.outside);
    assert.equal(h.panel().style.display, 'none');
  } finally {
    h.dispose();
  }
});

test('outside pointer dismisses without stealing focus, while inside selection remains open', async () => {
  const h = await harness();
  try {
    await h.open();
    h.panel()
      .querySelector('.project-overview-path')
      .dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true }));
    await tick();
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'true');
    h.outside.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true }));
    await tick();
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'false');
    assert.equal(h.panel().getAttribute('aria-hidden'), 'true');
    assert.ok(h.panel().hasAttribute('inert'));
  } finally {
    h.dispose();
  }
});

test('project switching closes instantly, resets scroll, and does not leave focus in hidden metadata', async () => {
  const h = await harness();
  try {
    await h.open(1);
    await sleep(45);
    h.panel().scrollTop = 140;
    h.button('刷新基线').focus();
    h.item.value = project({ id: 'B', name: '另一个项目', repository: '/different' });
    await tick();
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'false');
    assert.equal(h.panel().style.display, 'none');
    assert.equal(h.panel().scrollTop, 0);
    assert.equal(document.activeElement, h.trigger());
    assert.equal(h.panel().id, 'project-overview-B');
    assert.match(h.panel().textContent, /\/different/);
    assert.doesNotMatch(h.panel().textContent, /\/local\/project/);
    await sleep(230);
    assert.equal(h.panel().style.display, 'none');
    await h.open();
    h.item.value = { ...h.item.value, description: '同一项目实时更新' };
    await tick();
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'true');
  } finally {
    h.dispose();
  }
});

test('rapid pointer open-close-reopen cancels real Vue leave without stale display or inert state', async () => {
  const h = await harness();
  try {
    await h.open(1);
    assert.ok(h.panel().classList.contains('project-overview-enter-active'));
    await sleep(45);
    await h.click(h.trigger(), 1);
    assert.equal(h.panel().getAttribute('aria-hidden'), 'true');
    assert.ok(h.panel().hasAttribute('inert'));
    await sleep(30);
    await h.click(h.trigger(), 1);
    await sleep(250);
    assert.equal(h.trigger().getAttribute('aria-expanded'), 'true');
    assert.equal(h.panel().getAttribute('aria-hidden'), 'false');
    assert.equal(h.panel().hasAttribute('inert'), false);
    assert.equal(h.panel().style.display, '');
    assert.equal(h.panel().className, 'project-overview');
    // Keyboard activation during pointer exit must cancel motion immediately.
    await h.click(h.trigger(), 1);
    await sleep(30);
    await h.click(h.trigger(), 0);
    assert.equal(h.panel().style.display, '');
    assert.equal(h.panel().className, 'project-overview');
    await h.click(h.trigger(), 0);
    assert.equal(h.panel().style.display, 'none');
    assert.equal(h.panel().className, 'project-overview');
    await sleep(230);
    assert.equal(h.panel().style.display, 'none');
  } finally {
    h.dispose();
  }
});

test('disconnected repository remains actionable and settings return focus to a visible trigger', async () => {
  const h = await harness({ repository: '', baselines: [] });
  try {
    const connect = h.host.querySelector('.projectbar-connect');
    assert.match(connect.textContent, /连接仓库/);
    await h.click(connect);
    assert.deepEqual(h.edits, ['A']);
    assert.equal(document.activeElement, h.trigger());
    await h.open();
    await h.click(h.panel().querySelector('.project-overview-repository > button'));
    assert.deepEqual(h.edits, ['A', 'A']);
    assert.equal(h.panel().style.display, 'none');
    assert.equal(document.activeElement, h.trigger());
  } finally {
    h.dispose();
  }
});

test('incomplete and stale source states remain discoverable while generic busy stays honest', async () => {
  const h = await harness({ baselines: [{ id: 'B2', number: 2, complete: false }] });
  try {
    assert.match(h.trigger().textContent, /扫描不完整/);
    await h.open();
    assert.match(h.panel().textContent, /基线扫描不完整，请刷新/);
    assert.equal(h.button('倒推已实现里程碑').disabled, true);
    h.item.value = project({
      source_analysis_baseline_id: 'B0',
      source_milestones: [{ id: 'SRC' }],
    });
    await tick();
    assert.match(h.trigger().textContent, /基线待更新/);
    assert.match(h.panel().textContent, /基线已变化/);
    state.busy = true;
    await tick();
    assert.equal(h.button('刷新基线').disabled, true);
    assert.equal(h.button('倒推已实现里程碑').disabled, true);
    await h.click(h.trigger());
    assert.match(h.host.querySelector('.projectbar-busy').textContent, /处理中/);
    assert.doesNotMatch(h.host.querySelector('.projectbar-busy').textContent, /倒推|重建/);
    assert.equal(h.trigger().disabled, false);
  } finally {
    h.dispose();
  }
});

test('source transmission warning, read and reconstruction actions retain the current project', async () => {
  const h = await harness({ source_analysis_baseline_id: '' });
  try {
    state.settings = { provider: { config: { model: 'Test model' } } };
    await h.open();
    assert.match(
      h.panel().querySelector('.source-analysis-note').textContent,
      /源码上下文发送给已配置的模型/,
    );
    const refresh = h.button('刷新基线');
    assert.equal(
      refresh.getAttribute('aria-describedby'),
      h.panel().querySelector('.source-analysis-note').id,
    );
    await h.click(refresh);
    await h.click(h.button('倒推已实现里程碑'));
    assert.deepEqual(calls, [
      ['refresh', 'A'],
      ['reconstruct', 'A'],
    ]);
    h.item.value = project({ repository: '/new', baselines: [] });
    await tick();
    assert.match(h.trigger().textContent, /待读取基线/);
    assert.ok(h.button('读取基线'));
    assert.match(h.panel().textContent, /已关联 · 等待读取基线/);
  } finally {
    h.dispose();
  }
});

test('canvas no longer repeats project goal/source strips and overview is bounded without layout animation', () => {
  const graph = source('components/graph/MilestoneGraph.vue');
  assert.doesNotMatch(graph, /GoalMarker|BaselineMilestoneStatus/);
  const workspace = source('components/workspace/ProjectWorkspace.vue');
  assert.ok(workspace.indexOf('<WorkspaceHeader') < workspace.indexOf('<GraphToolbar'));
  assert.match(workspace, /class="workspace-chrome"/);
  const css = source('styles/compact-workspace.css');
  assert.match(css, /\.project-overview\s*\{\s*position: absolute/);
  assert.match(css, /max-height: min\(72dvh, 570px\)/);
  assert.match(css, /overflow-wrap: anywhere/);
  assert.doesNotMatch(css, /transform-origin|transform 180ms|scale\(0\.97\)/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(css, /@keyframes|transition: all|scale\(0\)/);
});
