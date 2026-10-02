import { Editor, type JSONContent } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import Paragraph from '@tiptap/extension-paragraph';
import Text from '@tiptap/extension-text';
import HardBreak from '@tiptap/extension-hard-break';
import Mention from '@tiptap/extension-mention';
import { UndoRedo } from '@tiptap/extensions/undo-redo';
import { Plugin, PluginKey, NodeSelection, EditorState, TextSelection } from '@tiptap/pm/state';
import {
  exitSuggestion,
  type SuggestionProps,
  type SuggestionKeyDownProps,
} from '@tiptap/suggestion';
import type { ComposerDocument, ComposerReferencePart, ReferenceItem } from '../types';
import {
  fromEditorDocument,
  toEditorDocument,
  renderComposerDocument,
  parseComposerDocument,
  composerDocumentIssue,
  documentAttachmentIds,
  referenceKey,
  referencePrefix,
  referenceKindLabels,
  MAX_COMPOSER_LENGTH,
  MAX_COMPOSER_PARTS,
  MAX_COMPOSER_REFERENCES,
} from './composerDocument';
import { fuzzyMatch } from './referenceMatch';

export const COMPOSER_CLIPBOARD_TYPE = 'application/x-evograph-composer';
const MAX_CLIPBOARD_METADATA = 512000;
const clipboardText = (text: string) => text.replace(/\r\n?/g, '\n');
const escapeClipboardHtml = (text: string) =>
  text.replace(
    /[&<>"]/g,
    (char) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
      })[char]!,
  );
export function readClipboardDocument(data: Pick<DataTransfer, 'getData'>) {
  const plain = clipboardText(data.getData('text/plain'));
  let raw = data.getData(COMPOSER_CLIPBOARD_TYPE);
  const html = data.getData('text/html');
  let marked = Boolean(raw);
  try {
    if (!raw && html.length <= MAX_CLIPBOARD_METADATA) {
      // Native Qt drops custom MIME types but keeps standard HTML. Inspect only
      // our encoded data attribute; never parse or insert untrusted markup.
      const marker = html.match(/<span data-evograph-composer="([^"]+)">/u);
      marked = Boolean(marker);
      if (marker) raw = decodeURIComponent(marker[1]);
    }
    if (!raw || raw.length > MAX_CLIPBOARD_METADATA) return { document: null, marked };
    const document = parseComposerDocument(JSON.parse(raw));
    if (!document || clipboardText(renderComposerDocument(document)) !== plain)
      return { document: null, marked };
    return { document, marked };
  } catch {
    return { document: null, marked };
  }
}

