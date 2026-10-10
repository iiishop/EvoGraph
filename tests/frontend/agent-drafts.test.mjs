import { markdownContentUrl } from './helpers/markdown-fixtures.mjs';
import { surfaceMotionUrl } from './helpers/surface-motion-fixtures.mjs';
import {
  composerDocumentUrl,
  composerEditorStubUrl,
  messageContentStubUrl,
} from './helpers/composer-fixtures.mjs';
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
  ts
    .transpileModule(code, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace(
      /(['"])(?:\.\.\/lib\/|\.\/|\.\.\/\.\.\/lib\/)composerDocument\1/g,
      JSON.stringify(composerDocumentUrl),
    );
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
    export const env = { calls: [], submissions: [], planningRequests: [], send: async () => true };
    export const agent = { state: reactive({ running: false, projectId: '', label: '', follow: {}, navigationTick: 0 }),
      send: (...args) => { env.calls.push(args.slice(0, 6)); env.submissions.push(args[6]); env.planningRequests.push(args[9]); return env.send(...args); }, stop() {}, freeView() {}, resumeFollow() {} };
    export const useAgent = () => agent; // ${id}`);
  const workspaceUrl = moduleUrl(`import { reactive, computed } from ${JSON.stringify(vueUrl)};
    const tabs = reactive({});
    const project = (id) => ({ id, name: id, repository: '', messages: [], milestones: [], source_milestones: [], attachments: [], question: null });
    export const workspace = { state: reactive({ selectedId: null, project: project('A'), projects: [], page: 'projects', loading: false, busy: false, settings: { provider: { config: { model: 'Test model' } } } }),
      bindWorkspaceTab: project => ({ key: project.id === 'A' ? 1 : 2, tab: computed({ get: () => tabs[project.id] ?? 'graph', set: value => { tabs[project.id] = value; } }) }),
      selected: computed(() => workspace.state.project.milestones.find(item => item.id === workspace.state.selectedId) ?? null), selectNode: id => { workspace.state.selectedId = id; }, calls: [], init() {}, dismiss() {}, undoDelete() {},
      applyProject: project => { if (workspace.state.project.id === project.id) workspace.state.project = project; },
      setPage(page) { this.state.page = page; }, setError(error) { this.state.error = error; },
      selectProject: async (id) => { workspace.calls.push(id); workspace.state.project = project(id); workspace.state.page = 'projects'; } };
    workspace.setPage = workspace.setPage.bind(workspace); workspace.setError = workspace.setError.bind(workspace);
    export const useWorkspace = () => workspace; // ${id}`);
  const apiUrl =
    moduleUrl(`export const api = { calls: [], catalog: [], load: async (id) => ({ id, name: id, repository: '', messages: [], milestones: [], source_milestones: [], attachments: [], question: null }) };
    export const command = async (action, params) => { api.calls.push([action, params]); return action === 'projects.get' ? api.load(params.project_id) : { items: api.catalog }; }; // ${id}`);
  const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
  const imports = {
    '../../composables/useSurfaceMotion': surfaceMotionUrl,
    './composables/useSurfaceMotion': surfaceMotionUrl,
    [composerDocumentUrl]: composerDocumentUrl,
    '../../lib/composerDocument': composerDocumentUrl,
    './ComposerEditor.vue': composerEditorStubUrl,
    './MessageContent.vue': messageContentStubUrl,
    './MarkdownContent': markdownContentUrl,
    vue: vueUrl,
    'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
    '../../composables/useAgentDrafts': draftsUrl,
    '../../composables/useAgent': agentUrl,
    '../../composables/useWorkspace': workspaceUrl,
    './composables/useWorkspace': workspaceUrl,
    '../../lib/turnSummary': moduleUrl(transpile(source('lib/turnSummary.ts'))),
    '../../lib/agentRetry': moduleUrl(transpile(source('lib/agentRetry.ts'))),
    '../../api/client': apiUrl,
    '../../composables/useEntrance': moduleUrl('export const useEntrance = () => {};'),
    '../../lib/workspaceViews': moduleUrl(
      `export const workspaceViews = ['graph', 'architecture'].map(id => ({ id, component: { render() { return null; } } }));`,
    ),
    '../graph/GraphToolbar.vue': moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['tab'], emits: ['tab'], setup(props, { emit }) { return () => h('toolbar', { tab: props.tab, change: tab => emit('tab', tab) }); } };`),
    './AgentReviewTray.vue': moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['project', 'summary', 'running', 'disabled', 'owner'], emits: ['locate'], setup(props) { return () => h('review-tray', { ...props }); } };`),
    './AgentQuestion.vue': moduleUrl(
      `import { h } from ${JSON.stringify(vueUrl)}; export default { emits: ['choose'], setup(_, { emit }) { return () => h('question', { choose: text => emit('choose', text) }); } };`,
    ),
    '../attachments/AttachmentPicker.vue': moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['modelValue'], emits: ['update:modelValue'], setup(props, { emit }) { return () => h('attachments', { selected: props.modelValue, choose: ids => emit('update:modelValue', ids) }); } };`),
  };
  for (const name of [
    '../attachments/AttachmentReceipt.vue',
    './AgentTurnSummary.vue',
    './PlanningJobPanel.vue',
    '../graph/FollowAgentButton.vue',
    './ReferenceMentionPicker.vue',
    './WorkspaceHeader.vue',
    './UnifiedPlanBar.vue',
    './PlanCandidatePanel.vue',
    '../graph/MilestoneGraph.vue',
    '../graph/MilestoneInspector.vue',
    '../graph/SourceInspector.vue',
    '../graph/MilestoneFinder.vue',
    './components/sidebar/AppSidebar.vue',
    './components/projects/ProjectDialog.vue',
    './components/projects/ProjectWelcome.vue',
    './components/projects/ProjectRecoveryDialog.vue',
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
  const question = await component('components/agent/AgentQuestion.vue');
  imports['./AgentQuestion.vue'] = moduleUrl(`import { h } from ${JSON.stringify(vueUrl)};
    import Question from ${JSON.stringify(question.url)};
    export default { props: ['question', 'answer', 'disabled'], emits: ['choose'], setup(props, { emit }) {
      return () => h('question', { choose: text => emit('choose', text) }, [h(Question, { ...props, onChoose: text => emit('choose', text) })]);
    } };`);
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
  const { api } = await import(apiUrl);
  const find = (tag) => all(root).find((node) => node.tag === tag);
  const button = (text) =>
    all(root).find((node) => node.tag === 'button' && textOf(node).trim() === text);
  return {
    root,
    workspace,
    agent,
    env,
    agentDrafts,
    api,
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
    assert.deepEqual(h.env.calls[0], [
      'A',
      'original request',
      undefined,
      ['A1'],
      undefined,
      { version: 1, parts: [{ type: 'text', text: 'original request' }] },
    ]);
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
    assert.deepEqual(h.env.calls[1], [
      'A',
      'older A',
      undefined,
      ['old'],
      undefined,
      { version: 1, parts: [{ type: 'text', text: 'older A' }] },
    ]);
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
      assert.deepEqual(h.env.calls[0], [
        'A',
        'option',
        'Q1',
        ['draft-file'],
        undefined,
        { version: 1, parts: [{ type: 'text', text: 'option' }] },
      ]);
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

test('retry preparation leaves the recovery intact and captures immutable original question context', () => {
  const store = createAgentDraftStore();
  const draft = store.bind(() => 'A');
  const question = {
    id: 'Q1',
    prompt: 'original prompt',
    context: 'original context',
    verification_milestone: 'M1',
  };
  draft.content.value = ' answer ';
  const attempt = store.start('A', {
    text: draft.content.value,
    ids: ['asset'],
    questionId: 'Q1',
    question,
  });
  question.prompt = 'external mutation';
  store.settle(attempt, false);
  const failure = draft.failures.value[0];
  const preparing = store.prepareRetry('A', failure.id);
  assert.equal(preparing.failure.question.prompt, 'original prompt');
  assert.equal(preparing.failure.verificationMilestone, 'M1');
  assert.equal(draft.failures.value[0].id, failure.id);
  assert.equal(draft.content.value, ' answer ');
  assert.equal(draft.pending.value, true);
  assert.equal(store.prepareRetry('A', failure.id), undefined);
  assert.equal(store.start('A', { text: 'new send', ids: [] }), undefined);
  store.cancelRetry(preparing);
  assert.equal(draft.pending.value, false);
  assert.equal(draft.failures.value[0].id, failure.id);
  const next = store.prepareRetry('A', failure.id);
  const retry = store.commitRetry(next, {
    text: 'outgoing continuation context',
    verificationMilestone: 'M1',
  });
  assert.equal(retry.text, ' answer ');
  assert.equal(retry.request.text, 'outgoing continuation context');
  store.settle(retry, false);
  assert.equal(draft.failures.value[0].text, ' answer ');
  assert.equal(draft.failures.value[0].request, undefined);
  assert.equal(draft.failures.value[0].question.prompt, 'original prompt');
});

const recoveryQuestion = (extra = {}) => ({
  id: 'Q1',
  prompt: '原路线选择？',
  context: '保留已经完成的模块',
  category: 'decision',
  options: ['路线 A'],
  verification_milestone: 'M1',
  ...extra,
});
const recoveryProject = (question = null, extra = {}) => ({
  id: 'A',
  name: 'A',
  repository: '',
  messages: [],
  milestones: [{ id: 'M1', title: 'Delivery' }],
  source_milestones: [],
  attachments: [],
  question,
  ...extra,
});
async function failedAnswer(h) {
  h.workspace.state.project = recoveryProject(recoveryQuestion());
  h.env.send = async () => false;
  await h.input(' \n路线 A \n ');
  await h.attach(['original-asset']);
  await h.submit();
  await tick();
  return h.agentDrafts.bind(() => 'A').failures.value[0];
}

test('explicit retry fetches the current project before resending a still-pending answer', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    h.api.load = async () => recoveryProject(recoveryQuestion());
    h.env.send = async () => {
      assert.equal(h.api.calls.at(-1)[0], 'projects.get');
      return true;
    };
    await h.button('重试这条请求').onClick();
    await tick();
    assert.deepEqual(h.env.calls[1], [
      'A',
      '路线 A',
      'Q1',
      ['original-asset'],
      'M1',
      { version: 1, parts: [{ type: 'text', text: '路线 A' }] },
    ]);
    assert.equal(h.find('textarea').value, '');
    assert.deepEqual(h.agentDrafts.bind(() => 'A').failures.value, []);
  } finally {
    h.dispose();
  }
});

test('consumed-answer retry survives navigation and repeated failure without wrapping the saved answer twice', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    h.api.load = async () => recoveryProject(null, { revision: 41 });
    h.env.send = async () => false;
    await h.button('重试这条请求').onClick();
    await tick();
    const first = h.env.calls[1];
    assert.equal(first[0], 'A');
    assert.equal(first[2], undefined);
    assert.equal(first[4], 'M1');
    assert.match(first[1], /当前已保存/);
    assert.match(first[1], /原路线选择/);
    assert.match(first[1], /路线 A/);
    assert.equal(h.workspace.state.project.revision, 41);
    assert.equal(h.find('textarea').value, ' \n路线 A \n ');
    const saved = h.agentDrafts.bind(() => 'A').failures.value[0];
    assert.equal(saved.questionId, 'Q1');
    assert.equal(saved.question.prompt, '原路线选择？');
    h.env.send = async () => true;
    await h.submit();
    await tick();
    assert.deepEqual(h.env.calls[2], first);
    assert.equal(h.find('textarea').value, '');
  } finally {
    h.dispose();
  }
});

test('source-answer retries keep their operation after question consumption and navigation, without affecting new prompts', async () => {
  const h = await harness();
  try {
    h.workspace.state.project = recoveryProject(recoveryQuestion({ verification_milestone: null }));
    h.env.send = async (...args) => {
      assert.equal(args[7], undefined);
      // The actual runtime receives this identity in the source turn's started frame.
      args[6].sourceAnalysis = true;
      h.workspace.state.project.question = null;
      return false;
    };
    await h.input('Inspect login');
    await h.submit();
    await tick();
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].sourceAnalysis, true);
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    h.api.load = async () => recoveryProject(null);
    h.env.send = async (...args) => {
      assert.equal(args[2], undefined, 'the old source question has already been consumed');
      assert.equal(args[7], true, 'only the explicit retry retains source analysis');
      return false;
    };
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].sourceAnalysis, true);
    await h.submit();
    await tick();
    assert.equal(h.env.calls.length, 3, 'untouched restored Send keeps the retry operation too');
    await h.input('Plan a genuinely new feature');
    h.env.send = async (...args) => {
      assert.equal(args[7], undefined, 'an edited new request must not inherit source analysis');
      return true;
    };
    await h.submit();
    await tick();
    assert.equal(h.env.calls.length, 4);
  } finally {
    h.dispose();
  }
});

test('new questions block explicit Retry and untouched restored Send without reusing the old answer', async () => {
  for (const viaSend of [false, true]) {
    const h = await harness();
    try {
      const saved = await failedAnswer(h);
      h.api.load = async () =>
        recoveryProject(recoveryQuestion({ id: 'Q2', prompt: '新的问题？' }));
      if (viaSend) {
        h.workspace.state.project.question = recoveryQuestion({ id: 'Q2', prompt: '新的问题？' });
        await tick();
        await h.submit();
      } else await h.button('重试这条请求').onClick();
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.equal(h.workspace.state.project.question.id, 'Q2');
      assert.equal(h.find('textarea').value, ' \n路线 A \n ');
      assert.deepEqual(h.find('attachments').selected, ['original-asset']);
      assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
      assert.match(h.workspace.state.error, /不会复用旧回答/);
      h.env.send = async () => true;
      h.find('question').choose('明确回答新问题');
      await tick();
      assert.equal(h.env.calls[1][1], '明确回答新问题');
      assert.equal(h.env.calls[1][2], 'Q2');
      assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    } finally {
      h.dispose();
    }
  }
});

test('a new-question option is an explicit new answer while the untouched old recovery stays retained', async () => {
  const h = await harness();
  try {
    const saved = await failedAnswer(h);
    h.workspace.state.project.question = recoveryQuestion({ id: 'Q2' });
    await tick();
    h.env.send = async () => true;
    h.find('question').choose('路线 A');
    await tick();
    assert.equal(h.env.calls[1][2], 'Q2');
    assert.equal(h.find('textarea').value, '');
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    assert.equal(
      h.api.calls.length,
      0,
      'Explicit current-question options are new answers, not old retries',
    );
  } finally {
    h.dispose();
  }
});

test('fresh-read failures and missing verification targets preserve recovery and release preparation', async () => {
  for (const reason of ['offline', 'missing-target']) {
    const h = await harness();
    try {
      const saved = await failedAnswer(h);
      h.api.load = async () => {
        if (reason === 'offline') throw new Error('fresh read offline');
        return recoveryProject(null, { milestones: [], source_milestones: [{ id: 'M1' }] });
      };
      await h.button('重试这条请求').onClick();
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
      assert.equal(h.find('textarea').value, ' \n路线 A \n ');
      assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
      assert.match(h.workspace.state.error, reason === 'offline' ? /offline/ : /M1.*已不存在/);
    } finally {
      h.dispose();
    }
  }
});

test('double-click retry coalesces its read and a newer draft survives the read and continuation failure', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    const retry = h.button('重试这条请求').onClick();
    await h.button('重试这条请求').onClick();
    assert.equal(h.api.calls.length, 1);
    await h.input('newer unsent request');
    await h.attach(['newer-asset']);
    read.resolve(recoveryProject());
    await retry;
    await tick();
    assert.equal(h.env.calls.length, 2);
    assert.equal(h.find('textarea').value, 'newer unsent request');
    assert.deepEqual(h.find('attachments').selected, ['newer-asset']);
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].text, ' \n路线 A \n ');
  } finally {
    h.dispose();
  }
});

test('navigation during the fresh read keeps the retry scoped to A without writing or focusing B', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    const retry = h.button('重试这条请求').onClick();
    await h.workspace.selectProject('B');
    await tick();
    await h.input('B draft');
    await h.attach(['B-asset']);
    const b = h.find('textarea');
    read.resolve(recoveryProject());
    await retry;
    await tick();
    assert.equal(h.env.calls[1][0], 'A');
    assert.equal(h.workspace.state.project.id, 'B');
    assert.equal(b.value, 'B draft');
    assert.equal(b.focused, undefined);
    assert.deepEqual(h.find('attachments').selected, ['B-asset']);
    assert.equal(h.agentDrafts.bind(() => 'A').content.value, ' \n路线 A \n ');
  } finally {
    h.dispose();
  }
});

test('another running turn during the fresh read leaves the recovery unsent and usable', async () => {
  const h = await harness();
  try {
    const saved = await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    const retry = h.button('重试这条请求').onClick();
    Object.assign(h.agent.state, { running: true, projectId: 'B' });
    h.workspace.state.busy = true;
    h.workspace.state.project = recoveryProject(recoveryQuestion({ id: 'Q-new' }), {
      revision: 42,
    });
    read.resolve(recoveryProject(null, { revision: 41 }));
    await retry;
    await tick();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
    assert.match(h.workspace.state.error, /其他操作正在进行/);
    assert.equal(h.workspace.state.project.revision, 42);
    assert.equal(h.workspace.state.project.question.id, 'Q-new');
  } finally {
    h.dispose();
  }
});

test('dismissal or deletion during a fresh read invalidates late sends and releases the reservation', async () => {
  for (const action of ['dismiss', 'delete-and-restore']) {
    const h = await harness();
    try {
      const saved = await failedAnswer(h);
      const read = deferred();
      h.api.load = () => read.promise;
      const retry = h.button('重试这条请求').onClick();
      if (action === 'dismiss') h.agentDrafts.dismissFailure('A', saved.id);
      else {
        h.agentDrafts.discard('A');
        h.agentDrafts.activate('A');
        h.agentDrafts.bind(() => 'A').content.value = 'new incarnation';
      }
      assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
      read.resolve(recoveryProject());
      await retry;
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.deepEqual(h.agentDrafts.bind(() => 'A').failures.value, []);
      if (action !== 'dismiss') assert.equal(h.find('textarea').value, 'new incarnation');
    } finally {
      h.dispose();
    }
  }
});

test('a delayed fresh read cannot roll back a newer visible project revision before retrying', async () => {
  const h = await harness();
  try {
    const saved = await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    const retry = h.button('重试这条请求').onClick();
    h.workspace.state.project = recoveryProject(recoveryQuestion({ id: 'Q-new' }), {
      revision: 42,
    });
    read.resolve(recoveryProject(null, { revision: 41 }));
    await retry;
    await tick();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.workspace.state.project.revision, 42);
    assert.equal(h.workspace.state.project.question.id, 'Q-new');
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
    assert.match(h.workspace.state.error, /读取期间已更新/);
  } finally {
    h.dispose();
  }
});

test('a retry read failure after navigation stays with its original project', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    const retry = h.button('重试这条请求').onClick();
    await h.workspace.selectProject('B');
    await tick();
    await h.input('B independent');
    read.reject(new Error('A read unavailable'));
    await retry;
    await tick();
    assert.equal(h.workspace.state.project.id, 'B');
    assert.equal(h.workspace.state.error, undefined);
    assert.equal(h.find('textarea').value, 'B independent');
    assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
    await h.workspace.selectProject('A');
    await tick();
    assert.match(textOf(h.root), /A read unavailable/);
    assert.equal(h.find('textarea').value, ' \n路线 A \n ');
  } finally {
    h.dispose();
  }
});

test('a newer draft survives a different-question retry block', async () => {
  const h = await harness();
  try {
    const saved = await failedAnswer(h);
    await h.input('newer edited draft');
    await h.attach(['newer-asset']);
    h.api.load = async () => recoveryProject(recoveryQuestion({ id: 'Q2' }));
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.find('textarea').value, 'newer edited draft');
    assert.deepEqual(h.find('attachments').selected, ['newer-asset']);
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    assert.equal(h.workspace.state.project.question.id, 'Q2');
  } finally {
    h.dispose();
  }
});

test('closing the failure hint or editing only attachments cannot rebind an untouched old answer to Q2', async () => {
  for (const change of ['dismiss-hint', 'attachments', 'both']) {
    const h = await harness();
    try {
      const saved = await failedAnswer(h);
      if (change !== 'attachments') {
        h.button('关闭提示').onClick();
        await tick();
      }
      if (change !== 'dismiss-hint') await h.attach(['new-context-asset']);
      h.workspace.state.project.question = recoveryQuestion({ id: 'Q2' });
      h.api.load = async () => recoveryProject(recoveryQuestion({ id: 'Q2' }));
      await tick();
      await h.submit();
      await tick();
      assert.equal(h.env.calls.length, 1, change);
      assert.equal(h.find('textarea').value, ' \n路线 A \n ', change);
      assert.deepEqual(
        h.find('attachments').selected,
        change === 'dismiss-hint' ? ['original-asset'] : ['new-context-asset'],
      );
      assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
      assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].text, ' \n路线 A \n ');
      assert.match(h.workspace.state.error, /不会复用旧回答/);
    } finally {
      h.dispose();
    }
  }
});

test('normal Send retries the original answer with intentionally edited attachments and clears only that snapshot', async () => {
  for (const question of [recoveryQuestion(), null]) {
    const h = await harness();
    try {
      await failedAnswer(h);
      h.button('关闭提示').onClick();
      await tick();
      await h.attach(['new-context-asset']);
      h.api.load = async () => recoveryProject(question);
      h.env.send = async () => true;
      await h.submit();
      await tick();
      assert.equal(h.env.calls.length, 2);
      assert.deepEqual(h.env.calls[1][3], ['new-context-asset']);
      assert.equal(h.env.calls[1][2], question?.id);
      assert.equal(h.env.calls[1][4], 'M1');
      assert.equal(h.find('textarea').value, '');
      assert.deepEqual(h.find('attachments').selected, []);
    } finally {
      h.dispose();
    }
  }
});

test('a real text edit after hint dismissal is a deliberate new answer', async () => {
  const h = await harness();
  try {
    await failedAnswer(h);
    h.button('关闭提示').onClick();
    await tick();
    h.workspace.state.project.question = recoveryQuestion({ id: 'Q2' });
    await tick();
    await h.input('new answer for Q2');
    h.env.send = async () => true;
    await h.submit();
    await tick();
    assert.equal(h.env.calls[1][1], 'new answer for Q2');
    assert.equal(h.env.calls[1][2], 'Q2');
    assert.equal(h.api.calls.length, 0);
  } finally {
    h.dispose();
  }
});

test('a hung retry read times out, preserves the recovery, releases pending, and ignores its late response', async () => {
  const h = await harness();
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  let expire,
    deadline,
    cleared = false;
  const token = {};
  try {
    const saved = await failedAnswer(h);
    const read = deferred();
    h.api.load = () => read.promise;
    globalThis.setTimeout = (callback, ms, ...args) => {
      if (ms !== 30000) return originalSetTimeout(callback, ms, ...args);
      expire = callback;
      deadline = ms;
      return token;
    };
    globalThis.clearTimeout = (timer) => {
      if (timer === token) cleared = true;
      else originalClearTimeout(timer);
    };
    const retry = h.button('重试这条请求').onClick();
    assert.equal(deadline, 30000);
    assert.equal(h.agentDrafts.bind(() => 'A').pending.value, true);
    expire();
    await retry;
    await tick();
    assert.equal(cleared, true);
    assert.equal(h.agentDrafts.bind(() => 'A').pending.value, false);
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].id, saved.id);
    assert.equal(h.find('textarea').value, ' \n路线 A \n ');
    assert.match(h.workspace.state.error, /超时/);
    read.resolve(recoveryProject());
    await tick();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.workspace.state.project.question.id, 'Q1');
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
    h.dispose();
  }
});

test('detached recovery identifies the original question with a bounded compact prompt preview', async () => {
  const h = await harness();
  try {
    const longPrompt = '原问题线索'.repeat(70);
    h.workspace.state.project = recoveryProject(recoveryQuestion({ prompt: longPrompt }));
    h.env.send = async () => false;
    await h.input('标题与作者');
    await h.submit();
    await tick();
    h.api.load = async () => recoveryProject(recoveryQuestion({ id: 'Q2', prompt: '新的问题' }));
    await h.button('重试这条请求').onClick();
    await tick();
    const preview = all(h.root).find((node) => node.class === 'agent-recovery-question');
    assert.ok(preview);
    assert.equal(textOf(preview), `原问题：${longPrompt.slice(0, 240)}…`);
    assert.equal(textOf(preview).length, 245);
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].question.prompt, longPrompt);
    assert.match(textOf(h.root), /标题与作者/);
  } finally {
    h.dispose();
  }
});

test('attachment preparation blocks sending with an upload explanation rather than a model-turn result message', async () => {
  const h = await harness();
  try {
    await h.input('draft waiting for its document');
    const transfer = h.agentDrafts.beginAttachmentTransfer('A', 1, 'preparing');
    await tick();
    assert.match(textOf(h.root), /正在读取资料格式，暂不能发送/);
    assert.doesNotMatch(textOf(h.root), /正在确认上一条请求的结果/);
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    h.agentDrafts.attachmentPhase(transfer, 'uploading', 0);
    await tick();
    assert.match(textOf(h.root), /资料正在保存，确认后可发送/);
    h.agentDrafts.finishAttachmentTransfer(transfer, null);
    await tick();
    assert.equal(h.find('textarea').value, 'draft waiting for its document');
  } finally {
    h.dispose();
  }
});

test('selecting and clearing a node keeps the same visible composer and attachment selection', async () => {
  const h = await harness();
  try {
    await h.input('Keep planning in one conversation');
    await h.attach(['A-material']);
    const input = h.find('textarea'),
      picker = h.find('attachments'),
      form = h.find('form');
    h.workspace.state.project.milestones = [{ id: 'M1', title: 'A planned slice' }];
    h.workspace.state.selectedId = 'M1';
    await tick();
    assert.equal(h.find('textarea'), input);
    assert.equal(h.find('attachments'), picker);
    assert.notEqual(h.find('form').style?.display, 'none');
    assert.equal(input.value, 'Keep planning in one conversation');
    assert.deepEqual(picker.selected, ['A-material']);
    h.workspace.state.selectedId = null;
    await tick();
    assert.equal(h.find('form'), form);
    assert.equal(h.find('textarea'), input);
    assert.equal(h.env.calls.length, 0);
  } finally {
    h.dispose();
  }
});

test('file drag discloses project persistence and the reference limit before drop', async () => {
  const h = await harness();
  try {
    const dock = all(h.root).find((node) =>
      String(node.class ?? '')
        .split(' ')
        .includes('agent-dock'),
    );
    assert.doesNotMatch(textOf(h.root), /松开以上传并保存到项目/);
    dock.onDragenter({ preventDefault() {}, dataTransfer: { types: ['Files'] } });
    await tick();
    assert.match(textOf(h.root), /松开以上传并保存到项目/);
    assert.match(textOf(h.root), /本条最多引用6份资料内容/);
    assert.equal(h.env.calls.length, 0);
    dock.onDragleave({ preventDefault() {} });
    await tick();
    assert.doesNotMatch(textOf(h.root), /松开以上传并保存到项目/);
  } finally {
    h.dispose();
  }
});

for (const attention of ['failed request', 'active upload', 'uncertain upload']) {
  test(`compact remount keeps ${attention} visible with an empty draft`, async () => {
    const h = await harness();
    try {
      h.workspace.state.project.milestones = [{ id: 'M1', title: 'Selected slice' }];
      h.workspace.state.selectedId = 'M1';
      await tick();
      if (attention === 'failed request') {
        h.env.send = async () => false;
        await h.input('Original failed request');
        await h.submit();
        await tick();
        await h.input('');
      } else {
        const transfer = h.agentDrafts.beginAttachmentTransfer('A', 1, 'uploading');
        if (attention === 'uncertain upload')
          h.agentDrafts.finishAttachmentTransfer(transfer, {
            assets: [],
            confirmedFiles: [],
            refresh: 'not_needed',
            unattempted: [],
            failure: { name: 'document.txt', status: 'unconfirmed', message: 'Result unknown' },
          });
      }
      h.workspace.setPage('settings');
      await tick();
      h.workspace.setPage('projects');
      await tick();
      const review = () => all(h.root).find((node) => node.class === 'agent-review');
      assert.ok(review());
      assert.notEqual(review().style?.display, 'none');
      assert.equal(h.find('textarea').value, '');
      assert.ok(h.button('收起项目对话'));
      h.workspace.state.selectedId = null;
      await tick();
      h.find('toolbar').change('architecture');
      await tick();
      assert.notEqual(review().style?.display, 'none');
      assert.ok(h.button('收起项目对话'));
      if (attention === 'failed request') assert.ok(h.button('重试这条请求'));
    } finally {
      h.dispose();
    }
  });
}

test('collapsing history keeps a pending question outside its scroller and preserves exact answer binding', async () => {
  const h = await harness();
  try {
    h.workspace.state.project.messages = [
      { id: 'old', role: 'assistant', content: 'Earlier result' },
    ];
    h.workspace.state.project.question = {
      id: 'Q-visible',
      prompt: 'Choose fields',
      context: 'Current contract',
      options: ['Title', 'Title and author'],
    };
    await h.input('A separate custom draft');
    const question = h.find('question');
    assert.equal(question.parent, h.find('form').parent);
    h.button('收起项目对话').onClick();
    await tick();
    assert.equal(h.find('question'), question);
    const review = h.find('review-tray');
    assert.equal(review.disabled, true);
    assert.notEqual(question.parent.style?.display, 'none');
    question.choose('Title and author');
    await tick();
    assert.equal(h.env.calls[0][1], 'Title and author');
    assert.equal(h.env.calls[0][2], 'Q-visible');
    assert.equal(h.find('textarea').value, 'A separate custom draft');
  } finally {
    h.dispose();
  }
});

test('saved retry ignores stale token in a newer unsent draft', async () => {
  const h = await harness();
  try {
    h.env.send = async () => false;
    await h.input('valid older request');
    await h.submit();
    await tick();
    const binding = h.agentDrafts.bind(() => 'A');
    const newer = {
      version: 1,
      parts: [
        { type: 'text', text: 'newer draft ' },
        { type: 'reference', kind: 'milestone', id: 'missing', project_id: 'A', label: 'deleted' },
      ],
    };
    binding.composerDocument.value = newer;
    await tick();
    assert.equal(binding.failures.value.length, 1);
    assert.equal(
      h.button('重试这条请求').disabled,
      false,
      'A saved valid retry must be enabled despite invalid unrelated draft',
    );
    h.env.send = async () => true;
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls.length, 2);
    assert.equal(h.env.calls[1][1], 'valid older request');
    assert.deepEqual(binding.composerDocument.value, newer, 'Newer draft must remain unchanged');
  } finally {
    h.dispose();
  }
});

test('floating review integration keeps the composer, question, Stop, and draft mounted', async () => {
  const h = await harness();
  try {
    await h.input('An untouched #typed @draft');
    h.workspace.state.project.messages = [
      { id: 'history', role: 'assistant', content: 'long saved text\n'.repeat(1000) },
    ];
    await tick();
    const input = h.find('textarea');
    const form = h.find('form');
    const dock = form.parent;
    const tray = h.find('review-tray');
    assert.equal(tray.parent, dock);
    assert.equal(tray.owner, 1);
    assert.equal(tray.project.messages[0].id, 'history');
    assert.doesNotMatch(dock.class, /is-reading/);
    assert.equal(h.find('textarea'), input);
    assert.equal(h.find('textarea').value, 'An untouched #typed @draft');
    h.workspace.state.project.question = {
      id: 'Q',
      prompt: '真实选择',
      category: 'decision',
      options: ['甲', '乙'],
    };
    await tick();
    assert.equal(h.find('question').parent, dock);
    assert.match(dock.class, /has-question/);
    h.agent.state.running = true;
    h.agent.state.projectId = 'A';
    await tick();
    const stop = all(h.root).find((node) => node['aria-label'] === '停止当前请求');
    assert.ok(stop);
    assert.equal(h.find('form'), form);
    assert.equal(h.find('textarea'), input);
    assert.equal(tray.running, true);
    h.button('收起项目对话').onClick();
    await tick();
    assert.equal(h.find('review-tray'), tray);
    assert.equal(tray.disabled, true);
    assert.doesNotMatch(dock.class, /is-reading/);
    assert.ok(all(h.root).includes(stop));
    const sourceText = source('components/agent/AgentDock.vue');
    assert.doesNotMatch(sourceText, /is-reading|conversationOpen/);
    assert.match(sourceText, /:disabled="dockCollapsed"/);
  } finally {
    h.dispose();
  }
});

test('late delivery confirmation preserves old-answer provenance before untouched Send into Q2', async () => {
  const h = await harness();
  try {
    const saved = await failedAnswer(h);
    const attempt = h.env.submissions[0];
    assert.equal(attempt.id, saved.id, 'Dock passes exact submission ownership to useAgent');
    h.agentDrafts.confirmDelivered(attempt);
    await tick();
    assert.equal(h.button('重试这条请求'), undefined);
    assert.equal(h.agentDrafts.bind(() => 'A').restoredFailure.value.id, saved.id);
    h.workspace.state.project.question = recoveryQuestion({ id: 'Q2' });
    h.api.load = async () => recoveryProject(recoveryQuestion({ id: 'Q2' }));
    await tick();
    await h.submit();
    await tick();
    assert.equal(h.env.calls.length, 1, 'confirmation cannot authorize sending Q1 answer as Q2');
    assert.equal(h.find('textarea').value, ' \n路线 A \n ');
    assert.deepEqual(h.find('attachments').selected, ['original-asset']);
    assert.match(h.workspace.state.error, /不会复用旧回答/);
  } finally {
    h.dispose();
  }
});

for (const reason of [
  'provider',
  'busy',
  'running-here',
  'running-elsewhere',
  'pending',
  'preparing',
  'uploading',
  'refreshing',
]) {
  test(`actual question buttons mirror ${reason} readiness and enable after it clears`, async () => {
    const h = await harness();
    try {
      h.workspace.state.project.question = {
        id: 'Q-ready',
        prompt: 'Choose',
        options: ['Ready option'],
      };
      let clear;
      if (reason === 'provider') {
        h.workspace.state.settings = null;
        clear = () => {
          h.workspace.state.settings = { provider: { config: { model: 'Test' } } };
        };
      } else if (reason === 'busy') {
        h.workspace.state.busy = true;
        clear = () => {
          h.workspace.state.busy = false;
        };
      } else if (reason.startsWith('running-')) {
        Object.assign(h.agent.state, {
          running: true,
          projectId: reason === 'running-here' ? 'A' : 'B',
        });
        clear = () => {
          h.agent.state.running = false;
        };
      } else if (reason === 'pending') {
        const attempt = h.agentDrafts.start('A', { text: 'Awaiting previous result', ids: [] });
        clear = () => h.agentDrafts.settle(attempt, true);
      } else {
        const transfer = h.agentDrafts.beginAttachmentTransfer('A', 1, 'preparing');
        if (reason !== 'preparing') h.agentDrafts.attachmentPhase(transfer, reason, 0);
        clear = () => h.agentDrafts.finishAttachmentTransfer(transfer, null);
      }
      await tick();
      assert.equal(h.button('Ready option').disabled, true);
      h.button('Ready option').onClick();
      await tick();
      assert.equal(
        h.env.calls.length,
        0,
        'Submission guard must also reject programmatic stale clicks',
      );
      clear();
      await tick();
      assert.equal(
        h.button('Ready option').disabled,
        false,
        'Empty composer is not a question-choice blocker',
      );
      h.button('Ready option').onClick();
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.equal(h.env.calls[0][1], 'Ready option');
      assert.equal(h.env.calls[0][2], 'Q-ready');
    } finally {
      h.dispose();
    }
  });
}

for (const delivered of [false, true]) {
  test(`question choice bypasses only stale ordinary composer context and preserves it on ${delivered ? 'success' : 'failure'}`, async () => {
    const h = await harness();
    try {
      h.workspace.state.project.question = {
        id: 'Q-current',
        prompt: 'Choose',
        options: ['Current answer'],
      };
      const binding = h.agentDrafts.bind(() => 'A');
      const stale = {
        version: 1,
        parts: [
          { type: 'text', text: 'Keep my unrelated idea ' },
          {
            type: 'reference',
            kind: 'milestone',
            id: 'removed',
            project_id: 'A',
            label: 'removed node',
          },
        ],
      };
      binding.composerDocument.value = stale;
      await tick();
      h.env.send = async () => delivered;
      const ordinarySend = all(h.root).find(
        (node) => node.tag === 'button' && node.type === 'submit',
      );
      assert.equal(ordinarySend.disabled, true);
      assert.equal(h.button('Current answer').disabled, false);
      h.button('Current answer').onClick();
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.equal(h.env.calls[0][1], 'Current answer');
      assert.equal(h.env.calls[0][2], 'Q-current');
      assert.deepEqual(h.env.calls[0][5], {
        version: 1,
        parts: [{ type: 'text', text: 'Current answer' }],
      });
      assert.deepEqual(binding.composerDocument.value, stale);
      if (!delivered) {
        assert.equal(binding.failures.value[0].questionId, 'Q-current');
        h.workspace.state.busy = true;
        await tick();
        assert.equal(h.button('Current answer').disabled, true);
        h.button('丢弃这条请求').onClick();
        await tick();
        assert.equal(
          binding.failures.value.length,
          0,
          'Deleting an old recovery is independent of send readiness',
        );
        assert.deepEqual(binding.composerDocument.value, stale);
      }
      h.workspace.state.busy = true;
      await tick();
      assert.equal(
        h.find('textarea').disabled,
        false,
        'Busy state must still allow editing/removing stale draft context',
      );
      await h.input('Corrected ordinary draft');
      assert.equal(
        binding.composerDocument.value.parts.some((part) => part.type === 'reference'),
        false,
      );
      assert.equal(h.button('Current answer').disabled, true);
      h.workspace.state.busy = false;
      await tick();
      assert.equal(h.button('Current answer').disabled, false);
      assert.equal(ordinarySend.disabled, false);
      assert.equal(h.env.calls.length, 1, 'Editing context must not send automatically');
    } finally {
      h.dispose();
    }
  });
}

test('recovery actions remain distinct, named, and independently usable in a wrapping group', async () => {
  const { descriptor } = parse(source('components/agent/AgentDock.vue'));
  const style = descriptor.styles.find((style) => style.scoped)?.content;
  const actions = style?.match(/\.agent-recovery-actions\s*\{([^}]+)\}/)?.[1];
  assert.match(actions, /display:\s*flex/);
  assert.match(actions, /flex-wrap:\s*wrap/);
  assert.match(actions, /gap:\s*8px 16px/);
  for (const detached of [false, true]) {
    const h = await harness();
    try {
      await failedAnswer(h);
      if (detached) await h.input('A newer unsent draft');
      const group = all(h.root).find((node) => node.class === 'agent-recovery-actions');
      assert.equal(group.role, 'group');
      assert.equal(group['aria-label'], '未完成请求操作');
      const buttons = all(group).filter((node) => node.tag === 'button');
      assert.deepEqual(
        buttons.map((button) => textOf(button).trim()),
        ['重试这条请求', detached ? '丢弃这条请求' : '关闭提示'],
      );
      const draft = h.find('textarea').value;
      h.workspace.state.busy = true;
      await tick();
      assert.equal(buttons[0].disabled, true);
      assert.notEqual(buttons[1].disabled, true);
      buttons[1].onClick();
      await tick();
      assert.equal(h.env.calls.length, 1);
      assert.equal(h.find('textarea').value, draft);
      assert.equal(
        all(h.root).some((node) => node.class === 'agent-recovery-actions'),
        false,
      );
    } finally {
      h.dispose();
    }
  }
});

test('retry admission starts without the previous failed turn identity', () => {
  const drafts = createAgentDraftStore();
  const first = drafts.start('P1', { text: 'Original request', ids: [] });
  first.turnId = 'first-turn';
  drafts.settle(first, false);
  const second = drafts.retry('P1', first.id);
  assert.equal(second.turnId, undefined);
  drafts.settle(second, false);
  assert.equal(drafts.bind(() => 'P1').failures.value[0].turnId, undefined);
});

test('restored status follows the newest failure while keeping older requests recoverable', () => {
  const drafts = createAgentDraftStore();
  const draft = drafts.bind(() => 'A');
  draft.content.value = 'Earlier repair request';
  const first = drafts.start('A', { text: draft.content.value, ids: ['earlier-file'] });
  first.turnId = 'earlier-turn';
  drafts.settle(first, false);
  draft.content.value = 'Continue only the remaining work';
  const second = drafts.start('A', { text: draft.content.value, ids: ['latest-file'] });
  second.turnId = 'latest-turn';
  drafts.settle(second, false);
  assert.deepEqual(
    draft.failures.value.map((failure) => failure.id),
    [first.id, second.id],
  );
  assert.equal(draft.failureRestored.value, true, 'Status belongs to the newest displayed request');
  assert.equal(draft.restoredFailure.value.id, second.id);
  drafts.dismissFailure('A', second.id);
  assert.equal(
    draft.failureRestored.value,
    false,
    'Older recovery must not claim the latest composer',
  );
  assert.equal(draft.content.value, second.text);
  assert.equal(draft.failures.value[0].turnId, 'earlier-turn');
});

async function twoFailedRequests(h) {
  const earlier = 'Earlier five-point repair: ' + 'Keep the established design. '.repeat(24);
  const latest = 'Continue only the remaining work; retain the saved changes.';
  h.env.send = async (...args) => {
    args[6].turnId = `request-turn-${h.env.calls.length}`;
    return false;
  };
  await h.input(earlier);
  await h.attach(['earlier-file']);
  await h.submit();
  await tick();
  await h.input(latest);
  await h.attach(['latest-file']);
  await h.submit();
  await tick();
  return { earlier, latest, first: h.env.submissions[0], second: h.env.submissions[1] };
}

test('normal edited Send followed by Retry sends the newest failure, then names any older fallback', async () => {
  const h = await harness();
  try {
    const { earlier, latest } = await twoFailedRequests(h);
    const card = () => all(h.root).find((node) => node.class === 'agent-recovery-card');
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value.length, 2);
    assert.match(textOf(card()), /内容已放回输入框/);
    assert.match(textOf(card()), /原请求：Continue only the remaining work/);
    assert.match(textOf(card()), /请求轮次：request-turn-2/);
    assert.doesNotMatch(textOf(card()), /Earlier five-point repair/);
    h.env.send = async () => true;
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls.length, 3);
    assert.equal(h.env.calls[2][1], latest);
    assert.deepEqual(h.env.calls[2][3], ['latest-file']);
    assert.deepEqual(h.env.calls[2][5], h.env.calls[1][5]);
    assert.equal(h.find('textarea').value, '');
    assert.match(textOf(card()), /原请求：Earlier five-point repair/);
    assert.match(textOf(card()), /请求轮次：request-turn-1/);
    assert.match(textOf(card()), /当前草稿未改动/);
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls[3][1], earlier.trim());
    assert.deepEqual(h.env.calls[3][3], ['earlier-file']);
    assert.equal(h.button('重试这条请求'), undefined);
  } finally {
    h.dispose();
  }
});

test('closing the newest hint exposes the named older request without changing the restored composer', async () => {
  const h = await harness();
  try {
    const { latest, second } = await twoFailedRequests(h);
    const card = () => all(h.root).find((node) => node.class === 'agent-recovery-card');
    h.button('关闭提示').onClick();
    await tick();
    assert.match(textOf(card()), /原请求：Earlier five-point repair/);
    assert.match(textOf(card()), /请求轮次：request-turn-1/);
    assert.equal(h.find('textarea').value, latest);
    assert.deepEqual(h.find('attachments').selected, ['latest-file']);
    assert.equal(h.agentDrafts.bind(() => 'A').restoredFailure.value.id, second.id);
    h.button('丢弃这条请求').onClick();
    await tick();
    assert.equal(h.button('重试这条请求'), undefined);
    h.env.send = async () => true;
    await h.submit();
    await tick();
    assert.equal(
      h.env.calls[2][1],
      latest,
      'Untouched Send still uses the latest composer provenance',
    );
    assert.deepEqual(h.env.calls[2][3], ['latest-file']);
  } finally {
    h.dispose();
  }
});

test('a successful new Send retains an older failure with explicit original request identity', async () => {
  const h = await harness();
  try {
    h.env.send = async (...args) => {
      args[6].turnId = 'older-unsuccessful-turn';
      return false;
    };
    await h.input('Earlier unfinished request');
    await h.submit();
    await tick();
    await h.input('A new successful request');
    h.env.send = async () => true;
    await h.submit();
    await tick();
    assert.equal(h.env.calls[1][1], 'A new successful request');
    assert.equal(h.find('textarea').value, '');
    const card = all(h.root).find((node) => node.class === 'agent-recovery-card');
    assert.match(textOf(card), /原请求：Earlier unfinished request/);
    assert.match(textOf(card), /请求轮次：older-unsuccessful-turn/);
    assert.match(textOf(card), /当前草稿未改动/);
    assert.doesNotMatch(textOf(card), /A new successful request/);
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls[2][1], 'Earlier unfinished request');
  } finally {
    h.dispose();
  }
});

test('late older receipts and duplicate failures cannot replace the newest retry identity', async () => {
  const h = await harness();
  try {
    const { latest, first, second } = await twoFailedRequests(h);
    h.agentDrafts.confirmDelivered(first);
    h.agentDrafts.settle(first, false);
    await tick();
    const draft = h.agentDrafts.bind(() => 'A');
    assert.deepEqual(
      draft.failures.value.map((failure) => failure.id),
      [second.id],
    );
    const card = all(h.root).find((node) => node.class === 'agent-recovery-card');
    assert.match(textOf(card), /原请求：Continue only the remaining work/);
    assert.match(textOf(card), /请求轮次：request-turn-2/);
    assert.equal(draft.failureRestored.value, true);
    h.env.send = async () => true;
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls[2][1], latest);
    assert.equal(h.button('重试这条请求'), undefined);
  } finally {
    h.dispose();
  }
});

test('unsent edits, blocked Send and duplicate completion leave failure ordering and identity unchanged', async () => {
  const h = await harness();
  try {
    const { first, second } = await twoFailedRequests(h);
    const draft = h.agentDrafts.bind(() => 'A');
    await h.input('A third unsent request');
    h.workspace.state.busy = true;
    await h.submit();
    await tick();
    h.workspace.state.busy = false;
    await h.input('');
    await h.submit();
    h.agentDrafts.settle(first, false);
    h.agentDrafts.settle(second, false);
    await tick();
    assert.equal(h.env.calls.length, 2);
    assert.deepEqual(
      draft.failures.value.map((failure) => failure.id),
      [first.id, second.id],
    );
    assert.equal(draft.failureRestored.value, false);
    const card = all(h.root).find((node) => node.class === 'agent-recovery-card');
    assert.match(textOf(card), /原请求：Continue only the remaining work/);
    assert.match(textOf(card), /请求轮次：request-turn-2/);
    assert.equal(h.find('textarea').value, '');
  } finally {
    h.dispose();
  }
});

async function boundedComposer(h) {
  h.workspace.state.project.unified_planning = true;
  h.workspace.state.project.planning_job_start_pins = { version: 'job-start/v1', base_hash: 'base-1' };
  await h.input('Substantive request');
  const inputs = () => all(h.root).filter(node => node.tag === 'input');
  const optIn = () => inputs().find(node => node.type === 'checkbox');
  optIn()['onUpdate:modelValue'](true);
  await tick();
  return {
    inputs,
    confirm: async () => { inputs().filter(node => node.type === 'checkbox').at(-1)['onUpdate:modelValue'](true); await tick(); },
    limits: () => inputs().filter(node => node.type === 'number'),
  };
}

test('a supported project still sends an ordinary request until bounded mode is explicitly selected', async () => {
  const h = await harness();
  try {
    h.workspace.state.project.unified_planning = true;
    h.workspace.state.project.planning_job_start_pins = { base_hash: 'base-1' };
    await h.input('Ordinary request');
    const optIn = all(h.root).find(node => node.tag === 'input' && node.type === 'checkbox');
    assert.equal(optIn.checked, false);
    assert.equal(all(h.root).find(node => node.class === 'planning-job-advanced'), undefined);
    assert.equal(h.env.calls.length, 0);
    await h.submit();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.env.planningRequests[0], undefined);
    assert.equal(h.env.calls[0][1], 'Ordinary request');
    assert.deepEqual(h.env.calls[0][5], { version: 1, parts: [{ type: 'text', text: 'Ordinary request' }] });
  } finally { h.dispose(); }
});

test('planning job composer defaults conservative and requires explicit confirmation before send', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    assert.deepEqual(ui.limits().map(node => node.value), [1, 5, 393216]);
    const advanced = all(h.root).find(node => node.class === 'planning-job-advanced');
    assert.equal(Boolean(advanced.open), false);
    assert.equal(advanced.children[0].tag, 'summary', 'native disclosure stays keyboard accessible');
    assert.deepEqual(all(advanced).filter(node => node.type === 'number'), ui.limits());
    assert.equal(all(advanced).some(node => node.type === 'checkbox'), false, 'confirmation stays outside the collapsed details');
    assert.match(textOf(all(h.root).find(node => node.class === 'planning-job-cap-summary')), /1 阶段 · 5 次模型调用 · 393,216 B/);
    assert.equal(h.env.calls.length, 0, 'opting in never sends automatically');
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    assert.match(textOf(h.root), /确认本次作业的总额度/);
    await ui.confirm();
    await h.submit();
    assert.equal(h.env.calls.length, 1);
    assert.equal(h.env.calls[0][1], 'Substantive request');
    assert.equal(h.env.calls[0][5], undefined, 'plain bounded input must omit the rich composer envelope');
    assert.deepEqual(h.env.planningRequests[0].limits, { max_phases: 1, max_calls: 5, max_input_bytes: 393216 });
    assert.deepEqual(h.env.planningRequests[0].pins, { version: 'job-start/v1', base_hash: 'base-1' });
    assert.equal(h.env.planningRequests[0].action, 'start');
    assert.ok(h.env.planningRequests[0].job_id);
    await h.input('ordinary follow-up');
    await h.submit();
    assert.equal(h.env.planningRequests[1], undefined, 'expanded authorization never becomes the default');
  } finally { h.dispose(); }
});

test('planning job edits invalidate consent and higher limits are sent only after reconfirming', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    await ui.confirm();
    ui.limits()[0]['onUpdate:modelValue'](3);
    ui.limits()[1]['onUpdate:modelValue'](12);
    ui.limits()[2]['onUpdate:modelValue'](1179648);
    await tick();
    assert.match(textOf(all(h.root).find(node => node.class === 'planning-job-cap-summary')), /3 阶段 · 12 次模型调用 · 1,179,648 B/);
    assert.equal(ui.inputs().filter(node => node.type === 'checkbox').at(-1).checked, false);
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    await ui.confirm();
    await h.input('Edited substantive request');
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    await ui.confirm();
    h.workspace.state.project.planning_job_start_pins = { base_hash: 'base-2' };
    await tick();
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    await ui.confirm();
    await h.submit();
    assert.equal(h.env.calls.length, 1);
    assert.deepEqual(h.env.planningRequests[0].limits, { max_phases: 3, max_calls: 12, max_input_bytes: 1179648 });
    assert.equal(h.env.planningRequests[0].pins.base_hash, 'base-2');
  } finally { h.dispose(); }
});

test('planning job composer refuses invalid limits and an unfinished existing job', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    for (const value of [0, -1, 1.5, 4, '', Number.MAX_SAFE_INTEGER + 1]) {
      ui.limits()[0]['onUpdate:modelValue'](value);
      await tick();
      await ui.confirm();
      await h.submit();
      assert.equal(h.env.calls.length, 0);
    }
    ui.limits()[0]['onUpdate:modelValue'](1);
    await tick();
    for (const status of ['authorized', 'running', 'paused']) {
      h.workspace.state.project.planning_job = { status };
      await tick();
      await ui.confirm();
      await h.submit();
      assert.equal(h.env.calls.length, 0);
    }
    assert.match(textOf(h.root), /已有未结束作业/);
  } finally { h.dispose(); }
});

test('planning job failed draft requires new authorization instead of ordinary retry', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    h.env.send = async () => false;
    await ui.confirm();
    await h.submit();
    await tick();
    assert.equal(h.agentDrafts.bind(() => 'A').failures.value[0].planningJob.action, 'start');
    await h.button('重试这条请求').onClick();
    await tick();
    assert.equal(h.env.calls.length, 1);
    assert.match(textOf(h.root), /有界作业不会重发原输入/);
  } finally { h.dispose(); }
});

test('planning job composer preserves unsupported attachment and reference drafts without sending', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    await h.attach(['saved-file']);
    await ui.confirm();
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    assert.deepEqual(h.agentDrafts.bind(() => 'A').attachmentIds.value, ['saved-file']);
    assert.match(textOf(h.root), /有界作业目前只接收纯文字/);
    await h.attach([]);
    h.agentDrafts.bind(() => 'A').composerDocument.value = {
      version: 1, parts: [{ type: 'text', text: 'Use ' }, { type: 'reference',
        kind: 'milestone', project_id: 'A', id: 'M1', label: 'Saved milestone' }],
    };
    await tick();
    await ui.confirm();
    await h.submit();
    assert.equal(h.env.calls.length, 0);
    assert.match(textOf(h.root), /有界作业目前只接收纯文字/);
  } finally { h.dispose(); }
});

test('planning job composer rejects aggregate allowances exceeding the authorized phase count', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    for (const [calls, bytes] of [[12, 393216], [5, 393217]]) {
      ui.limits()[1]['onUpdate:modelValue'](calls);
      ui.limits()[2]['onUpdate:modelValue'](bytes);
      await tick();
      assert.equal(ui.limits()[1].max, 5);
      assert.equal(ui.limits()[2].max, 393216);
      await ui.confirm();
      await h.submit();
      assert.equal(h.env.calls.length, 0);
      assert.match(textOf(h.root), /总调用不得超过阶段数 × 5，总输入不得超过阶段数 × 393,216 B/);
    }
    ui.limits()[0]['onUpdate:modelValue'](2);
    ui.limits()[1]['onUpdate:modelValue'](10);
    ui.limits()[2]['onUpdate:modelValue'](786432);
    await tick();
    await ui.confirm();
    await h.submit();
    assert.equal(h.env.calls.length, 1);
    assert.deepEqual(h.env.planningRequests[0].limits, { max_phases: 2, max_calls: 10, max_input_bytes: 786432 });
  } finally { h.dispose(); }
});


test('switching projects clears bounded opt-in, edited limits and confirmation', async () => {
  const h = await harness();
  try {
    const ui = await boundedComposer(h);
    ui.limits()[0]['onUpdate:modelValue'](2);
    ui.limits()[1]['onUpdate:modelValue'](10);
    await tick();
    await ui.confirm();
    await h.workspace.selectProject('B');
    h.workspace.state.project.unified_planning = true;
    h.workspace.state.project.planning_job_start_pins = { base_hash: 'base-B' };
    await tick();
    assert.equal(ui.inputs().find(node => node.type === 'checkbox').checked, false);
    assert.equal(ui.limits().length, 0);
    const next = await boundedComposer(h);
    assert.deepEqual(next.limits().map(node => node.value), [1, 5, 393216]);
    assert.equal(next.inputs().filter(node => node.type === 'checkbox').at(-1).checked, false);
    await h.submit();
    assert.equal(h.env.calls.length, 0);
  } finally { h.dispose(); }
});
