import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of [
  'window',
  'document',
  'Document',
  'navigator',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const vue = await import('vue');
const { createApp, effectScope, nextTick, reactive } = vue;
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const formsUrl = url(
  compile(source('composables/useSettingsForms.ts')).replace(
    /from 'vue'/g,
    `from ${JSON.stringify(import.meta.resolve('vue'))}`,
  ),
);
const { useModelSettings, useResearchSettings, queuedSettingsCommand } = await import(formsUrl);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const tick = async () => {
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
const copy = (value) => JSON.parse(JSON.stringify(value));
const field = (key, value) => ({
  key,
  label: key === 'model' ? '模型名称' : 'Base URL',
  default: value,
  placeholder: '',
  required: true,
});
const modelSettings = () => ({
  adapters: [
    {
      id: 'openai_compatible',
      name: 'OpenAI Compatible',
      fields: [field('base_url', 'http://127.0.0.1:8001/v1'), field('model', 'default')],
      secret_label: 'API Key',
      description: 'Local test adapter',
    },
    {
      id: 'anthropic',
      name: 'Anthropic',
      fields: [field('base_url', 'http://127.0.0.1:8002'), field('model', 'default')],
      secret_label: 'API Key',
      description: 'Second test adapter',
    },
  ],
  provider: {
    adapter: 'openai_compatible',
    config: { base_url: 'http://127.0.0.1:8001/v1', model: 'saved-model' },
    has_key: true,
  },
});
const researchSettings = () => ({
  providers: [
    { id: 'bing', name: 'Bing', description: 'No key', fields: [], secret_label: '' },
    {
      id: 'tavily',
      name: 'Tavily',
      description: 'Search',
      fields: [],
      secret_label: 'Tavily API Key',
    },
    {
      id: 'searxng',
      name: 'SearXNG',
      description: 'Self hosted',
      fields: [field('base_url', 'http://127.0.0.1:8080')],
      secret_label: '',
    },
  ],
  saved: { provider: 'bing', config: {}, has_key: false },
  vision_enabled: false,
});
function scoped(t, factory) {
  const scope = effectScope();
  const result = scope.run(factory);
  t.after(() => {
    result.dispose?.();
    scope.stop();
  });
  return result;
}

test('model test targets only the saved connection and leaves draft fields and key intact', async (t) => {
  const calls = [];
  const form = scoped(t, () =>
    useModelSettings(
      modelSettings(),
      async (action, params) => {
        calls.push({ action, params });
        return { message: 'synthetic OK' };
      },
      () => assert.fail('Testing must not apply a settings write'),
    ),
  );
  form.fields.value.model = 'unsaved-model';
  form.apiKey.value = 'synthetic-unsaved-key';
  await form.test();
  assert.deepEqual(calls, [{ action: 'settings.test', params: undefined }]);
  assert.equal(form.fields.value.model, 'unsaved-model');
  assert.equal(form.apiKey.value, 'synthetic-unsaved-key');
  assert.equal(form.dirty.value, true);
  assert.match(form.tested.value, /已保存的连接可用/);
});

test('confirmed model save updates baseline without a refresh and blocks duplicate/conflicting requests', async (t) => {
  const calls = [],
    applied = [],
    pending = deferred();
  const form = scoped(t, () =>
    useModelSettings(
      modelSettings(),
      (action, params) => {
        calls.push({ action, params });
        return pending.promise;
      },
      (result) => applied.push(result),
    ),
  );
  form.fields.value.model = 'new-model';
  form.apiKey.value = 'synthetic-new-key';
  const saving = form.save();
  await form.save();
  await form.test();
  assert.equal(calls.length, 1);
  assert.equal(form.operation.value, 'saving');
  const result = modelSettings();
  result.provider.config.model = 'new-model';
  pending.resolve(result);
  await saving;
  assert.equal(applied.length, 1);
  assert.deepEqual(
    calls.map((call) => call.action),
    ['settings.save'],
  );
  assert.equal(form.dirty.value, false);
  assert.equal(form.apiKey.value, '');
  assert.match(form.message.value, /模型配置已保存/);
  assert.equal(form.error.value, '');
});

test('model failure keeps draft/key, corrects saved-key scope and clears secrets on disposal', async (t) => {
  const form = scoped(t, () =>
    useModelSettings(
      modelSettings(),
      async () => {
        throw new Error('synthetic connection error');
      },
      () => {},
    ),
  );
  assert.equal(form.savedKey.value, true);
  form.fields.value.base_url += '/';
  assert.equal(form.savedKey.value, true);
  form.apiKey.value = 'synthetic';
  await form.save();
  assert.equal(form.apiKey.value, 'synthetic');
  assert.match(form.error.value, /synthetic connection error/);
  form.fields.value.base_url = 'http://127.0.0.1:8003/v1';
  assert.equal(form.savedKey.value, false);
  assert.equal(form.apiKey.value, '');
  form.adapter.value = 'anthropic';
  assert.equal(form.savedKey.value, false);
  form.apiKey.value = 'synthetic-again';
  form.showKey.value = true;
  form.dispose();
  assert.equal(form.apiKey.value, '');
  assert.equal(form.showKey.value, false);
});

test('search and vision saves preserve the other draft and update only their own baseline', async (t) => {
  const calls = [],
    saved = researchSettings();
  const form = scoped(t, () =>
    useResearchSettings(async (action, params) => {
      calls.push({ action, params });
      if (action === 'research.configure_search')
        saved.saved = {
          provider: params.provider,
          config: params.config,
          has_key: Boolean(params.api_key),
        };
      if (action === 'research.configure_vision') saved.vision_enabled = params.vision_enabled;
      return copy(saved);
    }),
  );
  await form.load();
  form.provider.value = 'tavily';
  form.apiKey.value = 'synthetic-search';
  form.vision.value = true;
  await form.saveVision();
  assert.equal(form.provider.value, 'tavily');
  assert.equal(form.apiKey.value, 'synthetic-search');
  assert.equal(form.searchDirty.value, true);
  assert.equal(form.visionDirty.value, false);
  assert.deepEqual(calls[1], {
    action: 'research.configure_vision',
    params: { vision_enabled: true },
  });
  form.vision.value = false;
  await form.saveSearch();
  assert.equal(form.vision.value, false);
  assert.equal(form.visionDirty.value, true);
  assert.equal(form.searchDirty.value, false);
  assert.equal(form.apiKey.value, '');
  assert.ok(!('vision_enabled' in calls[2].params));
  assert.match(form.searchStatus.message, /搜索设置已保存/);
});

test('tool errors are section scoped and retry succeeds without clearing another section feedback', async (t) => {
  let failLoad = true,
    failSave = true;
  const form = scoped(t, () =>
    useResearchSettings(async (action, params) => {
      if (action === 'research.settings' && failLoad) throw new Error('load offline');
      if (action === 'research.configure_search' && failSave) throw new Error('search offline');
      const result = researchSettings();
      if (action === 'research.configure_vision') result.vision_enabled = params.vision_enabled;
      return result;
    }),
  );
  await form.load();
  assert.equal(form.settings.value, undefined);
  assert.equal(form.loadError.value, 'load offline');
  failLoad = false;
  await form.load();
  assert.equal(form.loadError.value, '');
  form.provider.value = 'tavily';
  form.apiKey.value = 'synthetic';
  form.vision.value = true;
  await form.saveSearch();
  assert.equal(form.apiKey.value, 'synthetic');
  assert.equal(form.searchStatus.error, 'search offline');
  await form.saveVision();
  assert.equal(form.searchStatus.error, 'search offline');
  assert.equal(form.visionStatus.error, '');
  failSave = false;
  await form.saveSearch();
  assert.equal(form.searchStatus.error, '');
  assert.match(form.visionStatus.message, /视觉设置已保存/);
});

test('screen request queue survives failure and serializes independent section saves', async () => {
  const first = deferred(),
    started = [];
  const command = queuedSettingsCommand(async (action) => {
    started.push(action);
    if (action === 'first') return first.promise;
    return 'second result';
  });
  const a = command('first'),
    b = command('second');
  const rejection = assert.rejects(a, /expected/);
  await tick();
  assert.deepEqual(started, ['first']);
  first.reject(new Error('expected'));
  await rejection;
  assert.equal(await b, 'second result');
  assert.deepEqual(started, ['first', 'second']);
});

// Mount the actual Vue shell and forms, using only synthetic settings commands.
const harnessKey = '__evographSettingsTest';
const workspaceUrl = url(
  `export function useWorkspace() { return globalThis.${harnessKey}.workspace; }`,
);
const commandUrl = url(
  `export function command(action, params) { return globalThis.${harnessKey}.command(action, params); }`,
);
const imports = {
  vue: import.meta.resolve('vue'),
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  '../../api/client': commandUrl,
  '../../composables/useWorkspace': workspaceUrl,
  '../../composables/useSettingsForms': formsUrl,
};
function compileVue(path, additional = {}) {
  const { descriptor } = parse(source(path));
  const script = compileScript(descriptor, { id: path, inlineTemplate: true });
  return url(
    compile(script.content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => `from ${JSON.stringify({ ...imports, ...additional }[name] ?? name)}`,
    ),
  );
}
const providerUrl = compileVue('components/settings/ProviderForm.vue');
const researchUrl = compileVue('components/settings/ResearchForm.vue');
const shellUrl = compileVue('components/settings/SettingsView.vue', {
  './ProviderForm.vue': providerUrl,
  './ResearchForm.vue': researchUrl,
});
const SettingsView = (await import(shellUrl)).default;
let workspaceInstance = 0;
async function isolatedWorkspace(handler = async () => undefined) {
  const id = ++workspaceInstance;
  const api = url(
    `export const env = { handler: null }; export const readyTransport = async () => {}; export const command = (action, params) => env.handler(action, params); // ${id}`,
  );
  const dependencies = {
    vue: import.meta.resolve('vue'),
    '../api/client': api,
    './useNotifications': url('export const useNotifications = () => ({ push() {} });'),
    './useAgentDrafts': url(
      'export const agentDrafts = { retain() {}, discard() {}, activate() {} };',
    ),
    './useWorkflowDrafts': url(
      'export const workflowDrafts = { retain() {}, discard() {}, activate() {}, reconcile() {} };',
    ),
  };
  const actualWorkspace = url(
    compile(source('composables/useWorkspace.ts')).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => `from ${JSON.stringify(dependencies[name])}`,
    ),
  );
  const { env } = await import(api);
  env.handler = handler;
  const { useWorkspace } = await import(actualWorkspace);
  return useWorkspace();
}
async function mount(t, override, existingWorkspace) {
  const calls = [],
    saved = researchSettings(),
    pages = [];
  const workspace = existingWorkspace ?? (await isolatedWorkspace());
  if (!existingWorkspace) workspace.applySettings(modelSettings());
  const { state } = workspace;
  globalThis[harnessKey] = {
    workspace: {
      ...workspace,
      setPage: (page) => {
        pages.push(page);
        workspace.setPage(page);
      },
    },
    command: async (action, params) => {
      calls.push({ action, params });
      if (override) return override(action, params, saved);
      if (action === 'research.configure_search')
        saved.saved = {
          provider: params.provider,
          config: params.config,
          has_key: Boolean(params.api_key),
        };
      if (action === 'research.configure_vision') saved.vision_enabled = params.vision_enabled;
      if (action === 'settings.test') return { message: 'synthetic OK' };
      return copy(saved);
    },
  };
  const host = document.createElement('div');
  document.body.append(host);
  const app = createApp(SettingsView);
  app.mount(host);
  let mounted = true;
  const unmount = () => {
    if (mounted) {
      mounted = false;
      app.unmount();
      host.remove();
    }
  };
  t.after(() => {
    unmount();
    delete globalThis[harnessKey];
  });
  await tick();
  const button = (text) =>
    [...host.querySelectorAll('button')].find((item) => item.textContent.trim().includes(text));
  return { host, calls, state, saved, pages, button, app, workspace, unmount };
}
async function input(element, value, event = 'input') {
  element.value = value;
  element.dispatchEvent(new dom.window.Event(event, { bubbles: true }));
  await tick();
}
async function submit(form) {
  form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  await tick();
}

test('actual categories retain live drafts, keep planned categories disabled and never write browser storage', async (t) => {
  const { host, button, pages } = await mount(t);
  const model = host.querySelector('input[aria-label="模型 API Key"]');
  const modelName = [...host.querySelectorAll('.model-settings-form label')]
    .find((label) => label.textContent.includes('模型名称'))
    .querySelector('input');
  await input(modelName, 'unsaved model');
  await input(model, 'synthetic-visible-only');
  button('搜索与工具').click();
  await tick();
  assert.equal(host.querySelector('h1').textContent, '搜索与工具');
  assert.equal(host.querySelector('#model-settings').style.display, 'none');
  assert.equal(button('外观与交互').disabled, true);
  assert.equal(button('项目与存储').disabled, true);
  button('模型服务').click();
  await tick();
  assert.equal(model.value, 'synthetic-visible-only');
  assert.equal(modelName.value, 'unsaved model');
  assert.equal(button('模型服务').getAttribute('aria-current'), 'page');
  assert.equal(window.localStorage.length, 0);
  assert.equal(window.sessionStorage.length, 0);
  button('返回项目').click();
  assert.deepEqual(pages, ['projects']);
});

test('actual tool cards independently save, preserve hidden model drafts and show local errors', async (t) => {
  let failSearch = true;
  const { host, button, calls } = await mount(t, async (action, params, saved) => {
    if (action === 'research.configure_search' && failSearch)
      throw new Error('synthetic search failure');
    if (action === 'research.configure_vision') saved.vision_enabled = params.vision_enabled;
    if (action === 'research.configure_search')
      saved.saved = { provider: params.provider, config: params.config, has_key: true };
    return copy(saved);
  });
  const modelKey = host.querySelector('input[aria-label="模型 API Key"]');
  await input(modelKey, 'synthetic-model');
  button('搜索与工具').click();
  await tick();
  await input(host.querySelector('select[aria-label="搜索服务"]'), 'tavily', 'change');
  const searchKey = host.querySelector('input[aria-label="搜索 API Key"]');
  await input(searchKey, 'synthetic-search');
  host.querySelector('input[role="switch"]').click();
  await tick();
  await submit(host.querySelector('.vision-settings-form'));
  assert.equal(searchKey.value, 'synthetic-search');
  assert.match(host.querySelector('.vision-settings-form').textContent, /视觉设置已保存/);
  await submit(host.querySelector('.search-settings-form'));
  assert.equal(searchKey.value, 'synthetic-search');
  assert.match(
    host.querySelector('.search-settings-form [role="alert"]').textContent,
    /synthetic search failure/,
  );
  assert.equal(host.querySelector('.vision-settings-form [role="alert"]'), null);
  failSearch = false;
  await submit(host.querySelector('.search-settings-form'));
  assert.equal(searchKey.value, '');
  assert.equal(modelKey.value, 'synthetic-model');
  assert.equal(calls.filter((call) => call.action === 'settings.save').length, 0);
});

test('actual model controls block repeats while test uses no unsaved payload across category switches', async (t) => {
  const pending = deferred();
  const { host, button, calls } = await mount(t, async (action, params, saved) =>
    action === 'settings.test' ? pending.promise : copy(saved),
  );
  const key = host.querySelector('input[aria-label="模型 API Key"]');
  await input(key, 'synthetic-unsaved');
  button('测试已保存的连接').click();
  await tick();
  assert.equal(host.querySelector('.model-settings-form fieldset').disabled, true);
  assert.equal(button('保存模型配置').disabled, true);
  button('搜索与工具').click();
  await tick();
  pending.resolve({ message: 'synthetic OK' });
  await tick();
  button('模型服务').click();
  await tick();
  assert.equal(key.value, 'synthetic-unsaved');
  assert.match(host.querySelector('.model-settings-form').textContent, /已保存的连接可用/);
  assert.deepEqual(
    calls.find((call) => call.action === 'settings.test'),
    { action: 'settings.test', params: undefined },
  );
});

test('late tool responses cannot overwrite another section baseline or a disposed secret', async (t) => {
  const search = deferred(),
    vision = deferred();
  const form = scoped(t, () =>
    useResearchSettings(async (action) => {
      if (action === 'research.configure_search') return search.promise;
      if (action === 'research.configure_vision') return vision.promise;
      return researchSettings();
    }),
  );
  await form.load();
  form.provider.value = 'tavily';
  form.apiKey.value = 'synthetic';
  form.vision.value = true;
  const searchSave = form.saveSearch(),
    visionSave = form.saveVision();
  const visionResult = researchSettings();
  visionResult.vision_enabled = true;
  vision.resolve(visionResult);
  await visionSave;
  const searchResult = researchSettings();
  searchResult.saved = { provider: 'tavily', config: {}, has_key: true };
  // Its snapshot predates the vision write; only search is authoritative here.
  search.resolve(searchResult);
  await searchSave;
  assert.equal(form.settings.value.vision_enabled, true);
  assert.equal(form.visionDirty.value, false);
  assert.equal(form.searchDirty.value, false);
  form.apiKey.value = 'synthetic-disposed';
  form.dispose();
  assert.equal(form.apiKey.value, '');
});

test('disposing during a confirmed model save clears secret but still publishes the confirmed config', async (t) => {
  const pending = deferred(),
    applied = [];
  const form = scoped(t, () =>
    useModelSettings(
      modelSettings(),
      () => pending.promise,
      (value) => applied.push(value),
    ),
  );
  form.apiKey.value = 'synthetic';
  const saving = form.save();
  form.dispose();
  pending.resolve(modelSettings());
  await saving;
  assert.equal(form.apiKey.value, '');
  assert.equal(applied.length, 1);
  assert.equal(form.message.value, '');
});

test('confirmed workspace settings cannot be overwritten by an older in-flight refresh', async () => {
  const oldRead = deferred();
  const api = url(
    `export const env = { handler: null }; export const readyTransport = async () => {}; export const command = (action, params) => env.handler(action, params);`,
  );
  const dependencies = {
    vue: import.meta.resolve('vue'),
    '../api/client': api,
    './useNotifications': url('export const useNotifications = () => ({ push() {} });'),
    './useAgentDrafts': url(
      'export const agentDrafts = { retain() {}, discard() {}, activate() {} };',
    ),
    './useWorkflowDrafts': url(
      'export const workflowDrafts = { retain() {}, discard() {}, activate() {}, reconcile() {} };',
    ),
  };
  const actualWorkspace = url(
    compile(source('composables/useWorkspace.ts')).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => `from ${JSON.stringify(dependencies[name])}`,
    ),
  );
  const { env } = await import(api);
  env.handler = (action) => (action === 'projects.list' ? Promise.resolve([]) : oldRead.promise);
  const { useWorkspace } = await import(actualWorkspace);
  const workspace = useWorkspace();
  const refreshing = workspace.refresh();
  const confirmed = modelSettings();
  confirmed.provider.config.model = 'confirmed-new-model';
  workspace.applySettings(confirmed);
  oldRead.resolve(modelSettings());
  await refreshing;
  assert.equal(workspace.state.settings.provider.config.model, 'confirmed-new-model');
});

