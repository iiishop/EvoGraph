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
const turnSummaryUrl = moduleUrl(
  transpile(
    readFileSync(new URL('../../frontend/src/lib/turnSummary.ts', import.meta.url), 'utf8'),
  ),
);
const imports = {
  [composerDocumentUrl]: composerDocumentUrl,
  '../../lib/composerDocument': composerDocumentUrl,
  './ComposerEditor.vue': composerEditorStubUrl,
  './MessageContent.vue': messageContentStubUrl,
  '../../lib/turnSummary': turnSummaryUrl,
  '../../lib/agentRetry': moduleUrl(
    transpile(
      readFileSync(new URL('../../frontend/src/lib/agentRetry.ts', import.meta.url), 'utf8'),
    ),
  ),
  '../../composables/useAgentDrafts': moduleUrl(
    transpile(
      readFileSync(
        new URL('../../frontend/src/composables/useAgentDrafts.ts', import.meta.url),
        'utf8',
      ),
    ).replace("from 'vue'", `from ${JSON.stringify(pathToFileURL(require.resolve('vue')).href)}`),
  ),
  vue: pathToFileURL(require.resolve('vue')).href,
  'lucide-vue-next': pathToFileURL(require.resolve('lucide-vue-next')).href,
  '../../composables/useWorkspace': moduleUrl(`export const workspace = {
    state: { busy: false, error: 'unrelated previous failure' },
    perform: async () => undefined, selectProject: async () => {}, setPage() {}, setError() {}
  }; export const useWorkspace = () => workspace;`),
  '../../composables/useAgent': moduleUrl(`export const agent = {
    state: { running: false, projectId: '', label: '', follow: {} }, send: async () => true, stop() {}
  }; export const useAgent = () => agent;`),
  '../../api/client': moduleUrl('export const command = async () => ({ items: [] });'),
  '../../composables/useBaseline': moduleUrl(
    'export const useBaseline = () => ({ refresh() {} });',
  ),
  '../ui/AppModal.vue': moduleUrl(
    'export default { props: ["title"], emits: ["close"], setup(_, { slots }) { return () => slots.default?.(); } };',
  ),
  '../../lib/acceptance': moduleUrl(
    transpile(
      readFileSync(new URL('../../frontend/src/lib/acceptance.ts', import.meta.url), 'utf8'),
    ),
  ),
  '../../lib/presentation': moduleUrl(
    transpile(
      readFileSync(new URL('../../frontend/src/lib/presentation.ts', import.meta.url), 'utf8'),
    ).replace("'./turnSummary'", JSON.stringify(turnSummaryUrl)),
  ),
};
for (const name of [
  './AgentQuestion.vue',
  '../attachments/AttachmentReceipt.vue',
  './AgentTurnSummary.vue',
  '../graph/FollowAgentButton.vue',
  '../attachments/AttachmentPicker.vue',
  './ReferenceMentionPicker.vue',
  '../ui/StatusBadge.vue',
  '../attachments/AssetPreview.vue',
  '../design/DiagramView.vue',
  '../design/UmlView.vue',
  './TaskWorkflow.vue',
]) {
  imports[name] = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
}
async function component(path) {
  const filename = new URL(`../../frontend/src/components/${path}.vue`, import.meta.url);
  const { descriptor } = parse(readFileSync(filename, 'utf8'));
  const compiled = transpile(
    compileScript(descriptor, { id: path, inlineTemplate: true }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], `Unexpected component dependency: ${name}`);
    return `from ${JSON.stringify(imports[name])}`;
  });
  return (await import(moduleUrl(compiled))).default;
}
const MilestoneInspector = await component('graph/MilestoneInspector');
const ProjectDialog = await component('projects/ProjectDialog');
const WorkspaceHeader = await component('workspace/WorkspaceHeader');
const EvidencePanel = await component('workspace/EvidencePanel');
const AgentDock = await component('agent/AgentDock');
const AgentQuestion = await component('agent/AgentQuestion');
const { workspace } = await import(imports['../../composables/useWorkspace']);
const { agent } = await import(imports['../../composables/useAgent']);
const render = (view, props) => renderToString(createSSRApp(view, props));
const project = (overrides) => ({
  id: 'P1',
  name: '示例项目',
  repository: '',
  baselines: [],
  milestones: [],
  behaviors: [],
  targets: [],
  plans: [],
  acceptance: { passed: 0, total: 0 },
  evidence: [],
  evidence_validity: {},
  messages: [],
  events: [],
  source_milestones: [],
  diagrams: [],
  attachments: [],
  ...overrides,
});
const evidence = (overrides) => ({
  id: 'E1',
  milestone_id: 'M1',
  baseline_id: 'B1',
  behavior_revision_ids: ['BE1'],
  result: 'PASS',
  command: [],
  output: '',
  duration: 0,
  created_at: '2026-09-30T12:00:00Z',
  ...overrides,
});

