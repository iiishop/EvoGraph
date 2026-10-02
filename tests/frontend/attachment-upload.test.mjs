import { markdownContentUrl } from './helpers/markdown-fixtures.mjs';
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
import { createRenderer, h, nextTick, reactive, ref } from 'vue';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const compile = (code) =>
  ts
    .transpileModule(code, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace(
      /(['"])(?:\.\.\/lib\/|\.\/|\.\.\/\.\.\/lib\/)composerDocument\1/g,
      JSON.stringify(composerDocumentUrl),
    );
const vue = pathToFileURL(require.resolve('vue')).href;
const uploadUrl = url(compile(source('lib/attachmentUpload.ts')));
const { uploadAttachmentBatch } = await import(uploadUrl);
const asset = (id, name = `${id}.txt`) => ({
  id,
  name,
  media_type: 'text/plain',
  size: 8,
  excerpt: 'extracted contents are not needed in composer receipt cache',
});
const files = (count) =>
  Array.from(
    { length: count },
    (_, i) => new File([`content-${i + 1}`], `file-${i + 1}.txt`, { type: 'text/plain' }),
  );
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
const tick = async () => {
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};

function deps(overrides = {}) {
  return {
    maxBytes: 8 * 1024 * 1024,
    read: (file) => file.text(),
    save: async (name) => asset(name),
    refresh: async () => {},
    ...overrides,
  };
}

test('confirmed uploads survive a later command failure and a failed refresh; later files are not attempted', async () => {
  const calls = [],
    receipts = [];
  const result = await uploadAttachmentBatch(
    files(3),
    deps({
      save: async (name) => {
        calls.push(name);
        if (calls.length === 2) throw new Error('connection lost');
        return asset('one');
      },
      onConfirmed: (value) => receipts.push(value.id),
      refresh: async () => {
        throw new Error('refresh offline');
      },
    }),
  );
  assert.deepEqual(calls, ['file-1.txt', 'file-2.txt']);
  assert.deepEqual(receipts, ['one']);
  assert.deepEqual(
    result.assets.map((item) => item.id),
    ['one'],
  );
  assert.equal(result.failure.status, 'unconfirmed');
  assert.equal(result.failure.name, 'file-2.txt');
  assert.deepEqual(result.unattempted, ['file-3.txt']);
  assert.equal(result.refresh, 'failed');
  assert.equal(result.assets[0].excerpt, '');
});

test('local read and size failures are not uploads and preserve earlier confirmed assets', async () => {
  const calls = [];
  const readFailure = await uploadAttachmentBatch(
    files(3),
    deps({
      read: async (file) => {
        if (file.name === 'file-2.txt') throw new Error('read cancelled');
        return file.text();
      },
      save: async (name) => {
        calls.push(name);
        return asset(name);
      },
    }),
  );
  assert.deepEqual(calls, ['file-1.txt']);
  assert.equal(readFailure.failure.status, 'not_uploaded');
  assert.equal(readFailure.failure.message, 'read cancelled');
  assert.equal(readFailure.assets.length, 1);
  assert.deepEqual(readFailure.unattempted, ['file-3.txt']);
  const oversized = await uploadAttachmentBatch(
    files(2),
    deps({
      maxBytes: 1,
      read: () => {
        throw new Error('should not read');
      },
    }),
  );
  assert.equal(oversized.failure.status, 'not_uploaded');
  assert.match(oversized.failure.message, /大小上限/);
  assert.equal(oversized.refresh, 'not_needed');
});

test('canonical duplicate receipts count unique available assets rather than claiming new uploads', async () => {
  const result = await uploadAttachmentBatch(
    files(3),
    deps({ save: async () => asset('canonical') }),
  );
  assert.equal(result.confirmedFiles.length, 3);
  assert.equal(result.assets.length, 1);
  assert.equal(result.assets[0].id, 'canonical');
});

const all = (node) => [node, ...(node.children ?? []).flatMap(all)];
const textOf = (node) => `${node.text ?? ''}${(node.children ?? []).map(textOf).join('')}`;
class HostElement {
  get [Symbol.toStringTag]() {
    return 'HTMLElement';
  }
  constructor(tag = 'root') {
    Object.assign(this, { tag, children: [], parent: null, value: '', files: null, listeners: {} });
  }
  addEventListener(name, fn) {
    this.listeners[name] = fn;
  }
  removeEventListener(name) {
    delete this.listeners[name];
  }
  click() {
    this.clicked = true;
  }
  focus() {
    globalThis.document.activeElement = this;
  }
  contains(node) {
    return all(this).includes(node);
  }
  querySelector(selector) {
    return all(this).find((node) => node.tag === 'button' && !node.disabled);
  }
}
const renderer = createRenderer({
  createElement: (tag) => new HostElement(tag),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setText(node, text) {
    node.text = text;
  },
  setElementText(node, text) {
    node.text = text;
    node.children = [];
  },
  patchProp(node, key, oldValue, value) {
    node[key] = value;
  },
  insert(node, parent, anchor) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
    node.parent = parent;
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    if (index < 0) parent.children.push(node);
    else parent.children.splice(index, 0, node);
  },
  remove(node) {
    if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null,
});
let sequence = 0;
async function harness(options = {}) {
  const id = ++sequence;
  const draftsUrl = url(
    compile(source('composables/useAgentDrafts.ts')).replace(
      "from 'vue'",
      `from ${JSON.stringify(vue)}`,
    ) + `\n// ${id}`,
  );
  const workspaceUrl = url(`import { reactive } from ${JSON.stringify(vue)};
    export const workspace = { state: reactive({ busy: false, project: { id: 'A' } }), busy: [], refreshBody: async () => {}, refresh() { return workspace.refreshBody(); }, setBusy(value) { workspace.busy.push(value); workspace.state.busy = value; }, setError(error) { workspace.state.error = error; } }; export const useWorkspace = () => workspace; // ${id}`);
  const apiUrl =
    url(`export const api = { calls: [], count: 0, formats: async () => ({ extensions: ['.txt', '.md', '.png'], max_bytes: 8388608 }), save: async params => ({ id: params.project_id + '-' + (++api.count), name: params.name, media_type: 'text/plain', size: 8, excerpt: 'contents' }) };
    export const command = (action, params = {}) => { api.calls.push([action, params]); return action === 'attachments.formats' ? api.formats() : api.save(params); }; // ${id}`);
  const composableImports = {
    vue,
    '../api/client': apiUrl,
    './useWorkspace': workspaceUrl,
    '../lib/attachmentUpload': uploadUrl,
  };
  const attachmentsUrl = url(
    compile(source('composables/useAttachments.ts')).replace(
      /from (['"])([^'"]+)\1/g,
      (_, q, name) => {
        assert.ok(composableImports[name], name);
        return `from ${JSON.stringify(composableImports[name])}`;
      },
    ),
  );
  const noticeUrl = url(
    `export const notices = []; export const useNotifications = () => ({ push: message => notices.push(message) }); // ${id}`,
  );
  const imports = {
    [composerDocumentUrl]: composerDocumentUrl,
    '../../lib/composerDocument': composerDocumentUrl,
    './ComposerEditor.vue': composerEditorStubUrl,
    './MessageContent.vue': messageContentStubUrl,
    './MarkdownContent': markdownContentUrl,
    vue,
    'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
    '../../composables/useAttachments': attachmentsUrl,
    '../../composables/useWorkspace': workspaceUrl,
    '../../composables/useAgentDrafts': draftsUrl,
    '../../composables/useNotifications': noticeUrl,
  };
  const { descriptor } = parse(source('components/attachments/AttachmentPicker.vue'));
  const code = compile(
    compileScript(descriptor, { id: 'attachment-picker', inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, q, name) => {
    assert.ok(imports[name], name);
    return `from ${JSON.stringify(imports[name])}`;
  });
  const Picker = (await import(url(code))).default;
  const receiptSource = parse(source('components/attachments/AttachmentReceipt.vue')).descriptor;
  const receiptCode = compile(
    compileScript(receiptSource, { id: 'attachment-receipt', inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, q, name) => `from ${JSON.stringify(imports[name])}`);
  const Receipt = (await import(url(receiptCode))).default;
  const { agentDrafts } = await import(draftsUrl),
    { workspace } = await import(workspaceUrl),
    { api } = await import(apiUrl),
    { useAttachments } = await import(attachmentsUrl);
  const { notices } = await import(noticeUrl);
  if (options.formats) api.formats = options.formats;
  const originalReader = globalThis.FileReader,
    originalDocument = globalThis.document,
    originalNode = globalThis.Node;
  const domListeners = {};
  globalThis.Node = HostElement;
  globalThis.document = {
    activeElement: null,
    addEventListener: (name, fn) => {
      domListeners[name] = fn;
    },
    removeEventListener: (name) => {
      delete domListeners[name];
    },
  };
  const readEnv = { read: (file) => file.text() };
  globalThis.FileReader = class {
    readAsDataURL(file) {
      Promise.resolve()
        .then(() => readEnv.read(file))
        .then(
          (data) => {
            this.result = `data:text/plain;base64,${Buffer.from(data).toString('base64')}`;
            this.onload?.();
          },
          () => this.onerror?.(),
        );
    }
  };
  const contextKey = ref('graph');
  const projectId = ref('A'),
    shown = ref(true),
    picker = ref();
  const projects = reactive({ A: { id: 'A', attachments: [] }, B: { id: 'B', attachments: [] } });
  const app = renderer.createApp({
    setup: () => () => {
      if (!shown.value) return null;
      const current = projectId.value;
      const draft = agentDrafts.bind(() => current);
      return h('section', {}, [
        h(Picker, {
          ref: picker,
          key: current,
          project: projects[current],
          contextKey: contextKey.value,
          modelValue: draft.attachmentIds.value,
          'onUpdate:modelValue': (ids) => {
            draft.attachmentIds.value = ids;
          },
        }),
        h(Receipt, {
          transfer: draft.attachmentTransfer.value,
          selectedIds: draft.attachmentIds.value,
        }),
      ]);
    },
  });
  const root = new HostElement();
  app.mount(root);
  await tick();
  const draft = (pid) => agentDrafts.bind(() => pid);
  return {
    root,
    picker,
    projectId,
    shown,
    projects,
    draft,
    agentDrafts,
    workspace,
    api,
    useAttachments,
    notices,
    readEnv,
    fileInput: () => all(root).find((node) => node.tag === 'input'),
    contextKey,
    domListeners,
    tools: () => all(root).find((node) => node.role === 'dialog'),
    addButton: () => all(root).find((node) => node.class === 'attachment-add'),
    openTools: async () => {
      await all(root)
        .find((node) => node.class === 'attachment-add')
        .onClick();
      await tick();
    },
    openSaved: async () => {
      all(root)
        .find((node) => node.tag === 'button' && textOf(node).includes('引用项目资料'))
        .onClick();
      await tick();
    },
    savedChoices: () =>
      all(root).filter((node) => node.tag === 'button' && node['aria-pressed'] !== undefined),
    chips: () =>
      all(root).filter(
        (node) => node.tag === 'button' && String(node.class ?? '').includes('attachment-chip'),
      ),
    navigate: async (pid) => {
      projectId.value = pid;
      workspace.state.project = projects[pid];
      await tick();
    },
    dispose: () => {
      app.unmount();
      if (originalReader === undefined) delete globalThis.FileReader;
      else globalThis.FileReader = originalReader;
      if (originalDocument === undefined) delete globalThis.document;
      else globalThis.document = originalDocument;
      if (originalNode === undefined) delete globalThis.Node;
      else globalThis.Node = originalNode;
    },
  };
}

test('seven files remain saved and visible while only six contents are referenced, with honest pre-upload copy', async () => {
  const h = await harness();
  try {
    assert.doesNotMatch(textOf(h.root), /上传成功后都会保存到项目/);
    await h.openTools();
    assert.match(textOf(h.root), /上传成功后都会保存到项目/);
    assert.match(textOf(h.root), /最多引用 6 份资料内容/);
    await h.picker.value.acceptFiles(files(7));
    await tick();
    assert.equal(h.api.calls.filter(([action]) => action === 'attachments.upload').length, 7);
    assert.equal(h.chips().length, 6);
    await h.openSaved();
    assert.equal(h.savedChoices().length, 7);
    assert.equal(h.savedChoices().at(-1).disabled, true);
    assert.deepEqual(h.draft('A').attachmentIds.value, ['A-1', 'A-2', 'A-3', 'A-4', 'A-5', 'A-6']);
    assert.match(textOf(h.root), /已保存\/复用 7 份资料/);
    assert.match(textOf(h.root), /已引用 6 份，1 份未引用/);
    assert.doesNotMatch(textOf(h.root), /没有上传/);
    assert.equal(h.workspace.state.busy, false);
    h.chips()[0].onClick();
    await tick();
    assert.match(textOf(h.root), /已引用 5 份，2 份未引用/);
  } finally {
    h.dispose();
  }
});

test('receipts merge into live selections rather than replacing them, deduplicating already-selected assets', async () => {
  const h = await harness();
  try {
    h.draft('A').attachmentIds.value = ['old1', 'old2', 'old3', 'old4', 'old5'];
    let call = 0;
    h.api.save = async () => asset(++call === 1 ? 'old1' : `new${call}`);
    await h.picker.value.acceptFiles(files(3));
    await tick();
    assert.deepEqual(h.draft('A').attachmentIds.value, [
      'old1',
      'old2',
      'old3',
      'old4',
      'old5',
      'new2',
    ]);
    assert.match(textOf(h.root), /已保存\/复用 3 份资料/);
    assert.match(textOf(h.root), /已引用 2 份，1 份未引用/);
  } finally {
    h.dispose();
  }
});

test('partial success and failed refresh preserve saved assets, named failure, and unattempted count', async () => {
  const h = await harness();
  try {
    let calls = 0;
    h.api.save = async () => {
      if (++calls === 2) throw new Error('offline after request');
      return asset('saved');
    };
    h.workspace.refreshBody = async () => {
      throw new Error('refresh unavailable');
    };
    await h.picker.value.acceptFiles(files(3));
    await tick();
    assert.equal(calls, 2);
    assert.deepEqual(h.draft('A').attachmentIds.value, ['saved']);
    assert.equal(h.chips().length, 1);
    assert.match(textOf(h.root), /未确认保存「file-2.txt」/);
    assert.match(textOf(h.root), /其余 1 个文件尚未尝试/);
    assert.match(textOf(h.root), /查看尚未尝试的文件（1）/);
    assert.match(textOf(h.root), /file-3\.txt/);
    assert.match(textOf(h.root), /已确认保存的资料仍可引用/);
    assert.equal(h.workspace.state.busy, false);
  } finally {
    h.dispose();
  }
});

test('Settings and A→B→A remounts retain transfer state and merge newer A references without leaking into B', async () => {
  const h = await harness();
  try {
    const save = deferred();
    h.api.save = () => save.promise;
    const upload = h.picker.value.acceptFiles(files(1));
    await tick();
    h.shown.value = false;
    await tick();
    h.shown.value = true;
    await tick();
    assert.match(textOf(h.root), /正在处理资料/);
    await h.navigate('B');
    h.draft('B').attachmentIds.value = ['B-only'];
    h.draft('A').attachmentIds.value = ['newer-A-choice'];
    save.resolve(asset('saved-A'));
    await upload;
    await tick();
    assert.deepEqual(h.draft('B').attachmentIds.value, ['B-only']);
    assert.doesNotMatch(textOf(h.root), /已保存\/复用|saved-A/);
    await h.navigate('A');
    assert.deepEqual(h.draft('A').attachmentIds.value, ['newer-A-choice', 'saved-A']);
    assert.match(textOf(h.root), /已保存\/复用 1 份资料/);
    assert.equal(h.chips().length, 1);
  } finally {
    h.dispose();
  }
});

test('deletion and undo invalidate late receipts without resurrecting selection or hiding newer drafts', async () => {
  const h = await harness();
  try {
    const save = deferred();
    let calls = 0;
    h.api.save = () => {
      calls++;
      return save.promise;
    };
    const upload = h.picker.value.acceptFiles(files(2));
    await tick();
    h.agentDrafts.discard('A');
    h.agentDrafts.activate('A');
    h.draft('A').content.value = 'new incarnation';
    h.draft('A').attachmentIds.value = ['new'];
    save.resolve(asset('old-receipt'));
    await upload;
    await tick();
    assert.equal(calls, 1);
    assert.deepEqual(h.draft('A').attachmentIds.value, ['new']);
    assert.equal(h.draft('A').content.value, 'new incarnation');
    assert.deepEqual(h.draft('A').confirmedAttachments.value, []);
    assert.equal(h.draft('A').attachmentTransfer.value, null);
    assert.equal(h.workspace.state.busy, false);
  } finally {
    h.dispose();
  }
});

test('a hung read-only refresh is bounded after confirmed selection, with busy held until settlement', async () => {
  const h = await harness();
  const originalSet = globalThis.setTimeout,
    originalClear = globalThis.clearTimeout;
  let expire, settledBusy;
  const token = {};
  try {
    h.workspace.refreshBody = () => new Promise(() => {});
    globalThis.setTimeout = (fn, ms, ...args) => {
      if (ms === 3000) {
        expire = fn;
        return token;
      }
      return originalSet(fn, ms, ...args);
    };
    globalThis.clearTimeout = (timer) => {
      if (timer !== token) originalClear(timer);
    };
    const upload = h.picker.value.acceptFiles(files(1));
    await tick();
    assert.deepEqual(h.draft('A').attachmentIds.value, ['A-1']);
    assert.equal(h.workspace.state.busy, true);
    assert.equal(h.draft('A').attachmentTransfer.value.phase, 'refreshing');
    expire();
    await upload;
    await tick();
    settledBusy = h.workspace.state.busy;
    assert.equal(settledBusy, false);
    assert.equal(h.draft('A').attachmentTransfer.value.phase, 'done');
    assert.equal(h.draft('A').attachmentTransfer.value.report.refresh, 'timeout');
    assert.deepEqual(h.draft('A').attachmentIds.value, ['A-1']);
    assert.match(textOf(h.root), /项目同步暂未完成/);
  } finally {
    globalThis.setTimeout = originalSet;
    globalThis.clearTimeout = originalClear;
    h.dispose();
  }
});

test('an unresolved mutation stays pending rather than being called failed by a read deadline', async () => {
  const h = await harness();
  try {
    const save = deferred();
    h.api.save = () => save.promise;
    const upload = h.picker.value.acceptFiles(files(1));
    await tick();
    assert.equal(h.workspace.state.busy, true);
    assert.equal(h.draft('A').attachmentTransfer.value.phase, 'uploading');
    assert.equal(h.draft('A').attachmentTransfer.value.report, null);
    assert.deepEqual(h.draft('A').attachmentIds.value, []);
    assert.match(textOf(h.root), /已确认 0\/1/);
    save.resolve(asset('confirmed'));
    await upload;
    assert.equal(h.workspace.state.busy, false);
  } finally {
    h.dispose();
  }
});

test('chooser cancellation is a no-op and input resets after failure to allow the same file again', async () => {
  const h = await harness();
  try {
    h.draft('A').attachmentIds.value = ['existing'];
    const input = h.fileInput();
    input.files = [];
    input.value = 'cancelled';
    await input.onChange({ target: input });
    assert.deepEqual(h.draft('A').attachmentIds.value, ['existing']);
    assert.equal(h.draft('A').attachmentTransfer.value, null);
    assert.equal(input.value, '');
    h.api.save = async () => {
      throw new Error('failed upload');
    };
    input.files = files(1);
    input.value = 'file-1.txt';
    await input.onChange({ target: input });
    assert.equal(input.value, '');
    h.api.save = async () => asset('same-file-retry');
    input.value = 'file-1.txt';
    await input.onChange({ target: input });
    assert.equal(input.value, '');
    assert.deepEqual(h.draft('A').attachmentIds.value, ['existing', 'same-file-retry']);
  } finally {
    h.dispose();
  }
});

test('busy guards do not release another operation and the prior non-picker upload API still returns IDs', async () => {
  const h = await harness();
  try {
    const legacy = h.useAttachments();
    h.workspace.state.busy = true;
    assert.deepEqual(await legacy.upload('A', files(1)), []);
    assert.equal(h.workspace.state.busy, true);
    assert.deepEqual(h.workspace.busy, []);
    h.workspace.state.busy = false;
    assert.deepEqual(await legacy.upload('A', files(1)), ['A-1']);
    assert.deepEqual(h.workspace.busy, [true, false]);
  } finally {
    h.dispose();
  }
});

test('an automatic-selection error does not relabel an authoritative saved receipt as an uncertain upload', async () => {
  const result = await uploadAttachmentBatch(
    files(1),
    deps({
      onConfirmed: () => {
        throw new Error('selection update unavailable');
      },
    }),
  );
  assert.equal(result.assets.length, 1);
  assert.equal(result.failure, undefined);
  assert.match(result.selectionError, /已确认保存/);
});

test('format reads are recoverable before upload without clearing existing references', async () => {
  const h = await harness();
  try {
    const reader = h.useAttachments();
    h.api.formats = async () => {
      throw new Error('formats offline');
    };
    assert.equal(await reader.loadFormats(), false);
    assert.match(reader.formatError.value, /重试/);
    assert.equal(h.workspace.state.busy, false);
    h.api.formats = async () => ({ extensions: ['.txt'], max_bytes: 8388608 });
    assert.equal(await reader.loadFormats(), true);
    assert.equal(reader.formatError.value, '');
    assert.deepEqual(reader.formats.value.extensions, ['.txt']);
  } finally {
    h.dispose();
  }
});

test('partial outcomes retain actionable recovery guidance above the quiet input', async () => {
  const h = await harness();
  try {
    h.api.save = async () => {
      throw new Error('unconfirmed');
    };
    await h.picker.value.acceptFiles(files(2));
    await tick();
    assert.match(textOf(h.root), /请先检查项目资料/);
    assert.match(textOf(h.root), /重新选择未确认或尚未尝试的文件/);
    assert.match(textOf(h.root), /重复文件会复用已有资料/);
    assert.equal(h.tools(), undefined);
    assert.equal(h.chips().length, 0);
    assert.ok(all(h.root).some((node) => node['aria-label'] === '资料上传状态'));
  } finally {
    h.dispose();
  }
});

test('legacy partial uploads return confirmed IDs and retain the current-project error signal', async () => {
  const h = await harness();
  try {
    const legacy = h.useAttachments();
    let calls = 0;
    h.api.save = async () => {
      if (++calls === 2) throw new Error('receipt missing');
      return asset('legacy-saved');
    };
    assert.deepEqual(await legacy.upload('A', files(3)), ['legacy-saved']);
    assert.match(h.workspace.state.error, /file-2.txt.*receipt missing/);
    assert.equal(h.workspace.state.busy, false);
  } finally {
    h.dispose();
  }
});

test('the upload report is finalized before busy ownership is released', async () => {
  const h = await harness();
  try {
    const setBusy = h.workspace.setBusy;
    let phaseAtRelease;
    h.workspace.setBusy = (value) => {
      if (!value) phaseAtRelease = h.draft('A').attachmentTransfer.value?.phase;
      setBusy(value);
    };
    await h.picker.value.acceptFiles(files(1));
    assert.equal(phaseAtRelease, 'done');
    assert.equal(h.draft('A').attachmentTransfer.value.report.assets[0].id, 'A-1');
  } finally {
    h.dispose();
  }
});

test('a delayed format read cannot upload an old choice into a deleted and restored project incarnation', async () => {
  const formats = deferred();
  const h = await harness({ formats: () => formats.promise });
  try {
    const upload = h.picker.value.acceptFiles(files(1));
    await tick();
    assert.equal(h.draft('A').pending.value, true);
    assert.equal(h.draft('A').attachmentTransfer.value.phase, 'preparing');
    h.agentDrafts.discard('A');
    h.agentDrafts.activate('A');
    h.draft('A').attachmentIds.value = ['new-incarnation'];
    formats.resolve({ extensions: ['.txt'], max_bytes: 8388608 });
    await upload;
    await tick();
    assert.equal(h.api.calls.filter(([action]) => action === 'attachments.upload').length, 0);
    assert.deepEqual(h.draft('A').attachmentIds.value, ['new-incarnation']);
    assert.equal(h.draft('A').attachmentTransfer.value, null);
    assert.equal(h.draft('A').pending.value, false);
    assert.match(h.notices.at(-1), /原项目已删除或改变.*尚未上传/);
  } finally {
    h.dispose();
  }
});

test('another operation winning delayed-format admission produces an explicit non-uploaded outcome', async () => {
  const formats = deferred();
  const h = await harness({ formats: () => formats.promise });
  try {
    const upload = h.picker.value.acceptFiles(files(2));
    await tick();
    h.workspace.state.busy = true;
    formats.resolve({ extensions: ['.txt'], max_bytes: 8388608 });
    await upload;
    await tick();
    assert.equal(h.api.calls.filter(([action]) => action === 'attachments.upload').length, 0);
    assert.equal(h.draft('A').pending.value, false);
    assert.equal(h.workspace.state.busy, true, 'The other operation still owns busy');
    assert.match(textOf(h.root), /尚未上传，请稍后重新选择/);
    assert.deepEqual(h.draft('A').attachmentTransfer.value.report.unattempted, ['file-2.txt']);
  } finally {
    h.dispose();
  }
});

test('navigation during delayed formats retains the original project upload intent', async () => {
  const formats = deferred();
  const h = await harness({ formats: () => formats.promise });
  try {
    const upload = h.picker.value.acceptFiles(files(1));
    await tick();
    await h.navigate('B');
    h.draft('B').attachmentIds.value = ['B-choice'];
    await h.navigate('A');
    h.draft('A').attachmentIds.value = ['newer-A-choice'];
    formats.resolve({ extensions: ['.txt'], max_bytes: 8388608 });
    await upload;
    await tick();
    assert.deepEqual(h.draft('A').attachmentIds.value, ['newer-A-choice', 'A-1']);
    assert.deepEqual(h.draft('B').attachmentIds.value, ['B-choice']);
    assert.equal(h.draft('A').pending.value, false);
  } finally {
    h.dispose();
  }
});

test('tools open with disclosure, Escape restores focus, and outside/navigation close stale menus', async () => {
  const h = await harness();
  try {
    assert.equal(h.tools(), undefined);
    await h.openTools();
    assert.equal(h.addButton()['aria-expanded'], true);
    assert.equal(globalThis.document.activeElement.tag, 'button');
    assert.match(textOf(h.tools()), /上传成功后都会保存到项目/);
    const anchor = all(h.root).find((node) => node.class === 'attachment-tools-anchor');
    await anchor.onKeydown({ key: 'Escape', stopPropagation() {}, preventDefault() {} });
    await tick();
    assert.equal(h.tools(), undefined);
    assert.equal(globalThis.document.activeElement, h.addButton());
    await h.openTools();
    h.domListeners.pointerdown({ target: new HostElement('outside') });
    await tick();
    assert.equal(h.tools(), undefined);
    await h.openTools();
    h.contextKey.value = 'architecture';
    await tick();
    assert.equal(h.tools(), undefined);
    assert.deepEqual(h.draft('A').attachmentIds.value, []);
    assert.equal(h.api.calls.filter(([action]) => action === 'attachments.upload').length, 0);
  } finally {
    h.dispose();
  }
});

test('only selected assets occupy the input and saved assets remain selectable through tools', async () => {
  const h = await harness();
  try {
    h.projects.A.attachments = [asset('one'), asset('two')];
    await tick();
    assert.equal(h.chips().length, 0);
    await h.openTools();
    await h.openSaved();
    assert.equal(h.savedChoices().length, 2);
    h.savedChoices()[1].onClick();
    await tick();
    assert.equal(h.chips().length, 1);
    assert.deepEqual(h.draft('A').attachmentIds.value, ['two']);
    h.chips()[0].onClick();
    await tick();
    assert.equal(h.chips().length, 0);
    assert.equal(h.projects.A.attachments.length, 2);
  } finally {
    h.dispose();
  }
});
