import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parse, compileScript } from '@vue/compiler-sfc';
import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const read = (path) => readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const acceptanceUrl = moduleUrl(transpile(read('lib/acceptance.ts')));
const { acceptanceScope, acceptanceSummary } = await import(acceptanceUrl);
const imports = {
  vue: pathToFileURL(require.resolve('vue')).href,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../lib/acceptance': acceptanceUrl,
  '../../lib/turnSummary': moduleUrl(transpile(read('lib/turnSummary.ts'))),
  '../../composables/useWorkspace': moduleUrl(
    'export const useWorkspace = () => ({ state: { busy: false }, selectNode() {} });',
  ),
  '../../composables/useBaseline': moduleUrl(
    'export const useBaseline = () => ({ refresh() {} });',
  ),
};
for (const name of [
  '../ui/StatusBadge.vue',
  '../attachments/AssetPreview.vue',
  '../design/DiagramView.vue',
  '../design/UmlView.vue',
  './TaskWorkflow.vue',
  '../agent/AgentTurnSummary.vue',
]) {
  imports[name] = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
}
async function component(path) {
  const { descriptor } = parse(read(`components/${path}.vue`));
  const compiled = transpile(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected component dependency: ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
  return (await import(moduleUrl(compiled))).default;
}
const GoalMarker = await component('graph/GoalMarker');
const MilestoneInspector = await component('graph/MilestoneInspector');
const WorkspaceHeader = await component('workspace/WorkspaceHeader');
const render = (view, props) => renderToString(createSSRApp(view, props));
const behavior = (id, scope) => ({
  id,
  behavior_key: id.toLowerCase(),
  version: 1,
  statement: `${id} acceptance statement`,
  owner: 'M1',
  ...(scope ? { acceptance_scope: scope } : {}),
});
const milestone = (overrides = {}) => ({
  id: 'M1',
  title: 'First delivery',
  intent: 'Deliver the contract',
  status: 'PLANNED',
  behavior_revision_ids: ['T1', 'S1'],
  scope: ['src/delivery.ts'],
  dependencies: [],
  dependency_reasons: {},
  architecture_components: [],
  attachment_ids: [],
  ...overrides,
});
const project = (overrides = {}) => ({
  id: 'P1',
  name: 'Project',
  description: 'Evolve a durable capability',
  repository: '',
  baselines: [],
  plans: [],
  targets: [{ number: 1, statement: 'Final outcome', required_behavior_ids: ['T1'] }],
  milestones: [milestone()],
  behaviors: [behavior('T1', 'target'), behavior('S1', 'milestone')],
  verified_behaviors: [],
  acceptance: { passed: 0, total: 1, achieved: false },
  source_milestones: [],
  diagrams: [],
  uml_diagrams: [],
  evidence: [],
  attachments: [],
  ...overrides,
});

// Intent metadata survives historical revisions; it does not replace the target contract.
test('legacy behavior scopes remain final-goal requirements', () => {
  assert.equal(acceptanceScope(behavior('LEGACY')), 'target');
  assert.equal(acceptanceScope(behavior('T1', 'target')), 'target');
  assert.equal(acceptanceScope(behavior('S1', 'milestone')), 'milestone');
});

test('target counts come only from the latest required IDs, never graph position or scope', () => {
  const summary = acceptanceSummary(
    project({
      targets: [
        { number: 1, required_behavior_ids: ['OLD'] },
        { number: 2, required_behavior_ids: ['T1', 'S1'] },
      ],
      milestones: [
        milestone({ behavior_revision_ids: ['OLD', 'T1'] }),
        milestone({ id: 'M2', behavior_revision_ids: ['S1'] }),
      ],
      behaviors: [behavior('OLD', 'target'), behavior('T1'), behavior('S1', 'milestone')],
      verified_behaviors: ['OLD', 'S1'],
      acceptance: { passed: 3, total: 3, achieved: true },
    }),
  );
  assert.deepEqual([...summary.targetIds], ['T1', 'S1']);
  assert.equal(summary.total, 2);
  assert.equal(summary.passed, 1);
  assert.equal(summary.achieved, false);
  assert.equal(summary.stepOnlyTotal, 1);
});

test('step-only totals exclude archived, source-only, unknown, and duplicate references', () => {
  const summary = acceptanceSummary(
    project({
      behaviors: [
        behavior('T1', 'target'),
        behavior('S1', 'milestone'),
        behavior('ARCHIVED', 'milestone'),
        behavior('SOURCE', 'milestone'),
      ],
      milestones: [milestone({ behavior_revision_ids: ['T1', 'S1', 'S1', 'UNKNOWN'] })],
      source_milestones: [milestone({ id: 'SRC1', behavior_revision_ids: ['SOURCE'] })],
      verified_behaviors: ['T1', 'ARCHIVED', 'SOURCE', 'UNKNOWN'],
    }),
  );
  assert.equal(summary.passed, 1);
  assert.equal(summary.total, 1);
  assert.equal(summary.stepOnlyTotal, 1);
  assert.equal(summary.achieved, true);
});

test('completing a stage-only check cannot advance or achieve the final goal', async () => {
  const html = await render(GoalMarker, {
    project: project({
      verified_behaviors: ['S1'],
      acceptance: { passed: 1, total: 1, achieved: true },
    }),
  });
  assert.match(html, /最终目标 <b>0\/1<\/b>/);
  assert.match(html, /步骤专属 <b>1 项<\/b>/);
  assert.match(html, /待达成/);
  assert.doesNotMatch(html, /class="[^"]*achieved|已达成/);
  assert.match(html, /仍须通过所在里程碑的验收/);
});

