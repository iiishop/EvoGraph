import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
import { parse, compileScript } from '@vue/compiler-sfc';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
  url: 'http://localhost/',
});
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
// DOM events are real. jsdom has no layout; native screenshot/edge geometry QA is separate.
globalThis.ResizeObserver = class {
  observe() {}
  disconnect() {}
};
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
const { createApp, h, nextTick, ref } = await import('vue');
const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const vue = pathToFileURL(require.resolve('vue')).href;
const modelUrl = url(transpile(source('lib/sourceClassModel.ts')));
const apiUrl = url(
  'export const calls = []; export const handlers = []; export const command = (...args) => { calls.push(args); return handlers.shift()(...args); };',
);
const agentUrl = url(
  'export const useAgent = () => ({state:{projectId:"",view:"architecture",follow:{},navigationTick:0},freeView(){}});',
);
const stub = url('export default { render() { return null; } };');
const imports = {
  vue,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../api/client': apiUrl,
  '../api/client': apiUrl,
  '../../lib/sourceClassModel': modelUrl,
  '../lib/sourceClassModel': modelUrl,
  '../../composables/useAgent': agentUrl,
  '../../lib/architectureRoles': url(transpile(source('lib/architectureRoles.ts'))),
  './DiagramView.vue': stub,
  './ArchitectureQuality.vue': stub,
  './ComponentPassport.vue': stub,
};
const rewrite = (code) =>
  code.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected dependency ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
const compile = (path) => {
  const { descriptor } = parse(source(path));
  return url(
    rewrite(transpile(compileScript(descriptor, { id: path, inlineTemplate: true }).content)),
  );
};
imports['./SourceClassCards.vue'] = compile('components/design/SourceClassCards.vue');
imports['./DiagramImage.vue'] = compile('components/design/DiagramImage.vue');
imports['../ui/AppModal.vue'] = compile('components/ui/AppModal.vue');
imports['./UmlView.vue'] = compile('components/design/UmlView.vue');
imports['../../composables/useScopedClassDetail'] = url(
  rewrite(transpile(source('composables/useScopedClassDetail.ts'))),
);
const Cards = (await import(imports['./SourceClassCards.vue'])).default;
const UmlView = (await import(imports['./UmlView.vue'])).default;
const Panel = (await import(compile('components/design/ArchitecturePanel.vue'))).default;
const { calls, handlers } = await import(apiUrl);
const { validSourceClassModel } = await import(modelUrl);
const location = (line, end_line = line) => ({ path: 'orders.py', line, end_line });
const signature = 'async submit(self, name: str, /, *, token: str = …) : str';
const semantic = (extra = {}) => ({
  schema_version: 1,
  origin: 'source',
  project_id: 'P1',
  baseline_id: 'B1',
  architecture_revision: 0,
  component_ids: ['orders'],
  files: ['orders.py'],
  source_fingerprints: { 'orders.py': 'hash' },
  packages: [{ id: 'package_orders', component_id: 'orders', label: 'orders' }],
  classes: [
    {
      id: 'class_base',
      name: 'Base',
      kind: 'class',
      language: 'python',
      package_ids: ['package_orders'],
      location: location(1),
      members: [],
    },
    {
      id: 'class_order',
      name: 'OrderService',
      kind: 'class',
      language: 'python',
      package_ids: ['package_orders'],
      location: location(3, 10),
      members: [
        {
          id: 'member_repository',
          name: 'repository',
          kind: 'field',
          text: 'repository : Repository',
          visibility: 'public',
          qualifiers: [],
          location: location(4),
        },
        {
          id: 'member_submit',
          name: 'submit',
          kind: 'method',
          text: signature,
          visibility: 'public',
          qualifiers: ['async'],
          location: location(6, 8),
        },
        {
          id: 'member_private',
          name: '_validate',
          kind: 'method',
          text: '_validate(self) : bool',
          visibility: 'private',
          qualifiers: [],
          location: location(9, 10),
        },
      ],
    },
  ],
  boundaries: [
    {
      id: 'boundary_storage',
      label: 'storage.repository',
      kind: 'import',
      origin: 'source',
      reason: 'external_import',
    },
  ],
  relations: [
    {
      id: 'relation_base',
      source: 'class_order',
      target: 'class_base',
      kind: 'extends',
      origin: 'source',
      resolution: 'selected',
      label: 'Base',
      location: location(3),
    },
    {
      id: 'relation_import',
      source: 'package_orders',
      target: 'boundary_storage',
      kind: 'import',
      origin: 'source',
      resolution: 'boundary',
      label: 'storage.repository',
      location: location(1),
    },
  ],
  limitations: ['仅解析所选文件'],
  ...extra,
});
const diagram = (extra = {}) => ({
  id: 'scoped',
  title: '局部类细节',
  kind: 'class',
  revision: 0,
  origin: 'source',
  component_ids: ['orders'],
  architecture_revision: 0,
  baseline_id: 'B1',
  scope: 'orders',
  source_refs: ['orders.py'],
  source: '@startuml\nclass OrderService {\n' + signature + '\n}\n@enduml',
  ...extra,
});
const image = 'data:image/svg+xml;base64,PHN2Zy8+';
const ready = (extra = {}) => ({
  status: 'ready',
  message: '已提取所选声明',
  files: ['orders.py'],
  component_ids: ['orders'],
  boundaries: [],
  limitations: [],
  diagram: diagram(),
  image,
  semantic: semantic(),
  ...extra,
});
const graph = {
  id: 'architecture',
  title: 'SRC',
  nodes: [{ id: 'orders', label: 'orders', source_refs: ['orders.py'], role: 'backend' }],
  edges: [],
  groups: [],
};
const project = (extra = {}) => ({
  id: 'P1',
  source_fingerprint: 'FP1',
  baselines: [{ id: 'B1' }],
  research: [],
  attachments: [],
  diagrams: [],
  uml_diagrams: [],
  milestones: [],
  architectures: [],
  source_diagram: graph,
  ...extra,
});
const tick = async () => {
  await nextTick();
  await Promise.resolve();
  await nextTick();
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
async function mount(view, initial) {
  const props = ref(initial),
    root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ setup: () => () => h(view, props.value) });
  app.mount(root);
  await tick();
  return {
    root,
    props,
    async update(next) {
      props.value = next;
      await tick();
    },
    destroy() {
      app.unmount();
      root.remove();
    },
  };
}
function button(root, text) {
  const found = [...root.querySelectorAll('button')].find(
    (item) => item.textContent.trim() === text,
  );
  assert.ok(found, `Button ${text}`);
  return found;
}
async function click(element) {
  element.click();
  await tick();
}
async function requestScope(root) {
  await click(root.querySelector('.class-scope-options input'));
  await click(button(root, '查看局部类结构'));
}