test('new project stays optional and does not inherit an unrelated workspace error', async () => {
  const html = await render(ProjectDialog);
  assert.match(html, /暂不连接/);
  assert.match(html, /创建项目/);
  assert.doesNotMatch(html, /unrelated previous failure/);
  assert.doesNotMatch(html, /readonly/);
});

test('repository hints follow Windows, macOS, and Linux', async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  try {
    for (const [userAgent, expected] of [
      ['Windows NT 10.0', 'C:\\Projects\\my-project'],
      ['Macintosh; Intel Mac OS X', '/Users/你的用户名/Projects/my-project'],
      ['X11; Linux x86_64', '/home/你的用户名/projects/my-project'],
    ]) {
      Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { userAgent } });
      assert.ok((await render(ProjectDialog)).includes(expected));
    }
  } finally {
    if (original) Object.defineProperty(globalThis, 'navigator', original);
    else delete globalThis.navigator;
  }
});

test('existing baseline locks only repository edits with a reason', async () => {
  const html = await render(ProjectDialog, {
    project: project({ repository: '/repo', baselines: [{ number: 2 }] }),
  });
  assert.match(html, /readonly/);
  assert.match(html, /路径已锁定/);
  assert.match(html, /名称与描述仍可修改/);
  assert.match(html, /保存修改/);
});

test('active tasks lock the repository even before a baseline exists', async () => {
  const html = await render(ProjectDialog, {
    project: project({ repository: '/repo', milestones: [{ lease_active: true }] }),
  });
  assert.match(html, /readonly/);
  assert.match(html, /请先释放在途任务/);
});

test('header differentiates disconnected, attached, and incomplete baselines', async () => {
  const disconnected = await render(WorkspaceHeader, { project: project() });
  assert.match(disconnected, /未连接仓库/);
  assert.match(disconnected, /连接仓库/);
  assert.doesNotMatch(disconnected, /disabled/);
  const attached = await render(WorkspaceHeader, { project: project({ repository: '/repo' }) });
  assert.match(attached, /已关联 · 等待读取基线/);
  assert.match(attached, /读取基线/);
  assert.match(attached, /尚未读取/);
  assert.doesNotMatch(attached, /尚未连接/);
  const incomplete = await render(WorkspaceHeader, {
    project: project({ repository: '/repo', baselines: [{ number: 1, complete: false }] }),
  });
  assert.match(incomplete, /B1 · 扫描不完整/);
  assert.match(incomplete, /刷新基线/);
});

test('first baseline discloses automatic model analysis when a provider is configured', async () => {
  workspace.state.settings = { provider: { config: { model: 'Test model' } } };
  const html = await render(WorkspaceHeader, { project: project({ repository: '/repo' }) });
  assert.match(html, /源码上下文发送给已配置的模型/);
  assert.match(html, /已配置模型分析/);
});

test('empty evidence explains the actual external-agent handoff', async () => {
  const html = await render(EvidencePanel, { project: project() });
  assert.match(html, /外部 Agent/);
  assert.match(html, /JSON 报告/);
  assert.match(html, /不在本地执行验收命令/);
});

test('external reports expose provenance and checks without a fabricated run duration', async () => {
  const report = {
    provider: 'Test Agent',
    summary: '登录检查完成',
    checks: [
      { behavior_id: 'BE1', result: 'PASS', method: '打开登录页', evidence: '实际登录成功' },
    ],
  };
  const html = await render(EvidencePanel, {
    project: project({
      milestones: [{ id: 'M1', title: '用户登录' }],
      behaviors: [{ id: 'BE1', statement: '可以使用真实账号登录' }],
      evidence: [evidence({ request_id: 'R1', output: JSON.stringify(report) })],
      evidence_validity: { E1: 'CURRENT' },
    }),
  });
  for (const label of [
    '外部验收报告',
    'Test Agent',
    '登录检查完成',
    '可以使用真实账号登录',
    '打开登录页',
    '实际登录成功',
    '查看原始 JSON 报告',
    '匹配当前版本',
    '来源为报告中的自述',
  ]) {
    assert.ok(html.includes(label), `Missing report content: ${label}`);
  }
  assert.doesNotMatch(html, /0s|<code>\[\]<\/code>/);
});

