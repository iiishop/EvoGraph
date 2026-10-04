import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(
  new URL('../../frontend/src/lib/turnSummary.ts', import.meta.url),
  'utf8',
);
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { selectTurnReceipt, readTurnReceipt } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);
const summary = (turn, overrides = {}) => ({
  version: 1,
  turn_id: turn,
  status: 'failed',
  changed: false,
  before_revision: 32,
  after_revision: 32,
  changes: {
    milestones: { added: [], updated: [], removed: [] },
    dependencies: { added: [], updated: [], removed: [] },
    target: null,
    architecture: null,
    other: [],
  },
  ...overrides,
});
const event = (id, detail = summary(id)) => ({
  id,
  kind: 'agent_turn_finished',
  created_at: '2026-10-04T00:47:04Z',
  detail: typeof detail === 'string' ? detail : JSON.stringify(detail),
});
const project = () => ({
  id: 'P',
  created_at: 'incarnation-1',
  events: [event('latest'), event('old')],
});

test('selection resolves chosen immutable event identity and never latest or same-index replacement', () => {
  const p = project();
  const original = JSON.stringify(p);
  const selection = selectTurnReceipt(p, 'old');
  assert.equal(readTurnReceipt(p, selection).summary.turn_id, 'old');
  p.events.unshift(event('newer'));
  assert.equal(readTurnReceipt(p, selection).summary.turn_id, 'old');
  p.events = p.events.filter((e) => e.id !== 'old');
  assert.equal(readTurnReceipt(p, selection).summary, null);
  assert.match(readTurnReceipt(p, selection).unavailable, /不在当前记录/);
  p.events.push(event('old', summary('replacement')));
  assert.equal(readTurnReceipt(p, selection).summary, null);
  p.events = JSON.parse(original).events;
  assert.equal(readTurnReceipt(p, selection).summary.turn_id, 'old');
  assert.equal(JSON.stringify(p), original);
  assert.equal(selectTurnReceipt(p, 'missing'), null);
  p.events.push(event('old'));
  assert.equal(selectTurnReceipt(p, 'old'), null);
  assert.equal(readTurnReceipt(p, selection).summary, null);
});

test('legacy valid, missing, malformed, future schema and project mismatch receipts fail safely', () => {
  const p = project();
  for (const detail of [
    '',
    '{bad',
    '[]',
    JSON.stringify({ version: 1 }),
    summary('future', { version: 2 }),
  ]) {
    p.events = [event('legacy', detail), event('latest')];
    const result = readTurnReceipt(p, selectTurnReceipt(p, 'legacy'));
    assert.equal(result.summary, null);
    assert.match(result.unavailable, /未保存可读取/);
  }
  p.events = [event('legacy')];
  const selected = selectTurnReceipt(p, 'legacy');
  assert.equal(
    readTurnReceipt(p, selected).summary.turn_id,
    'legacy',
    'valid v1 without contract_details remains readable',
  );
  assert.equal(readTurnReceipt({ ...p, id: 'Q' }, selected).summary, null);
  assert.equal(readTurnReceipt({ ...p, created_at: 'recreated' }, selected).summary, null);
  p.events = [
    event(
      'mismatch',
      summary('other', {
        contract_details: {
          project_id: 'Q',
          project_created_at: p.created_at,
          before_target_version: null,
          after_target_version: null,
          behaviors: [],
        },
      }),
    ),
  ];
  assert.match(readTurnReceipt(p, selectTurnReceipt(p, 'mismatch')).unavailable, /不匹配/);
});

test('failed partial save, zero changes and stopped outcomes preserve their saved contract exactly', () => {
  const p = project();
  for (const [status, changed] of [
    ['failed', true],
    ['failed', false],
    ['stopped', true],
    ['stopped', false],
    ['completed', true],
  ]) {
    const saved = summary(`${status}-${changed}`, {
      status,
      changed,
      after_revision: changed ? 38 : 32,
      changes: { ...summary('').changes, other: changed ? ['project'] : [] },
    });
    p.events = [event('chosen', saved), event('latest')];
    assert.deepEqual(readTurnReceipt(p, selectTurnReceipt(p, 'chosen')).summary, saved);
  }
});