test('actual member buttons preserve complete canonical signatures and open provenance details', async () => {
  const view = await mount(Cards, { model: semantic() });
  assert.equal(view.root.querySelectorAll('.source-class-card').length, 2);
  assert.equal(view.root.querySelectorAll('.source-class-member').length, 3);
  assert.deepEqual(
    [...view.root.querySelectorAll('.source-class-member code')].map((item) => item.textContent),
    ['repository : Repository', signature, '_validate(self) : bool'],
  );
  const submit = [...view.root.querySelectorAll('.source-class-member')].find((item) =>
    item.textContent.includes('submit('),
  );
  await click(submit);
  assert.equal(submit.getAttribute('aria-pressed'), 'true');
  const detail = view.root.querySelector('.source-member-detail');
  assert.equal(detail.querySelector('pre').textContent, signature);
  assert.match(detail.textContent, /orders.py:6–8/);
  assert.match(detail.textContent, /async/);
  assert.match(detail.textContent, /命名约定/);
  assert.match(view.root.querySelector('.source-class-relations').textContent, /package orders/);
  assert.match(
    view.root.querySelector('.source-class-relations').textContent,
    /不代表类或方法调用/,
  );
  await click(view.root.querySelector('[aria-label="清除成员选择"]'));
  assert.equal(submit.getAttribute('aria-pressed'), 'false');
  view.destroy();
});

test('replacement model clears selection, including a removed member with the same display name', async () => {
  const view = await mount(Cards, { model: semantic() });
  await click([...view.root.querySelectorAll('.source-class-member')][1]);
  const replacement = semantic();
  replacement.classes[1].members[1] = {
    ...replacement.classes[1].members[1],
    id: 'member_new_signature',
    text: 'submit(self, request: Request) : Receipt',
  };
  await view.update({ model: replacement });
  assert.match(view.root.querySelector('.source-member-detail').textContent, /选择类或成员/);
  assert.equal(view.root.querySelector('.source-class-member[aria-pressed="true"]'), null);
  view.destroy();
});

