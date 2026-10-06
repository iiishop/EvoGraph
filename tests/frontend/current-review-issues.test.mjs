import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';

const frozen = JSON.parse(
  readFileSync(new URL('./fixtures/current-review-issues.json', import.meta.url), 'utf8'),
);
const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const vue = import.meta.resolve('vue');
const { createApp, h, nextTick, reactive } = await import(vue);
const modelUrl = moduleUrl(compile(source('lib/planPreview.ts')));
const { currentReviewIssues, planReviewDisclosure } = await import(modelUrl);
const stateUrl = moduleUrl(`export const useWorkspace = () => ({ state: { busy: false } });
export const useAgent = () => ({ state: { running: false } });`);
const imports = {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  '../../lib/planPreview': modelUrl,
  '../../composables/useWorkspace': stateUrl,
  '../../composables/useAgent': stateUrl,
  '../design/DiagramView.vue': moduleUrl('export default { render: () => null };'),
};
function component(name) {
  const { descriptor } = parse(source(`components/workspace/${name}.vue`));
  return moduleUrl(
    compile(compileScript(descriptor, { id: name, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, _quote, name) => {
        assert.ok(imports[name], name);
        return `from ${JSON.stringify(imports[name])}`;
      },
    ),
  );
}
imports['./PlanReviewDisclosure.vue'] = component('PlanReviewDisclosure');
const Panel = (await import(component('PlanCandidatePanel'))).default;

function fixture() {
  const review = structuredClone(frozen);
  return {
    id: 'frozen-candidate',
    status: 'needs_resolution',
    base_revision: 2,
    revision: 1,
    candidate_hash: review.candidate_hash,
    input: 'Historical planning review',
    project: { id: 'project', revision: 2, architectures: [], milestones: [], behaviors: [] },
    report: { findings: [], semantic: review.semantic, semantic_batch: review.semantic_batch },
    metrics: { provider_calls: 1, tokens: 0, elapsed_seconds: 0 },
  };
}
function mount(candidate) {
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(Panel, { candidate, canonical: candidate.project }) });
  app.mount(root);
  return {
    root,
    close() {
      app.unmount();
      root.remove();
    },
  };
}
const cards = (root) => [...root.querySelectorAll('.candidate-findings [data-review-issue]')];
const rawChecks = (root) => [...root.querySelectorAll('.candidate-review-details article')];
const occurrences = (text, value) => text.split(value).length - 1;

function assertSubjectFallback(candidate) {
  const issues = currentReviewIssues(candidate);
  const expected = candidate.report.semantic.checks.filter(
    (check) => check.verdict !== 'supported',
  );
  assert.equal(issues.length, expected.length);
  assert.equal(new Set(issues.map((issue) => issue.key)).size, expected.length);
  expected.forEach((check, index) => {
    assert.deepEqual(issues[index].subjects, [check.subject]);
    assert.equal(issues[index].verdict, check.verdict);
    assert.equal(issues[index].reason, check.reason);
    assert.equal(issues[index].counterexample, check.counterexample);
  });
  const ui = mount(candidate);
  try {
    assert.equal(cards(ui.root).length, expected.length);
    assert.equal(rawChecks(ui.root).length, candidate.report.semantic.checks.length);
  } finally {
    ui.close();
  }
}

test('frozen six-subject review renders two issue cards with all associations and unchanged evidence', () => {
  const candidate = fixture();
  const before = structuredClone(candidate);
  assert.equal(candidate.report.semantic.checks.length, 6);
  const issues = currentReviewIssues(candidate);
  assert.equal(issues.length, 2);
  assert.deepEqual(
    issues.flatMap((issue) => issue.subjects).sort(),
    candidate.report.semantic.checks.map((check) => check.subject).sort(),
  );
  const ui = mount(candidate);
  try {
    assert.equal(cards(ui.root).length, 2);
    candidate.report.semantic_batch.issues.forEach((issue, index) => {
      const card = cards(ui.root)[index];
      assert.equal(card.dataset.reviewIssue, `issue:${issue.id}`);
      assert.equal(issues[index].verdict, issue.verdict);
      assert.equal(occurrences(card.textContent, issue.reason), 1);
      assert.equal(occurrences(card.textContent, issue.counterexample), 1);
      assert.ok(card.textContent.includes('尚无法判断'));
      for (const subject of issue.subjects) assert.ok(card.textContent.includes(subject));
    });
    assert.equal(rawChecks(ui.root).length, 6);
    candidate.report.semantic.checks.forEach((check, index) => {
      const record = rawChecks(ui.root)[index].textContent;
      for (const value of [check.subject, check.reason, check.counterexample])
        assert.ok(record.includes(value));
    });
    assert.match(ui.root.textContent, /语义评审记录 · 6 项/);
    assert.match(ui.root.textContent, /不代表代码已经实现或通过测试/);
    assert.deepEqual(candidate, before);
  } finally {
    ui.close();
  }
});