test('legacy and malformed evidence remain inspectable', async () => {
  for (const output of [
    'plain test output',
    '{malformed JSON',
    '{"provider":"A","summary":"B","checks":[null]}',
  ]) {
    const html = await render(EvidencePanel, {
      project: project({ evidence: [evidence({ output })] }),
    });
    assert.match(html, /历史验收记录/);
    assert.match(html, /历史 · 需重新验收/);
    assert.match(html, /<pre>/);
  }
  const local = await render(EvidencePanel, {
    project: project({
      evidence: [
        evidence({
          command: ['pytest'],
          output: 'all tests passed',
          duration: 2.5,
        }),
      ],
    }),
  });
  assert.match(local, /历史本地命令记录/);
  assert.match(local, /pytest/);
  assert.match(local, /2.5s/);
  assert.match(local, /all tests passed/);
});

test('agent conversation previews the latest assistant response and retains the transcript', async () => {
  const html = await render(AgentDock, {
    project: project({
      messages: [
        { id: '1', role: 'assistant', content: '旧回复' },
        { id: '2', role: 'user', content: '继续调整' },
        { id: '3', role: 'assistant', content: '最新回复：规划已更新' },
        { id: '4', role: 'user', content: '还有一个问题' },
      ],
    }),
  });
  const summary = html.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || '';
  assert.match(summary, /最新回复：规划已更新/);
  assert.doesNotMatch(summary, /旧回复|还有一个问题/);
  assert.match(html, /aria-label="项目对话记录"/);
  for (const content of ['旧回复', '继续调整', '最新回复：规划已更新', '还有一个问题']) {
    assert.ok(html.includes(content));
  }
});

test('agent completion remains visible only in the project that produced it', async () => {
  Object.assign(agent.state, { running: false, projectId: 'P1', label: '图已更新' });
  const html = await render(AgentDock, { project: project() });
  const status = html.match(/class="agent-live-status"[^>]*>([\s\S]*?)<\/span>/)?.[1] || '';
  assert.match(status, /图已更新/);
  const other = await render(AgentDock, { project: project({ id: 'P2' }) });
  const otherStatus = other.match(/class="agent-live-status"[^>]*>([\s\S]*?)<\/span>/)?.[1] || '';
  assert.doesNotMatch(otherStatus, /图已更新/);
  Object.assign(agent.state, { running: false, projectId: '', label: '' });
});

test('agent send button submits the composer when clicked', async () => {
  const html = await render(AgentDock, { project: project() });
  const sendButton = html.match(/<button\b[^>]*class="send-button"[^>]*>/)?.[0] || '';
  assert.match(sendButton, /type="submit"/);
});

test('long milestone instructions stay collapsed inside the scrollable inspector', async () => {
  const intent = 'Long model-written task description. '.repeat(80);
  const html = await render(MilestoneInspector, {
    project: project(),
    milestone: {
      id: 'M1',
      title: 'A long task title',
      intent,
      status: 'ready',
      behavior_revision_ids: [],
      scope: ['src/feature.ts'],
      dependencies: [],
      architecture_components: [],
      attachment_ids: [],
    },
  });
  const details = html.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
  assert.ok(details);
  assert.doesNotMatch(details[1], /\bopen\b/);
  assert.ok(details[2].includes(intent));
  assert.match(html, /class="inspector-scroll"[\s\S]*?<details/);
  assert.doesNotMatch(
    html.match(/class="inspector-title"[\s\S]*?<nav/)?.[0] || '',
    /Long model-written/,
  );
});

test('compact conversation collapses history while keeping its natural-language composer visible', async () => {
  const html = await render(AgentDock, {
    project: project({ messages: [{ id: 'm1', role: 'assistant', content: '已保存的结果' }] }),
    compact: true,
  });
  assert.match(html, /is-collapsed/);
  assert.match(html, /展开项目对话/);
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /<form\b[^>]*style="[^"]*display:none/);
  assert.match(html, /id="agent-message"/);
});

test('a pending decision stays visible even in compact architecture mode', async () => {
  const html = await render(AgentDock, {
    project: project({ question: { id: 'Q1', prompt: '需要确认', options: [] } }),
    compact: true,
  });
  assert.doesNotMatch(html, /is-collapsed/);
  assert.match(html, /需要你的判断/);
  assert.doesNotMatch(html, /<form\b[^>]*style="[^"]*display:none/);
});

