import test from 'node:test';
import assert from 'node:assert/strict';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';
const { planReviewDisclosure } = await import(moduleUrl(compile(source('lib/planPreview.ts'))));

test('retained acceptance check names structural coverage without claiming semantic proof', () => {
  const candidate = {
    status: 'reviewing', candidate_hash: 'pinned', report: { findings: [] },
    validation_receipt: {
      schema_version: 'planning-validation/v1', candidate_hash: 'pinned',
      structural: { status: 'clear', finding_count: 0 },
      model: { kind: 'model_opinion', status: 'not_run', subject_count: 0 },
      implementation: { status: 'not_run', existing_record_count: 0 },
      harness: {
        schema_version: 'planning-harness-disclosure/v1', snapshot_id: 'snapshot',
        policy_version: 'plan-harness-policy/v7', decision: 'hold',
        checks: [{ id: 'retained_acceptance', kind: 'deterministic', version: '1',
          execution: 'completed', verdict: 'pass', findings: 0, message: '' }],
      },
    },
  };
  const check = planReviewDisclosure(candidate).harness.checks[0];
  assert.equal(check.name, '既有验收保留');
  assert.match(check.status, /结构覆盖检查通过；语义保留仍需独立评审/);
});
