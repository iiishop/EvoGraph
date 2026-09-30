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
const imports = {
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
  '../../lib/presentation': moduleUrl(
    transpile(
      readFileSync(new URL('../../frontend/src/lib/presentation.ts', import.meta.url), 'utf8'),
    ),
  ),
};
for (const name of [
  './AgentQuestion.vue',
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
    milestone: { id: 'M1', title: 'A long task title', intent, status: 'ready', behavior_revision_ids: [] },
  });
  const details = html.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
  assert.ok(details);
  assert.doesNotMatch(details[1], /\bopen\b/);
  assert.ok(details[2].includes(intent));
  assert.match(html, /class="inspector-scroll"[^>]*><details/);
  assert.doesNotMatch(html.match(/class="inspector-title"[\s\S]*?<nav/)?.[0] || '', /Long model-written/);
});
