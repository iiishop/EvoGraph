import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const vueUrl = pathToFileURL(require.resolve('vue')).href;
const draftCode = transpile(source('composables/useAgentDrafts.ts')).replace(
  "from 'vue'",
  `from ${JSON.stringify(vueUrl)}`,
);
const { createAgentDraftStore } = await import(moduleUrl(draftCode));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
const tick = async () => {
  await nextTick();
  await nextTick();
};

test('project drafts preserve exact whitespace, empty edits, and independent attachment snapshots', () => {
  const store = createAgentDraftStore();
  const a = store.bind(() => 'A'),
    b = store.bind(() => 'B');
  const ids = ['one', 'two'];
  a.content.value = ' \n A draft \n ';
  a.attachmentIds.value = ids;
  ids.push('external mutation');
  assert.deepEqual(a.attachmentIds.value, ['one', 'two']);
  a.attachmentIds.value.push('read mutation');
  assert.deepEqual(store.bind(() => 'A').attachmentIds.value, ['one', 'two']);
  assert.equal(b.content.value, '');
  b.content.value = 'B draft';
  assert.equal(store.bind(() => 'A').content.value, ' \n A draft \n ');
  a.content.value = '';
  a.attachmentIds.value = [];
  assert.equal(store.bind(() => 'A').content.value, '');
  assert.deepEqual(store.bind(() => 'A').attachmentIds.value, []);
  assert.equal(b.content.value, 'B draft');
  assert.equal(store.start('A', { text: ' \n ', ids: [] }), undefined);
});

test('failure restores only its original project and preserves the original question context', () => {
  const store = createAgentDraftStore();
  const a = store.bind(() => 'A'),
    b = store.bind(() => 'B');
  a.content.value = ' \n exact request \n ';
  a.attachmentIds.value = ['A-file'];
  const attempt = store.start('A', {
    text: a.content.value,
    ids: a.attachmentIds.value,
    questionId: 'Q-original',
  });
  assert.equal(a.content.value, '');
  b.content.value = 'B stays here';
  b.attachmentIds.value = ['B-file'];
  assert.equal(store.settle(attempt, false), true);
  assert.equal(a.content.value, ' \n exact request \n ');
  assert.deepEqual(a.attachmentIds.value, ['A-file']);
  assert.equal(b.content.value, 'B stays here');
  assert.deepEqual(b.attachmentIds.value, ['B-file']);
  const retried = store.retry('A', a.failures.value[0].id);
  assert.equal(retried.questionId, 'Q-original');
  assert.equal(a.content.value, '');
  store.settle(retried, true);
  assert.deepEqual(a.failures.value, []);
  assert.equal(a.content.value, '');
});

test('late failure and retries preserve newer drafts, including an intentional newer empty draft', () => {
  for (const newer of ['newer request', '']) {
    const store = createAgentDraftStore();
    const a = store.bind(() => 'A');
    a.content.value = 'older request';
    const attempt = store.start('A', { text: a.content.value, ids: ['old-file'] });
    a.content.value = 'typing after send';
    a.content.value = newer;
    a.attachmentIds.value = ['new-file'];
    assert.equal(store.settle(attempt, false), false);
    assert.equal(a.content.value, newer);
    assert.deepEqual(a.attachmentIds.value, ['new-file']);
    assert.equal(a.failures.value[0].text, 'older request');
    const retry = store.retry('A', a.failures.value[0].id);
    assert.equal(a.content.value, newer);
    assert.deepEqual(a.attachmentIds.value, ['new-file']);
    store.settle(retry, false);
    assert.equal(a.content.value, newer);
    assert.equal(a.failures.value.length, 1);
    store.dismissFailure('A', a.failures.value[0].id);
    assert.equal(a.content.value, newer);
    assert.deepEqual(a.failures.value, []);
  }
});

