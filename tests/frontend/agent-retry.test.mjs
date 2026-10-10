import {
  composerDocumentUrl,
  composerEditorStubUrl,
  messageContentStubUrl,
} from './helpers/composer-fixtures.mjs';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const code = ts
  .transpileModule(
    readFileSync(new URL('../../frontend/src/lib/agentRetry.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
  )
  .outputText.replace(
    /(['"])(?:\.\.\/lib\/|\.\/|\.\.\/\.\.\/lib\/)composerDocument\1/g,
    JSON.stringify(composerDocumentUrl),
  );
const { planAgentRetry } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);
const question = (extra = {}) => ({
  id: 'Q1',
  prompt: '选择哪条路线？',
  context: '保留已有模块',
  verification_milestone: 'M1',
  ...extra,
});
const failure = (extra = {}) => ({
  id: 1,
  text: ' \n采用路线 A \n ',
  ids: ['asset'],
  questionId: 'Q1',
  question: question(),
  verificationMilestone: 'M1',
  ...extra,
});
const current = (extra = {}) => ({ question: null, milestones: [{ id: 'M1' }], ...extra });

test('a still-pending identical question uses the original answer and exact current verification target', () => {
  const saved = failure();
  const result = planAgentRetry(saved, current({ question: question() }));
  assert.deepEqual(result, {
    kind: 'ready',
    request: { text: '采用路线 A', questionId: 'Q1', verificationMilestone: 'M1' },
  });
  assert.equal(saved.text, ' \n采用路线 A \n ');
  assert.equal(
    planAgentRetry(
      saved,
      current({ question: question({ verification_milestone: null }), milestones: [] }),
    ).request.verificationMilestone,
    undefined,
  );
});

test('a consumed answer continues from current state with original question context and no stale question ID', () => {
  const saved = failure();
  const result = planAgentRetry(saved, current());
  assert.equal(result.kind, 'ready');
  assert.equal(result.request.questionId, undefined);
  assert.equal(result.request.verificationMilestone, 'M1');
  for (const text of ['当前已保存', '不要重放', '选择哪条路线？', '保留已有模块', '采用路线 A'])
    assert.ok(result.request.text.includes(text), text);
  assert.equal(saved.text, ' \n采用路线 A \n ');
  assert.equal(planAgentRetry(saved, current()).request.text, result.request.text);
});

test('a different pending question blocks old answers and ordinary failed requests equally', () => {
  for (const saved of [failure(), failure({ questionId: undefined, question: undefined })]) {
    const result = planAgentRetry(saved, current({ question: question({ id: 'Q2' }) }));
    assert.equal(result.kind, 'blocked');
    assert.equal(result.differentQuestion, true);
    assert.match(result.message, /不会复用旧回答/);
    assert.equal(result.request, undefined);
  }
});

test('ordinary failed requests are resent unchanged when no question intervenes', () => {
  assert.deepEqual(
    planAgentRetry(
      failure({ questionId: undefined, question: undefined, verificationMilestone: undefined }),
      current(),
    ),
    {
      kind: 'ready',
      request: { text: '采用路线 A', questionId: undefined, verificationMilestone: undefined },
    },
  );
});

test('missing verification milestones block safely; source-only IDs cannot substitute for delivery milestones', () => {
  for (const state of [
    current({ milestones: [] }),
    current({ milestones: [], source_milestones: [{ id: 'M1' }] }),
    current({ question: question(), milestones: [] }),
  ]) {
    const result = planAgentRetry(failure(), state);
    assert.equal(result.kind, 'blocked');
    assert.match(result.message, /M1.*已不存在/);
  }
  assert.equal(
    planAgentRetry(failure(), current({ milestones: [{ id: 'M1', archived: true }] })).kind,
    'ready',
    'Match backend milestone lookup, not a stricter UI status filter',
  );
});

test('oversized recovery context is never sent or silently truncated', () => {
  const saved = failure({ text: 'x'.repeat(16000) });
  const result = planAgentRetry(saved, current());
  assert.equal(result.kind, 'blocked');
  assert.match(result.message, /16000/);
  assert.equal(saved.text.length, 16000);
  assert.equal(planAgentRetry(saved, current({ question: question() })).kind, 'ready');
});

test('legacy failures without a captured prompt remain honest and do not invent question content', () => {
  const result = planAgentRetry(failure({ question: undefined }), current());
  assert.equal(result.kind, 'ready');
  assert.match(result.request.text, /原问题内容未保留/);
  assert.match(result.request.text, /Q1/);
});

test('document retries preserve atomic references and build recovery context from the immutable original each time', () => {
  const document = {
    version: 1,
    parts: [
      { type: 'text', text: ' \n采用 ' },
      { type: 'reference', kind: 'milestone', id: 'M1', project_id: 'A', label: '路线 A' },
      { type: 'text', text: ' \n' },
    ],
  };
  const saved = failure({ text: ' \n采用 #路线 A \n', composerDocument: document });
  const original = JSON.stringify(saved);
  const first = planAgentRetry(saved, current()),
    second = planAgentRetry(saved, current());
  assert.equal(first.kind, 'ready');
  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(saved), original);
  assert.equal(
    first.request.composerDocument.parts.filter((part) => part.type === 'reference').length,
    1,
  );
  assert.equal(first.request.composerDocument.parts.at(-1).id, 'M1');
  assert.equal(first.request.text.match(/原回答：/g).length, 1);
  const render = (part) => (part.type === 'text' ? part.text : `#${part.label}`);
  assert.equal(first.request.text, first.request.composerDocument.parts.map(render).join(''));
  assert.equal(
    planAgentRetry(saved, current({ question: question({ id: 'Q2' }) })).kind,
    'blocked',
  );
  const pending = planAgentRetry(saved, current({ question: question() }));
  assert.equal(pending.request.text, '采用 #路线 A');
  assert.equal(pending.request.questionId, 'Q1');
});


test('complete-change failed input is terminal and cannot fall through to ordinary retry', () => {
  const saved = failure({ planningJob: {
    action: 'start', job_id: 'complete-change-1', mode: 'bounded-complete-change/v1',
    limits: { max_phases: 1, max_calls: 2, max_input_bytes: 393216 }, pins: {},
  } });
  const result = planAgentRetry(saved, current());
  assert.equal(result.kind, 'blocked');
  assert.equal(result.request, undefined);
  assert.match(result.message, /不会重试或重发原输入/);
  assert.match(result.message, /重新确认完整变更额度/);
  assert.doesNotMatch(result.message, /面板继续/);
});