export function matchingReferences(
  catalog: ReferenceItem[] | null,
  trigger: '#' | '@',
  query: string,
) {
  return (catalog ?? []).filter(
    (item) =>
      referencePrefix(item.kind) === trigger &&
      fuzzyMatch(
        `${item.label} ${item.detail} ${item.path} ${item.id} ${referenceKindLabels[item.kind]}`,
        query,
      ),
  );
}
export interface ComposerEditorOptions {
  element: HTMLElement;
  document: ComposerDocument;
  projectId: () => string;
  catalog: () => ReferenceItem[] | null;
  attachments: () => string[];
  editable: boolean;
  ariaLabel: string;
  placeholder: string;
  onChange: (document: ComposerDocument) => void;
  onSubmit: () => void;
  onError: (message: string) => void;
  onRequestCatalog: () => void;
  onSuggestion: (trigger: '#' | '@', props: SuggestionProps<ReferenceItem>) => void;
  onSuggestionKey: (props: SuggestionKeyDownProps) => boolean;
  onCloseSuggestion: () => void;
  onInspect: (reference: ComposerReferencePart, rect: DOMRect) => void;
  onEscape?: () => boolean;
}
export function createComposerEditor(options: ComposerEditorOptions) {
  const keys = [new PluginKey('evograph-hash-reference'), new PluginKey('evograph-at-reference')];
  const nodeViews = new Set<() => void>();
  let suggestionOpen = false;
  let editor: Editor;
  const Reference = Mention.extend({
    name: 'projectReference',
    selectable: true,
    addAttributes() {
      return { ...this.parent?.(), kind: { default: 'milestone' }, project_id: { default: '' } };
    },
    addNodeView() {
      return ({ node: initial, getPos }) => {
        let node = initial;
        const dom = document.createElement('span');
        dom.contentEditable = 'false';
        const paint = () => {
          const reference = node.attrs as ComposerReferencePart;
          const current = options
            .catalog()
            ?.find((item) => referenceKey(item) === referenceKey(reference));
          const invalid = options.catalog() !== null && !current;
          dom.className = `composer-reference ${referencePrefix(reference.kind) === '@' ? 'material' : 'object'}${invalid ? ' invalid' : ''}`;
          dom.dataset.referenceKey = referenceKey(reference);
          dom.dataset.invalid = String(invalid);
          dom.textContent = `${referencePrefix(reference.kind)}${current?.label ?? reference.label}`;
          dom.title = `${invalid ? '引用已失效 · ' : ''}${referenceKindLabels[reference.kind]} · ${current?.path || reference.id}`;
          dom.setAttribute('aria-label', `${dom.textContent}，${dom.title}`);
        };
        paint();
        nodeViews.add(paint);
        dom.addEventListener('mousedown', (event) => {
          event.preventDefault();
          const position = getPos();
          if (typeof position === 'number') editor.chain().focus().setNodeSelection(position).run();
          options.onInspect(
            {
              type: 'reference',
              kind: node.attrs.kind,
              id: node.attrs.id,
              project_id: node.attrs.project_id,
              label: node.attrs.label,
            },
            dom.getBoundingClientRect(),
          );
        });
        return {
          dom,
          update(next) {
            if (next.type !== node.type) return false;
            node = next;
            paint();
            return true;
          },
          ignoreMutation: () => true,
          destroy: () => nodeViews.delete(paint),
        };
      };
    },
  }).configure({
    deleteTriggerWithBackspace: true,
    renderText: ({ node }) => `${referencePrefix(node.attrs.kind)}${node.attrs.label}`,
    renderHTML: ({ node }) => [
      'span',
      { 'data-type': 'projectReference', class: 'composer-reference' },
      `${referencePrefix(node.attrs.kind)}${node.attrs.label}`,
    ],
    suggestions: (['#', '@'] as const).map((trigger, index) => ({
      char: trigger,
      pluginKey: keys[index],
      allowedPrefixes: null,
      items: ({ query }) => matchingReferences(options.catalog(), trigger, query),
      command: ({ editor: activeEditor, range, props }) => {
        const item = props as ReferenceItem;
        const current = options
          .catalog()
          ?.find((candidate) => referenceKey(candidate) === referenceKey(item));
        if (!current || current.project_id !== options.projectId()) {
          options.onError('这个引用已变化，请重新选择。');
          return;
        }
        const reference: ComposerReferencePart = {
          type: 'reference',
          kind: current.kind,
          id: current.id,
          project_id: current.project_id,
          label: current.label,
        };
        activeEditor
          .chain()
          .focus()
          .insertContentAt(range, [
            { type: 'projectReference', attrs: { ...reference, mentionSuggestionChar: trigger } },
            { type: 'text', text: ' ' },
          ])
          .run();
      },
      render: () => ({
        onStart(props) {
          suggestionOpen = true;
          options.onRequestCatalog();
          options.onSuggestion(trigger, props);
        },
        onUpdate(props) {
          options.onSuggestion(trigger, props);
        },
        onKeyDown(props) {
          if (props.event.isComposing || props.event.keyCode === 229 || props.view.composing)
            return false;
          return options.onSuggestionKey(props);
        },
        onExit() {
          suggestionOpen = false;
          options.onCloseSuggestion();
        },
      }),
    })),
  });
  const limits = new Plugin({
    filterTransaction(transaction) {
      if (!transaction.docChanged) return true;
      const document = fromEditorDocument(transaction.doc.toJSON());
      const references = document.parts.filter((part) => part.type === 'reference').length;
      const message =
        renderComposerDocument(document).length > MAX_COMPOSER_LENGTH
          ? '内容最多16000字符，粘贴或输入未应用。'
          : document.parts.length > MAX_COMPOSER_PARTS
            ? '输入段落过多，请减少内容后重试。'
            : references > MAX_COMPOSER_REFERENCES
              ? '每条消息最多引用32处对象，请减少引用。'
              : new Set([...options.attachments(), ...documentAttachmentIds(document)]).size > 6
                ? '本条最多引用6份项目资料，请移除一份后再添加。'
                : '';
      if (message) options.onError(message);
      return !message;
    },
  });
  const closeSuggestions = () => {
    if (!editor || editor.isDestroyed) return;
    keys.forEach((key) => exitSuggestion(editor.view, key));
    suggestionOpen = false;
    options.onCloseSuggestion();
  };
  function copySelection(event: ClipboardEvent, cut: boolean) {
    if (!event.clipboardData || editor.state.selection.empty) return false;
    const content = editor.state.selection.content().content.toJSON() ?? [];
    const block = content.some((node: JSONContent) => node.type === 'paragraph');
    const selected = fromEditorDocument({
      type: 'doc',
      content: block ? content : [{ type: 'paragraph', content }],
    });
    const plain = renderComposerDocument(selected);
    const metadata = JSON.stringify(selected);
    event.clipboardData.setData('text/plain', plain);
    event.clipboardData.setData(COMPOSER_CLIPBOARD_TYPE, metadata);
    event.clipboardData.setData(
      'text/html',
      `<span data-evograph-composer="${encodeURIComponent(metadata)}">${escapeClipboardHtml(plain)}</span>`,
    );
    event.preventDefault();
    if (cut) editor.view.dispatch(editor.state.tr.deleteSelection().scrollIntoView());
    return true;
  }
  editor = new Editor({
    element: options.element,
    content: toEditorDocument(options.document) as JSONContent,
    editable: options.editable,
    extensions: [Document, Paragraph, Text, HardBreak, UndoRedo, Reference],
    editorProps: {
      attributes: {
        id: 'agent-message',
        role: 'combobox',
        'aria-multiline': 'true',
        'aria-autocomplete': 'list',
        'aria-expanded': 'false',
        'aria-label': options.ariaLabel,
        'data-placeholder': options.placeholder,
      },
      transformPastedHTML: () => '',
      handleKeyDown(view, event) {
        if (event.isComposing || event.keyCode === 229 || view.composing) return false;
        if (event.key === 'Backspace' || event.key === 'Delete') {
          const selection = view.state.selection;
          if (
            selection instanceof NodeSelection &&
            selection.node.type.name === 'projectReference'
          ) {
            event.preventDefault();
            view.dispatch(view.state.tr.deleteSelection().scrollIntoView());
            return true;
          }
          if (selection.empty) {
            const node =
              event.key === 'Backspace' ? selection.$from.nodeBefore : selection.$from.nodeAfter;
            if (node?.type.name === 'projectReference') {
              const position =
                event.key === 'Backspace' ? selection.from - node.nodeSize : selection.from;
              event.preventDefault();
              view.dispatch(
                view.state.tr.setSelection(NodeSelection.create(view.state.doc, position)),
              );
              return true;
            }
          }
        }
        if (event.key === 'Escape' && !suggestionOpen && options.onEscape?.()) {
          event.preventDefault();
          return true;
        }
        if (
          event.key === 'Enter' &&
          !event.shiftKey &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.altKey &&
          !suggestionOpen
        ) {
          event.preventDefault();
          options.onSubmit();
          return true;
        }
        return false;
      },
      handlePaste(view, event) {
        if (event.defaultPrevented || event.clipboardData?.files.length) return true;
        const { document: copied, marked } = event.clipboardData
          ? readClipboardDocument(event.clipboardData)
          : { document: null, marked: false };
        if (
          copied &&
          !composerDocumentIssue(
            copied,
            options.projectId(),
            options.catalog(),
            options.attachments(),
          )
        ) {
          editor.commands.insertContentAt(
            { from: view.state.selection.from, to: view.state.selection.to },
            toEditorDocument(copied).content![0].content as JSONContent[],
          );
        } else {
          const text = event.clipboardData?.getData('text/plain') ?? '';
          if (!text && event.clipboardData?.getData('text/html'))
            options.onError('请以纯文本粘贴；草稿未改变。');
          else if (text) {
            editor.commands.insertContentAt(
              { from: view.state.selection.from, to: view.state.selection.to },
              toEditorDocument({
                version: 1,
                parts: [{ type: 'text', text: text.replace(/\r\n?/g, '\n') }],
              }).content![0].content as JSONContent[],
            );
            if (marked) options.onError('粘贴中的引用未通过当前项目校验，已作为普通文字插入。');
          } else if (marked) options.onError('粘贴中的引用未通过当前项目校验，草稿未改变。');
        }
        event.preventDefault();
        return true;
      },
      handleDrop: (view, event) => (event.dataTransfer?.files.length ? true : !view.dragging),
      handleDOMEvents: {
        keydown: (_, event) => {
          if (['Backspace', 'Delete', 'Enter'].includes(event.key)) event.stopPropagation();
          return false;
        },
        copy: (_, event) => copySelection(event, false),
        cut: (_, event) => copySelection(event, true),
        blur: () => {
          closeSuggestions();
          return false;
        },
      },
    },
    onTransaction: ({ editor: current }) => {
      current.view.dom.dataset.empty = String(current.isEmpty);
    },
    onUpdate: ({ editor: current }) =>
      options.onChange(
        fromEditorDocument(current.getJSON() as ReturnType<typeof toEditorDocument>),
      ),
  });
  editor.registerPlugin(limits);
  editor.view.dom.dataset.empty = String(editor.isEmpty);
  function replaceDocument(document: ComposerDocument) {
    // A send-clear, recovery restore or other external draft replacement starts
    // a new editing session. Do not let Undo revive a submitted/other draft.
    closeSuggestions();
    const doc = editor.schema.nodeFromJSON(toEditorDocument(document));
    editor.view.updateState(
      EditorState.create({
        schema: editor.schema,
        doc,
        selection: TextSelection.atEnd(doc),
        plugins: editor.state.plugins,
      }),
    );
    editor.view.dom.dataset.empty = String(editor.isEmpty);
  }
  return {
    editor,
    closeSuggestions,
    replaceDocument,
    refreshReferences: () => nodeViews.forEach((paint) => paint()),
  };
}
