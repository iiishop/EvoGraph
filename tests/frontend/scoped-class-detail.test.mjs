import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { effectScope, ref, createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
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
const apiUrl = moduleUrl(
  'export const calls = []; export const command = async (...args) => { calls.push(args); return { image: "data:image/svg+xml;base64,PHN2Zy8+" }; };',
);
const scopedUrl = moduleUrl(
  transpile(source('composables/useScopedClassDetail.ts'))
    .replace("from 'vue'", `from ${JSON.stringify(vueUrl)}`)
    .replace("from '../api/client'", `from ${JSON.stringify(apiUrl)}`),
);
const { useScopedClassDetail, classDetailGuidance, matchingScopedDesigns } = await import(
  scopedUrl
);
const result = (ids = ['a'], extra = {}) => ({
  status: 'ready',
  message: '局部结构已提取',
  component_ids: ids,
  image: 'data:image/svg+xml;base64,PHN2Zy8+',
  files: ['a.py'],
  boundaries: [],
  limitations: [],
  ...extra,
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
function session(load) {
  const context = ref({
    key: 'P1:A41:B1',
    projectId: 'P1',
    architectureRevision: 41,
    componentIds: ['a', 'b', 'c', 'd'],
    componentFiles: { a: ['a.py', 'shared.py'], b: ['b.py', 'shared.py'], c: ['c.py'], d: [] },
  });
  const scope = effectScope();
  const detail = scope.run(() => useScopedClassDetail(() => context.value, load));
  return { detail, context, dispose: () => scope.stop() };
}

test('selection is explicit, deduplicated, bounded, and never invokes a global generator', async () => {
  const requests = [];
  const { detail, dispose } = session(async (args) => {
    requests.push(args);
    return result(args.component_ids);
  });
  await detail.show();
  assert.equal(requests.length, 0);
  assert.equal(detail.open.value, false);
  assert.equal(detail.select(['a', 'a', 'b', 'c']), true);
  assert.deepEqual(detail.componentIds.value, ['a', 'b', 'c']);
  assert.equal(detail.toggle('d'), false);
  assert.equal(detail.select(['unknown']), false);
  await detail.show();
  assert.deepEqual(requests, [
    { project_id: 'P1', component_ids: ['a', 'b', 'c'], architecture_revision: 41 },
  ]);
  assert.equal(detail.loading.value, false);
  assert.equal(detail.result.value.status, 'ready');
  detail.toggle('b');
  assert.deepEqual(detail.componentIds.value, ['a', 'c']);
  assert.equal(detail.result.value, null);
  dispose();
});

test('source requests use revision zero, target and historical requests use their exact number', async () => {
  for (const revision of [0, 7, 41]) {
    const requests = [];
    const { detail, context, dispose } = session(async (args) => {
      requests.push(args);
      return result();
    });
    context.value = { ...context.value, key: `A${revision}`, architectureRevision: revision };
    detail.select(['a']);
    await detail.show();
    assert.equal(requests[0].architecture_revision, revision);
    dispose();
  }
});

test('repeated clicks are coalesced; Back keeps scope and reuses a ready preview', async () => {
  const pending = deferred();
  let calls = 0;
  const { detail, dispose } = session(() => {
    calls++;
    return pending.promise;
  });
  detail.select(['a']);
  const first = detail.show();
  await detail.show();
  assert.equal(calls, 1);
  pending.resolve(result());
  await first;
  detail.close();
  assert.deepEqual(detail.componentIds.value, ['a']);
  assert.equal(detail.open.value, false);
  await detail.show();
  assert.equal(detail.open.value, true);
  assert.equal(calls, 1);
  dispose();
});

test('closing during loading ignores the late response and permits a fresh request', async () => {
  const pending = [deferred(), deferred()];
  let calls = 0;
  const { detail, dispose } = session(() => pending[calls++].promise);
  detail.select(['a']);
  const first = detail.show();
  detail.close();
  const second = detail.show();
  pending[0].resolve(result(['a'], { message: 'stale' }));
  await first;
  assert.equal(detail.result.value, null);
  assert.equal(detail.loading.value, true);
  pending[1].resolve(result(['a'], { message: 'fresh' }));
  await second;
  assert.equal(detail.result.value.message, 'fresh');
  dispose();
});

test('a scope change clears stale output and prevents a late error replacing the new result', async () => {
  const pending = deferred();
  let calls = 0;
  const { detail, dispose } = session(async (args) =>
    ++calls === 1 ? pending.promise : result(args.component_ids),
  );
  detail.select(['a']);
  const first = detail.show();
  detail.select(['b']);
  assert.equal(detail.open.value, false);
  await detail.show();
  pending.reject(new Error('old request error'));
  await first;
  assert.deepEqual(detail.result.value.component_ids, ['b']);
  assert.equal(detail.error.value, '');
  dispose();
});

test('project, baseline, source and architecture context changes clear scope and pending detail', async () => {
  for (const key of ['P2:A41:B1', 'P1:A41:B2', 'P1:SRC:new-fingerprint', 'P1:A42:B1']) {
    const pending = deferred();
    const { detail, context, dispose } = session(() => pending.promise);
    detail.select(['a']);
    detail.selectFiles(['a.py']);
    const request = detail.show();
    context.value = { ...context.value, key };
    assert.deepEqual(detail.componentIds.value, []);
    assert.equal(detail.filePaths.value, null);
    assert.equal(detail.open.value, false);
    pending.resolve(result());
    await request;
    assert.equal(detail.result.value, null);
    assert.equal(detail.loading.value, false);
    dispose();
  }
});

test('load errors are local and retryable; a wrong scope or revision is never displayed', async () => {
  let calls = 0;
  const { detail, dispose } = session(async () => {
    if (++calls === 1) throw new Error('连接中断');
    return result();
  });
  detail.select(['a']);
  await detail.show();
  assert.equal(detail.error.value, '连接中断');
  assert.equal(detail.open.value, true);
  await detail.show();
  assert.equal(detail.error.value, '');
  assert.equal(detail.result.value.status, 'ready');
  dispose();
  for (const invalid of [
    result(['b']),
    result(['a'], { diagram: { architecture_revision: 8 } }),
    result(['a'], { image: undefined }),
  ]) {
    const next = session(async () => invalid);
    next.detail.select(['a']);
    await next.detail.show();
    assert.equal(next.detail.result.value, null);
    assert.ok(next.detail.error.value);
    next.dispose();
  }
});

test('empty, unmapped, oversized and unsupported responses retain backend reasons and usable guidance', async () => {
  for (const status of ['empty', 'unmapped', 'too_large', 'unsupported']) {
    const { detail, dispose } = session(async () =>
      result(['a'], { status, message: '具体原因', image: undefined }),
    );
    detail.select(['a']);
    await detail.show();
    assert.equal(detail.result.value.message, '具体原因');
    assert.ok(classDetailGuidance(status).length > 12);
    assert.equal(detail.error.value, '');
    dispose();
  }
});

test('file narrowing accepts only mapped files, requires a selection, and resets with module scope', async () => {
  const requests = [];
  const { detail, context, dispose } = session(async (args) => {
    requests.push(args);
    return result(args.component_ids);
  });
  detail.select(['a']);
  assert.equal(detail.selectFiles(['b.py']), false);
  assert.equal(detail.selectFiles([]), true);
  await detail.show();
  assert.equal(requests.length, 0);
  detail.toggleFile('a.py');
  await detail.show();
  assert.deepEqual(requests[0].file_paths, ['a.py']);
  detail.close();
  assert.deepEqual(detail.filePaths.value, ['a.py']);
  detail.select(['b']);
  assert.equal(detail.filePaths.value, null);
  context.value.componentFiles.b = Array.from({ length: 13 }, (_, i) => `b${i}.py`);
  assert.equal(detail.selectFiles(context.value.componentFiles.b), false);
  dispose();
});

test('unmount and switching to saved design discard in-flight extraction without losing scope', async () => {
  for (const leave of ['saved', 'unmount']) {
    const pending = deferred();
    const { detail, dispose } = session(() => pending.promise);
    detail.select(['a']);
    const request = detail.show();
    if (leave === 'saved') detail.showSaved();
    else dispose();
    pending.resolve(result());
    await request;
    assert.equal(detail.result.value, null);
    assert.deepEqual(detail.componentIds.value, ['a']);
    dispose();
  }
});

const design = (extra = {}) => ({
  id: 'local',
  kind: 'class',
  origin: 'design',
  component_ids: ['a'],
  architecture_revision: 41,
  revision: 1,
  ...extra,
});
test('saved designs match exact scope and architecture, excluding globals and superseded revisions', () => {
  const diagrams = [
    design({ id: 'global', component_ids: undefined }),
    design({ id: 'source', origin: 'source' }),
    design({ id: 'other-version', architecture_revision: 40 }),
    design({ id: 'larger', component_ids: ['a', 'b'] }),
    design(),
    design({ revision: 2 }),
    design({ id: 'mixed', origin: 'mixed' }),
    design({ id: 'moved' }),
    design({ id: 'moved', revision: 2, component_ids: ['b'] }),
  ];
  assert.deepEqual(
    matchingScopedDesigns(diagrams, ['a'], 41).map((d) => [d.id, d.revision]),
    [
      ['local', 2],
      ['mixed', 1],
    ],
  );
  assert.deepEqual(matchingScopedDesigns(diagrams, [], 41), []);
  assert.equal(
    matchingScopedDesigns([design({ component_ids: ['a', 'b'] })], ['b', 'a'], 41).length,
    1,
  );
});

const agentUrl = moduleUrl(
  `export const agent = { state: { projectId: '', view: 'architecture', diagramKind: '', diagramId: '', follow: {}, navigationTick: 0 }, freeView() {} }; export const useAgent = () => agent;`,
);
const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
const imports = {
  vue: vueUrl,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../api/client': apiUrl,
  '../../composables/useAgent': agentUrl,
  '../../composables/useScopedClassDetail': scopedUrl,
  '../../lib/architectureRoles': moduleUrl(transpile(source('lib/architectureRoles.ts'))),
  './DiagramView.vue': stub,
  './ArchitectureQuality.vue': stub,
  './ComponentPassport.vue': stub,
  './DiagramImage.vue': stub,
  '../ui/AppModal.vue': stub,
  '../attachments/AssetPreview.vue': stub,
};
async function component(path) {
  const { descriptor } = parse(source(`components/${path}.vue`));
  const compiled = transpile(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected component dependency: ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
  const url = moduleUrl(compiled);
  return { url, view: (await import(url)).default };
}
const UmlView = await component('design/UmlView');
imports['./UmlView.vue'] = UmlView.url;
const ArchitecturePanel = (await component('design/ArchitecturePanel')).view;
const DesignLibrary = (await component('design/DesignLibrary')).view;
const { agent } = await import(agentUrl);
const { calls: apiCalls } = await import(apiUrl);
const render = (view, props) => renderToString(createSSRApp(view, props));
const graph = {
  id: 'architecture',
  title: '架构',
  nodes: [{ id: 'a', label: '服务模块', description: '', source_refs: [] }],
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
  architectures: [
    {
      number: 41,
      summary: '目标版本',
      technologies: [],
      decisions: [],
      research_ids: [],
      diagram: graph,
    },
  ],
  source_diagram: graph,
  ...extra,
});
const completeDesign = (extra = {}) =>
  design({
    title: '局部设计',
    source: '@startuml\nclass A\n@enduml',
    source_refs: [],
    design_elements: ['A'],
    scope: '服务模块',
    ...extra,
  });

test('architecture starts at the module overview with bounded scope controls, no global UML tab', async () => {
  const html = await render(ArchitecturePanel, {
    project: project({
      uml_diagrams: [completeDesign({ component_ids: undefined, title: '旧全局类图' })],
    }),
  });
  for (const text of [
    '目标架构',
    'SRC 源码现状',
    '查看局部类结构',
    '选择 1–3 个模块',
    'DESIGN · 未关联源码',
    '技术选型、决策与来源',
  ])
    assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /UML 类图|旧全局类图|同步类图|生成类图/);
  assert.match(html, /class="architecture-stage"/);
  assert.equal(apiCalls.length, 0);
});

test('class navigation opens only the matching saved local DESIGN without source extraction', async () => {
  Object.assign(agent.state, { projectId: 'P1', diagramKind: 'class', diagramId: 'local' });
  const html = await render(ArchitecturePanel, {
    project: project({ uml_diagrams: [completeDesign()] }),
  });
  assert.match(html, /局部设计/);
  assert.match(html, /DESIGN 设计类结构/);
  assert.match(html, /SRC 源码类结构/);
  assert.match(html, /返回模块总览/);
  assert.ok(apiCalls.every(([action]) => action === 'uml.preview'));
  Object.assign(agent.state, { projectId: '', diagramKind: '', diagramId: '' });
  apiCalls.length = 0;
});

test('the reference library hides all class diagrams but preserves other UML', async () => {
  const html = await render(DesignLibrary, {
    project: project({
      uml_diagrams: [
        completeDesign({ title: '隐藏的局部类图' }),
        completeDesign({ id: 'sequence', kind: 'sequence', title: '保留的时序图' }),
      ],
    }),
  });
  assert.doesNotMatch(html, /隐藏的局部类图/);
  assert.match(html, /保留的时序图/);
  apiCalls.length = 0;
});

test('a returned local preview is rendered directly and zero revision means immediate extraction', async () => {
  const html = await render(UmlView.view, {
    projectId: 'P1',
    diagram: completeDesign({ revision: 0, origin: 'source' }),
    previewImage: 'data:image/svg+xml;base64,PHN2Zy8+',
  });
  assert.match(html, /即时提取/);
  assert.doesNotMatch(html, /v0|正在本地编译/);
  assert.equal(apiCalls.length, 0);
});

test('baseline refresh no longer depends on global class freshness or requests UML generation', () => {
  const baseline = source('composables/useBaseline.ts');
  assert.doesNotMatch(baseline, /class_model_state|syncClassModel|save_uml|类图/);
  assert.match(baseline, /source_analysis_baseline_id !== updated.baselines.at\(-1\)\?\.id/);
  assert.match(
    source('composables/useAgent.ts'),
    /event\.diagram_kind === 'class' \? 'architecture' : event\.view/,
  );
});

test('local detail describes all supported parsers without a stale Python-only claim', () => {
  const panel = source('components/design/ArchitecturePanel.vue');
  assert.doesNotMatch(panel, /Python\s+AST/);
  assert.match(panel, /Python \/ TypeScript \/ Vue \/ C\+\+/);
  assert.match(panel, /静态类结构/);
});