test('actual loading errors disable both tool sections and retry restores only confirmed settings', async (t) => {
  let fail = true;
  const { host, button } = await mount(t, async (action, params, saved) => {
    if (fail) throw new Error('synthetic loading failure');
    return copy(saved);
  });
  button('搜索与工具').click();
  await tick();
  assert.equal(host.querySelector('.search-settings-form fieldset').disabled, true);
  assert.equal(host.querySelector('.vision-settings-form fieldset').disabled, true);
  assert.match(
    host.querySelector('.settings-load-state [role="alert"]').textContent,
    /synthetic loading failure/,
  );
  fail = false;
  button('重新读取工具设置').click();
  await tick();
  assert.equal(host.querySelector('.settings-load-state'), null);
  assert.equal(host.querySelector('.search-settings-form fieldset').disabled, false);
});

test('workspace busy lease rejects conflicts and only its owner can release it', async () => {
  const calls = [];
  const workspace = await isolatedWorkspace(async (action) => {
    calls.push(action);
  });
  workspace.setBusy(true);
  assert.equal(workspace.reserveSettingsOperation(), null);
  workspace.setBusy(false);
  const release = workspace.reserveSettingsOperation();
  assert.equal(workspace.state.busy, true);
  workspace.setBusy(false);
  assert.equal(workspace.state.busy, true);
  assert.equal(workspace.reserveSettingsOperation(), null);
  assert.equal(await workspace.perform('projects.create', { name: 'must not start' }), undefined);
  assert.deepEqual(calls, []);
  release();
  assert.equal(workspace.state.busy, false);
  const next = workspace.reserveSettingsOperation();
  release();
  assert.equal(workspace.state.busy, true);
  next();
  assert.equal(workspace.state.busy, false);
});

