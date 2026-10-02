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
  'KeyboardEvent',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, reactive, nextTick } = await import('vue');
const vue = import.meta.resolve('vue');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const read = (path) => readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const draftsUrl = url(
  compile(read('composables/useWorkflowDrafts.ts')).replace(
    "from 'vue'",
    `from ${JSON.stringify(vue)}`,
  ),
);
const { createWorkflowDrafts, workflowDrafts, runWorkflowAction } = await import(draftsUrl);
const envUrl = url(`import { reactive } from ${JSON.stringify(vue)};
export const env = { state: reactive({ busy: false, project: null, selectedId: null, error: 'untouched global error' }), mutate: async () => ({}), sync: async () => {}, calls: [] };
export const command = async (action, params) => { env.calls.push({ action, params }); return env.mutate(action, params); };
export const useWorkspace = () => ({ state: env.state, refresh: () => env.sync(), setBusy: (value) => env.state.busy = value, selectNode: (id) => env.state.selectedId = id });
export const useAgent = () => ({ send() {} });`);
const { env } = await import(envUrl);
const imports = {
  vue,
  '../../api/client': envUrl,
  '../../composables/useWorkspace': envUrl,
  '../../composables/useAgent': envUrl,
  '../../composables/useWorkflowDrafts': draftsUrl,
  './ObligationItem.vue': url('export default { render() { return null; } };'),
};
const { descriptor } = parse(read('components/graph/TaskWorkflow.vue'));
const Workflow = (
  await import(
    url(
      compile(compileScript(descriptor, { id: 'workflow', inlineTemplate: true }).content).replace(
        /from (['"])([^'"]+)\1/g,
        (_, q, name) => {
          assert.ok(imports[name], name);
          return `from ${JSON.stringify(imports[name])}`;
        },
      ),
    )
  )
).default;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const flush = async () => {
  await nextTick();
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
let sequence = 0;
const node = (id, extra = {}) => ({
  id,
  title: `Title ${id}`,
  status: 'AWAITING_ACCEPTANCE',
  lease_active: true,
  dependencies: [],
  dependency_reasons: {},
  resources: [],
  obligations: [],
  ...extra,
});
function harness() {
  const projectId = `P${++sequence}`;
  const project = {
    id: projectId,
    readiness: {
      A: { safe_to_execute: true, blockers: [] },
      B: { safe_to_execute: true, blockers: [] },
    },
  };
  const current = reactive({ project, milestone: node('A') });
  env.state.project = project;
  env.state.selectedId = 'A';
  env.state.busy = false;
  env.calls = [];
  env.mutate = async () => ({});
  env.sync = async () => {};
  const copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (value) => copied.push(value) },
  });
  let app, root;
  function mount() {
    root = document.createElement('div');
    document.body.append(root);
    app = createApp({ render: () => h(Workflow, current) });
    app.mount(root);
  }
  function unmount() {
    app.unmount();
    root.remove();
  }
  mount();
  const select = async (id) => {
    current.milestone = node(id);
    env.state.selectedId = id;
    await flush();
  };
  return {
    projectId,
    current,
    copied,
    mount,
    unmount,
    select,
    get root() {
      return root;
    },
    draft: (id = 'A') => workflowDrafts.entry(projectId, id),
  };
}
function click(harness, text) {
  const button = [...harness.root.querySelectorAll('button')].find(
    (item) => item.textContent.trim() === text,
  );
  assert.ok(button, text);
  button.click();
}
async function input(harness, value) {
  const textarea = harness.root.querySelector('#acceptance-report');
  assert.ok(textarea);
  textarea.value = value;
  textarea.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await flush();
}

test('report bindings survive inspector unmount, nodes and identical IDs in different projects', async () => {
  const view = harness();
  await input(view, ' exact A\n draft ');
  await view.select('B');
  await input(view, 'B draft');
  await view.select('A');
  assert.equal(view.root.querySelector('textarea').value, ' exact A\n draft ');
  view.unmount();
  view.mount();
  assert.equal(view.root.querySelector('textarea').value, ' exact A\n draft ');
  const other = workflowDrafts.bind(
    () => 'other-project',
    () => 'A',
  );
  other.report.value = 'other A';
  assert.equal(view.draft().report, ' exact A\n draft ');
  assert.equal(other.report.value, 'other A');
  view.unmount();
});

