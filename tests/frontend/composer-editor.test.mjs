import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
import { composerDocumentUrl } from './helpers/composer-fixtures.mjs';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
  url: 'http://localhost/',
});
for (const key of [
  'window',
  'document',
  'navigator',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'MutationObserver',
  'DOMParser',
  'KeyboardEvent',
  'DOMRect',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
dom.window.scrollBy = () => {};
globalThis.innerHeight = 720;
globalThis.innerWidth = 1050;
// jsdom supplies DOM behavior, not native layout. Pixel/viewport QA is separate.
dom.window.Range.prototype.getBoundingClientRect = () => new dom.window.DOMRect(50, 500, 1, 18);
dom.window.Range.prototype.getClientRects = () => [];
dom.window.HTMLElement.prototype.scrollIntoView = () => {};
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const referenceMatchUrl = url(compile(source('lib/referenceMatch.ts')));
const editorCode = compile(source('lib/composerEditor.ts')).replace(
  /from (['"])([^'"]+)\1/g,
  (_, q, name) =>
    `from ${JSON.stringify(name === './composerDocument' ? composerDocumentUrl : name === './referenceMatch' ? referenceMatchUrl : import.meta.resolve(name))}`,
);
const { createComposerEditor, matchingReferences, COMPOSER_CLIPBOARD_TYPE } = await import(
  url(editorCode)
);
const { textDocument, renderComposerDocument, fromEditorDocument, composerDocumentIssue } =
  await import(composerDocumentUrl);
const reference = (extra = {}) => ({
  type: 'reference',
  kind: 'milestone',
  id: 'M1',
  project_id: 'P1',
  label: '查询 API',
  ...extra,
});
const row = (part) => ({ ...part, name: part.label, detail: '当前对象', path: '' });
const tick = () => new Promise((resolve) => setTimeout(resolve, 12));
async function harness(
  documentValue = textDocument(''),
  initialCatalog = [row(reference())],
  onChange = () => {},
) {
  const form = document.createElement('form'),
    element = document.createElement('div');
  form.append(element);
  document.body.append(form);
  const env = {
    catalog: initialCatalog,
    doc: documentValue,
    changes: [],
    errors: [],
    submitted: 0,
    formSubmitted: 0,
    suggestion: null,
    requestReads: 0,
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    env.formSubmitted++;
  });
  let controller;
  controller = createComposerEditor({
    element,
    document: documentValue,
    projectId: () => 'P1',
    catalog: () => env.catalog,
    attachments: () => [],
    editable: true,
    ariaLabel: '修改建议',
    placeholder: '描述想法…',
    onChange: (doc) => {
      env.doc = doc;
      env.changes.push(doc);
      onChange(doc);
    },
    onSubmit: () => env.submitted++,
    onError: (message) => env.errors.push(message),
    onRequestCatalog: () => env.requestReads++,
    onSuggestion: (trigger, props) => {
      env.suggestion = { trigger, props };
    },
    onCloseSuggestion: () => {
      env.suggestion = null;
    },
    onInspect: () => {},
    onSuggestionKey: ({ event }) => {
      if (event.key === 'Escape') {
        controller.closeSuggestions();
        return true;
      }
      if (event.key === 'Enter') {
        const s = env.suggestion;
        const item = matchingReferences(env.catalog, s.trigger, s.props.query)[0];
        if (item) s.props.command(item);
        return true;
      }
      return false;
    },
  });
  await tick();
  return {
    env,
    ...controller,
    element,
    type: async (text) => {
      controller.editor.commands.insertContent({ type: 'text', text });
      await tick();
    },
    key: async (key, extra = {}) => {
      controller.editor.view.dom.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra }),
      );
      await tick();
    },
    paste: async (data) => {
      const event = new dom.window.Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', {
        value: { files: [], getData: (type) => data[type] ?? '' },
      });
      controller.editor.view.dom.dispatchEvent(event);
      await tick();
    },
    dispose: () => {
      controller.editor.destroy();
      form.remove();
    },
  };
}