test('busy workspace blocks conflicting model and tool commands while allowing draft edits', async (t) => {
  const calls = [],
    shared = reactive({ busy: true });
  const model = scoped(t, () =>
    useModelSettings(
      modelSettings(),
      async (action) => {
        calls.push(action);
      },
      () => {},
      { blocked: () => shared.busy },
    ),
  );
  const research = scoped(t, () =>
    useResearchSettings(
      async (action) => {
        calls.push(action);
        return researchSettings();
      },
      () => shared.busy,
    ),
  );
  await research.load();
  model.fields.value.model = 'editable draft';
  model.apiKey.value = 'synthetic';
  research.provider.value = 'tavily';
  research.apiKey.value = 'synthetic-tools';
  research.vision.value = true;
  await model.save();
  await model.test();
  await research.saveSearch();
  await research.saveVision();
  assert.deepEqual(calls, ['research.settings']);
  assert.equal(model.fields.value.model, 'editable draft');
  assert.equal(model.apiKey.value, 'synthetic');
  assert.equal(research.apiKey.value, 'synthetic-tools');
  assert.equal(research.vision.value, true);
});

test('actual Agent-busy screen explains disabled saves but keeps category/draft controls usable', async (t) => {
  const { host, calls, button, workspace } = await mount(t);
  workspace.setBusy(true);
  await tick();
  assert.equal(button('保存模型配置').disabled, true);
  assert.equal(button('测试已保存的连接').disabled, true);
  assert.equal(host.querySelector('.model-settings-form fieldset').disabled, false);
  assert.match(host.querySelector('.model-settings-form .waiting').textContent, /仍可编辑草稿/);
  await input(host.querySelector('input[aria-label="模型 API Key"]'), 'synthetic');
  await submit(host.querySelector('.model-settings-form'));
  button('搜索与工具').click();
  await tick();
  assert.equal(host.querySelector('h1').textContent, '搜索与工具');
  assert.equal(button('保存搜索设置').disabled, true);
  assert.equal(button('保存视觉设置').disabled, true);
  assert.equal(host.querySelector('.search-settings-form fieldset').disabled, false);
  assert.equal(calls.filter((call) => call.action !== 'research.settings').length, 0);
  workspace.setBusy(false);
});