test('semantic tabs use real cards and retain standard image, raw source, and saved DESIGN fallback', async () => {
  const props = { diagram: diagram(), projectId: 'P1', previewImage: image, semantic: semantic() };
  const view = await mount(UmlView, props);
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.equal(view.root.querySelector('img'), null);
  await click(button(view.root, '标准 PlantUML 图'));
  assert.equal(view.root.querySelector('img').getAttribute('src'), image);
  await click(button(view.root, 'PlantUML 源码'));
  assert.equal(view.root.querySelector('.uml-source').textContent, props.diagram.source);
  await click(button(view.root, '成员与关系'));
  await click(button(view.root, '查看 PlantUML 源码 ↗'));
  assert.equal(view.root.querySelector('.uml-source').textContent, props.diagram.source);
  await view.update({ ...props, diagram: diagram({ origin: 'design', revision: 2 }) });
  assert.equal(view.root.querySelector('.source-class-view'), null);
  assert.equal(view.root.querySelector('.uml-semantic-tabs'), null);
  assert.ok(view.root.querySelector('img'));
  assert.match(view.root.textContent, /DESIGN 设计待实现/);
  assert.ok(view.root.querySelector('details .uml-source'));
  view.destroy();
});

test('unsupported semantic payload falls back without interpreting PlantUML as source classes', async () => {
  for (const model of [
    undefined,
    semantic({ schema_version: 99 }),
    semantic({ project_id: 'OTHER' }),
    semantic({ baseline_id: 'OLD' }),
  ]) {
    const view = await mount(UmlView, {
      diagram: diagram(),
      projectId: 'P1',
      previewImage: image,
      semantic: model,
    });
    assert.equal(view.root.querySelector('.source-class-card'), null);
    assert.ok(view.root.querySelector('img'));
    assert.equal(view.root.querySelector('.uml-source').textContent, diagram().source);
    view.destroy();
  }
});

test('mounted scope extraction opens semantic cards; Close and project switch reject late source results', async () => {
  calls.length = 0;
  handlers.length = 0;
  const first = deferred(),
    second = deferred(),
    third = deferred();
  handlers.push(
    () => first.promise,
    () => second.promise,
    () => third.promise,
  );
  const view = await mount(Panel, { project: project() });
  await requestScope(view.root);
  await click(button(view.root, '返回模块总览'));
  first.resolve(ready());
  await tick();
  assert.equal(view.root.querySelector('.source-class-view'), null);
  await click(button(view.root, '查看局部类结构'));
  second.resolve(ready());
  await tick();
  assert.ok(view.root.querySelector('.source-class-card'));
  await click(button(view.root, '返回模块总览'));
  await view.update({ project: project({ source_fingerprint: 'FP2', baselines: [{ id: 'B2' }] }) });
  await requestScope(view.root);
  await view.update({ project: project({ id: 'P2', baselines: [{ id: 'B9' }] }) });
  third.resolve(ready({ semantic: semantic({ baseline_id: 'B2' }) }));
  await tick();
  assert.equal(view.root.querySelector('.source-class-view'), null);
  assert.equal(view.root.querySelector('.architecture-class-detail'), null);
  assert.equal(calls.length, 3);
  assert.ok(calls.every(([action]) => action === 'architecture.class_detail'));
  view.destroy();
});

test('mounted source scope rejects mismatched project, baseline, and narrowed file evidence', async () => {
  for (const invalid of [
    semantic({ project_id: 'OTHER' }),
    semantic({ baseline_id: 'OLD' }),
    semantic({ files: ['outside.py'] }),
  ]) {
    handlers.push(async () => ready({ semantic: invalid }));
    const view = await mount(Panel, { project: project() });
    await requestScope(view.root);
    await tick();
    assert.equal(view.root.querySelector('.source-class-card'), null);
    assert.match(
      view.root.querySelector('[role="alert"]').textContent,
      /当前项目、基线或文件范围不一致/,
    );
    view.destroy();
  }
});