test('real editor inserts an atomic typed reference; first Enter selects, second Enter alone sends', async () => {
  const h = await harness();
  try {
    await h.type('请完善 #查');
    assert.equal(h.env.suggestion.trigger, '#');
    await h.key('Enter');
    assert.equal(h.env.submitted, 0);
    assert.deepEqual(
      h.env.doc.parts.find((part) => part.type === 'reference'),
      reference(),
    );
    await h.key('Enter');
    assert.equal(h.env.submitted, 1);
    assert.equal(h.env.formSubmitted, 0);
    assert.match(renderComposerDocument(h.env.doc), /请完善 #查询 API/);
  } finally {
    h.dispose();
  }
});

test('atomic Backspace and actual transaction undo/redo retain identity rather than a label approximation', async () => {
  const h = await harness({ version: 1, parts: [reference()] });
  try {
    h.editor.commands.focus();
    h.editor.commands.setTextSelection(2);
    await h.key('Backspace');
    assert.equal(
      h.env.doc.parts.some((part) => part.type === 'reference'),
      true,
    );
    assert.equal(h.editor.state.selection.constructor.name, 'NodeSelection');
    await h.key('Backspace');
    assert.equal(
      h.env.doc.parts.some((part) => part.type === 'reference'),
      false,
    );
    h.editor.commands.undo();
    await tick();
    assert.deepEqual(h.env.doc.parts, [reference()]);
    h.editor.commands.redo();
    await tick();
    assert.deepEqual(h.env.doc.parts, []);
  } finally {
    h.dispose();
  }
});

test('display-only catalog renames preserve stored labels and invalid IDs remain typed visible references', async () => {
  const initial = { version: 1, parts: [reference()] };
  const h = await harness(initial);
  try {
    const before = JSON.stringify(h.editor.getJSON()),
      changes = h.env.changes.length;
    h.env.catalog = [row(reference({ label: '当前的新名称' }))];
    h.refreshReferences();
    await tick();
    assert.match(h.element.querySelector('.composer-reference').textContent, /当前的新名称/);
    assert.equal(JSON.stringify(h.editor.getJSON()), before);
    assert.equal(h.env.changes.length, changes);
    h.env.catalog = [row(reference({ id: 'M2', label: '查询 API' }))];
    h.refreshReferences();
    await tick();
    assert.equal(h.element.querySelector('.composer-reference').dataset.invalid, 'true');
    assert.match(
      composerDocumentIssue(fromEditorDocument(h.editor.getJSON()), 'P1', h.env.catalog),
      /失效/,
    );
    assert.equal(h.editor.getJSON().content[0].content[0].attrs.id, 'M1');
  } finally {
    h.dispose();
  }
});

test('external HTML and unknown typed clipboard IDs become explicit plain text, never same-name bindings', async () => {
  const h = await harness();
  try {
    await h.paste({
      'text/html':
        '<img src="https://example.invalid/private"><span data-type="projectReference" data-id="M1">查询 API</span>',
      'text/plain': '#查询 API <script>literal</script>',
    });
    assert.equal(
      h.env.doc.parts.every((part) => part.type === 'text'),
      true,
    );
    const unknown = { version: 1, parts: [reference({ id: 'missing' })] };
    await h.paste({
      [COMPOSER_CLIPBOARD_TYPE]: JSON.stringify(unknown),
      'text/plain': '#查询 API',
    });
    assert.equal(
      h.env.doc.parts.every((part) => part.type === 'text'),
      true,
    );
    assert.match(h.env.errors.at(-1), /普通文字/);
    assert.equal(h.element.querySelector('img'), null);
  } finally {
    h.dispose();
  }
});

test('validated typed clipboard content preserves IDs and supports transaction undo', async () => {
  const h = await harness();
  try {
    const copy = { version: 1, parts: [{ type: 'text', text: '参考\n' }, reference()] };
    await h.paste({
      [COMPOSER_CLIPBOARD_TYPE]: JSON.stringify(copy),
      'text/plain': '参考\n#查询 API',
    });
    assert.deepEqual(h.env.doc, copy);
    h.editor.commands.undo();
    await tick();
    assert.equal(renderComposerDocument(h.env.doc), '');
  } finally {
    h.dispose();
  }
});

test('IME confirmation and editor deletion keys do not send or escape to graph/global shortcuts', async () => {
  const h = await harness(textDocument('中文草稿'));
  let leaked = 0;
  const listener = () => leaked++;
  document.addEventListener('keydown', listener);
  try {
    h.editor.view.dom.dispatchEvent(
      new dom.window.CompositionEvent('compositionstart', { bubbles: true }),
    );
    await h.key('Enter', { isComposing: true, keyCode: 229 });
    assert.equal(h.env.submitted, 0);
    assert.equal(leaked, 0);
    h.editor.view.dom.dispatchEvent(
      new dom.window.CompositionEvent('compositionend', { bubbles: true, data: '' }),
    );
    await tick();
    await h.key('Delete');
    assert.equal(leaked, 0);
    assert.equal(h.env.submitted, 0);
  } finally {
    document.removeEventListener('keydown', listener);
    h.dispose();
  }
});

test('plain text with trigger characters stays ordinary text when suggestions are dismissed', async () => {
  const h = await harness();
  try {
    await h.type('保留 #某个词');
    await h.key('Escape');
    assert.equal(h.env.suggestion, null);
    assert.equal(
      h.env.doc.parts.every((part) => part.type === 'text'),
      true,
    );
  } finally {
    h.dispose();
  }
});

const draftCode = compile(source('composables/useAgentDrafts.ts')).replace(
  /from (['"])([^'"]+)\1/g,
  (_, q, name) =>
    `from ${JSON.stringify(name === '../lib/composerDocument' ? composerDocumentUrl : import.meta.resolve(name))}`,
);
const { createAgentDraftStore } = await import(url(draftCode));
test('real provisional trigger, query, token selection and undo retain original recovered question provenance', async () => {
  const store = createAgentDraftStore(),
    binding = store.bind(() => 'P1');
  const original = textDocument('采用路线 A');
  binding.composerDocument.value = original;
  const attempt = store.start('P1', {
    text: '采用路线 A',
    composerDocument: original,
    ids: [],
    questionId: 'Q1',
    question: { id: 'Q1', prompt: '原问题' },
  });
  store.settle(attempt, false);
  const h = await harness(binding.composerDocument.value, [row(reference())], (doc) => {
    binding.composerDocument.value = doc;
  });
  try {
    h.editor.commands.setTextSelection(h.editor.state.doc.content.size - 1);
    await h.type(' #');
    await h.type('查');
    assert.equal(
      binding.restoredFailure.value,
      undefined,
      'Literal unfinished query is still ordinary edited text',
    );
    await h.key('Enter');
    assert.equal(
      binding.restoredFailure.value.questionId,
      'Q1',
      'Completed context selection restores protected provenance',
    );
    const prepared = store.prepareRetry('P1', binding.restoredFailure.value.id, true);
    assert.equal(prepared.failure.questionId, 'Q1');
    assert.deepEqual(prepared.failure.composerDocument, h.env.doc);
    assert.deepEqual(
      binding.failures.value[0].composerDocument,
      original,
      'Original saved failure is immutable',
    );
    store.cancelRetry(prepared);
    h.editor.commands.undo();
    await tick();
    h.editor.commands.redo();
    await tick();
    assert.equal(binding.restoredFailure.value.questionId, 'Q1');
    await h.type(' 改成路线 B');
    assert.equal(
      binding.restoredFailure.value,
      undefined,
      'Deliberate free-text change releases original provenance',
    );
  } finally {
    h.dispose();
  }
});

test('candidate replacement is validated at its actual final size, and forward Delete selects before deleting', async () => {
  const h = await harness();
  try {
    await h.type('x'.repeat(15990) + ' #查询');
    await h.key('Enter');
    assert.equal(
      h.env.doc.parts.some((part) => part.type === 'reference'),
      true,
    );
    assert.ok(renderComposerDocument(h.env.doc).length <= 16000);
    assert.deepEqual(h.env.errors, []);
    const position = h.editor.state.doc.content.size - 3;
    h.editor.commands.setTextSelection(position);
    await h.key('Delete');
    assert.equal(h.editor.state.selection.constructor.name, 'NodeSelection');
    assert.equal(
      h.env.doc.parts.some((part) => part.type === 'reference'),
      true,
    );
    await h.key('Delete');
    assert.equal(
      h.env.doc.parts.some((part) => part.type === 'reference'),
      false,
    );
  } finally {
    h.dispose();
  }
});

test('real Vue wrapper resets external replacement history but preserves ordinary edit undo and project isolation', async () => {
  const { parse, compileScript } = await import('@vue/compiler-sfc');
  const { createApp, h, ref, nextTick } = await import('vue');
  const { descriptor } = parse(source('components/agent/ComposerEditor.vue'));
  const wrapperCode = compile(
    compileScript(descriptor, { id: 'composer-wrapper-test', inlineTemplate: true }).content,
  ).replace(
    /from (['"])([^'"]+)\1/g,
    (_, quote, name) =>
      `from ${JSON.stringify(
        name === '../../lib/composerEditor'
          ? url(editorCode)
          : name === '../../lib/composerDocument'
            ? composerDocumentUrl
            : import.meta.resolve(name),
      )}`,
  );
  const Component = (await import(url(wrapperCode))).default;
  const model = ref(textDocument('original draft'));
  const project = ref('P1');
  const disabled = ref(false);
  let updates = 0;
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    setup: () => () =>
      h(Component, {
        key: project.value,
        projectId: project.value,
        modelValue: model.value,
        'onUpdate:modelValue': (value) => {
          model.value = value;
          updates++;
        },
        catalog: [],
        explicitAttachmentIds: [],
        disabled: disabled.value,
        placeholder: 'Write',
        inputLabel: 'Message',
      }),
  });
  app.mount(root);
  const settle = async () => {
    await nextTick();
    await tick();
    await nextTick();
  };
  const undo = async () => {
    root
      .querySelector('.tiptap')
      .dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }),
      );
    await settle();
  };
  const paste = async (text) => {
    const event = new dom.window.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', {
      value: { files: [], getData: (type) => (type === 'text/plain' ? text : '') },
    });
    root.querySelector('.tiptap').dispatchEvent(event);
    await settle();
  };
  try {
    await settle();
    await paste('new ');
    assert.equal(renderComposerDocument(model.value).includes('new '), true);
    await undo();
    assert.equal(renderComposerDocument(model.value), 'original draft');
    await paste('sent ');
    const beforeDisable = updates;
    disabled.value = true;
    await settle();
    assert.equal(
      updates,
      beforeDisable,
      'Read-only transitions must not create a newer draft revision',
    );
    model.value = textDocument('');
    await settle();
    disabled.value = false;
    await settle();
    assert.equal(updates, beforeDisable, 'Completing a turn must not emit its cleared draft');
    await undo();
    assert.equal(renderComposerDocument(model.value), '');
    model.value = textDocument('restored answer');
    await settle();
    await undo();
    assert.equal(renderComposerDocument(model.value), 'restored answer');
    await paste('same project text ');
    const before = renderComposerDocument(model.value);
    project.value = 'P2';
    await settle();
    await undo();
    assert.equal(renderComposerDocument(model.value), before);
    assert.equal(root.querySelector('.tiptap').textContent, before);
  } finally {
    app.unmount();
    root.remove();
  }
});