test('success never clears newer edits and duplicate completions cannot resurrect a request', () => {
  const store = createAgentDraftStore();
  const a = store.bind(() => 'A');
  a.content.value = 'sent';
  const attempt = store.start('A', { text: a.content.value, ids: [] });
  assert.equal(store.start('A', { text: 'second', ids: [] }), undefined);
  a.content.value = 'newer';
  a.attachmentIds.value = ['new'];
  store.settle(attempt, true);
  store.settle(attempt, false);
  assert.equal(a.content.value, 'newer');
  assert.deepEqual(a.attachmentIds.value, ['new']);
  assert.deepEqual(a.failures.value, []);
});

test('repeated failures retain separate requests and ordinary resubmission consumes its restored recovery', () => {
  const store = createAgentDraftStore();
  const a = store.bind(() => 'A');
  a.content.value = 'first';
  const first = store.start('A', { text: a.content.value, ids: [] });
  a.content.value = 'second';
  store.settle(first, false);
  const second = store.start('A', { text: a.content.value, ids: [] });
  store.settle(second, false);
  assert.deepEqual(
    a.failures.value.map((item) => item.text),
    ['first', 'second'],
  );
  const resubmission = store.start('A', { text: a.content.value, ids: [] });
  store.settle(resubmission, true);
  assert.deepEqual(
    a.failures.value.map((item) => item.text),
    ['first'],
  );
  assert.equal(a.content.value, '');
});

test('deletion blocks stale writes and settlements, and undo creates a fresh project incarnation', () => {
  const store = createAgentDraftStore();
  const a = store.bind(() => 'A');
  a.content.value = 'old incarnation';
  const attempt = store.start('A', { text: a.content.value, ids: ['old-file'] });
  store.discard('A');
  a.content.value = 'late unmounted callback';
  a.attachmentIds.value = ['late-file'];
  assert.equal(a.content.value, '');
  assert.deepEqual(a.attachmentIds.value, []);
  assert.equal(store.start('A', { text: 'stale', ids: [] }), undefined);
  store.activate('A');
  a.content.value = 'restored project new draft';
  store.settle(attempt, false);
  assert.equal(a.content.value, 'restored project new draft');
  assert.deepEqual(a.failures.value, []);
  store.retain([]);
  assert.equal(a.content.value, '');
});

