import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';
const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const vue = import.meta.resolve('vue');
const { createApp, h, nextTick, ref } = await import(vue);
const modelUrl = moduleUrl(compile(source('lib/planPreview.ts')));
const { contractRows, candidateNotice, requirementSource, planReviewDisclosure } = await import(
  modelUrl
);
const workspaceUrl = moduleUrl(`import { reactive } from ${JSON.stringify(vue)};
import { ref, computed } from ${JSON.stringify(vue)};
export const state = reactive({ busy: false, page: 'projects', project: null, selectedId: null }); export const calls = [];
const tab = ref('graph');
export const useWorkspace = () => ({ state, perform: async (...args) => calls.push(args),
  selected: computed(() => null), selectNode() {}, bindWorkspaceTab: () => ({ key: 1, tab }) });`);
const graphUrl = moduleUrl(`import { h } from ${JSON.stringify(vue)};
export default { props: ['diagram', 'isolated'], render() { return h('div', { 'data-candidate-diagram': this.diagram.id, 'data-isolated': this.isolated }); } };`);
const imports = {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  '../../lib/planPreview': modelUrl,
  '../../composables/useWorkspace': workspaceUrl,
  '../design/DiagramView.vue': graphUrl,
};
async function component(path) {
  const { descriptor } = parse(source(`components/workspace/${path}.vue`));
  return (
    await import(
      moduleUrl(
        compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
          /from (['"])([^'"]+)\1/g,
          (_, quote, name) => {
            assert.ok(imports[name], name);
            return `from ${JSON.stringify(imports[name])}`;
          },
        ),
      )
    )
  ).default;
}
imports['./PlanReviewDisclosure.vue'] = moduleUrl(
  compile(
    compileScript(parse(source('components/workspace/PlanReviewDisclosure.vue')).descriptor, {
      id: 'review-disclosure',
      inlineTemplate: true,
    }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => `from ${JSON.stringify(imports[name])}`),
);
const ReviewDisclosure = (await import(imports['./PlanReviewDisclosure.vue'])).default;
const Panel = await component('PlanCandidatePanel');
const Bar = await component('UnifiedPlanBar');
const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
const canonicalGraph = moduleUrl(`import { h } from ${JSON.stringify(vue)};
export default { props: ['project'], render() { return h('div', { 'data-canonical-revision': this.project.revision }, 'Formal plan ' + this.project.milestones.map(x => x.id).join(',')); } };`);
imports['./WorkspaceHeader.vue'] = stub;
imports['./UnifiedPlanBar.vue'] = moduleUrl(
  compile(
    compileScript(parse(source('components/workspace/UnifiedPlanBar.vue')).descriptor, {
      id: 'bar',
      inlineTemplate: true,
    }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => `from ${JSON.stringify(imports[name])}`),
);
imports['./PlanCandidatePanel.vue'] = moduleUrl(
  compile(
    compileScript(parse(source('components/workspace/PlanCandidatePanel.vue')).descriptor, {
      id: 'panel',
      inlineTemplate: true,
    }).content,
  ).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => `from ${JSON.stringify(imports[name])}`),
);
for (const name of [
  '../graph/GraphToolbar.vue',
  '../graph/MilestoneFinder.vue',
  '../graph/MilestoneInspector.vue',
  '../graph/SourceInspector.vue',
  '../agent/AgentDock.vue',
])
  imports[name] = stub;
imports['../graph/MilestoneGraph.vue'] = canonicalGraph;
imports['../../composables/useSurfaceMotion'] = moduleUrl(
  'export const useSurfaceMotion = () => ({ reveal() {}, finish() {}, enter() {}, leave() {}, cancel() {} });',
);
imports['../../composables/useAgent'] = moduleUrl(`import { reactive } from ${JSON.stringify(vue)};
const state = reactive({ projectId: 'P1', follow: {}, navigationTick: 0, pulse: 0, view: 'graph' });
export const useAgent = () => ({ state, freeView() {}, resumeFollow() {} });`);
imports['../../lib/workspaceViews'] =
  moduleUrl(`import { markRaw } from ${JSON.stringify(vue)}; import Graph from ${JSON.stringify(canonicalGraph)};
export const workspaceViews = [{ id: 'graph', component: markRaw(Graph) }];`);
const Workspace = await component('ProjectWorkspace');
const { state, calls } = await import(workspaceUrl);
const fixture = () => {
  const project = {
    id: 'P1',
    created_at: 'original',
    revision: 5,
    unified_planning: true,
    messages: [{ id: 'MSG1', content: 'Build it, but keep all saved tasks available offline.' }],
    architectures: [
      {
        summary: 'Local persistence',
        diagram: {
          id: 'A1',
          nodes: [{ id: 'C1', label: 'Local store', description: 'Stores tasks on device' }],
          edges: [],
          groups: [],
        },
      },
    ],
    milestones: [
      {
        id: 'M1',
        title: 'Offline task store',
        intent: 'Retain saved tasks',
        dependencies: [],
        architecture_components: ['C1'],
        behavior_revision_ids: ['B1'],
      },
    ],
    behaviors: [
      {
        id: 'B1',
        behavior_key: 'offline',
        statement: 'Saved tasks remain readable without a network',
        owner: 'M1',
      },
      {
        id: 'B2',
        behavior_key: 'offline',
        statement: 'NEWER MUST NOT REPLACE THE EXACT REVISION',
        owner: 'M1',
      },
    ],
    plan_contract: {
      requirements: [
        {
          id: 'R1',
          source_id: 'MSG1',
          quote: 'keep all saved tasks available offline',
          origin: 'user',
          kind: 'constraint',
          active: true,
        },
      ],
      bindings: [
        {
          behavior_key: 'offline',
          behavior_revision_id: 'B1',
          requirement_ids: ['R1'],
          component_ids: ['C1'],
          mechanism: 'Read the on-device store while disconnected',
          requires_behavior_keys: [],
        },
      ],
    },
  };
  const candidate = {
    id: 'PC1',
    base_revision: 5,
    revision: 1,
    status: 'needs_resolution',
    candidate_hash: 'abc',
    project,
    input: 'Plan',
    report: {
      findings: [
        {
          code: 'MISSING_PATH',
          subject: 'R1',
          message: 'The offline read path needs a concrete contract',
        },
      ],
      semantic: {
        candidate_hash: 'abc',
        summary: 'One unresolved requirement',
        checks: [
          {
            subject: 'R1',
            verdict: 'unknown',
            reason: 'No read path yet',
            counterexample: 'Airplane mode after restart',
          },
        ],
      },
    },
    metrics: { provider_calls: 2, tokens: 250, elapsed_seconds: 4.5, usage_reported: true },
  };
  return { project, candidate };
};
function mount(component, props) {
  const root = document.createElement('div');
  document.body.append(root);
  const values = ref(props);
  const app = createApp({ render: () => h(component, values.value) });
  app.mount(root);
  return {
    root,
    values,
    close() {
      app.unmount();
      root.remove();
    },
  };
}
const tick = async () => {
  await nextTick();
  await nextTick();
};

test('contracts resolve exact behavior revisions, source anchors, component and owner', () => {
  const { project } = fixture();
  const [row] = contractRows(project);
  assert.equal(row.behavior.id, 'B1');
  assert.equal(row.owner.title, 'Offline task store');
  assert.equal(row.requirements[0].requirement.source_id, 'MSG1');
  assert.equal(row.components[0].component.label, 'Local store');
  project.plan_contract.bindings[0].behavior_revision_id = 'missing';
  assert.equal(contractRows(project)[0].behavior, undefined);
});

test('failed review retains an inspectable read-only candidate with exact provenance and counterexample', async () => {
  calls.length = 0;
  state.busy = false;
  const { project, candidate } = fixture();
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    assert.match(ui.root.textContent, /候选预览 · 只读/);
    assert.match(ui.root.textContent, /正式方案未被替换/);
    assert.match(ui.root.textContent, /keep all saved tasks available offline/);
    assert.match(ui.root.textContent, /MSG1/);
    assert.match(ui.root.textContent, /Saved tasks remain readable without a network/);
    assert.doesNotMatch(ui.root.textContent, /NEWER MUST/);
    assert.match(ui.root.textContent, /Read the on-device store/);
    assert.match(ui.root.textContent, /Local store/);
    assert.match(ui.root.textContent, /Offline task store/);
    assert.match(ui.root.textContent, /Airplane mode after restart/);
    assert.ok(ui.root.querySelector('a[href="#candidate-requirement-R1"]'));
    assert.doesNotMatch(
      [...ui.root.querySelectorAll('button')].map((x) => x.textContent).join(' '),
      /执行|应用|提交|验证/,
    );
    [...ui.root.querySelectorAll('nav button')]
      .find((x) => x.textContent.includes('架构预览'))
      .click();
    await tick();
    assert.equal(
      ui.root.querySelector('[data-candidate-diagram]').getAttribute('data-isolated'),
      'true',
    );
    assert.deepEqual(calls, []);
    [...ui.root.querySelectorAll('button')]
      .find((x) => x.textContent.trim() === '放弃候选')
      .click();
    await tick();
    assert.deepEqual(calls, [
      ['plan.candidate_discard', { project_id: 'P1', candidate_id: 'PC1' }],
    ]);
  } finally {
    ui.close();
  }
});

test('empty first project shows generation early and does not permit discard during an active turn', async () => {
  const { project, candidate } = fixture();
  candidate.status = 'generating';
  candidate.report = { findings: [] };
  candidate.project = {
    ...project,
    milestones: [],
    architectures: [],
    behaviors: [],
    plan_contract: { requirements: [], bindings: [] },
  };
  state.busy = true;
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    assert.match(ui.root.textContent, /正在生成/);
    assert.match(ui.root.textContent, /正在提取需求/);
    assert.equal(
      [...ui.root.querySelectorAll('button')].find((x) => x.textContent.trim() === '放弃候选')
        .disabled,
      true,
    );
    state.busy = false;
    await tick();
    assert.equal(
      [...ui.root.querySelectorAll('button')].find((x) => x.textContent.trim() === '放弃候选')
        .disabled,
      false,
    );
  } finally {
    ui.close();
  }
});