test('standard HTML clipboard transport preserves typed references when native custom MIME is absent', async () => {
  const original = {
    version: 1,
    parts: [
      { type: 'text', text: 'line <one>\n' },
      reference(),
      { type: 'text', text: '\nlast & "quoted"' },
    ],
  };
  const h = await harness(original);
  try {
    h.editor.commands.selectAll();
    const values = {};
    const copy = new dom.window.Event('copy', { bubbles: true, cancelable: true });
    Object.defineProperty(copy, 'clipboardData', {
      value: {
        setData: (type, value) => {
          values[type] = value;
        },
      },
    });
    h.editor.view.dom.dispatchEvent(copy);
    assert.match(values['text/html'], /data-evograph-composer=/);
    assert.match(values['text/html'], /&lt;one&gt;/);
    assert.match(values['text/html'], /&amp; &quot;quoted&quot;/);
    h.replaceDocument(textDocument(''));
    await h.paste({
      'text/plain': values['text/plain'].replaceAll('\n', '\r\n'),
      'text/html': values['text/html'],
    });
    assert.deepEqual(h.env.doc, original);
    h.editor.commands.undo();
    await tick();
    assert.equal(renderComposerDocument(h.env.doc), '');
  } finally {
    h.dispose();
  }
});

test('untrusted clipboard metadata cannot replace visible text or bypass current typed identity checks', async () => {
  const doc = { version: 1, parts: [reference()] };
  const wrapper = (value) =>
    `<span data-evograph-composer="${encodeURIComponent(JSON.stringify(value))}">ignored</span>`;
  const h = await harness();
  try {
    for (const data of [
      { 'text/plain': 'visible different text', [COMPOSER_CLIPBOARD_TYPE]: JSON.stringify(doc) },
      { 'text/plain': 'visible different text', 'text/html': wrapper(doc) },
      {
        'text/plain': '#查询 API',
        'text/html': '<span data-evograph-composer="%E0%A4%A">bad URI</span>',
      },
      {
        'text/plain': '#查询 API',
        'text/html': '<span data-evograph-composer="%7B">bad JSON</span>',
      },
      {
        'text/plain': '#查询 API',
        'text/html': wrapper({ version: 1, parts: [reference({ project_id: 'P2' })] }),
      },
      {
        'text/plain': '#查询 API',
        'text/html': wrapper({ version: 1, parts: [reference({ id: 'deleted' })] }),
      },
      { 'text/plain': '#查询 API', [COMPOSER_CLIPBOARD_TYPE]: 'x'.repeat(512001) },
    ]) {
      h.replaceDocument(textDocument(''));
      await h.paste(data);
      assert.equal(
        h.env.doc.parts.every((part) => part.type === 'text'),
        true,
      );
      assert.equal(renderComposerDocument(h.env.doc), data['text/plain']);
    }
    h.replaceDocument(textDocument(''));
    await h.paste({
      'text/plain': '#查询 API',
      'text/html': `<img src="https://example.invalid/private" onerror="alert(1)">${wrapper(doc)}<script>bad()</script>`,
    });
    assert.deepEqual(h.env.doc, doc);
    assert.equal(Boolean(h.element.querySelector('img[src],script,[onerror]')), false);
  } finally {
    h.dispose();
  }
});