function element(tag = 'root') {
  return {
    tag,
    tagName: tag.toUpperCase(),
    children: [],
    parent: null,
    style: {},
    value: '',
    listeners: {},
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    removeEventListener(name) {
      delete this.listeners[name];
    },
    setAttribute(name, value) {
      this[name] = value;
    },
    removeAttribute(name) {
      delete this[name];
    },
    focus() {
      this.focused = true;
    },
    getRootNode() {
      return globalThis.document;
    },
  };
}
const renderer = createRenderer({
  createElement: element,
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText(node, text) {
    node.text = text;
  },
  setElementText(node, text) {
    node.text = text;
    node.children = [];
  },
  patchProp(node, key, previous, value) {
    node[key] = value;
  },
  insert(node, parent, anchor) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
    node.parent = parent;
    const i = anchor ? parent.children.indexOf(anchor) : -1;
    if (i < 0) parent.children.push(node);
    else parent.children.splice(i, 0, node);
  },
  remove(node) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null,
});
const all = (node) => [node, ...(node.children ?? []).flatMap(all)];
const textOf = (node) => `${node.text ?? ''}${(node.children ?? []).map(textOf).join('')}`;
let harnessSequence = 0;
async function harness() {
  const id = ++harnessSequence;
  const draftsUrl = moduleUrl(`${draftCode}\n// harness ${id}`);
  const agentUrl = moduleUrl(`import { reactive } from ${JSON.stringify(vueUrl)};
    export const env = { calls: [], send: async () => true };
    export const agent = { state: reactive({ running: false, projectId: '', label: '', follow: {}, navigationTick: 0 }),
      send: (...args) => { env.calls.push(args); return env.send(...args); }, stop() {}, freeView() {}, resumeFollow() {} };
    export const useAgent = () => agent; // ${id}`);
  const workspaceUrl = moduleUrl(`import { reactive, computed } from ${JSON.stringify(vueUrl)};
    const project = (id) => ({ id, name: id, repository: '', messages: [], milestones: [], source_milestones: [], attachments: [] });
    export const workspace = { state: reactive({ project: project('A'), projects: [], page: 'projects', loading: false, busy: false, settings: { provider: { config: { model: 'Test model' } } } }),
      selected: computed(() => null), calls: [], init() {}, dismiss() {}, undoDelete() {},
      setPage(page) { this.state.page = page; }, setError(error) { this.state.error = error; },
      selectProject: async (id) => { workspace.calls.push(id); workspace.state.project = project(id); workspace.state.page = 'projects'; } };
    workspace.setPage = workspace.setPage.bind(workspace); workspace.setError = workspace.setError.bind(workspace);
    export const useWorkspace = () => workspace; // ${id}`);
  const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
  const imports = {
    vue: vueUrl,
    'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
    '../../composables/useAgentDrafts': draftsUrl,
    '../../composables/useAgent': agentUrl,
    '../../composables/useWorkspace': workspaceUrl,
    './composables/useWorkspace': workspaceUrl,
    '../../lib/turnSummary': moduleUrl(transpile(source('lib/turnSummary.ts'))),
    '../../api/client': moduleUrl('export const command = async () => ({ items: [] });'),
    '../../composables/useEntrance': moduleUrl('export const useEntrance = () => {};'),
    '../../lib/workspaceViews': moduleUrl(
      `export const workspaceViews = ['graph', 'architecture'].map(id => ({ id, component: { render() { return null; } } }));`,
    ),
    '../graph/GraphToolbar.vue': moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['tab'], emits: ['tab'], setup(props, { emit }) { return () => h('toolbar', { tab: props.tab, change: tab => emit('tab', tab) }); } };`),
    './AgentQuestion.vue': moduleUrl(
      `import { h } from ${JSON.stringify(vueUrl)}; export default { emits: ['choose'], setup(_, { emit }) { return () => h('question', { choose: text => emit('choose', text) }); } };`,
    ),
    '../attachments/AttachmentPicker.vue': moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['modelValue'], emits: ['update:modelValue'], setup(props, { emit }) { return () => h('attachments', { selected: props.modelValue, choose: ids => emit('update:modelValue', ids) }); } };`),
  };
  for (const name of [
    './AgentTurnSummary.vue',
    '../graph/FollowAgentButton.vue',
    './ReferenceMentionPicker.vue',
    './WorkspaceHeader.vue',
    '../graph/MilestoneGraph.vue',
    '../graph/MilestoneInspector.vue',
    '../graph/SourceInspector.vue',
    './components/sidebar/AppSidebar.vue',
    './components/projects/ProjectDialog.vue',
    './components/ui/NotificationStack.vue',
  ])
    imports[name] = stub;
  async function component(path) {
    const { descriptor } = parse(source(path));
    const code = transpile(
      compileScript(descriptor, { id: path, inlineTemplate: true }).content,
    ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
      assert.ok(imports[name], `Unexpected dependency ${name}`);
      return `from ${JSON.stringify(imports[name])}`;
    });
    const url = moduleUrl(code);
    return { url, component: (await import(url)).default };
  }
  imports['../agent/AgentDock.vue'] = (await component('components/agent/AgentDock.vue')).url;
  const project = await component('components/workspace/ProjectWorkspace.vue');
  imports['./lib/navigation'] = moduleUrl(`import Project from ${JSON.stringify(project.url)};
    export const navigation = [{ id: 'projects', component: Project, countProjects: true }, { id: 'settings', component: { render() { return null; } }, countProjects: false }];`);
  const App = (await component('App.vue')).component;
  const root = element();
  globalThis.Document = class {};
  globalThis.ShadowRoot = class {};
  globalThis.document = Object.assign(new Document(), { activeElement: null });
  const app = renderer.createApp(App);
  app.mount(root);
  const { workspace } = await import(workspaceUrl);
  const { agent, env } = await import(agentUrl);
  const { agentDrafts } = await import(draftsUrl);
  const find = (tag) => all(root).find((node) => node.tag === tag);
  const button = (text) =>
    all(root).find((node) => node.tag === 'button' && textOf(node).trim() === text);
  return {
    root,
    workspace,
    agent,
    env,
    agentDrafts,
    find,
    button,
    input: async (text) => {
      const node = find('textarea');
      node.value = text;
      node.listeners.input({ target: node });
      await tick();
    },
    attach: async (ids) => {
      find('attachments').choose(ids);
      await tick();
    },
    submit: () => find('form').onSubmit({ preventDefault() {} }),
    dispose: () => {
      app.unmount();
      delete globalThis.document;
      delete globalThis.Document;
      delete globalThis.ShadowRoot;
    },
  };
}