test('project toggle is explicit, current-project scoped and disabled while running', async () => {
  const { project, candidate } = fixture();
  calls.length = 0;
  state.busy = false;
  const previews = [];
  const ui = mount(Bar, {
    project: { ...project, plan_candidate: candidate },
    preview: true,
    onPreview: (v) => previews.push(v),
  });
  try {
    ui.root.querySelector('.unified-plan-toggle').click();
    await tick();
    assert.deepEqual(calls, [['plan.unified_enable', { project_id: 'P1', enabled: false }]]);
    state.busy = true;
    await tick();
    assert.equal(ui.root.querySelector('.unified-plan-toggle').disabled, true);
    ui.root.querySelector('.plan-version-switch button').click();
    await tick();
    assert.deepEqual(previews, [false]);
  } finally {
    state.busy = false;
    ui.close();
  }
});

test('stale, interrupted and successful candidate copy describes the actual saved boundary', () => {
  const { candidate } = fixture();
  assert.match(candidateNotice(candidate, 6), /正式方案已发生变化/);
  for (const status of ['failed', 'stopped'])
    assert.match(candidateNotice({ ...candidate, status }, 5), /尚未提交/);
  assert.match(
    candidateNotice({ ...candidate, status: 'applied', applied_revision: 6 }, 6),
    /已自动应用/,
  );
  assert.match(
    candidateNotice({ ...candidate, status: 'applied', applied_revision: 5 }, 5),
    /没有应用规划变更/,
  );
  assert.match(candidateNotice({ ...candidate, status: 'applied' }, 6), /记录不完整/);
});