test('real Vue composer restores exact typed draft through send-clear, read-only turn and failed settlement', async () => {
  const { parse, compileScript } = await import('@vue/compiler-sfc');
  const { createApp, h, ref, nextTick } = await import('vue');
  const { descriptor } = parse(source('components/agent/ComposerEditor.vue'));
  const wrapperCode = compile(
    compileScript(descriptor, { id: 'composer-lifecycle-test', inlineTemplate: true }).content,
  ).replace(
    /from (['"])([^'"]+)\1/g,
    (_, quote, name) =>
      `from ${JSON.stringify(
        name === '../../lib/composerEditor'
          ? url(editorCode)
          : name === '../../lib/composerDocument'
            ? composerDocumentUrl
            : import.meta.resolve(name),
      )}`,
  );
  const Component = (await import(url(wrapperCode))).default;
  const store = createAgentDraftStore();
  const binding = store.bind(() => 'P1');
  const original = {
    version: 1,
    parts: [
      { type: 'text', text: '  preserve\n' },
      reference(),
      { type: 'text', text: ' answer\n ' },
    ],
  };
  binding.composerDocument.value = original;
  const disabled = ref(false);
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    setup: () => () =>
      h(Component, {
        projectId: 'P1',
        modelValue: binding.composerDocument.value ?? textDocument(binding.content.value),
        'onUpdate:modelValue': (value) => {
          binding.composerDocument.value = value;
        },
        catalog: [row(reference())],
        explicitAttachmentIds: [],
        disabled: disabled.value,
        placeholder: 'Write',
        inputLabel: 'Message',
      }),
  });
  const settle = async () => {
    await nextTick();
    await tick();
    await nextTick();
  };
  app.mount(root);
  try {
    await settle();
    const attempt = store.start('P1', {
      text: renderComposerDocument(original),
      composerDocument: original,
      ids: [],
    });
    disabled.value = true;
    await settle();
    assert.equal(root.querySelector('.tiptap').textContent, '');
    assert.equal(
      store.settle(attempt, false),
      true,
      'Read-only transition must not prevent draft recovery',
    );
    disabled.value = false;
    await settle();
    assert.deepEqual(binding.composerDocument.value, original);
    assert.equal(root.querySelectorAll('.composer-reference').length, 1);
    assert.deepEqual(binding.failures.value[0].composerDocument, original);
  } finally {
    app.unmount();
    root.remove();
  }
});