test('empty, unsupported, oversized, and stale scopes remain honest and render no semantic cards', async () => {
  for (const status of ['empty', 'unsupported', 'too_large', 'unmapped']) {
    handlers.push(async () =>
      ready({
        status,
        message: `真实原因 ${status}`,
        semantic: undefined,
        diagram: undefined,
        image: undefined,
      }),
    );
    const view = await mount(Panel, { project: project() });
    await requestScope(view.root);
    await tick();
    assert.equal(view.root.querySelector('.source-class-card'), null);
    assert.match(
      view.root.querySelector('.class-detail-state').textContent,
      new RegExp(`真实原因 ${status}`),
    );
    view.destroy();
  }
});

test('model contract rejects duplicate IDs, missing endpoints, and outside-file members', () => {
  assert.equal(validSourceClassModel(semantic()), true);
  const duplicate = semantic();
  duplicate.classes[1].members[1].id = duplicate.classes[1].members[0].id;
  const missing = semantic();
  missing.relations[0].target = 'unseen-class';
  const outside = semantic();
  outside.classes[1].members[0].location.path = 'outside.py';
  for (const invalid of [duplicate, missing, outside])
    assert.equal(validSourceClassModel(invalid), false);
});

test('mounted standard preview ignores a late image after project and diagram changes', async () => {
  const old = deferred(),
    current = deferred();
  handlers.push(
    () => old.promise,
    () => current.promise,
  );
  const view = await mount(UmlView, {
    diagram: diagram({ origin: 'design', id: 'D1' }),
    projectId: 'P1',
  });
  await view.update({ diagram: diagram({ origin: 'design', id: 'D2' }), projectId: 'P2' });
  current.resolve({ image: 'data:image/svg+xml;base64,TkVX' });
  await tick();
  old.resolve({ image: 'data:image/svg+xml;base64,T0xE' });
  await tick();
  assert.equal(
    view.root.querySelector('img').getAttribute('src'),
    'data:image/svg+xml;base64,TkVX',
  );
  view.destroy();
});

test('renderer failure keeps actual semantic members and raw source without requesting an unsaved diagram ID', async () => {
  calls.length = 0;
  const view = await mount(UmlView, {
    diagram: diagram(),
    projectId: 'P1',
    semantic: semantic(),
    renderStatus: 'unavailable',
    renderError: '本地 PlantUML 编译器暂不可用',
  });
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.match(view.root.querySelector('.uml-render-notice').textContent, /标准图暂不可用/);
  assert.equal(button(view.root, '展开大图').disabled, false);
  await click(button(view.root, '标准 PlantUML 图'));
  assert.match(
    view.root.querySelector('[role="alert"]').textContent,
    /本地 PlantUML 编译器暂不可用/,
  );
  assert.equal(view.root.querySelector('img'), null);
  await click(button(view.root, 'PlantUML 源码'));
  assert.equal(view.root.querySelector('.uml-source').textContent, diagram().source);
  assert.equal(calls.length, 0);
  // Returning to saved history must still use its existing saved-ID preview contract.
  handlers.push(async () => ({ image }));
  await view.update({
    diagram: diagram({ origin: 'design', revision: 2, id: 'saved-design' }),
    projectId: 'P1',
  });
  assert.deepEqual(calls, [
    ['uml.preview', { project_id: 'P1', diagram_id: 'saved-design', revision: 2 }],
  ]);
  assert.equal(view.root.querySelector('img').getAttribute('src'), image);
  assert.equal(view.root.querySelector('.uml-render-notice'), null);
  view.destroy();
});

test('semantic-only extraction is cached only in its exact source scope and refresh replaces the unavailable state', async () => {
  calls.length = 0;
  handlers.push(async () =>
    ready({ image: undefined, render_status: 'unavailable', render_error: '标准图编译失败' }),
  );
  const view = await mount(Panel, { project: project() });
  await requestScope(view.root);
  await tick();
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.match(view.root.textContent, /标准图暂不可用/);
  await click(button(view.root, '返回模块总览'));
  await click(button(view.root, '查看局部类结构'));
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.equal(calls.length, 1);
  await view.update({ project: project({ source_fingerprint: 'FP2', baselines: [{ id: 'B2' }] }) });
  assert.equal(view.root.querySelector('.source-class-card'), null);
  handlers.push(async () =>
    ready({
      diagram: diagram({ baseline_id: 'B2' }),
      semantic: semantic({ baseline_id: 'B2' }),
      render_status: 'ready',
    }),
  );
  await requestScope(view.root);
  await tick();
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.equal(view.root.querySelector('.uml-render-notice'), null);
  await click(button(view.root, '标准 PlantUML 图'));
  assert.equal(view.root.querySelector('img').getAttribute('src'), image);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(([action]) => action === 'architecture.class_detail'));
  view.destroy();
});