test('saved contract sources take precedence over missing chat history without inventing citations', () => {
  const { project } = fixture();
  const requirement = project.plan_contract.requirements[0];
  project.messages = [];
  project.plan_contract.sources = [
    {
      id: 'MSG1',
      origin: 'user',
      text: 'The exact durable request: keep all saved tasks available offline',
    },
  ];
  assert.equal(
    requirementSource(project, requirement).content,
    project.plan_contract.sources[0].text,
  );
  assert.equal(requirementSource(project, requirement).available, true);
  assert.equal(
    requirementSource(project, { ...requirement, source_id: 'unknown' }).available,
    false,
  );
});

test('workspace automatically restores candidate preview and exposes canonical revision only on commit', async () => {
  const { project, candidate } = fixture();
  project.milestones = [];
  const canonical = { ...project, plan_candidate: null };
  state.project = canonical;
  const ui = mount(Workspace, { project: canonical });
  try {
    assert.equal(ui.root.querySelector('.plan-candidate-panel'), null);
    ui.values.value = { project: { ...canonical, plan_candidate: candidate } };
    await tick();
    assert.ok(ui.root.querySelector('.plan-candidate-panel'));
    const savedGraph = ui.root.querySelector('[data-canonical-revision]');
    assert.equal(savedGraph.getAttribute('data-canonical-revision'), '5');
    assert.equal(savedGraph.style.display, 'none');
    ui.root.querySelector('.plan-version-switch button').click();
    await tick();
    assert.equal(ui.root.querySelector('.plan-candidate-panel'), null);
    assert.notEqual(savedGraph.style.display, 'none');
    ui.root.querySelectorAll('.plan-version-switch button')[1].click();
    await tick();
    assert.ok(ui.root.querySelector('.plan-candidate-panel'));
    const applied = { ...candidate, status: 'applied' };
    ui.values.value = { project: { ...canonical, plan_candidate: applied } };
    await tick();
    assert.ok(
      ui.root.querySelector('.plan-candidate-panel'),
      'an applied candidate event alone is not a canonical commit',
    );
    ui.values.value = { project: { ...canonical, revision: 6, plan_candidate: applied } };
    await tick();
    assert.equal(ui.root.querySelector('.plan-candidate-panel'), null);
    assert.equal(savedGraph.getAttribute('data-canonical-revision'), '6');
  } finally {
    ui.close();
    state.project = null;
  }
  const reopened = mount(Workspace, { project: { ...canonical, plan_candidate: candidate } });
  try {
    assert.ok(
      reopened.root.querySelector('.plan-candidate-panel'),
      'reopen restores the unresolved candidate',
    );
  } finally {
    reopened.close();
  }
});