test('actual App keyed unmounts preserve Settings and A→B→A drafts; graph/architecture keeps the same composer', async () => {
  const h = await harness();
  try {
    await h.input(' \n A unsent \n ');
    await h.attach(['A1', 'A2']);
    const original = h.find('textarea');
    h.workspace.setPage('settings');
    await tick();
    assert.equal(h.find('textarea'), undefined);
    h.workspace.setPage('projects');
    await tick();
    assert.notEqual(h.find('textarea'), original, 'Settings must really unmount the composer');
    assert.equal(h.find('textarea').value, ' \n A unsent \n ');
    assert.deepEqual(h.find('attachments').selected, ['A1', 'A2']);
    await h.workspace.selectProject('B');
    await tick();
    assert.equal(h.find('textarea').value, '');
    assert.deepEqual(h.find('attachments').selected, []);
    await h.input('B unsent');
    await h.attach(['B1']);
    await h.workspace.selectProject('A');
    await tick();
    assert.equal(h.find('textarea').value, ' \n A unsent \n ');
    assert.deepEqual(h.find('attachments').selected, ['A1', 'A2']);
    const current = h.find('textarea');
    h.find('toolbar').change('architecture');
    await tick();
    assert.equal(h.find('textarea'), current);
    assert.equal(current.value, ' \n A unsent \n ');
    h.find('toolbar').change('graph');
    await tick();
    assert.equal(h.find('textarea'), current);
    await h.input('');
    await h.attach([]);
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    assert.equal(h.find('textarea').value, '');
    assert.deepEqual(h.find('attachments').selected, []);
    await h.workspace.selectProject('B');
    await tick();
    assert.equal(h.find('textarea').value, 'B unsent');
  } finally {
    h.dispose();
  }
});

test('an unmounted submit restores exact text and IDs to A without writing or focusing B', async () => {
  const h = await harness();
  try {
    const outcome = deferred();
    h.env.send = () => outcome.promise;
    await h.input(' \n original request \n ');
    await h.attach(['A1']);
    const sending = h.submit();
    await tick();
    assert.equal(h.find('textarea').value, '');
    assert.deepEqual(h.env.calls[0], ['A', 'original request', undefined, ['A1']]);
    await h.workspace.selectProject('B');
    await tick();
    await h.input('B newer');
    await h.attach(['B1']);
    const b = h.find('textarea');
    outcome.resolve(false);
    await sending;
    await tick();
    assert.equal(b.value, 'B newer');
    assert.equal(b.focused, undefined);
    await h.workspace.selectProject('A');
    await tick();
    assert.equal(h.find('textarea').value, ' \n original request \n ');
    assert.deepEqual(h.find('attachments').selected, ['A1']);
    assert.match(textOf(h.root), /内容已放回输入框/);
  } finally {
    h.dispose();
  }
});