test('retrying a failed standard source render reloads only the exact bounded scope and ignores duplicate clicks', async () => {
  calls.length = 0;
  handlers.push(async () =>
    ready({ image: undefined, render_status: 'unavailable', render_error: '临时编译失败' }),
  );
  const view = await mount(Panel, { project: project() });
  await requestScope(view.root);
  await tick();
  await click(button(view.root, '标准 PlantUML 图'));
  const retry = button(view.root, '重新提取并编译');
  const next = deferred();
  handlers.push(() => next.promise);
  retry.click();
  retry.click();
  await tick();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], [
    'architecture.class_detail',
    { project_id: 'P1', component_ids: ['orders'], architecture_revision: 0 },
  ]);
  assert.match(view.root.textContent, /正在提取所选模块/);
  next.resolve(ready({ render_status: 'ready' }));
  await tick();
  assert.ok(view.root.querySelector('.source-class-card'));
  assert.equal(view.root.querySelector('.uml-render-notice'), null);
  await click(button(view.root, '标准 PlantUML 图'));
  assert.equal(view.root.querySelector('img').getAttribute('src'), image);
  assert.ok(calls.every(([action]) => action === 'architecture.class_detail'));
  view.destroy();
});

test('source semantic validator is total over malformed JSON and bounds every collection', () => {
  const bad = [
    null,
    undefined,
    [],
    2,
    'text',
    {},
    semantic({ classes: [null] }),
    semantic({ packages: [null] }),
    semantic({ boundaries: [null] }),
    semantic({ relations: [null] }),
    semantic({ source_fingerprints: null }),
    semantic({ component_ids: [null] }),
    semantic({ files: [null] }),
    semantic({ limitations: [null] }),
    semantic({ relations: Array(1025).fill({}) }),
  ];
  for (const [collection, field, value] of [
    ['classes', 'name', null],
    ['classes', 'language', null],
    ['classes', 'members', [null]],
    ['classes', 'location', null],
    ['packages', 'label', null],
    ['boundaries', 'kind', 'toString'],
    ['relations', 'kind', 'toString'],
    ['relations', 'label', null],
    ['relations', 'location', {}],
    ['relations', 'resolution', 'invented'],
  ]) {
    const model = semantic();
    model[collection][0][field] = value;
    bad.push(model);
  }
  for (const [field, value] of [
    ['name', null],
    ['text', null],
    ['qualifiers', [null]],
    ['visibility', 'toString'],
    ['location', null],
  ]) {
    const model = semantic();
    model.classes[1].members[0][field] = value;
    bad.push(model);
  }
  for (const model of bad) assert.equal(validSourceClassModel(model), false);
});

test('malformed additive payload falls back to standard rendering without crashing', async () => {
  const view = await mount(UmlView, {
    diagram: diagram(),
    projectId: 'P1',
    previewImage: image,
    semantic: semantic({ classes: [null] }),
  });
  assert.equal(view.root.querySelector('.source-class-card'), null);
  assert.equal(view.root.querySelector('img')?.getAttribute('src'), image);
  view.destroy();
});

