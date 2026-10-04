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
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, createSSRApp, h, reactive, nextTick } = await import('vue');
const { renderToString } = await import('@vue/server-renderer');
const read = (path) => readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const summaryUrl = url(transpile(read('lib/turnSummary.ts')));
const {
  parseTurnSummary,
  turnContractViews,
  turnTargetView,
  turnHistoryBound,
  turnSummaryStatus,
  turnSummaryHeadline,
} = await import(summaryUrl);
const { descriptor } = parse(read('components/agent/AgentTurnSummary.vue'));
const component = transpile(
  compileScript(descriptor, { id: 'receipt', inlineTemplate: true }).content,
)
  .replaceAll('"vue"', JSON.stringify(import.meta.resolve('vue')))
  .replaceAll("'vue'", JSON.stringify(import.meta.resolve('vue')))
  .replaceAll("'../../lib/turnSummary'", JSON.stringify(summaryUrl));
const Receipt = (await import(url(component))).default;
const empty = () => ({
  milestones: { added: [], updated: [], removed: [] },
  dependencies: { added: [], updated: [], removed: [] },
  target: null,
  architecture: null,
  other: [],
});
const side = (active_id, required_id = active_id) => ({ active_id, required_id });
const item = (overrides = {}) => ({
  behavior_key: 'extract',
  before: side('B1'),
  after: side('B2'),
  fields: ['statement'],
  restored: false,
  ...overrides,
});
const summary = (overrides = {}) => ({
  version: 1,
  turn_id: 'T',
  status: 'completed',
  changed: true,
  before_revision: 4,
  after_revision: 7,
  contract_details: {
    project_id: 'P',
    project_created_at: '2026-10-01',
    before_target_version: 1,
    after_target_version: 2,
    behaviors: [item()],
  },
  changes: empty(),
  ...overrides,
});
const raw =
  '**All** links\n<script>alert("no")</script>\n[link](javascript:alert(1))\n```html\n<img src=x onerror=alert(1)>\n```';
const behavior = (id, version, statement, overrides = {}) => ({
  id,
  behavior_key: 'extract',
  version,
  statement,
  acceptance_scope: 'target',
  owner: 'LIB',
  supersedes: null,
  ...overrides,
});
const project = (overrides = {}) => ({
  id: 'P',
  created_at: '2026-10-01',
  events: [],
  targets: [
    { number: 1, statement: 'Original goal', required_behavior_ids: ['B1'] },
    { number: 2, statement: 'Original goal', required_behavior_ids: ['B2'] },
    { number: 99, statement: 'LATEST TARGET MUST NOT APPEAR', required_behavior_ids: ['LATEST'] },
  ],
  behaviors: [
    behavior('B1', 1, raw),
    behavior('B2', 2, 'Only simple links'),
    behavior('LATEST', 99, 'LATEST BEHAVIOR MUST NOT APPEAR'),
  ],
  ...overrides,
});
const render = (value = summary(), history = project()) =>
  renderToString(createSSRApp(Receipt, { summary: value, project: history, milestones: [] }));
const legacy = () => {
  const value = summary();
  delete value.contract_details;
  value.changes.target = {
    before_version: 1,
    after_version: 2,
    statement_changed: false,
    required_behavior_ids: { added: ['B2'], removed: ['B1'] },
    required_behavior_changes: [
      { behavior_key: 'extract', before_id: 'B1', after_id: 'B2', fields: ['statement'] },
    ],
  };
  return value;
};
const withEvent = (value, history = project()) => ({
  ...history,
  events: [
    {
      id: 'E',
      kind: 'agent_turn_finished',
      detail: JSON.stringify(value),
      created_at: '2026-10-02',
    },
  ],
});

test('additive contract parser accepts old receipts and rejects malformed new sides/binding', () => {
  assert.deepEqual(parseTurnSummary(JSON.stringify(summary())), summary());
  assert.deepEqual(parseTurnSummary(JSON.stringify(legacy())), legacy());
  for (const mutate of [
    (d) => delete d.project_created_at,
    (d) => (d.behaviors[0].before.active_id = 3),
    (d) => (d.behaviors[0].restored = 'yes'),
    (d) => (d.before_target_version = 'latest'),
    (d) => (d.behaviors[0].fields = {}),
  ]) {
    const value = summary();
    mutate(value.contract_details);
    assert.equal(parseTurnSummary(JSON.stringify(value)), null);
  }
});

test('exact raw Markdown and immutable owner/scope are escaped without executing or truncating text', async () => {
  const value = summary();
  value.contract_details.behaviors[0].fields = ['statement', 'owner', 'acceptance_scope'];
  const history = project();
  history.behaviors[1].owner = 'CLI';
  history.behaviors[1].acceptance_scope = 'milestone';
  const html = await render(value, history);
  assert.ok(html.includes('**All** links\n&lt;script&gt;'));
  assert.ok(html.includes('[link](javascript:alert(1))'));
  assert.ok(html.includes('```html\n&lt;img'));
  assert.doesNotMatch(html, /<script>|<img|<a\b|LATEST/);
  for (const text of [
    '所属里程碑：LIB',
    '所属里程碑：CLI',
    '目标验收（target）',
    '阶段验收（milestone）',
    '版本 v1 · B1',
    '版本 v2 · B2',
  ])
    assert.ok(html.includes(text), text);
  assert.equal((html.match(/Original goal/g) ?? []).length, 1);
  assert.match(html, /目标描述未变/);
});

