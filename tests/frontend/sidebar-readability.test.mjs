import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>');
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) {
  globalThis[name] = dom.window[name];
}
const { createApp, nextTick } = await import('vue');
const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const vueUrl = pathToFileURL(require.resolve('vue')).href;
const workspaceUrl = moduleUrl(`
  import { reactive } from ${JSON.stringify(vueUrl)};
  export const state = reactive({ busy: false });
  export const deleted = [];
  export const useWorkspace = () => ({ state, deleteProject: project => deleted.push(project) });
`);
const imports = {
  vue: vueUrl,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../composables/useWorkspace': workspaceUrl,
};
const { descriptor } = parse(source('components/sidebar/ProjectItem.vue'));
const compiled = ts
  .transpileModule(
    compileScript(descriptor, { id: 'sidebar-readability', inlineTemplate: true }).content,
    { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
  )
  .outputText.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected component dependency: ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
const ProjectItem = (await import(moduleUrl(compiled))).default;
const { state, deleted } = await import(workspaceUrl);

// Preserve the production cascade, including later global focus rules.
for (const path of ['base', 'sidebar', 'agent', 'workbench', 'studio', 'spatial']) {
  const style = document.createElement('style');
  // JSDOM does not model keyboard modality; :focus has the same specificity.
  style.textContent = source(`styles/${path}.css`).replaceAll(':focus-visible', ':focus');
  document.head.append(style);
}
const longName = 'Beacon 长对话与流式预览 · SYNTHETIC QA15 '.repeat(8);
const project = (overrides = {}) => ({
  id: 'sidebar-test',
  name: longName,
  acceptance: { passed: 1, total: 3, achieved: false },
  ...overrides,
});
function mount(props) {
  const host = document.createElement('div');
  host.className = 'spatial-app';
  host.innerHTML = '<aside class="sidebar"><div class="project-list"></div></aside>';
  document.body.append(host);
  let selections = 0;
  const app = createApp(ProjectItem, { ...props, onSelect: () => selections++ });
  app.mount(host.querySelector('.project-list'));
  return {
    host,
    selections: () => selections,
    find: (selector) => host.querySelector(selector),
    close() {
      app.unmount();
      host.remove();
    },
  };
}
const css = (element, property) => dom.window.getComputedStyle(element).getPropertyValue(property);

test('long mixed-language names retain a full accessible label and tooltip without changing actions', async () => {
  const item = project();
  const view = mount({ project: item, active: true });
  try {
    const select = view.find('.project-select');
    assert.equal(select.getAttribute('aria-label'), `${longName}，1/3 行为已验证`);
    assert.equal(select.getAttribute('aria-current'), 'page');
    assert.equal(view.find('.project-name').title, longName);
    assert.equal(view.find('.project-title').textContent, longName);
    assert.equal(view.find('.project-name small').title, '1/3 行为已验证');
    select.click();
    assert.equal(view.selections(), 1);
    const remove = view.find('.project-delete');
    assert.equal(remove.getAttribute('aria-label'), `删除项目 ${longName}`);
    remove.click();
    assert.equal(deleted.at(-1), item);
    assert.equal(view.selections(), 1, 'deleting must not also select the project');
    state.busy = true;
    await nextTick();
    const count = deleted.length;
    remove.click();
    assert.equal(remove.disabled, true);
    assert.equal(deleted.length, count, 'busy guard still blocks deletion');
    assert.equal(view.find('.project-bar').title, '33% 的行为已通过验证');
    assert.equal(view.find('.project-bar i').style.transform, 'scaleX(0.33)');
  } finally {
    state.busy = false;
    view.close();
  }
});

test('pinned and empty-target status remains complete even when its visible text is truncated', () => {
  for (const [props, expected] of [
    [{ project: project(), pinned: true }, '当前项目 · 已被筛选隐藏'],
    [
      { project: project({ acceptance: { passed: 0, total: 0, achieved: false } }) },
      '尚无验收目标',
    ],
    [{ project: project({ is_demo: true }) }, '示例 · 1/3'],
  ]) {
    const view = mount({ active: false, ...props });
    try {
      assert.equal(view.find('.project-name small').textContent, expected);
      assert.equal(view.find('.project-name small').title, expected);
      assert.equal(
        view.find('.project-select').getAttribute('aria-label'),
        `${longName}，${expected}`,
      );
      assert.equal(view.find('.project-select').hasAttribute('aria-current'), false);
    } finally {
      view.close();
    }
  }
});

test('the production CSS bounds names to two lines and reserves status, actions and progress space', () => {
  const view = mount({
    project: project({ name: 'UnbrokenProjectName'.repeat(30) }),
    active: true,
  });
  try {
    const title = view.find('.project-title');
    assert.equal(css(title, 'display'), '-webkit-box');
    assert.equal(css(title, '-webkit-line-clamp'), '2');
    assert.equal(css(title, '-webkit-box-orient'), 'vertical');
    assert.equal(css(title, 'overflow'), 'hidden');
    assert.equal(css(title, 'overflow-wrap'), 'anywhere');
    assert.equal(css(title, 'min-height'), '36.4px');
    assert.equal(css(title, 'max-height'), '36.4px');
    assert.equal(css(title, 'line-height'), '1.4');
    assert.equal(css(view.find('.project-name'), 'min-width'), '0px');
    const status = view.find('.project-name small');
    assert.equal(css(status, 'white-space'), 'nowrap');
    assert.equal(css(status, 'text-overflow'), 'ellipsis');
    assert.equal(css(status, 'overflow'), 'hidden');
    assert.equal(css(view.find('.project-icon'), 'flex-shrink'), '0');
    assert.equal(css(view.find('.project-delete'), 'flex'), '0 0 28px');
    assert.equal(css(view.find('.project-item'), 'gap'), '0px');
    assert.equal(css(view.find('.project-select'), 'min-width'), '0px');
    assert.equal(css(view.find('.project-select'), 'padding-bottom'), '12px');
    assert.equal(css(view.find('.project-bar'), 'position'), 'absolute');
    assert.equal(css(view.find('.project-bar'), 'bottom'), '4px');
    assert.equal(css(view.find('.sidebar'), 'width'), '220px', 'sidebar width stays unchanged');
  } finally {
    view.close();
  }
});

test('both keyboard focus rings stay inset from scrollport edges', () => {
  const view = mount({ project: project(), active: true });
  try {
    for (const selector of ['.project-select', '.project-delete']) {
      const button = view.find(selector);
      button.focus();
      assert.equal(document.activeElement, button);
      assert.equal(css(button, 'outline-offset'), '-3px');
    }
  } finally {
    view.close();
  }
});