test('applied candidates offer no discard action and missing token usage is not displayed as zero', () => {
  const { project, candidate } = fixture();
  candidate.status = 'applied';
  candidate.metrics = { provider_calls: 2, tokens: 0, elapsed_seconds: 2, usage_reported: false };
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    assert.doesNotMatch(ui.root.textContent, /放弃候选|0 tokens/);
    assert.match(ui.root.textContent, /token 用量未返回/);
    assert.match(ui.root.textContent, /请求尝试 2 次/);
  } finally {
    ui.close();
  }
});

test('initial candidate exposes the exact raw request until source anchors arrive', async () => {
  const { project, candidate } = fixture();
  candidate.input = 'Keep <all> links offline.\nPreserve repeated  spaces and original wording.';
  candidate.status = 'generating';
  candidate.project.plan_contract = { requirements: [], bindings: [] };
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    const pending = ui.root.querySelector('.candidate-pending-input');
    assert.match(pending.textContent, /当前请求原文.*待整理/);
    assert.equal(pending.querySelector('.requirement-quote').textContent, candidate.input);
    assert.equal(pending.querySelector('all'), null, 'raw input remains escaped text');
    ui.values.value = { ...ui.values.value, candidate: fixture().candidate };
    await tick();
    assert.equal(ui.root.querySelector('.candidate-pending-input'), null);
  } finally {
    ui.close();
  }
});

