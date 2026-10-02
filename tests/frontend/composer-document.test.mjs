import test from 'node:test';
import assert from 'node:assert/strict';
import { composerDocumentUrl } from './helpers/composer-fixtures.mjs';
const {
  textDocument,
  renderComposerDocument,
  cloneComposerDocument,
  normalizeComposerDocument,
  trimComposerDocument,
  parseComposerDocument,
  toEditorDocument,
  fromEditorDocument,
  referenceKey,
  composerDocumentIssue,
  documentAttachmentIds,
} = await import(composerDocumentUrl);
const reference = (extra = {}) => ({
  type: 'reference',
  kind: 'milestone',
  id: 'M1',
  project_id: 'P1',
  label: '查询API',
  ...extra,
});
const catalog = (parts) =>
  parts
    .filter((part) => part.type === 'reference')
    .map((part) => ({ ...part, name: part.label, detail: '当前对象', path: '' }));

test('document and editor roundtrip preserve whitespace, Unicode, newlines and typed identities', () => {
  const doc = {
    version: 1,
    parts: [
      { type: 'text', text: ' \n你好😀 ' },
      reference(),
      { type: 'text', text: '\n\n请参考 ' },
      reference({ kind: 'attachment', id: 'A1', label: '需求.md' }),
      { type: 'text', text: '\n ' },
    ],
  };
  assert.equal(renderComposerDocument(doc), ' \n你好😀 #查询API\n\n请参考 @需求.md\n ');
  assert.deepEqual(fromEditorDocument(toEditorDocument(doc)), doc);
  const clone = cloneComposerDocument(doc);
  clone.parts[1].label = '外部修改';
  assert.equal(doc.parts[1].label, '查询API');
  assert.equal(
    renderComposerDocument(trimComposerDocument(doc)),
    '你好😀 #查询API\n\n请参考 @需求.md',
  );
  assert.equal(renderComposerDocument(doc).startsWith(' \n'), true);
});

test('plain prefixes never acquire identity and adjacent text normalizes without touching references', () => {
  const doc = {
    version: 1,
    parts: [
      { type: 'text', text: '#查询API' },
      { type: 'text', text: ' @某个词' },
      reference({ id: 'same-label' }),
      reference({ id: 'different-id' }),
    ],
  };
  const normalized = normalizeComposerDocument(doc);
  assert.equal(normalized.parts.length, 3);
  assert.equal(normalized.parts[0].type, 'text');
  assert.notEqual(referenceKey(normalized.parts[1]), referenceKey(normalized.parts[2]));
  assert.equal(
    parseComposerDocument({
      version: 1,
      parts: [{ type: 'html', html: '<script>bad()</script>' }],
    }),
    null,
  );
});

test('current catalog validation never rebinds missing or foreign IDs by label', () => {
  const doc = { version: 1, parts: [reference()] };
  assert.match(composerDocumentIssue(doc, 'P1', null), /正在确认/);
  assert.match(composerDocumentIssue(doc, 'P1', catalog([reference({ id: 'M2' })])), /失效/);
  assert.match(composerDocumentIssue(doc, 'P2', catalog(doc.parts)), /其他项目/);
  assert.equal(
    composerDocumentIssue(doc, 'P1', catalog([reference({ label: '重命名的API' })])),
    null,
  );
  assert.equal(doc.parts[0].label, '查询API');
});

test('explicit files and inline files count as one deduplicated six-file union', () => {
  const doc = {
    version: 1,
    parts: [
      reference({ kind: 'attachment', id: 'A1' }),
      reference({ kind: 'attachment', id: 'A1' }),
      reference({ kind: 'repository', id: 'repo:a.txt' }),
    ],
  };
  assert.deepEqual(documentAttachmentIds(doc), ['A1']);
  assert.equal(
    composerDocumentIssue(doc, 'P1', catalog(doc.parts), ['A1', 'A2', 'A3', 'A4', 'A5', 'A6']),
    null,
  );
  assert.match(
    composerDocumentIssue(doc, 'P1', catalog(doc.parts), ['A2', 'A3', 'A4', 'A5', 'A6', 'A7']),
    /6份/,
  );
});

test('document limits reject extra identities and long content instead of silently truncating', () => {
  const doc = {
    version: 1,
    parts: Array.from({ length: 33 }, (_, i) => reference({ id: `M${i}` })),
  };
  assert.equal(parseComposerDocument(doc), null);
  assert.match(composerDocumentIssue(doc, 'P1', catalog(doc.parts)), /32处/);
  assert.equal(parseComposerDocument(textDocument('x'.repeat(16000)))?.parts[0].text.length, 16000);
  assert.equal(parseComposerDocument(textDocument('x'.repeat(16001))), null);
  assert.equal(
    parseComposerDocument({
      version: 1,
      parts: Array.from({ length: 513 }, () => ({ type: 'text', text: 'x' })),
    }),
    null,
  );
});

test('typed clipboard token fields enforce transport bounds before reference admission', () => {
  for (const [field, limit] of [
    ['id', 1024],
    ['project_id', 64],
    ['label', 1024],
  ]) {
    assert.ok(
      parseComposerDocument({ version: 1, parts: [reference({ [field]: 'x'.repeat(limit) })] }),
    );
    assert.equal(
      parseComposerDocument({ version: 1, parts: [reference({ [field]: 'x'.repeat(limit + 1) })] }),
      null,
    );
  }
});