test('late handoff stores its original prompt after unmount without clipboard or global feedback', async () => {
  const view = harness(),
    action = deferred();
  env.mutate = () => action.promise;
  click(view, '重新生成验收提示词');
  await flush();
  view.unmount();
  action.resolve({ prompt: 'Original A prompt' });
  await flush();
  assert.equal(view.draft().prompt, 'Original A prompt');
  assert.deepEqual(view.copied, []);
  assert.match(view.draft().notice, /Title A/);
  assert.equal(env.state.error, 'untouched global error');
  view.mount();
  assert.ok(
    view.root.textContent.includes('Original A prompt') ||
      view.root.querySelector('textarea[readonly]').value === 'Original A prompt',
  );
  view.unmount();
});

test('A to B to A navigation invalidates old clipboard intent even if component is reused', async () => {
  const view = harness(),
    action = deferred();
  env.mutate = () => action.promise;
  click(view, '重新生成验收提示词');
  await flush();
  await view.select('B');
  await input(view, 'new B');
  await view.select('A');
  action.resolve({ prompt: 'A prompt' });
  await flush();
  assert.deepEqual(view.copied, []);
  assert.equal(view.draft('B').report, 'new B');
  assert.equal(view.draft('B').prompt, '');
  view.unmount();
});

test('confirmed report import preserves a newer edit and does not clear another node draft', async () => {
  const view = harness(),
    action = deferred();
  env.mutate = () => action.promise;
  await input(view, '{"result":"FAIL"}');
  click(view, '导入报告并更新状态');
  await flush();
  await input(view, 'newer A draft');
  await view.select('B');
  await input(view, 'B draft');
  action.resolve({ result: 'FAIL' });
  await flush();
  assert.equal(view.draft().report, 'newer A draft');
  assert.equal(view.draft('B').report, 'B draft');
  assert.match(view.draft().notice, /Title A.*未通过/);
  assert.equal(view.draft('B').notice, '');
  assert.deepEqual(env.calls[0].params, {
    project_id: view.projectId,
    milestone_id: 'A',
    report: { result: 'FAIL' },
  });
  view.unmount();
});

test('confirmed mutation remains confirmed after failed refresh and prevents duplicate submission', async () => {
  const view = harness();
  env.mutate = async () => ({ result: 'FAIL' });
  env.sync = async () => {
    throw new Error('sync offline');
  };
  await input(view, '{"result":"FAIL"}');
  click(view, '导入报告并更新状态');
  await flush();
  assert.equal(view.draft().report, '');
  assert.equal(view.draft().error, '');
  assert.match(view.draft().notice, /验收未通过/);
  assert.match(view.root.textContent, /操作已保存.*不要重复提交/);
  assert.equal(env.state.error, 'untouched global error');
  await input(view, '{"result":"FAIL"}');
  click(view, '导入报告并更新状态');
  await flush();
  assert.equal(env.calls.length, 1);
  env.sync = async () => {};
  click(view, '刷新状态');
  await flush();
  assert.equal(view.draft().syncError, '');
  view.unmount();
});

test('invalid JSON and rejected import preserve exact report text with origin-scoped errors', async () => {
  const view = harness();
  await input(view, ' invalid report ');
  click(view, '导入报告并更新状态');
  await flush();
  assert.equal(env.calls.length, 0);
  assert.equal(view.draft().report, ' invalid report ');
  assert.match(view.draft().error, /有效 JSON/);
  const action = deferred();
  env.mutate = () => action.promise;
  await input(view, ' {"result":"FAIL"} ');
  click(view, '导入报告并更新状态');
  await flush();
  await view.select('B');
  action.reject(new Error('A report rejected'));
  await flush();
  assert.equal(view.draft().report, ' {"result":"FAIL"} ');
  assert.equal(view.draft().error, 'A report rejected');
  assert.equal(view.draft('B').error, '');
  view.unmount();
});