test('long architecture description is collapsed above the accessible diagram and keeps its full text', async () => {
  const { project, candidate } = fixture();
  const text = 'Full architecture description. '.repeat(160);
  candidate.project.architectures[0].summary = text;
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    [...ui.root.querySelectorAll('nav button')]
      .find((x) => x.textContent.includes('架构预览'))
      .click();
    await tick();
    const details = ui.root.querySelector('.candidate-architecture-summary');
    assert.equal(details.open, false);
    assert.equal(details.querySelector('p').textContent, text);
    assert.equal(details.querySelector('p').tabIndex, 0);
    assert.equal(details.nextElementSibling.getAttribute('data-candidate-diagram'), 'A1');
    assert.match(
      source('components/workspace/PlanCandidatePanel.vue'),
      /\.candidate-architecture-summary p\s*\{[^}]*max-height: min\(25vh, 180px\);[^}]*overflow: auto;/,
    );
  } finally {
    ui.close();
  }
});

test('process constraints remain source-scoped and separate from product counts and acceptance', () => {
  const { project, candidate } = fixture();
  candidate.project.plan_contract.sources = [
    {
      id: 'request-old',
      origin: 'user',
      text: 'For the first planning request: only plan, do not change files.',
    },
    {
      id: 'request-current',
      origin: 'user',
      text: 'For this request, do not perform external research.',
    },
  ];
  candidate.project.plan_contract.process_constraints = [
    {
      id: 'PC-old',
      source_id: 'request-old',
      quote: 'only plan, do not change files',
      rule: 'planning_only',
    },
    {
      id: 'PC-current',
      source_id: 'request-current',
      quote: 'do not perform external research',
      rule: 'no_external_research',
    },
  ];
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    const section = ui.root.querySelector('[aria-label="本轮过程约束"]');
    assert.ok(section);
    assert.match(section.textContent, /不计入产品需求或验收/);
    assert.match(section.textContent, /不表示永久产品承诺/);
    const items = section.querySelectorAll('.candidate-process-constraint');
    assert.equal(items.length, 2);
    for (let i = 0; i < items.length; i++) {
      const constraint = candidate.project.plan_contract.process_constraints[i];
      assert.equal(items[i].querySelector('.requirement-quote').textContent, constraint.quote);
      assert.equal(
        items[i].querySelector('.candidate-process-scope').textContent,
        `仅适用于来源请求：${constraint.source_id}`,
      );
      assert.equal(
        items[i].querySelector('details p').textContent,
        candidate.project.plan_contract.sources[i].text,
      );
    }
    assert.equal(ui.root.querySelector('.candidate-tabs button span').textContent, '1');
    assert.equal(ui.root.querySelectorAll('.candidate-requirements article').length, 1);
    assert.equal(ui.root.querySelectorAll('.candidate-contract').length, 1);
    assert.doesNotMatch(
      ui.root.querySelector('.candidate-contract').textContent,
      /PC-old|PC-current|only plan|external research/,
    );
  } finally {
    ui.close();
  }
});