test('source card measurement ignores callbacks after unmount and uses flow bounds', async () => {
  const previousFrame = globalThis.requestAnimationFrame,
    previousCancel = globalThis.cancelAnimationFrame,
    previousObserver = globalThis.ResizeObserver;
  const callbacks = new Map();
  let sequence = 0,
    observed = 0;
  const observerCallbacks = [];
  globalThis.requestAnimationFrame = (callback) => {
    callbacks.set(++sequence, callback);
    return sequence;
  };
  globalThis.cancelAnimationFrame = (id) => {
    callbacks.delete(id);
  };
  globalThis.ResizeObserver = class {
    constructor(callback) {
      observerCallbacks.push(callback);
    }
    observe() {
      observed++;
    }
    disconnect() {}
  };
  try {
    const view = await mount(Cards, { model: semantic() });
    const canvas = view.root.querySelector('.source-class-canvas');
    canvas.getBoundingClientRect = () => ({ width: 790, height: 900, top: 0, left: 0 });
    Object.defineProperty(canvas, 'scrollWidth', { value: 99999 });
    Object.defineProperty(canvas, 'scrollHeight', { value: 99999 });
    for (const callback of [...callbacks.values()]) callback();
    callbacks.clear();
    await tick();
    assert.equal(view.root.querySelector('svg').getAttribute('width'), '790');
    assert.equal(view.root.querySelector('svg').getAttribute('height'), '900');
    view.props.value = { model: semantic() };
    await nextTick(); // observe() may still be waiting for its own nextTick.
    view.destroy();
    const before = observed;
    await tick();
    for (const callback of observerCallbacks) callback();
    assert.equal(observed, before);
    assert.equal(callbacks.size, 0);
  } finally {
    globalThis.requestAnimationFrame = previousFrame;
    globalThis.cancelAnimationFrame = previousCancel;
    globalThis.ResizeObserver = previousObserver;
  }
});

test('class canonical declaration is displayed without changing stable identity or relationship names', async () => {
  const model = semantic();
  model.classes[1].declaration = 'class OrderService<T extends Request = …>';
  assert.equal(validSourceClassModel(model), true);
  const view = await mount(Cards, { model });
  const heading = [...view.root.querySelectorAll('.source-class-heading')][1];
  assert.match(heading.textContent, /class OrderService<T extends Request = …>/);
  await click(heading);
  assert.equal(
    view.root.querySelector('.source-member-detail pre').textContent,
    model.classes[1].declaration,
  );
  assert.equal(model.classes[1].name, 'OrderService');
  view.destroy();
  model.classes[1].declaration = null;
  assert.equal(validSourceClassModel(model), false);
});

test('embedded source drill-down starts with one contextual bar and keeps overview navigation reachable', async () => {
  handlers.push(async () => ready());
  const view = await mount(Panel, { project: project() });
  await requestScope(view.root);
  await tick();
  assert.equal(view.root.querySelector('.architecture-heading').style.display, 'none');
  assert.equal(view.root.querySelector('.architecture-tabs').style.display, 'none');
  assert.ok(view.root.querySelector('.uml-view.uml-compact'));
  assert.match(
    view.root.querySelector('.class-detail-disclaimer summary').textContent,
    /当前源码提取/,
  );
  assert.equal(view.root.querySelector('.class-detail-disclaimer').open, false);
  assert.match(view.root.querySelector('.class-detail-disclaimer').textContent, /不代表设计已实现/);
  assert.ok(view.root.querySelector('[aria-label="类结构显示方式"]'));
  assert.ok(button(view.root, '导出 .puml'));
  await click(button(view.root, '返回模块总览'));
  assert.equal(view.root.querySelector('.architecture-heading').style.display, '');
  assert.equal(view.root.querySelector('.architecture-tabs').style.display, '');
  assert.equal(view.root.querySelector('.architecture-class-detail'), null);
  assert.equal(document.activeElement, view.root.querySelector('.class-detail-trigger'));
  view.destroy();
});