for (const editAfterRemount of [false, true])
  test(`pending save survives remount and ${editAfterRemount ? 'preserves newer draft before a later confirmed save' : 'updates the pristine form to confirmed config'}`, async (t) => {
    const oldPending = deferred(),
      newerPending = deferred(),
      saves = [];
    const handler = async (action, params, saved) => {
      if (action === 'settings.save') {
        saves.push(params);
        return saves.length === 1 ? oldPending.promise : newerPending.promise;
      }
      return copy(saved);
    };
    const old = await mount(t, handler);
    const modelInput = (host) =>
      [...host.querySelectorAll('.model-settings-form label')]
        .find((label) => label.textContent.includes('模型名称'))
        .querySelector('input');
    await input(modelInput(old.host), 'older-confirmed');
    await input(old.host.querySelector('input[aria-label="模型 API Key"]'), 'synthetic-old');
    await submit(old.host.querySelector('.model-settings-form'));
    assert.equal(old.workspace.state.busy, true);
    old.unmount();
    assert.equal(old.workspace.state.busy, true);
    const reopened = await mount(t, handler, old.workspace);
    assert.equal(reopened.button('保存模型配置').disabled, true);
    if (editAfterRemount) {
      await input(modelInput(reopened.host), 'newer-draft');
      await input(reopened.host.querySelector('input[aria-label="模型 API Key"]'), 'synthetic-new');
      await submit(reopened.host.querySelector('.model-settings-form'));
      assert.equal(saves.length, 1);
    }
    const oldResult = modelSettings();
    oldResult.provider.config.model = 'older-confirmed';
    oldPending.resolve(oldResult);
    await tick();
    assert.equal(old.workspace.state.settings.provider.config.model, 'older-confirmed');
    assert.equal(old.workspace.state.busy, false);
    assert.equal(
      modelInput(reopened.host).value,
      editAfterRemount ? 'newer-draft' : 'older-confirmed',
    );
    assert.equal(
      reopened.host.querySelector('.model-settings-form .settings-status-tag').textContent,
      editAfterRemount ? '未保存' : '已保存',
    );
    if (editAfterRemount) {
      assert.equal(
        reopened.host.querySelector('input[aria-label="模型 API Key"]').value,
        'synthetic-new',
      );
      await submit(reopened.host.querySelector('.model-settings-form'));
      assert.equal(saves.length, 2);
      assert.equal(saves[1].config.model, 'newer-draft');
      const result = modelSettings();
      result.provider.config.model = 'newer-draft';
      newerPending.resolve(result);
      await tick();
      assert.equal(old.workspace.state.settings.provider.config.model, 'newer-draft');
      assert.equal(old.workspace.state.busy, false);
    }
  });

test('different settings-view request queues cannot overtake an earlier pending operation', async () => {
  const pending = deferred(),
    started = [];
  const oldView = queuedSettingsCommand(async () => {
    started.push('old');
    return pending.promise;
  });
  const newView = queuedSettingsCommand(async () => {
    started.push('new');
    return 'new confirmed';
  });
  const old = oldView('settings.save'),
    next = newView('settings.save');
  await tick();
  assert.deepEqual(started, ['old']);
  pending.resolve('old confirmed');
  assert.equal(await old, 'old confirmed');
  assert.equal(await next, 'new confirmed');
  assert.deepEqual(started, ['old', 'new']);
});
