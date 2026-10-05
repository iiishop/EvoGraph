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