test('real saved-request editor keeps new-request intent through delete undo/redo and releases it only with an undo-safe reset', async () => {
  const { parse, compileScript } = await import('@vue/compiler-sfc');
  const { createApp, h, reactive, nextTick } = await import('vue');
  const vueUrl = import.meta.resolve('vue');
  const storeUrl = url(`${draftCode}\n// real saved-request undo lifecycle`);
  const { agentDrafts: store } = await import(storeUrl);
  const controllersUrl = url(`import { createComposerEditor as create, matchingReferences } from ${JSON.stringify(url(editorCode))};
    export const controllers = []; export { matchingReferences };
    export function createComposerEditor(options) { const controller = create(options); controllers.push(controller); return controller; }`);
  const { controllers } = await import(controllersUrl);
  const agentUrl = url(`import { reactive } from ${JSON.stringify(vueUrl)};
    export const calls = [];
    export const agent = { state: reactive({ running: false, projectId: '', follow: {} }),
      send: async (...args) => { calls.push(args); return true; }, stop() {} };
    export const useAgent = () => agent;`);
  const { calls } = await import(agentUrl);
  const workspaceUrl = url(`import { reactive } from ${JSON.stringify(vueUrl)};
    export const state = reactive({ busy: false, settings: { provider: {} } });
    export const useWorkspace = () => ({ state, setPage() {}, setError() {}, selectProject() {}, applyProject() {} });`);
  const empty = url('export default { render() { return null; } };');
  const imports = {
    vue: vueUrl,
    'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
    '../../composables/useAgentDrafts': storeUrl,
    '../../composables/useAgent': agentUrl,
    '../../composables/useWorkspace': workspaceUrl,
    '../../lib/composerDocument': composerDocumentUrl,
    '../../lib/composerEditor': controllersUrl,
    '../../lib/turnSummary': url(compile(source('lib/turnSummary.ts'))),
    '../../lib/agentRetry': url(compile(source('lib/agentRetry.ts')).replace("'./composerDocument'", JSON.stringify(composerDocumentUrl))),
    '../../api/client': url(`export const command = async () => ({ items: ${JSON.stringify([row(reference())])} });`),
    './AgentReviewTray.vue': url(`import { h } from ${JSON.stringify(vueUrl)};
      export default { props: ['project', 'owner'], emits: ['reuse'], setup(props, { emit, expose }) {
        expose({ closeForDraft() {} }); return () => h('button', { 'data-reuse': '',
          onClick: () => emit('reuse', { projectId: props.project.id, projectCreatedAt: props.project.created_at, owner: props.owner, messageId: 'saved' }) }, '用作新草稿');
      } };`),
  };
  for (const name of ['./PlanningJobPanel.vue', '../graph/FollowAgentButton.vue', '../attachments/AttachmentPicker.vue', '../attachments/AttachmentReceipt.vue']) imports[name] = empty;
  async function component(name) {
    const { descriptor } = parse(source(`components/agent/${name}.vue`));
    return url(compile(compileScript(descriptor, { id: `saved-undo-${name}`, inlineTemplate: true }).content)
      .replace(/from (['"])([^'"]+)\1/g, (_, quote, dependency) => {
        assert.ok(imports[dependency], `Unexpected dependency ${dependency}`);
        return `from ${JSON.stringify(imports[dependency])}`;
      }));
  }
  imports['./ComposerEditor.vue'] = await component('ComposerEditor');
  imports['./AgentQuestion.vue'] = await component('AgentQuestion');
  const Dock = (await import(await component('AgentDock'))).default;
  const original = { version: 1, parts: [{ type: 'text', text: 'Revise ' }, reference(), { type: 'text', text: ' next' }] };
  const project = reactive({
    id: 'P1', created_at: 'incarnation', revision: 1, name: 'Project', question: null,
    messages: [{ id: 'saved', project_id: 'P1', role: 'user', content: renderComposerDocument(original), composer_document: original }],
    milestones: [], source_milestones: [], attachments: [], events: [],
  });
  const historyBefore = JSON.stringify(project.messages);
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ setup: () => () => h(Dock, { project, reviewOwner: 9 }) });
  const settle = async () => { await nextTick(); await tick(); await nextTick(); };
  const editor = () => controllers.at(-1).editor;
  const enter = async () => {
    editor().view.dom.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    await settle();
  };
  const button = (text) => [...root.querySelectorAll('button')].find((item) => item.textContent.trim() === text);
  app.mount(root);
  try {
    await settle();
    root.querySelector('[data-reuse]').click();
    await settle();
    const binding = store.bind(() => 'P1');
    assert.equal(binding.savedRequest.value, true);
    assert.deepEqual(binding.composerDocument.value, original);
    editor().commands.selectAll();
    editor().commands.deleteSelection();
    await settle();
    assert.equal(binding.content.value, '');
    assert.equal(binding.savedRequest.value, true, 'undoable deletion retains intent');
    editor().commands.undo();
    await settle();
    assert.deepEqual(binding.composerDocument.value, original);
    project.question = { id: 'Q-later', prompt: 'Choose?', options: ['Explicit answer'] };
    await settle();
    await enter();
    assert.equal(calls.length, 0, 'restored saved request cannot become an implicit answer');
    editor().commands.redo();
    await settle();
    assert.equal(binding.content.value, '');
    assert.equal(binding.savedRequest.value, true);
    editor().commands.undo();
    await settle();
    await enter();
    assert.equal(calls.length, 0);
    button('Explicit answer').click();
    await settle();
    assert.equal(calls.length, 1);
    assert.equal(calls[0][2], 'Q-later');
    assert.deepEqual(binding.composerDocument.value, original, 'explicit question choices preserve the held request');
    assert.equal(binding.savedRequest.value, true);
    // Reset while already visually empty must still replace the editor and its
    // undo history, rather than depending on a changed document value.
    editor().commands.selectAll();
    editor().commands.deleteSelection();
    await settle();
    const oldEditor = editor();
    button('清空新草稿').click();
    await settle();
    assert.notEqual(editor(), oldEditor);
    assert.equal(binding.savedRequest.value, false);
    assert.equal(editor().commands.undo(), false);
    await settle();
    assert.equal(binding.content.value, '');
    editor().commands.insertContent('My deliberate new answer');
    await settle();
    await enter();
    assert.equal(calls.length, 2);
    assert.equal(calls[1][1], 'My deliberate new answer');
    assert.equal(calls[1][2], 'Q-later', 'explicit reset leaves the ordinary answer flow usable');
    assert.equal(JSON.stringify(project.messages), historyBefore);
  } finally {
    app.unmount();
    root.remove();
  }
});
