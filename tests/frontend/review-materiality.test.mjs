import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';

const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const vue = import.meta.resolve('vue');
const { createApp, h } = await import(vue);
const modelUrl = moduleUrl(compile(source('lib/planPreview.ts')));
const { planReviewDisclosure } = await import(modelUrl);
const imports = { vue, '../../lib/planPreview': modelUrl };
const { descriptor } = parse(source('components/workspace/PlanReviewDisclosure.vue'));
const component = (await import(moduleUrl(compile(compileScript(descriptor, {
  id: 'review-materiality', inlineTemplate: true,
}).content).replace(/from (['"])([^'"]+)\1/g, (_, _quote, name) => {
  assert.ok(imports[name], name);
  return `from ${JSON.stringify(imports[name])}`;
})))).default;

function candidateFixture() {
  return {
    id: 'candidate', base_revision: 5, revision: 1, status: 'applied', applied_revision: 6,
    candidate_hash: 'exact-hash', turn_summary: { changed: true },
    report: {
      findings: [],
      semantic: { candidate_hash: 'exact-hash', summary: 'No material defect', checks: [
        { subject: 'slice_activation:M1', verdict: 'supported', reason: 'Owned work', counterexample: 'None' },
      ] },
      semantic_batch: { candidate_hash: 'exact-hash', issues: [], observations: [{
        id: 'cleanup', subjects: ['slice_activation:M1'], kind: 'editorial',
        reason: 'Remove stray wording without changing the valid dependency',
        basis: 'source_statement', execution_evidence_ids: [],
        evidence_refs: ['/candidate/milestones/0'],
      }] },
    },
  };
}

function harnessCandidate(findings, verdict = 'unknown') {
  const candidate = candidateFixture();
  const check = {
    id: 'semantic_review', kind: 'model_opinion', version: '2',
    execution: 'completed', verdict, findings: findings.length, message: '',
  };
  candidate.validation_receipt = {
    schema_version: 'planning-validation/v1', candidate_hash: candidate.candidate_hash,
    structural: { status: 'clear', finding_count: 0 },
    model: { kind: 'model_opinion', status: verdict === 'pass' ? 'no_issue_found' : 'issues', subject_count: 16 },
    implementation: { scope: 'current_planning_turn', status: 'not_run', existing_record_count: 0 },
    harness: {
      schema_version: 'planning-harness-disclosure/v1', snapshot_id: 'snapshot',
      policy_version: 'policy-v6', decision: verdict === 'pass' ? 'apply' : 'hold', checks: [check],
    },
  };
  candidate.harness_run = {
    schema_version: 'plan-harness-run/v1', candidate_id: candidate.id,
    candidate_hash: candidate.candidate_hash, snapshot_id: 'snapshot', policy_version: 'policy-v6',
    executions: [{
      plugin_id: check.id, plugin_version: check.version, kind: check.kind, status: check.execution,
      result: {
        schema_version: 'plan-harness-result/v1', plugin_id: check.id, plugin_version: check.version,
        snapshot_id: 'snapshot', verdict, findings,
      },
    }],
  };
  return candidate;
}

function checkText(candidate) {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(component, { candidate }) });
  app.mount(root);
  try {
    return root.querySelector('[data-harness-check="semantic_review"]').textContent;
  } finally {
    app.unmount();
    root.remove();
  }
}

test('observations are advisory, exact-candidate bound and optional for historical records', () => {
  const candidate = candidateFixture();
  assert.equal(planReviewDisclosure(candidate, 6).observations.length, 1);
  assert.match(planReviewDisclosure(candidate, 6).semantic, /未发现问题/);
  assert.equal(planReviewDisclosure(candidate, 7).observations.length, 0);
  candidate.report.semantic_batch.candidate_hash = 'stale';
  assert.deepEqual(planReviewDisclosure(candidate, 6).observations, []);
  delete candidate.report.semantic_batch;
  assert.deepEqual(planReviewDisclosure(candidate, 6).observations, []);
});

test('review details retain observation text, basis and exact reference without calling it blocking', () => {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(component, { candidate: candidateFixture(), canonicalRevision: 6 }) });
  app.mount(root);
  try {
    assert.match(root.textContent, /非阻断观察 · 1 项/);
    assert.match(root.textContent, /文字整理/);
    assert.match(root.textContent, /Remove stray wording/);
    assert.match(root.textContent, /source_statement/);
    assert.match(root.textContent, /\/candidate\/milestones\/0/);
    assert.match(root.textContent, /不覆盖未决项/);
  } finally {
    app.unmount();
    root.remove();
  }
});