test('deletion invalidates pending results and restored project gets a clean incarnation', async () => {
  const store = createWorkflowDrafts(),
    action = deferred();
  store.bind(
    () => 'P',
    () => 'A',
  ).report.value = 'old report';
  const attempt = store.start('P', 'A');
  const pending = runWorkflowAction({
    store,
    attempt,
    mutate: () => action.promise,
    refresh: async () => {},
    confirmed: (result, entry) => {
      entry.prompt = result.prompt;
    },
  });
  store.discard('P');
  store.activate('P');
  const next = store.entry('P', 'A');
  next.report = 'new incarnation';
  action.resolve({ prompt: 'stale' });
  const result = await pending;
  assert.equal(result.current, false);
  assert.equal(next.prompt, '');
  assert.equal(next.report, 'new incarnation');
  assert.equal(next.pending, null);
});

test('stage claim and release stay available as secondary workflow actions', async () => {
  const view = harness();
  view.current.milestone = node('A', { status: 'PLANNED', lease_active: false });
  await flush();
  click(view, '领取任务');
  await flush();
  assert.equal(env.calls[0].action, 'milestone.start');
  view.current.milestone = node('A', { status: 'IN_PROGRESS', lease_active: true });
  await flush();
  click(view, '暂时释放任务');
  await flush();
  assert.equal(env.calls[1].action, 'milestone.release');
  view.unmount();
});

test('baseline refresh sends only the strict backend project parameter; missing readiness is not success', async () => {
  const view = harness();
  view.current.milestone = node('A', { status: 'PLANNED', lease_active: false });
  view.current.project.readiness = {};
  await flush();
  assert.doesNotMatch(view.root.textContent, /前置条件已满足/);
  assert.match(view.root.textContent, /尚未确认全部前置条件/);
  click(view, '检查前置条件');
  await flush();
  assert.deepEqual(env.calls[0], {
    action: 'baseline.refresh',
    params: { project_id: view.projectId },
  });
  view.unmount();
});

test('existing workflow binding observes discard then restore without stale computed state', () => {
  const store = createWorkflowDrafts(),
    binding = store.bind(
      () => 'P',
      () => 'A',
    );
  binding.report.value = 'old';
  store.discard('P');
  assert.equal(binding.draft.value, undefined);
  store.activate('P');
  assert.ok(binding.draft.value);
  assert.equal(binding.report.value, '');
  binding.report.value = 'restored';
  assert.equal(store.entry('P', 'A').report, 'restored');
});

test('removed then recreated milestone gets fresh draft identity and rejects its late export', async () => {
  const store = createWorkflowDrafts(),
    action = deferred();
  store.reconcile('P', ['A', 'B']);
  const a = store.bind(
      () => 'P',
      () => 'A',
    ),
    b = store.bind(
      () => 'P',
      () => 'B',
    );
  a.report.value = 'old A';
  b.report.value = 'keep B';
  const attempt = store.start('P', 'A');
  const pending = runWorkflowAction({
    store,
    attempt,
    mutate: () => action.promise,
    refresh: async () => {},
    confirmed: (result, entry) => {
      entry.prompt = result.prompt;
    },
  });
  store.reconcile('P', ['B']);
  assert.equal(a.draft.value, undefined);
  assert.equal(store.entry('P', 'A'), undefined);
  store.reconcile('P', ['A', 'B']);
  assert.equal(a.report.value, '');
  assert.equal(b.report.value, 'keep B');
  a.report.value = 'new A';
  action.resolve({ prompt: 'old prompt' });
  const result = await pending;
  assert.equal(result.current, false);
  assert.equal(a.report.value, 'new A');
  assert.equal(a.draft.value.prompt, '');
});

test('newer unsubmitted report stays visible after a confirmed import changes the workflow stage', async () => {
  for (const status of ['VERIFIED_COMPLETE', 'IN_PROGRESS']) {
    const view = harness(),
      action = deferred();
    env.mutate = () => action.promise;
    env.sync = async () => {
      view.current.milestone.status = status;
    };
    await input(view, '{"result":"FAIL"}');
    click(view, '导入报告并更新状态');
    await flush();
    await input(view, ' exact newer\nreport ');
    action.resolve({ result: status === 'VERIFIED_COMPLETE' ? 'PASS' : 'FAIL' });
    await flush();
    assert.equal(view.root.querySelector('#acceptance-report'), null);
    assert.equal(view.root.querySelector('#retained-report').value, ' exact newer\nreport ');
    assert.match(view.root.textContent, /未提交的报告草稿/);
    assert.match(view.root.textContent, /未随上一份报告提交/);
    view.unmount();
  }
});
