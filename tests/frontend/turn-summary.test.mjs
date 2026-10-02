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
import { parse, compileScript } from '@vue/compiler-sfc';
import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (code) =>
  ts
    .transpileModule(code, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace(
      /(['"])(?:\.\.\/lib\/|\.\/|\.\.\/\.\.\/lib\/)composerDocument\1/g,
      JSON.stringify(composerDocumentUrl),
    );
const read = (path) => readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const summaryUrl = moduleUrl(transpile(read('lib/turnSummary.ts')));
const {
  parseTurnSummary,
  milestoneTurnHistory,
  latestTurnSummary,
  turnSummaryHasChanges,
  turnSummaryHeadline,
  turnSummaryMessage,
  turnSummaryStatus,
} = await import(summaryUrl);
const { eventDetail } = await import(
  moduleUrl(
    transpile(read('lib/presentation.ts')).replace("'./turnSummary'", JSON.stringify(summaryUrl)),
  )
);
const imports = {
  [composerDocumentUrl]: composerDocumentUrl,
  '../../lib/composerDocument': composerDocumentUrl,
  './ComposerEditor.vue': composerEditorStubUrl,
  './MessageContent.vue': messageContentStubUrl,
  vue: pathToFileURL(require.resolve('vue')).href,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../lib/turnSummary': summaryUrl,
  '../../lib/agentRetry': moduleUrl(transpile(read('lib/agentRetry.ts'))),
  '../../composables/useAgentDrafts': moduleUrl(
    transpile(read('composables/useAgentDrafts.ts')).replace(
      "from 'vue'",
      `from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`,
    ),
  ),
  '../../api/client': moduleUrl('export const command = async () => ({ items: [] });'),
  '../../composables/useWorkspace': moduleUrl(`export const useWorkspace = () => ({
    state: { busy: false, settings: { provider: { config: { model: 'test' } } } },
    setPage() {}, setError() {},
  });`),
  '../../composables/useAgent': moduleUrl(`export const agent = {
    state: { running: false, projectId: '', label: '', follow: {} }, send: async () => true, stop() {},
  }; export const useAgent = () => agent;`),
};
for (const name of [
  '../attachments/AttachmentReceipt.vue',
  './AgentQuestion.vue',
  '../graph/FollowAgentButton.vue',
  '../attachments/AttachmentPicker.vue',
  './ReferenceMentionPicker.vue',
]) {
  imports[name] = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
}
function compileComponent(name) {
  const { descriptor } = parse(read(`components/agent/${name}.vue`));
  return moduleUrl(
    transpile(compileScript(descriptor, { id: name, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, quote, name) => {
        assert.ok(imports[name], `Unexpected component dependency: ${name}`);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['./AgentTurnSummary.vue'] = compileComponent('AgentTurnSummary');
const AgentTurnSummary = (await import(imports['./AgentTurnSummary.vue'])).default;
imports['./AgentReviewTray.vue'] = compileComponent('AgentReviewTray');
const AgentDock = (await import(compileComponent('AgentDock'))).default;
const { agent } = await import(imports['../../composables/useAgent']);
const render = (view, props) => renderToString(createSSRApp(view, props));
const summary = (overrides = {}) => ({
  version: 1,
  turn_id: 'turn-1',
  status: 'completed',
  changed: true,
  before_revision: 4,
  after_revision: 7,
  changes: {
    milestones: {
      added: [{ id: 'M2', title: '迁移到数据库', fields: [] }],
      updated: [{ id: 'M1', title: '订单录入', fields: ['scope', 'behavior_revision_ids'] }],
      removed: [{ id: 'M0', title: '临时 CSV 存储', fields: [] }],
    },
    dependencies: {
      added: [{ source: 'M1', target: 'M2', reason: '使用订单契约', type: 'implementation' }],
      updated: [
        {
          source: 'M3',
          target: 'M2',
          fields: ['reason', 'type'],
          before: { reason: '共用输入', type: 'implementation' },
          after: { reason: '先完成数据迁移', type: 'migration' },
        },
      ],
      removed: [{ source: 'M0', target: 'M1', reason: '旧存储接口', type: 'implementation' }],
    },
    target: {
      before_version: 1,
      after_version: 2,
      statement_changed: true,
      required_behavior_ids: { added: ['B2'], removed: ['B1'] },
      required_behavior_changes: [
        { behavior_key: 'order.persist', before_id: 'B1', after_id: 'B2', fields: ['statement'] },
      ],
    },
    architecture: { before_revision: 1, after_revision: 2 },
    other: ['research'],
  },
  ...overrides,
});
const noChanges = () => ({
  milestones: { added: [], updated: [], removed: [] },
  dependencies: { added: [], updated: [], removed: [] },
  target: null,
  architecture: null,
  other: [],
});
const event = (value = summary(), overrides = {}) => ({
  id: 'event-1',
  kind: 'agent_turn_finished',
  detail: JSON.stringify(value),
  created_at: '2026-10-01T12:01:00Z',
  ...overrides,
});
const project = (overrides = {}) => ({
  id: 'P1',
  messages: [],
  events: [event()],
  milestones: [],
  attachments: [],
  repository: '',
  ...overrides,
});

test('versioned terminal summary round trips all four statuses', () => {
  for (const status of ['completed', 'waiting', 'stopped', 'failed']) {
    const value = summary({ status });
    assert.deepEqual(parseTurnSummary(JSON.stringify(value)), value);
    assert.ok(turnSummaryStatus(value));
  }
});

test('legacy, malformed and incomplete data never fabricate a turn summary', () => {
  const invalid = [
    'graph_changed=True; tokens=24',
    'not JSON',
    '{broken',
    '',
    'null',
    '[]',
    '{}',
    JSON.stringify(summary({ version: 2 })),
    JSON.stringify(summary({ status: 'unknown' })),
    JSON.stringify(summary({ status: ['completed'] })),
    JSON.stringify(summary({ turn_id: '' })),
    JSON.stringify(summary({ changed: 'true' })),
    JSON.stringify(summary({ before_revision: -1 })),
    JSON.stringify(summary({ changes: null })),
  ];
  for (const field of ['milestones', 'dependencies', 'target', 'architecture', 'other']) {
    const value = summary();
    value.changes[field] = { invalid: true };
    invalid.push(JSON.stringify(value));
  }
  const brokenNested = summary();
  brokenNested.changes.dependencies.updated[0].before = null;
  invalid.push(JSON.stringify(brokenNested));
  const brokenTarget = summary();
  brokenTarget.changes.target.required_behavior_changes[0].after_id = 4;
  invalid.push(JSON.stringify(brokenTarget));
  for (const value of invalid) assert.equal(parseTurnSummary(value), null, value);
});

test('reload uses latest persisted terminal event and never falls back to old success', () => {
  const latest = summary({ turn_id: 'new-turn', status: 'failed' });
  const events = [event(latest), event(summary())];
  assert.equal(
    latestTurnSummary(JSON.parse(JSON.stringify(project({ events })))).turn_id,
    'new-turn',
  );
  assert.equal(latestTurnSummary(events).status, 'failed');
  events[0].detail = 'legacy terminal';
  assert.equal(latestTurnSummary(events), null);
  events[0].detail = '{broken';
  assert.equal(latestTurnSummary(events), null);
  assert.equal(latestTurnSummary(project({ events: [] })), null);
});

test('a newer user request suppresses a stale receipt after an unrecorded failure', () => {
  const messages = [
    { id: 'msg-2', role: 'user', content: '继续', created_at: '2026-10-01T12:02:00Z' },
  ];
  assert.equal(latestTurnSummary(project({ messages })), null);
  messages[0].role = 'assistant';
  assert.ok(latestTurnSummary(project({ messages })));
  messages[0].role = 'user';
  messages[0].created_at = '2026-10-01T12:00:00Z';
  assert.ok(latestTurnSummary(project({ messages })));
});

test('no-op and revision-only snapshots do not claim that planning changed', async () => {
  for (const changed of [true, false]) {
    const value = summary({
      changed,
      before_revision: 4,
      after_revision: 99,
      changes: noChanges(),
    });
    assert.equal(turnSummaryHasChanges(value), false);
    assert.equal(turnSummaryHeadline(value), '无净变更');
    assert.doesNotMatch(turnSummaryMessage(value), /已保存变更|架构更新|更新.*里程碑/);
  }
  const value = summary({ changed: false });
  const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
  assert.match(html, /无净变更/);
  assert.doesNotMatch(html, /迁移到数据库|订单录入|临时 CSV 存储|架构更新/);
});

test('named milestone changes and dependency before/after details render compactly', async () => {
  const html = await render(AgentTurnSummary, {
    summary: summary(),
    milestones: [{ id: 'M3', title: '准备数据库' }],
  });
  for (const label of [
    '本轮变更',
    '本轮完成',
    '迁移到数据库',
    '订单录入',
    '临时 CSV 存储',
    '范围、验收标准',
    '前置里程碑',
    '准备数据库',
    '共用输入',
    '先完成数据迁移',
    'A1 → A2',
    '研究记录',
  ]) {
    assert.ok(html.includes(label), `Missing ${label}`);
  }
  assert.doesNotMatch(html.match(/<details\b[^>]*>/)[0], /\bopen\b/);
  assert.match(html, /aria-label="本轮已保存的规划变更" tabindex="0"/);
});

test('a stable-key requirement revision is one update, not a removed and added requirement', async () => {
  const value = summary();
  assert.match(turnSummaryHeadline(value), /最终验收 1 项/);
  const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
  assert.match(html, /修订<\/span>「order.persist」/);
  assert.doesNotMatch(
    html,
    /纳入<\/span>「order.persist」|移出<\/span>「order.persist」|「B1」|「B2」/,
  );
});

test('target membership changes distinguish moving into and out of final acceptance', async () => {
  const value = summary();
  value.changes.target.required_behavior_ids = { added: ['B3'], removed: ['B4'] };
  value.changes.target.required_behavior_changes = [
    {
      behavior_key: 'database.persist',
      before_id: null,
      after_id: 'B3',
      fields: ['acceptance_scope'],
    },
    { behavior_key: 'csv.persist', before_id: 'B4', after_id: null, fields: ['acceptance_scope'] },
  ];
  const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
  assert.match(html, /纳入<\/span>「database.persist」/);
  assert.match(html, /移出<\/span>「csv.persist」/);
  assert.match(html, /验收归属/);
});

test('stopped and failed turns preserve saved partial changes without implying completion', async () => {
  for (const status of ['stopped', 'failed']) {
    const value = summary({ status });
    const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
    assert.match(html, /已保存的部分变更保留，本轮未完成/);
    assert.doesNotMatch(html, /本轮完成|已回滚|全部完成/);
    assert.match(eventDetail(event(value)), /已保存的部分变更保留/);
    const empty = summary({ status, changed: false, changes: noChanges() });
    assert.match(turnSummaryMessage(empty), /无净变更/);
    assert.doesNotMatch(turnSummaryMessage(empty), /部分变更/);
  }
});

test('waiting remains pending and legacy history detail is still readable', async () => {
  const value = summary({ status: 'waiting' });
  const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
  assert.match(html, /等待你的回答/);
  assert.match(html, /当前变更已保存/);
  assert.doesNotMatch(html, /本轮完成/);
  for (const detail of ['graph_changed=True; tokens=24', '旧规划已记录', '{broken']) {
    assert.equal(eventDetail(event(undefined, { detail })), detail);
  }
});

test('saved-change trigger sits above composer and remains unavailable during a new turn', async () => {
  let html = await render(AgentDock, { project: project() });
  assert.ok(html.indexOf('最近变更') < html.indexOf('class="agent-input"'));
  assert.match(html, /最近变更/);
  assert.doesNotMatch(html, /class="agent-turn-summary"/);
  html = await render(AgentDock, { project: project(), compact: true });
  assert.match(html, /style="display:none;"/);
  assert.match(html, /id="agent-message"/);
  Object.assign(agent.state, { running: true, projectId: 'P1' });
  try {
    html = await render(AgentDock, { project: project() });
    assert.doesNotMatch(html, /最近变更/);
  } finally {
    Object.assign(agent.state, { running: false, projectId: '' });
  }
  html = await render(AgentDock, {
    project: project({ events: [event(undefined, { detail: 'old format' })] }),
  });
  assert.doesNotMatch(html, /最近变更/);
});

test('receipt content is escaped, bounded, focus-neutral and reduced-motion safe', async () => {
  const value = summary();
  value.changes.milestones.added[0].title = '<script>alert(1)</script>';
  const html = await render(AgentTurnSummary, { summary: value, milestones: [] });
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  const source = read('components/agent/AgentTurnSummary.vue');
  assert.match(source, /max-height: min\(160px, 24dvh\)/);
  assert.match(source, /overflow-y: auto/);
  assert.match(source, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(source, /autofocus|\.focus\(|scrollIntoView|position: (?:fixed|absolute)/);
  assert.match(
    read('components/agent/AgentDock.vue'),
    /本轮未完成，内容已放回输入框；已保存的修改会保留/,
  );
});

test('saved reply and change controls share a fixed strip without enabling a reading layout', async () => {
  const source = read('components/agent/AgentDock.vue');
  const workspace = read('components/workspace/ProjectWorkspace.vue');
  const html = await render(AgentDock, {
    project: project({
      messages: [
        {
          id: 'msg-1',
          role: 'assistant',
          content: '规划已更新',
          created_at: '2026-10-01T12:00:00Z',
        },
      ],
    }),
  });
  assert.match(html, /本轮回复/);
  assert.match(html, /最近变更/);
  assert.ok(html.indexOf('本轮回复') < html.indexOf('class="agent-input"'));
  assert.ok(html.indexOf('最近变更') < html.indexOf('class="agent-input"'));
  assert.doesNotMatch(source, /is-reading|conversationOpen/);
  assert.match(source, /:disabled="dockCollapsed"/);
  assert.match(source, /v-if="attachmentTransfer \|\| failedAttempt"/);
  assert.match(
    source,
    /\.agent-dock\s*\{[^}]*display: flex;[^}]*flex: 0 1 auto;[^}]*min-height: 0;/,
  );
  assert.match(
    source,
    /\.agent-dock-heading,[^}]*\.agent-input,[^}]*\.agent-dock-note\s*\{\s*flex-shrink: 0;/,
  );
  assert.match(workspace, /\.workspace-body\s*\{\s*overflow: visible;/);
  assert.match(
    workspace,
    /\.planning-content :deep\(\.milestone-stage > \.graph-canvas\)\s*\{\s*min-height: 0;/,
  );
});

test('receipt exposes current changed-node and dependency links without invented removed-node destinations', async () => {
  const html = await render(AgentTurnSummary, {
    summary: summary(),
    milestones: [
      { id: 'M1', title: '订单录入' },
      { id: 'M2', title: '迁移到数据库' },
      { id: 'M3', title: '准备数据库' },
    ],
  });
  for (const title of ['订单录入', '迁移到数据库', '准备数据库'])
    assert.ok(html.includes(`aria-label="定位里程碑：${title}"`));
  assert.doesNotMatch(html, /aria-label="定位里程碑：临时 CSV 存储"/);
  const missing = await render(AgentTurnSummary, { summary: summary(), milestones: [] });
  assert.doesNotMatch(missing, /class="turn-node-link"/);
  assert.match(missing, /临时 CSV 存储/);
});

test('node history filters persisted explicit node changes and never attributes global target or architecture changes', () => {
  const history = milestoneTurnHistory(
    {
      events: [
        event(summary({ status: 'failed' })),
        event(undefined, { detail: '{broken' }),
        event(summary({ changed: false })),
      ],
    },
    'M2',
  );
  assert.equal(history.length, 1);
  assert.equal(history[0].summary.status, 'failed');
  assert.deepEqual(
    history[0].summary.changes.milestones.added.map((item) => item.id),
    ['M2'],
  );
  assert.deepEqual(history[0].summary.changes.milestones.updated, []);
  assert.deepEqual(history[0].summary.changes.dependencies.removed, []);
  assert.equal(history[0].summary.changes.dependencies.updated[0].after.reason, '先完成数据迁移');
  assert.equal(history[0].summary.changes.target, null);
  assert.equal(history[0].summary.changes.architecture, null);
  assert.deepEqual(milestoneTurnHistory(project(), 'unrelated'), []);
  assert.equal(
    milestoneTurnHistory(project(), 'M3').length,
    1,
    'dependency endpoint alone is a factual node connection',
  );
});

test('optional saved-history warning survives parsing and is visible in the receipt', async () => {
  const saved = summary({
    status: 'stopped',
    history_warning: '部分 Agent 回复未能保存到对话记录',
  });
  assert.equal(parseTurnSummary(JSON.stringify(saved)).history_warning, saved.history_warning);
  assert.ok(parseTurnSummary(JSON.stringify(summary())));
  assert.equal(parseTurnSummary(JSON.stringify(summary({ history_warning: {} }))), null);
  const html = await render(AgentTurnSummary, { summary: saved, milestones: [] });
  assert.match(html, /已停止/);
  assert.match(html, /对话未完整保存/);
  assert.match(html, /部分 Agent 回复未能保存到对话记录/);
});
