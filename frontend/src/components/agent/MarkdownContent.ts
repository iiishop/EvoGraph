import { defineComponent, h, type VNodeChild } from 'vue';
import MarkdownIt from 'markdown-it';
import type Token from 'markdown-it/lib/token.mjs';

// Parse CommonMark plus tables, but never hand model output to an HTML sink.
// Only the elements and attributes below can be constructed; raw HTML is text.
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true });
const tags = new Set([
  'p',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'blockquote',
  'strong',
  'em',
  's',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
]);

export function safeMarkdownHref(value: string | null): string | undefined {
  if (!value || /[\u0000-\u0020\u007f]/.test(value)) return;
  // Do not resolve relative paths against the desktop app or accept local files,
  // protocol-relative URLs, data URLs, or application-specific URL handlers.
  if (!/^(?:https?:\/\/|mailto:)/i.test(value)) return;
  try {
    const url = new URL(value);
    if (['https:', 'http:', 'mailto:'].includes(url.protocol)) return value;
  } catch {
    // A malformed destination remains readable, without becoming a link.
  }
}
markdown.validateLink = (value) => Boolean(safeMarkdownHref(value));

// The compact tile uses the same grammar as the reader. Strip structural
// Markdown by token type, never punctuation patterns that could corrupt code,
// escaped characters or URLs. The saved source remains untouched.
export function markdownTextPreview(content: string): string {
  function text(tokens: Token[]): string {
    return tokens
      .map((token) => {
        if (token.type === 'inline') return text(token.children ?? []);
        if (token.type === 'image')
          return `图片：${token.children?.length ? text(token.children) : token.content}`;
        if (token.type === 'text' || token.type === 'code_inline') return token.content;
        if (token.type === 'fence' || token.type === 'code_block') return ` ${token.content} `;
        if (token.type === 'softbreak' || token.type === 'hardbreak') return ' ';
        return token.block && token.nesting !== 1 ? ' ' : '';
      })
      .join('');
  }
  return text(markdown.parse(content, {})).replace(/\s+/gu, ' ').trim();
}

function renderTokens(tokens: Token[]): VNodeChild[] {
  let position = 0;
  function read(): VNodeChild[] {
    const children: VNodeChild[] = [];
    while (position < tokens.length) {
      const token = tokens[position++]!;
      if (token.nesting === -1) break;
      if (token.type === 'inline') {
        children.push(...renderTokens(token.children ?? []));
      } else if (token.nesting === 1) {
        const body = read();
        if (token.hidden) {
          children.push(...body);
          continue;
        }
        const attrs: Record<string, unknown> = {};
        let tag = tags.has(token.tag) ? token.tag : 'span';
        if (token.type === 'link_open') {
          const href = safeMarkdownHref(token.attrGet('href'));
          if (href) {
            tag = 'a';
            Object.assign(attrs, {
              href,
              target: '_blank',
              rel: 'noopener noreferrer',
              referrerpolicy: 'no-referrer',
            });
            const title = token.attrGet('title');
            if (title) attrs.title = title;
          }
        }
        if (tag === 'ol') {
          const start = token.attrGet('start');
          if (start && /^\d{1,9}$/.test(start)) attrs.start = Number(start);
        }
        if (tag === 'th' || tag === 'td') {
          const align = /^text-align:(left|center|right)$/.exec(token.attrGet('style') ?? '');
          if (align) attrs.style = { textAlign: align[1] };
        }
        const node = h(tag, attrs, body);
        children.push(
          tag === 'table'
            ? h(
                'div',
                {
                  class: 'markdown-table-scroll',
                  tabindex: 0,
                  role: 'region',
                  'aria-label': '表格，可横向滚动',
                },
                [node],
              )
            : node,
        );
      } else if (token.type === 'fence' || token.type === 'code_block') {
        const language = token.info.trim().split(/\s+/)[0] ?? '';
        children.push(
          h('pre', { tabindex: 0, 'aria-label': language ? `${language} 代码` : '代码' }, [
            h('code', {}, token.content),
          ]),
        );
      } else if (token.type === 'code_inline') {
        children.push(h('code', {}, token.content));
      } else if (token.type === 'hardbreak' || token.type === 'softbreak') {
        children.push(h('br'));
      } else if (token.type === 'hr') {
        children.push(h('hr'));
      } else if (token.type === 'image') {
        // Images can contain tracking URLs. Keep their alt text without ever
        // creating an img/src, fetching a URL, or embedding active content.
        children.push(
          h(
            'span',
            { class: 'markdown-image', title: '外部图片未自动加载' },
            `［图片：${token.content || '未提供说明'}］`,
          ),
        );
      } else {
        children.push(token.content);
      }
    }
    return children;
  }
  return read();
}

export default defineComponent({
  name: 'MarkdownContent',
  props: { content: { type: String, required: true } },
  setup: (props) => () =>
    h('div', { class: 'markdown-body' }, renderTokens(markdown.parse(props.content, {}))),
});