test('old candidates omit the process section and process-only requests do not become product acceptance', () => {
  const { project, candidate } = fixture();
  const old = mount(Panel, { candidate, canonical: project });
  try {
    assert.equal(old.root.querySelector('.candidate-process-constraints'), null);
  } finally {
    old.close();
  }
  candidate.project.plan_contract = {
    requirements: [],
    bindings: [],
    process_constraints: [
      {
        id: 'PC1',
        source_id: 'unknown-request',
        quote: 'Keep this planning turn concise',
        rule: 'other',
      },
    ],
  };
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    assert.equal(ui.root.querySelector('.candidate-tabs button span').textContent, '0');
    assert.equal(ui.root.querySelectorAll('.candidate-contract').length, 0);
    assert.equal(ui.root.querySelector('.candidate-requirements'), null);
    const section = ui.root.querySelector('.candidate-process-constraints');
    assert.match(section.textContent, /其他过程约束/);
    assert.match(section.textContent, /unknown-request/);
    assert.match(section.textContent, /来源全文未包含在当前视图中/);
  } finally {
    ui.close();
  }
});

test('generation protocol errors use the existing truthful findings surface', () => {
  const { project, candidate } = fixture();
  candidate.report = {
    findings: ['provider_output_incomplete', 'provider_output_limited', 'invalid_plan_delta'].map(
      (code) => ({ code, subject: 'candidate', message: `Saved failure: ${code}` }),
    ),
  };
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    const findings = ui.root.querySelector('.candidate-findings');
    for (const finding of candidate.report.findings)
      assert.ok(findings.textContent.includes(finding.message));
  } finally {
    ui.close();
  }
});

function reviewedCandidate() {
  const { candidate } = fixture();
  return {
    ...candidate,
    status: 'applied',
    applied_revision: 6,
    turn_summary: { changed: true },
    validation_receipt: {
      schema_version: 'planning-validation/v1',
      candidate_hash: candidate.candidate_hash,
      structural: { status: 'clear', finding_count: 0 },
      model: { kind: 'model_opinion', status: 'no_issue_found', subject_count: 1 },
      implementation: {
        scope: 'current_planning_turn',
        status: 'not_run',
        existing_record_count: 2,
      },
    },
    report: {
      findings: [],
      semantic: {
        candidate_hash: candidate.candidate_hash,
        summary: 'The model did not identify a problem',
        checks: [
          {
            subject: 'R1',
            verdict: 'supported',
            reason: 'Model assessment only',
            counterexample: 'No counterexample identified by the model',
          },
        ],
      },
    },
  };
}