test('scope flips and milestone-only updates retain both active versions separately from target membership', async () => {
  for (const [before, after] of [
    [side('B1'), side('B2', null)],
    [side('B1', null), side('B2')],
    [side('B1', null), side('B2', null)],
  ]) {
    const value = summary();
    value.contract_details.behaviors = [item({ before, after, fields: ['acceptance_scope'] })];
    const view = turnContractViews(value, project())[0];
    assert.equal(view.label, '修订');
    assert.deepEqual(
      view.sides.map((s) => s.id),
      ['B1', 'B2'],
    );
    const html = await render(value);
    assert.match(html, /Only simple links/);
    assert.doesNotMatch(html, /停用验收项|新增验收项/);
  }
});

test('partial saves show stale committed-target ID without claiming the new active ID is required', async () => {
  for (const status of ['failed', 'stopped']) {
    const value = summary({ status });
    value.contract_details.after_target_version = 1;
    value.contract_details.behaviors = [item({ after: side('B2', 'B1') })];
    const html = await render(value);
    assert.match(html, /该侧已启用 · 目标仍引用其他版本 B1/);
    assert.doesNotMatch(html, /目标引用 B2|本轮完成|目标已通过|已回滚/);
    assert.match(html, /已保存的部分变更保留，本轮未完成/);
  }
  const draft = summary({ status: 'failed' });
  draft.contract_details.after_target_version = 1;
  draft.changes.other = ['target_draft'];
  const html = await render(draft);
  assert.match(html, /已提交目标描述未变/);
  assert.match(html, /该轮已提交目标/);
  assert.match(html, /未提交目标草案/);
});

test('actual add/remove/restore and unchanged-text revision references use factual labels', () => {
  for (const [change, label] of [
    [item({ before: side(null, null) }), '新增验收项'],
    [item({ after: side(null, null) }), '停用验收项'],
    [item({ before: side(null, null), after: side('B1'), restored: true }), '恢复启用'],
    [item({ fields: [] }), '更新版本引用'],
  ]) {
    const value = summary();
    value.contract_details.behaviors = [change];
    assert.equal(turnContractViews(value, project())[0].label, label);
  }
});

test('new receipts reject other projects and reused IDs with different creation identity', async () => {
  for (const history of [project({ id: 'OTHER' }), project({ created_at: 'another creation' })]) {
    assert.equal(turnHistoryBound(summary(), history), false);
    const html = await render(summary(), history);
    assert.doesNotMatch(html, /Only simple links|\*\*All\*\*|Original goal|LATEST/);
    assert.match(html, /缺少匹配的项目历史/);
  }
});

test('legacy histories require the exact project event and do not infer active deletion from target exit', async () => {
  const value = legacy();
  assert.equal(turnHistoryBound(value, project()), false);
  assert.equal(turnHistoryBound(value, withEvent(value)), true);
  assert.match(await render(value, withEvent(value)), /Only simple links/);
  const wrong = withEvent(value);
  wrong.events[0].detail = JSON.stringify({ ...value, after_revision: 8 });
  assert.doesNotMatch(await render(value, wrong), /Only simple links/);
  value.changes.target.required_behavior_changes[0].after_id = null;
  value.changes.target.required_behavior_ids.added = [];
  const html = await render(value, withEvent(value));
  assert.match(html, /移出目标/);
  assert.match(html, /旧回执未记录此侧启用版本/);
  assert.doesNotMatch(html, /停用验收项|Only simple links/);
});

test('old or missing behavior IDs never borrow latest revisions or mismatched keys', async () => {
  for (const history of [
    project({ behaviors: project().behaviors.filter((b) => b.id !== 'B1') }),
    project({
      behaviors: project().behaviors.map((b) =>
        b.id === 'B1' ? { ...b, behavior_key: 'unrelated' } : b,
      ),
    }),
  ]) {
    const html = await render(summary(), history);
    assert.match(html, /历史验收记录缺失：B1/);
    assert.doesNotMatch(html, /\*\*All\*\*|LATEST/);
    assert.match(html, /Only simple links/);
  }
  const value = legacy();
  value.changes.target.required_behavior_changes = [];
  const html = await render(value, withEvent(value, project({ behaviors: [] })));
  assert.match(html, /历史验收记录缺失：B1/);
  assert.match(html, /历史验收记录缺失：B2/);
});