test('mixed semantic findings separate blocking subject records from advisory findings', () => {
  const candidate = harnessCandidate([
    ...Array.from({ length: 6 }, (_, index) => ({ subject: `subject:${index}`, severity: 'error' })),
    { subject: 'cohesion_and_scope', severity: 'review' },
  ]);
  // Two material issues can cover six subjects; the row must not invent six unique issues.
  candidate.report.semantic_batch.issues = [
    { id: 'issue-1', subjects: ['subject:0', 'subject:1', 'subject:2'], verdict: 'unknown' },
    { id: 'issue-2', subjects: ['subject:3', 'subject:4', 'subject:5'], verdict: 'unknown' },
  ];
  const original = JSON.stringify(candidate);
  const evidence = planReviewDisclosure(candidate);
  assert.equal(evidence.harness.checks[0].findingSummary, '6 条阻断主体记录 · 1 条非阻断建议');
  assert.equal(evidence.harness.checks[0].verdict, 'unknown');
  assert.equal(evidence.harness.decision, 'hold');
  assert.equal(evidence.observations.length, 1);
  const text = checkText(candidate);
  assert.match(text, /已完成 · 尚无法判断.*6 条阻断主体记录 · 1 条非阻断建议/s);
  assert.doesNotMatch(text, /[267] 项问题|7 条阻断|6 项/);
  assert.equal(JSON.stringify(candidate), original);
});

test('passing checks with review-severity findings stay advisory', () => {
  const candidate = harnessCandidate([{ severity: 'review' }], 'pass');
  const evidence = planReviewDisclosure(candidate);
  assert.equal(evidence.harness.checks[0].findingSummary, '1 条非阻断建议');
  assert.equal(evidence.harness.decision, 'apply');
  assert.match(checkText(candidate), /模型未发现问题.*1 条非阻断建议/s);
  assert.doesNotMatch(checkText(candidate), /条阻断|项问题/);
  const check = candidate.validation_receipt.harness.checks[0];
  const execution = candidate.harness_run.executions[0];
  check.id = execution.plugin_id = execution.result.plugin_id = 'design_consistency';
  check.kind = execution.kind = 'deterministic';
  assert.equal(planReviewDisclosure(candidate).harness.checks[0].findingSummary, '1 条非阻断建议');
});

test('historical totals stay readable and missing finding severity keeps the error default', () => {
  const candidate = harnessCandidate([{}, { severity: 'review' }]);
  assert.equal(planReviewDisclosure(candidate).harness.checks[0].findingSummary,
    '1 条阻断主体记录 · 1 条非阻断建议');
  delete candidate.harness_run;
  assert.equal(planReviewDisclosure(candidate).harness.checks[0].findingSummary,
    '2 条发现记录（严重程度明细未知）');
  candidate.validation_receipt.harness.checks[0].verdict = 'pass';
  assert.equal(planReviewDisclosure(candidate).harness.checks[0].findingSummary, '2 条非阻断建议');
});

test('finding severity counts require the same candidate, snapshot, policy and plugin result', () => {
  const edits = [
    (run) => { run.schema_version = 'future'; },
    (run) => { run.candidate_id = 'other'; },
    (run) => { run.candidate_hash = 'stale'; },
    (run) => { run.snapshot_id = 'other'; },
    (run) => { run.policy_version = 'old'; },
    (run) => { run.executions[0].plugin_id = 'other'; },
    (run) => { run.executions[0].plugin_version = 'old'; },
    (run) => { run.executions[0].kind = 'deterministic'; },
    (run) => { run.executions[0].status = 'unavailable'; },
    (run) => { run.executions[0].result = null; },
    (run) => { run.executions[0].result.schema_version = 'future'; },
    (run) => { run.executions[0].result.plugin_id = 'other'; },
    (run) => { run.executions[0].result.plugin_version = 'old'; },
    (run) => { run.executions[0].result.snapshot_id = 'other'; },
    (run) => { run.executions[0].result.verdict = 'pass'; },
    (run) => { run.executions[0].result.findings.push({ severity: 'error' }); },
  ];
  for (const edit of edits) {
    const candidate = harnessCandidate([{ severity: 'error' }, { severity: 'review' }]);
    edit(candidate.harness_run);
    assert.equal(planReviewDisclosure(candidate).harness.checks[0].findingSummary,
      '2 条发现记录（严重程度明细未知）');
  }
  const empty = harnessCandidate([], 'pass');
  assert.equal(planReviewDisclosure(empty).harness.checks[0].findingSummary, '');
  assert.doesNotMatch(checkText(empty), /0 条/);
});