test('a target can be achieved without counting an excluded step-only check as a target', async () => {
  const html = await render(GoalMarker, { project: project({ verified_behaviors: ['T1'] }) });
  assert.match(html, /class="goal-marker achieved"/);
  assert.match(html, /最终目标 <b>1\/1<\/b>/);
  assert.match(html, /步骤专属 <b>1 项<\/b>/);
});

test('all-stage and empty targets show explicit guidance and never a green achieved state', async () => {
  for (const targets of [[], [{ number: 2, required_behavior_ids: [] }]]) {
    const data = project({
      targets,
      behaviors: [behavior('S1', 'milestone')],
      milestones: [milestone({ behavior_revision_ids: ['S1'] })],
      verified_behaviors: ['S1'],
      acceptance: { passed: 1, total: 1, achieved: true },
    });
    const html = await render(GoalMarker, { project: data });
    assert.match(html, /needs-target/);
    assert.match(html, /尚未定义最终目标标准/);
    assert.match(html, /最终目标 <b>0\/0<\/b>/);
    assert.match(html, /步骤专属 <b>1 项<\/b>/);
    assert.doesNotMatch(html, /class="[^"]*achieved|已达成/);
    const header = await render(WorkspaceHeader, { project: data });
    assert.match(header, /尚未定义最终目标标准/);
    assert.match(header, /width:0%/);
    assert.match(header, /<strong>0 \/ 0<\/strong>/);
  }
});

test('inspector distinguishes lasting requirements from mandatory stage checks', async () => {
  const html = await render(MilestoneInspector, { project: project(), milestone: milestone() });
  assert.match(html, /class="acceptance-scope target"[^>]*>最终目标要求/);
  assert.match(html, /class="acceptance-scope milestone"[^>]*>阶段检查/);
  assert.match(html, /计入最终目标 1 \/ 本步共 2 项/);
  assert.match(html, /两类都必须通过本步验收/);
  assert.match(html, /T1 acceptance statement/);
  assert.match(html, /S1 acceptance statement/);
  assert.match(html, /t1 · v1/);
  assert.match(html, /s1 · v1/);
  for (const control of ['关闭节点详情', '交付约定', '执行与证据', '变更记录', '展开完整说明']) {
    assert.ok(html.includes(control), `Missing existing control: ${control}`);
  }
});

test('legacy intent remains visible but unselected revisions do not count toward current target', async () => {
  const html = await render(MilestoneInspector, {
    project: project({
      targets: [{ number: 2, required_behavior_ids: ['T2'] }],
      behaviors: [behavior('T1'), behavior('T2', 'target'), behavior('S1', 'milestone')],
    }),
    milestone: milestone(),
  });
  assert.match(html, /class="acceptance-scope target"[^>]*>最终目标要求/);
  assert.match(html, /未纳入当前目标版本/);
  assert.match(html, /计入最终目标 0 \/ 本步共 2 项/);
  assert.doesNotMatch(html, /T2 acceptance statement/);
});

test('header reports target-specific verification rather than all verified behaviors', async () => {
  const html = await render(WorkspaceHeader, {
    project: project({
      verified_behaviors: ['S1'],
      acceptance: { passed: 9, total: 9, achieved: true },
    }),
  });
  assert.match(html, /<strong>0 \/ 1<\/strong>/);
  assert.match(html, /最终目标已验证/);
  assert.match(html, /width:0%/);
  assert.doesNotMatch(html, /9 \/ 9/);
});

test('goal strip reserves its own space instead of overlapping node headers', () => {
  const css = read('styles/design.css');
  assert.match(css, /\.milestone-stage > \.graph-canvas\s*\{[^}]*flex-direction: column;/);
  assert.match(css, /\.milestone-stage > \.graph-canvas > \.vue-flow\s*\{[^}]*min-height: 0;/);
  assert.match(
    css,
    /\.milestone-stage > \.graph-canvas > \.edge-legend\s*\{[^}]*position: static;/,
  );
  const marker = css.match(/\.goal-marker\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(marker, /position: relative/);
  assert.match(marker, /flex-shrink: 0/);
  assert.doesNotMatch(marker, /position: absolute|max-width:/);
  assert.match(css, /\.goal-marker \.goal-copy\s*\{[^}]*min-width: 0/);
});