test('unchanged target exposes missing before/after history and never substitutes today’s target', async () => {
  for (const missing of [1, 2]) {
    const history = project({ targets: project().targets.filter((t) => t.number !== missing) });
    const html = await render(summary(), history);
    assert.match(html, new RegExp(`历史目标记录缺失：T${missing}`));
    assert.match(html, /目标描述未变/);
    assert.doesNotMatch(html, /LATEST/);
    assert.match(html, /Original goal/);
    const old = legacy();
    const oldHtml = await render(old, withEvent(old, history));
    assert.match(oldHtml, new RegExp(`历史目标记录缺失：T${missing}`));
    assert.match(oldHtml, /Original goal/);
  }
  const value = summary();
  value.contract_details.before_target_version = value.contract_details.after_target_version = 1;
  assert.equal(turnTargetView(value, project()).sides[1].target.statement, 'Original goal');
  assert.doesNotMatch(await render(value), /LATEST/);
});

test('changed target renders exact both texts while unavailable legacy reference stays missing', async () => {
  const value = legacy();
  value.changes.target.statement_changed = true;
  const history = project();
  history.targets[1].statement = '**Changed goal**';
  const html = await render(value, withEvent(value, history));
  assert.match(html, /Original goal/);
  assert.match(html, /\*\*Changed goal\*\*/);
  assert.match(html, /已提交目标描述已更新/);
  assert.doesNotMatch(await render(value, project()), /Original goal/);
});

test('completion is factual saved/ended status, with no-change and waiting states preserved', async () => {
  assert.equal(turnSummaryStatus(summary()), '变更已保存');
  const value = summary({ changed: false });
  value.contract_details.behaviors = [];
  assert.equal(turnSummaryStatus(value), '本轮已结束');
  assert.equal(turnSummaryHeadline(value), '无净变更');
  const html = await render(value);
  assert.doesNotMatch(html, /turn-contract-change|Original goal/);
  assert.match(html, /保存与结构检查不代表目标已验收或语义一致/);
  assert.equal(turnSummaryStatus(summary({ status: 'waiting' })), '等待你的回答');
});

test('long exact text stays complete and safe in responsive side-by-side or stacked columns', async () => {
  const long = '长文本 with whitespace\n' + 'unbroken'.repeat(2000) + '\nEND';
  const history = project();
  history.behaviors[1].statement = long;
  const html = await render(summary(), history);
  assert.ok(html.includes(long));
  const css = descriptor.styles.map((s) => s.content).join('\n');
  assert.match(
    css,
    /\.turn-summary-scroll \.turn-contract-text\s*\{[^}]*white-space: pre-wrap;[^}]*overflow-wrap: anywhere;/,
  );
  assert.doesNotMatch(
    css.match(/\.turn-summary-scroll \.turn-contract-text\s*\{[^}]*\}/)[0],
    /max-height|overflow: hidden|line-clamp|text-overflow/,
  );
  assert.match(css, /\.turn-contract-side\s*\{[^}]*min-width: 0/);
  assert.match(css, /@container \(max-width: 600px\)/);
  assert.match(css, /grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /container-type: inline-size/);
  assert.doesNotMatch(read('components/agent/AgentTurnSummary.vue'), /v-html|window\.open|fetch\(/);
});

test('details start collapsed, toggle independently, preserve scroll controls and reset across project/turn navigation', async () => {
  const props = reactive({ summary: summary(), project: project(), milestones: [] });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(Receipt, props) });
  app.mount(root);
  try {
    let outer = root.querySelector('.agent-turn-summary');
    let change = root.querySelector('.turn-contract-change');
    const reference = root.querySelector('.turn-target-reference');
    assert.equal(outer.open, false);
    assert.equal(change.open, false);
    assert.equal(reference.open, false);
    outer.querySelector(':scope > summary').click();
    change.querySelector('summary').click();
    await nextTick();
    assert.equal(outer.open, true);
    assert.equal(change.open, true);
    assert.equal(reference.open, false);
    assert.equal(root.querySelector('.turn-contract-text').textContent, 'Original goal');
    const texts = [...root.querySelectorAll('.turn-contract-text')].map((e) => e.textContent);
    assert.ok(texts.includes(raw));
    assert.equal(root.querySelectorAll('script, img, a').length, 0);
    change.querySelector('summary').click();
    assert.equal(change.open, false);
    assert.equal(outer.open, true);
    change.querySelector('summary').click();
    props.project = project({ id: 'OTHER' });
    await nextTick();
    outer = root.querySelector('.agent-turn-summary');
    change = root.querySelector('.turn-contract-change');
    assert.equal(outer.open, false);
    assert.equal(change.open, false);
    assert.doesNotMatch(root.textContent, /Only simple links|Original goal/);
    props.project = project();
    await nextTick();
    root.querySelector('.turn-contract-change > summary').click();
    assert.equal(root.querySelector('.turn-contract-change').open, true);
    props.project = project({ created_at: 'different incarnation' });
    await nextTick();
    assert.equal(root.querySelector('.turn-contract-change').open, false);
    props.project = project();
    props.summary = summary({ turn_id: 'NEXT' });
    await nextTick();
    assert.equal(root.querySelector('.turn-contract-change').open, false);
    assert.equal(root.querySelector('.turn-summary-scroll').getAttribute('tabindex'), '0');
  } finally {
    app.unmount();
    root.remove();
  }
});