test('milestone detail opens on delivery planning with execution still available', async () => {
  const html = await render(MilestoneInspector, {
    project: project({
      milestones: [{ id: 'M0', title: 'Shared contract' }],
      behaviors: [
        {
          id: 'B1',
          behavior_key: 'feature.works',
          version: 1,
          statement: 'The outcome is reviewable',
        },
      ],
    }),
    milestone: {
      id: 'M1',
      title: 'Delivery',
      intent: 'Intent',
      status: 'PLANNED',
      scope: ['src/feature.ts'],
      behavior_revision_ids: ['B1'],
      dependencies: ['M0'],
      dependency_reasons: { M0: 'Consumes its contract' },
      architecture_components: [],
      attachment_ids: [],
    },
  });
  assert.match(html, /aria-pressed="true"[^>]*>交付规划/);
  assert.match(html, /执行与验收/);
  for (const text of [
    '验收标准',
    'The outcome is reviewable',
    'Shared contract',
    'Consumes its contract',
    'src/feature.ts',
  ])
    assert.ok(html.includes(text));
});

test('selecting a milestone gives detail space without unmounting the conversation', () => {
  const source = readFileSync(
    new URL('../../frontend/src/components/workspace/ProjectWorkspace.vue', import.meta.url),
    'utf8',
  );
  assert.match(
    source,
    /:compact="tab === 'architecture' \|\| \(tab === 'graph' && Boolean\(selected\)\)"/,
  );
  assert.equal((source.match(/<AgentDock/g) || []).length, 1);
});

test('inspector transitions preserve the viewport and respect reduced motion', () => {
  const css = readFileSync(
    new URL('../../frontend/src/styles/studio.css', import.meta.url),
    'utf8',
  );
  assert.match(css, /\.inspector-scroll\s*\{\s*scrollbar-gutter: stable;/);
  assert.match(
    css,
    /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.inspector-panel,[\s\S]*?animation: none;[\s\S]*?\.detail-tabs button\s*\{\s*transition: none;/,
  );
  const animation = css.match(/@keyframes inspector-reveal\s*\{([\s\S]*?)\n\}/)?.[1] || '';
  assert.match(animation, /opacity: 0/);
  assert.doesNotMatch(animation, /height|width|transform|margin/);
});

test('unmapped roadmap steps stay visibly pending without claiming architecture coverage', async () => {
  const milestone = {
    id: 'M1',
    title: 'New roadmap slice',
    intent: 'Plan first',
    status: 'PLANNED',
    behavior_revision_ids: [],
    scope: ['src/new/'],
    dependencies: [],
    architecture_components: [],
    attachment_ids: [],
  };
  const pending = await render(MilestoneInspector, {
    project: project({ architectures: [{ number: 1 }] }),
    milestone,
  });
  assert.match(pending, /组件待关联/);
  assert.match(pending, /执行与验收要求仍然保留/);
  const mapped = await render(MilestoneInspector, {
    project: project({ architectures: [{ number: 1 }] }),
    milestone: { ...milestone, architecture_components: ['existing-api'] },
  });
  assert.match(mapped, /关联组件/);
  assert.match(mapped, /existing-api/);
  assert.doesNotMatch(mapped, /组件待关联/);
  const absent = await render(MilestoneInspector, {
    project: project({ architectures: [] }),
    milestone,
  });
  assert.doesNotMatch(absent, /组件待关联/);
});

test('long questions retain complete accessible copy and separate immediate-answer choices', async () => {
  const prompt = '请确认字段、顺序及空值处理。'.repeat(50).slice(0, 600);
  const context = '这些约定用于文档与验收。'.repeat(30);
  const question = {
    id: 'Q-long',
    prompt,
    context,
    options: ['仅标题', '标题与作者', '加入年份', '加入分类', '全部字段'],
  };
  const html = await render(AgentQuestion, { question, answer: '标题与作者', disabled: false });
  assert.ok(html.includes(prompt));
  assert.ok(html.includes(context));
  assert.match(html, /aria-describedby="question-copy-Q-long"/);
  assert.match(html, /class="question-copy" tabindex="0" role="region" aria-label="完整问题说明"/);
  assert.doesNotMatch(html.match(/<legend[^>]*>(.*?)<\/legend>/)?.[1] ?? '', /请确认字段/);
  assert.equal((html.match(/title="点击即作为你的回答发送"/g) ?? []).length, 5);
  assert.match(html, /aria-pressed="true"[^>]*[^]*?标题与作者/);
  const blocked = await render(AgentQuestion, { question, answer: '', disabled: true });
  assert.equal((blocked.match(/ disabled/g) ?? []).length, 5);
});
