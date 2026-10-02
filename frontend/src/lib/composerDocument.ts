import type {
  ComposerDocument,
  ComposerPart,
  ComposerReferencePart,
  ReferenceItem,
  ReferenceKind,
} from '../types';

export const MAX_COMPOSER_PARTS = 512;
export const MAX_COMPOSER_REFERENCES = 32;
export const MAX_COMPOSER_LENGTH = 16000;
export const referenceKinds: readonly ReferenceKind[] = [
  'milestone',
  'source_milestone',
  'architecture_component',
  'source_component',
  'attachment',
  'repository',
];
export const referenceKindLabels: Record<ReferenceKind, string> = {
  milestone: '规划里程碑',
  source_milestone: 'SRC 源码能力',
  architecture_component: '目标架构组件',
  source_component: 'SRC 源码组件',
  attachment: '项目资料',
  repository: '仓库文件',
};
export const referencePrefix = (kind: ReferenceKind) =>
  kind === 'attachment' || kind === 'repository' ? '@' : '#';
export const referenceKey = (part: Pick<ComposerReferencePart, 'kind' | 'id' | 'project_id'>) =>
  JSON.stringify([part.project_id, part.kind, part.id]);
export function textDocument(text: string): ComposerDocument {
  return { version: 1, parts: text ? [{ type: 'text', text }] : [] };
}
export function cloneComposerDocument(document: ComposerDocument): ComposerDocument;
export function cloneComposerDocument(
  document: ComposerDocument | undefined,
): ComposerDocument | undefined;
export function cloneComposerDocument(document: ComposerDocument | undefined) {
  return document
    ? { version: 1 as const, parts: document.parts.map((part) => ({ ...part })) }
    : undefined;
}
export function renderComposerDocument(document: ComposerDocument): string {
  return document.parts
    .map((part) =>
      part.type === 'text' ? part.text : `${referencePrefix(part.kind)}${part.label}`,
    )
    .join('');
}
export function normalizeComposerDocument(document: ComposerDocument): ComposerDocument {
  const parts: ComposerPart[] = [];
  for (const part of document.parts) {
    if (part.type === 'text') {
      if (!part.text) continue;
      const previous = parts.at(-1);
      if (previous?.type === 'text') previous.text += part.text;
      else parts.push({ ...part });
    } else parts.push({ ...part });
  }
  return { version: 1, parts };
}
export function trimComposerDocument(document: ComposerDocument): ComposerDocument {
  const copy = normalizeComposerDocument(document);
  const first = copy.parts[0],
    last = copy.parts.at(-1);
  if (first?.type === 'text') first.text = first.text.trimStart();
  if (last?.type === 'text') last.text = last.text.trimEnd();
  return normalizeComposerDocument(copy);
}
export function parseComposerDocument(value: unknown): ComposerDocument | null {
  if (!value || typeof value !== 'object') return null;
  const doc = value as Record<string, unknown>;
  if (doc.version !== 1 || !Array.isArray(doc.parts) || doc.parts.length > MAX_COMPOSER_PARTS)
    return null;
  const parts: ComposerPart[] = [];
  let references = 0;
  for (const raw of doc.parts) {
    if (!raw || typeof raw !== 'object') return null;
    const part = raw as Record<string, unknown>;
    if (part.type === 'text' && typeof part.text === 'string')
      parts.push({ type: 'text', text: part.text });
    else if (
      part.type === 'reference' &&
      referenceKinds.includes(part.kind as ReferenceKind) &&
      typeof part.id === 'string' &&
      part.id.length > 0 &&
      part.id.length <= 1024 &&
      typeof part.project_id === 'string' &&
      part.project_id.length > 0 &&
      part.project_id.length <= 64 &&
      typeof part.label === 'string' &&
      part.label.length > 0 &&
      part.label.length <= 1024
    ) {
      if (++references > MAX_COMPOSER_REFERENCES) return null;
      parts.push({
        type: 'reference',
        kind: part.kind as ReferenceKind,
        id: part.id,
        project_id: part.project_id,
        label: part.label,
      });
    } else return null;
  }
  const document: ComposerDocument = { version: 1, parts };
  return renderComposerDocument(document).length <= MAX_COMPOSER_LENGTH ? document : null;
}
export function composerFreeText(document: ComposerDocument | undefined, fallback = ''): string {
  return (
    document
      ? document.parts.flatMap((part) => (part.type === 'text' ? [part.text] : [])).join('')
      : fallback
  )
    .replace(/\s+/gu, ' ')
    .trim();
}
export function documentAttachmentIds(document?: ComposerDocument): string[] {
  return [
    ...new Set(
      document?.parts.flatMap((part) =>
        part.type === 'reference' && part.kind === 'attachment' ? [part.id] : [],
      ) ?? [],
    ),
  ];
}
export function composerDocumentIssue(
  document: ComposerDocument,
  projectId: string,
  catalog: ReferenceItem[] | null,
  explicitIds: string[] = [],
): string | null {
  if (document.parts.length > MAX_COMPOSER_PARTS)
    return `输入结构过长，请合并或缩短内容（最多${MAX_COMPOSER_PARTS}段）。`;
  const references = document.parts.filter(
    (part): part is ComposerReferencePart => part.type === 'reference',
  );
  if (references.length > MAX_COMPOSER_REFERENCES)
    return `每条消息最多引用${MAX_COMPOSER_REFERENCES}处对象，请减少引用。`;
  if (renderComposerDocument(document).length > MAX_COMPOSER_LENGTH)
    return '内容超过16000字符，请缩短后发送。';
  if (references.some((part) => part.project_id !== projectId))
    return '引用属于其他项目，请移除后重新选择当前项目对象。';
  if (references.length && !catalog) return '正在确认引用，请稍后发送。';
  const keys = new Set(catalog?.map(referenceKey) ?? []);
  const missing = references.find((part) => !keys.has(referenceKey(part)));
  if (missing) return `引用「${missing.label}」已失效，请移除或重新选择；草稿仍然保留。`;
  if (new Set([...explicitIds, ...documentAttachmentIds(document)]).size > 6)
    return '本条最多引用6份项目资料，请移除一份附件或行内资料引用。';
  return null;
}
export interface EditorDocumentNode {
  type: string;
  text?: string;
  attrs?: Record<string, unknown>;
  content?: EditorDocumentNode[];
}
export function toEditorDocument(document: ComposerDocument): EditorDocumentNode {
  const content: EditorDocumentNode[] = [];
  for (const part of document.parts) {
    if (part.type === 'reference') {
      const { type, ...attrs } = part;
      content.push({ type: 'projectReference', attrs });
    } else {
      part.text.split('\n').forEach((text, index) => {
        if (index) content.push({ type: 'hardBreak' });
        if (text) content.push({ type: 'text', text });
      });
    }
  }
  return { type: 'doc', content: [{ type: 'paragraph', content }] };
}
export function fromEditorDocument(document: EditorDocumentNode): ComposerDocument {
  const parts: ComposerPart[] = [];
  function visit(node: EditorDocumentNode) {
    if (node.type === 'text') parts.push({ type: 'text', text: node.text ?? '' });
    else if (node.type === 'hardBreak') parts.push({ type: 'text', text: '\n' });
    else if (node.type === 'projectReference') {
      const part = { type: 'reference', ...node.attrs };
      const parsed = parseComposerDocument({ version: 1, parts: [part] });
      if (parsed) parts.push(...parsed.parts);
      else parts.push({ type: 'text', text: String(node.attrs?.label ?? '') });
    } else
      (node.content ?? []).forEach((child, index) => {
        if (node.type === 'doc' && index) parts.push({ type: 'text', text: '\n' });
        visit(child);
      });
  }
  visit(document);
  return normalizeComposerDocument({ version: 1, parts });
}
