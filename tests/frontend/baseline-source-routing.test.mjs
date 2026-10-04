import test from 'node:test';
import assert from 'node:assert/strict';
import { compile, source, moduleUrl } from './helpers/architecture-fixtures.mjs';

let sequence = 0;
async function harness() {
  const environment =
    moduleUrl(`export const env = { calls: [], errors: [], updated: null, provider: {} };
    export const useWorkspace = () => ({ state: { settings: { get provider() { return env.provider; } } },
      perform: async (...args) => { env.calls.push(['perform', ...args]); return env.updated; },
      setError: message => env.errors.push(message) });
    export const useAgent = () => ({ send: async (...args) => { env.calls.push(['send', ...args]); return true; } });
    // ${++sequence}`);
  const { useBaseline } = await import(
    moduleUrl(
      compile(source('composables/useBaseline.ts')).replace(
        /from (['"])([^'"]+)\1/g,
        () => `from ${JSON.stringify(environment)}`,
      ),
    )
  );
  const { env } = await import(environment);
  return { baseline: useBaseline(), env };
}
const project = (extra = {}) => ({
  id: 'P1',
  unified_planning: true,
  question: null,
  baselines: [{ id: 'B1', complete: true }],
  source_analysis_baseline_id: '',
  ...extra,
});

test('manual source reconstruction explicitly opts into the source-only route without shifting positional arguments', async () => {
  const { baseline, env } = await harness();
  await baseline.reconstruct(project());
  const [
    kind,
    projectId,
    content,
    questionId,
    attachments,
    verification,
    document,
    submission,
    sourceAnalysis,
  ] = env.calls[0];
  assert.equal(kind, 'send');
  assert.equal(projectId, 'P1');
  assert.match(content, /reconstruct_baseline_milestones/);
  assert.equal(questionId, undefined);
  assert.deepEqual(attachments, []);
  assert.equal(verification, undefined);
  assert.equal(document, undefined);
  assert.equal(submission, undefined);
  assert.equal(sourceAnalysis, true);
});

test('automatic reconstruction after baseline refresh uses the same explicit source route', async () => {
  const { baseline, env } = await harness();
  env.updated = project();
  await baseline.refresh(project());
  assert.deepEqual(env.calls[0], [
    'perform',
    'baseline.refresh',
    { project_id: 'P1' },
    '仓库基线已刷新',
  ]);
  assert.equal(env.calls[1][0], 'send');
  assert.equal(env.calls[1].at(-1), true);
});

test('source reconstruction retains existing provider, question and baseline-completeness guards', async () => {
  const { baseline, env } = await harness();
  env.provider = null;
  await baseline.reconstruct(project());
  assert.equal(env.calls.length, 0);
  assert.match(env.errors.at(-1), /Provider/);
  env.provider = {};
  await baseline.reconstruct(project({ question: { id: 'Q1' } }));
  assert.equal(env.calls.length, 0);
  assert.match(env.errors.at(-1), /待确认问题/);
  for (const updated of [
    project({ baselines: [{ id: 'B1', complete: false }] }),
    project({ source_analysis_baseline_id: 'B1' }),
  ]) {
    env.updated = updated;
    await baseline.refresh(project());
  }
  assert.ok(env.calls.every(([kind]) => kind === 'perform'));
});