test('different stable IDs stay separate even when subjects and prose are identical', () => {
  for (const verdict of ['unknown', 'contradicted']) {
    const candidate = fixture();
    const first = { ...candidate.report.semantic_batch.issues[0], verdict };
    candidate.report.semantic_batch.issues = [first, { ...first, id: 'another-issue' }];
    const ui = mount(candidate);
    try {
      assert.equal(cards(ui.root).length, 2);
      assert.deepEqual(
        cards(ui.root).map((card) => card.dataset.reviewIssue),
        ['issue:issue-tech-assignment', 'issue:another-issue'],
      );
      for (const card of cards(ui.root))
        assert.ok(card.textContent.includes(verdict === 'unknown' ? '尚无法判断' : '存在矛盾'));
    } finally {
      ui.close();
    }
  }
});

test('legacy reviews never infer issue identity from repeated prose', () => {
  const candidate = fixture();
  delete candidate.report.semantic_batch;
  candidate.report.semantic.checks.push({
    subject: 'supported-subject',
    verdict: 'supported',
    reason: 'Supported row',
    counterexample: '',
  });
  assertSubjectFallback(candidate);
});

test('missing or duplicate stable IDs and unmatched batches keep each normalized subject row', () => {
  for (const edit of [
    (candidate) => {
      candidate.report.semantic_batch.issues[0].id = '';
    },
    (candidate) => {
      delete candidate.report.semantic_batch.issues[0].id;
    },
    (candidate) => {
      candidate.report.semantic_batch.issues[1].id = candidate.report.semantic_batch.issues[0].id;
    },
    (candidate) => {
      candidate.report.semantic_batch.issues = [];
    },
    (candidate) => {
      candidate.report.semantic_batch.candidate_hash = 'other-review';
    },
  ]) {
    const candidate = fixture();
    edit(candidate);
    assertSubjectFallback(candidate);
  }
});

test('stale, unversioned, missing and no-op reviews do not become current issue cards', () => {
  for (const edit of [
    (candidate) => {
      candidate.candidate_hash = 'new-candidate';
    },
    (candidate) => {
      candidate.report.semantic.candidate_hash = 'old-review';
    },
    (candidate) => {
      delete candidate.report.semantic.candidate_hash;
    },
    (candidate) => {
      delete candidate.report.semantic;
    },
    (candidate) => {
      candidate.status = 'applied';
      candidate.applied_revision = candidate.base_revision;
    },
  ]) {
    const candidate = fixture();
    edit(candidate);
    assert.deepEqual(currentReviewIssues(candidate), []);
    const ui = mount(candidate);
    try {
      assert.equal(cards(ui.root).length, 0);
      assert.equal(rawChecks(ui.root).length, 0);
    } finally {
      ui.close();
    }
  }
});

test('changing the candidate fingerprint removes current cards and raw checks reactively', async () => {
  const candidate = reactive(fixture());
  const ui = mount(candidate);
  try {
    assert.equal(cards(ui.root).length, 2);
    candidate.candidate_hash = 'new-candidate';
    await nextTick();
    assert.equal(cards(ui.root).length, 0);
    assert.equal(rawChecks(ui.root).length, 0);
  } finally {
    ui.close();
  }
});

test('programmatic findings, review-severity advice and non-blocking observations stay separate', () => {
  const candidate = fixture();
  candidate.report.findings = [
    {
      code: 'missing-reference',
      subject: 'requirement:other',
      message: 'Programmatic finding',
      severity: 'error',
    },
    {
      code: 'suggestion',
      subject: 'architecture',
      message: 'Optional improvement',
      severity: 'review',
    },
  ];
  candidate.report.semantic_batch.observations = [
    {
      id: 'wording',
      subjects: ['cohesion_and_scope'],
      kind: 'editorial',
      reason: 'Editorial observation',
      basis: 'model_inference',
      execution_evidence_ids: [],
      evidence_refs: [],
    },
  ];
  const before = structuredClone(candidate);
  const ui = mount(candidate);
  try {
    const unresolved = ui.root.querySelector('.candidate-findings');
    assert.equal(unresolved.querySelectorAll('li').length, 3);
    assert.equal(cards(ui.root).length, 2);
    assert.match(unresolved.textContent, /Programmatic finding/);
    assert.doesNotMatch(unresolved.textContent, /Optional improvement|Editorial observation/);
    const advisory = [...ui.root.querySelectorAll('.candidate-review-details')].find((details) =>
      details.querySelector('summary').textContent.includes('非阻断建议'),
    );
    assert.match(advisory.textContent, /非阻断建议 · 1 项.*Optional improvement/s);
    assert.deepEqual(
      planReviewDisclosure(candidate).observations,
      candidate.report.semantic_batch.observations,
    );
    assert.deepEqual(candidate, before);
  } finally {
    ui.close();
  }
});