test('expanded semantic structure retains selected members, raw access and Escape return focus', async () => {
  const view = await mount(UmlView, {
    diagram: diagram(),
    projectId: 'P1',
    previewImage: image,
    semantic: semantic(),
  });
  const originalMember = view.root.querySelector('[data-source-member="member_submit"]');
  await click(originalMember);
  const expand = button(view.root, '展开大图');
  expand.focus();
  await click(expand);
  const dialog = view.root.querySelector('dialog');
  assert.ok(dialog?.open);
  assert.match(dialog.querySelector('.source-member-detail pre').textContent, /async submit\(self/);
  assert.equal(dialog.querySelector('img'), null);
  await click(dialog.querySelector('[data-source-member="member_private"]'));
  dialog.dispatchEvent(new Event('cancel', { cancelable: true }));
  await tick();
  assert.equal(view.root.querySelector('dialog'), null);
  assert.equal(document.activeElement, expand);
  assert.match(
    view.root.querySelector('.source-member-detail pre').textContent,
    /_validate\(self\)/,
  );
  for (let i = 0; i < 3; i++) {
    expand.focus();
    await click(expand);
    assert.match(
      view.root.querySelector('dialog .source-member-detail pre').textContent,
      /_validate/,
    );
    await click(view.root.querySelector('dialog [aria-label="关闭"]'));
    assert.equal(document.activeElement, expand);
  }
  await click(expand);
  await click(button(view.root.querySelector('dialog'), '查看 PlantUML 源码 ↗'));
  assert.equal(view.root.querySelector('dialog'), null);
  assert.equal(view.root.querySelector('.uml-source').textContent, diagram().source);
  view.destroy();
});

test('semantic expansion works without a renderer and closes on scope replacement', async () => {
  const props = {
    diagram: diagram(),
    projectId: 'P1',
    semantic: semantic(),
    renderStatus: 'unavailable',
    renderError: 'unavailable',
  };
  const view = await mount(UmlView, props);
  await click(button(view.root, '展开大图'));
  assert.ok(view.root.querySelector('dialog .source-class-card'));
  await view.update({
    ...props,
    diagram: diagram({ baseline_id: 'B2' }),
    semantic: semantic({ baseline_id: 'B2' }),
  });
  assert.equal(view.root.querySelector('dialog'), null);
  assert.equal(view.root.querySelector('.source-class-member[aria-pressed="true"]'), null);
  view.destroy();
});

test('renderer-unavailable modal to raw source hands focus to the visible source block', async () => {
  const view = await mount(UmlView, {
    diagram: diagram(),
    projectId: 'P1',
    semantic: semantic(),
    renderStatus: 'unavailable',
    renderError: 'unavailable',
  });
  const expand = button(view.root, '展开大图');
  expand.focus();
  await click(expand);
  await click(button(view.root.querySelector('dialog'), '查看 PlantUML 源码 ↗'));
  const raw = view.root.querySelector('.uml-source');
  assert.equal(view.root.querySelector('dialog'), null);
  assert.equal(expand.disabled, true);
  assert.equal(document.activeElement, raw);
  assert.equal(raw.textContent, diagram().source);
  view.destroy();
});

test('source boundary paths remeasure their actual endpoints after scrolling', async () => {
  const previousFrame = globalThis.requestAnimationFrame,
    previousCancel = globalThis.cancelAnimationFrame;
  const callbacks = new Map();
  let sequence = 0;
  globalThis.requestAnimationFrame = (callback) => {
    callbacks.set(++sequence, callback);
    return sequence;
  };
  globalThis.cancelAnimationFrame = (id) => callbacks.delete(id);
  const drain = async () => {
    const pending = [...callbacks.values()];
    callbacks.clear();
    for (const fn of pending) fn();
    await tick();
  };
  try {
    const model = semantic();
    model.classes[1].declaration = 'class MemoryInventoryRepository(InventoryRepository)';
    const view = await mount(Cards, { model });
    const canvas = view.root.querySelector('.source-class-canvas');
    canvas.getBoundingClientRect = () => ({ width: 740, height: 1100, top: 0, left: 0 });
    const boundary = view.root.querySelector('[data-semantic-id="boundary_storage"]');
    let top = 80;
    boundary.getBoundingClientRect = () => ({
      top,
      left: 510,
      right: 730,
      width: 220,
      height: 100,
    });
    await drain();
    const initial = view.root.querySelector('.source-class-edge.import path').getAttribute('d');
    top = 280;
    view.root.querySelector('.source-class-scroll').dispatchEvent(new Event('scroll'));
    await drain();
    const updated = view.root.querySelector('.source-class-edge.import path').getAttribute('d');
    assert.notEqual(updated, initial);
    assert.ok(updated.endsWith(',510 324'));
    assert.equal(canvas.querySelector('svg').getAttribute('width'), '740');
    const heading = view.root.querySelectorAll('.source-class-heading strong')[1];
    assert.equal(heading.textContent, model.classes[1].declaration);
    assert.ok(heading.querySelector('wbr'));
    view.destroy();
  } finally {
    globalThis.requestAnimationFrame = previousFrame;
    globalThis.cancelAnimationFrame = previousCancel;
  }
});