test('late failure, remount, and explicit retry preserve newer text and attachments in the same project', async () => {
  const h = await harness();
  try {
    const outcome = deferred();
    h.env.send = () => outcome.promise;
    await h.input('older A');
    await h.attach(['old']);
    const sending = h.submit();
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    await h.input('newer A');
    await h.attach(['new']);
    outcome.resolve(false);
    await sending;
    await tick();
    assert.equal(h.find('textarea').value, 'newer A');
    assert.deepEqual(h.find('attachments').selected, ['new']);
    assert.match(textOf(h.root), /当前草稿未改动/);
    assert.match(textOf(h.root), /older A/);
    h.env.send = async () => true;
    await h.button('重试这条请求').onClick();
    await tick();
    assert.deepEqual(h.env.calls[1], ['A', 'older A', undefined, ['old']]);
    assert.equal(h.find('textarea').value, 'newer A');
    assert.deepEqual(h.find('attachments').selected, ['new']);
    assert.equal(h.button('重试这条请求'), undefined);
  } finally {
    h.dispose();
  }
});

test('a successful submission stays cleared through Settings; active-task View selects its actual project', async () => {
  const h = await harness();
  try {
    await h.input('delivered');
    await h.attach(['sent']);
    await h.submit();
    await tick();
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    assert.equal(h.find('textarea').value, '');
    assert.deepEqual(h.find('attachments').selected, []);
    await h.workspace.selectProject('B');
    await tick();
    Object.assign(h.agent.state, { running: true, projectId: 'A' });
    await tick();
    await h.button('查看').onClick();
    await tick();
    assert.equal(h.workspace.calls.at(-1), 'A');
    assert.equal(h.workspace.state.project.id, 'A');
    assert.match(textOf(h.root), /上一轮仍在处理/);
    await h.workspace.selectProject('B');
    h.workspace.state.settings = null;
    await tick();
    h.button('配置 Provider').onClick();
    await tick();
    assert.equal(h.workspace.state.page, 'settings');
  } finally {
    h.dispose();
  }
});

test('quick answers preserve unrelated typed drafts on success and failure, retaining question context', async () => {
  for (const delivered of [false, true]) {
    const h = await harness();
    try {
      h.workspace.state.project.question = { id: 'Q1', prompt: 'Choose', options: ['option'] };
      await h.input('unrelated typed plan');
      await h.attach(['draft-file']);
      h.env.send = async () => delivered;
      h.find('question').choose('option');
      await tick();
      assert.deepEqual(h.env.calls[0], ['A', 'option', 'Q1', ['draft-file']]);
      assert.equal(h.find('textarea').value, 'unrelated typed plan');
      assert.deepEqual(h.find('attachments').selected, ['draft-file']);
      if (!delivered) {
        assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].questionId, 'Q1');
        assert.match(textOf(h.root), /当前草稿未改动/);
      }
    } finally {
      h.dispose();
    }
  }
});

test('another project running allows draft edits but blocks sending; the active project stays disabled', async () => {
  const h = await harness();
  try {
    Object.assign(h.agent.state, { running: true, projectId: 'A' });
    h.workspace.state.busy = true;
    await tick();
    assert.equal(h.find('textarea').disabled, true);
    await h.workspace.selectProject('B');
    await tick();
    assert.equal(h.find('textarea').disabled, false);
    await h.input('B draft while A works');
    await h.submit();
    await tick();
    assert.equal(h.env.calls.length, 0);
    assert.equal(h.find('textarea').value, 'B draft while A works');
    await h.button('查看').onClick();
    await tick();
    assert.equal(h.find('textarea').disabled, true);
    await h.workspace.selectProject('B');
    await tick();
    assert.equal(h.find('textarea').value, 'B draft while A works');
  } finally {
    h.dispose();
  }
});