test('three-layer review disclosure is compact by default and attributes reviewed application without claiming implementation proof', async () => {
  const candidate = reviewedCandidate();
  const ui = mount(ReviewDisclosure, { candidate, canonicalRevision: 6 });
  try {
    const disclosure = ui.root.querySelector('details');
    const summary = disclosure.querySelector('summary');
    assert.equal(disclosure.open, false);
    assert.equal(summary.textContent, '模型评审，可能遗漏问题');
    assert.deepEqual(
      [...disclosure.querySelectorAll('dt')].map((node) => node.textContent),
      ['结构检查', '模型评审', '未运行实现'],
    );
    summary.getBoundingClientRect = () => ({ left: 20, right: 160, top: 20, bottom: 40 });
    summary.getClientRects = () => [summary.getBoundingClientRect()];
    summary.click();
    disclosure.dispatchEvent(new dom.window.Event('toggle'));
    await tick();
    assert.equal(disclosure.open, true);
    const reader = document.getElementById(summary.getAttribute('aria-controls'));
    assert.match(reader.textContent, /已自动应用 · 模型评审未发现问题，仍可能有错误/);
    assert.match(reader.textContent, /结构检查未发现问题/);
    assert.match(reader.textContent, /2 条既有验收记录不代表本轮运行/);
    assert.match(reader.textContent, /不代表实现已验证/);
    assert.match(reader.textContent, /候选 PC1 · 基于 R5/);
    disclosure.dispatchEvent(
      new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
    assert.equal(disclosure.open, false);
    assert.equal(document.activeElement, summary);
  } finally {
    ui.close();
  }
});

test('no-op, legacy and mismatched semantic receipts never invent a completed model review', () => {
  const reviewed = { ...reviewedCandidate(), validation_receipt: undefined };
  const cases = [
    { ...reviewed, applied_revision: 5, turn_summary: { changed: false } },
    { ...reviewed, applied_revision: undefined },
    { ...reviewed, report: { findings: [] } },
    {
      ...reviewed,
      report: {
        findings: [],
        semantic: { ...reviewed.report.semantic, candidate_hash: 'another-version' },
      },
    },
    {
      ...reviewed,
      report: { findings: [], semantic: { ...reviewed.report.semantic, checks: [] } },
    },
  ];
  for (const candidate of cases) {
    const info = planReviewDisclosure(candidate, 6);
    assert.equal(info.label, '评审记录未知');
    assert.doesNotMatch(info.semantic, /未发现问题|已自动应用/);
  }
  assert.doesNotMatch(planReviewDisclosure(reviewed, 7).semantic, /未发现问题|已自动应用/);
  assert.match(planReviewDisclosure(reviewed, 7).source, /正式方案 R7.*尚无匹配/);
  assert.equal(planReviewDisclosure().label, '评审记录未知');
});

test('candidate status is not enough to claim reviewed success while generating or with unresolved checks', () => {
  const reviewed = reviewedCandidate();
  for (const status of ['generating', 'reviewing']) {
    const candidate = {
      ...reviewed,
      status,
      validation_receipt: undefined,
      report: { findings: [] },
    };
    const info = planReviewDisclosure(candidate);
    assert.equal(info.label, '评审记录未知');
    assert.doesNotMatch(info.semantic, /未发现问题|已自动应用/);
  }
  const pending = {
    ...reviewed,
    status: 'needs_resolution',
    validation_receipt: undefined,
    report: {
      findings: [],
      semantic: {
        ...reviewed.report.semantic,
        checks: [
          {
            subject: 'R1',
            verdict: 'unknown',
            reason: 'uncertain',
            counterexample: 'possible failure',
          },
        ],
      },
    },
  };
  assert.match(planReviewDisclosure(pending).semantic, /矛盾或未决项/);
  assert.doesNotMatch(planReviewDisclosure(pending).semantic, /未发现问题|已自动应用/);
});

test('in-flight candidates distinguish pending model results from terminal not-run receipts', () => {
  const candidate = reviewedCandidate();
  candidate.validation_receipt.model.status = 'not_run';
  for (const status of ['generating', 'reviewing']) {
    const info = planReviewDisclosure({ ...candidate, status });
    assert.equal(info.label, '尚无模型评审结果');
    assert.match(info.semantic, /尚无模型评审结果/);
    assert.doesNotMatch(info.semantic, /未进行模型评审/);
  }
  const noop = planReviewDisclosure({
    ...candidate,
    status: 'applied',
    applied_revision: candidate.base_revision,
    turn_summary: { changed: false },
  });
  assert.equal(noop.label, '模型评审未运行');
  assert.equal(noop.semantic, '本轮未进行模型评审');
});

test('formal view always exposes a compact review disclosure and candidate changes close old details', async () => {
  const { project } = fixture();
  const candidate = reviewedCandidate();
  const ui = mount(Bar, {
    project: { ...project, revision: 6, plan_candidate: candidate },
    preview: false,
  });
  try {
    const disclosure = ui.root.querySelector('.plan-review-disclosure');
    assert.ok(disclosure);
    assert.equal(disclosure.open, false);
    disclosure.open = true;
    ui.values.value = {
      ...ui.values.value,
      project: { ...project, revision: 7, plan_candidate: candidate },
    };
    await tick();
    assert.equal(disclosure.open, false);
    assert.match(disclosure.querySelector('summary').textContent, /未知/);
    ui.values.value = { ...ui.values.value, preview: true };
    await tick();
    assert.equal(ui.root.querySelector('.plan-review-disclosure'), null);
  } finally {
    ui.close();
  }
  const preview = mount(Panel, { candidate, canonical: project });
  try {
    assert.ok(preview.root.querySelector('.candidate-heading .plan-review-disclosure'));
    assert.match(preview.root.querySelector('h2').textContent, /已自动应用/);
  } finally {
    preview.close();
  }
});

test('version-bound receipt distinguishes checks, model opinions and this-turn execution from legacy uncertainty', () => {
  const candidate = reviewedCandidate();
  const emptyHash = {
    ...candidate,
    candidate_hash: '',
    report: { findings: [] },
    validation_receipt: { ...candidate.validation_receipt, candidate_hash: '' },
  };
  assert.equal(planReviewDisclosure(emptyHash, 6).label, '评审记录未知');
  const legacy = { ...candidate, validation_receipt: undefined };
  const old = planReviewDisclosure(legacy, 6);
  assert.equal(old.label, '旧模型意见，可能遗漏问题');
  assert.match(old.structural, /状态未知/);
  assert.match(old.implementation, /运行状态未知/);
  assert.equal(old.implementationTitle, '实现执行证据');
  const stale = {
    ...candidate,
    report: { findings: [] },
    validation_receipt: { ...candidate.validation_receipt, candidate_hash: 'stale-hash' },
  };
  assert.equal(planReviewDisclosure(stale, 6).label, '评审记录未知');
  assert.match(planReviewDisclosure(stale, 6).structural, /状态未知/);
  assert.match(planReviewDisclosure(stale, 6).implementation, /运行状态未知/);
  for (const [status, expected] of [
    ['not_run', '模型评审未运行'],
    ['unavailable', '模型评审未完成'],
    ['issues', '模型评审，可能遗漏问题'],
  ]) {
    const record = {
      ...candidate,
      validation_receipt: {
        ...candidate.validation_receipt,
        model: { ...candidate.validation_receipt.model, status },
      },
    };
    const info = planReviewDisclosure(record, 6);
    assert.equal(info.label, expected);
    assert.doesNotMatch(info.semantic, /未发现问题|已自动应用/);
  }
  const notRun = {
    ...candidate,
    status: 'generating',
    validation_receipt: {
      ...candidate.validation_receipt,
      structural: { status: 'not_run', finding_count: null },
      model: { kind: 'model_opinion', status: 'not_run', subject_count: 0 },
    },
  };
  assert.match(planReviewDisclosure(notRun).structural, /尚未进行结构检查/);
  assert.match(planReviewDisclosure(notRun).semantic, /尚无模型评审结果/);
});

test('semantic bases describe existing references without claiming execution in this planning turn', () => {
  const { project } = fixture();
  const candidate = reviewedCandidate();
  candidate.report.semantic.checks = [
    {
      subject: 'R1',
      verdict: 'supported',
      reason: 'Reason one',
      counterexample: 'Counterexample one',
    },
    {
      subject: 'R2',
      verdict: 'supported',
      reason: 'Reason two',
      counterexample: 'Counterexample two',
      basis: 'source_statement',
    },
    {
      subject: 'R3',
      verdict: 'supported',
      reason: 'Reason three',
      counterexample: 'Counterexample three',
      basis: 'existing_execution_record',
      execution_evidence_ids: ['E-old-1'],
    },
  ];
  const ui = mount(Panel, { candidate, canonical: project });
  try {
    const details = ui.root.querySelector('.candidate-review-details');
    for (const text of [
      '模型推断',
      '模型引用原文',
      '引用既有验收记录',
      'E-old-1',
      '非本轮执行',
      '未重新验证',
    ])
      assert.ok(details.textContent.includes(text));
    assert.doesNotMatch(details.textContent, /本轮已执行/);
  } finally {
    ui.close();
  }
  candidate.report.semantic.candidate_hash = 'older-revision';
  const stale = mount(Panel, { candidate, canonical: project });
  try {
    assert.equal(stale.root.querySelector('.candidate-review-details'), null);
    assert.match(
      stale.root.querySelector('.plan-review-layers').textContent,
      /旧模型意见.*不适用于当前候选/,
    );
  } finally {
    stale.close();
  }
});
