import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import ts from 'typescript';
import { markdownContentUrl } from './helpers/markdown-fixtures.mjs';
import { composerDocumentUrl } from './helpers/composer-fixtures.mjs';
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, nextTick, reactive } = await import('vue');
const {
  default: Markdown,
  safeMarkdownHref,
  markdownTextPreview,
} = await import(markdownContentUrl);
const source = readFileSync(
  new URL('../../frontend/src/components/agent/MessageContent.vue', import.meta.url),
  'utf8',
);
const { descriptor } = parse(source);
const imports = {
  vue: import.meta.resolve('vue'),
  './MarkdownContent': markdownContentUrl,
  '../../lib/composerDocument': composerDocumentUrl,
};
const compiled = ts
  .transpileModule(compileScript(descriptor, { id: 'message', inlineTemplate: true }).content, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  .outputText.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
    assert.ok(imports[name], name);
    return `from ${JSON.stringify(imports[name])}`;
  });
const MessageContent = (
  await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
).default;
function mount(content, component = Markdown) {
  const props = reactive(component === Markdown ? { content } : { message: content });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(component, props) });
  app.mount(root);
  return {
    root,
    props,
    dispose() {
      app.unmount();
      root.remove();
    },
  };
}

test('renders headings, nested lists, emphasis, quotes, rules and aligned tables as semantic elements', () => {
  const view = mount(
    '# 标题\n\n## 子标题\n\n1. **重点**\n   - *说明* 与 ~~旧项~~\n\n> 引用\n\n---\n\n| 项目 | 状态 |\n| :--- | ---: |\n| A | 完成 |',
  );
  try {
    assert.equal(view.root.querySelector('h1').textContent, '标题');
    assert.equal(view.root.querySelector('h2').textContent, '子标题');
    assert.equal(view.root.querySelector('ol li ul li em').textContent, '说明');
    assert.equal(view.root.querySelector('strong').textContent, '重点');
    assert.equal(view.root.querySelector('s').textContent, '旧项');
    assert.equal(view.root.querySelector('blockquote').textContent, '引用');
    assert.ok(view.root.querySelector('hr'));
    assert.equal(view.root.querySelectorAll('tbody td').length, 2);
    assert.equal(view.root.querySelector('tbody td:last-child').style.textAlign, 'right');
    assert.equal(view.root.querySelector('.markdown-table-scroll').tabIndex, 0);
  } finally {
    view.dispose();
  }
});

test('fenced, indented and inline code preserve literal text; incomplete fences update safely', async () => {
  const partial = '```html\n<script>alert("x")</script>\n  <div> & text';
  const view = mount(partial);
  try {
    assert.equal(
      view.root.querySelector('pre code').textContent,
      '<script>alert("x")</script>\n  <div> & text',
    );
    assert.equal(view.root.querySelector('pre').tabIndex, 0);
    assert.equal(view.root.querySelector('pre').getAttribute('aria-label'), 'html 代码');
    assert.equal(view.root.querySelector('script'), null);
    view.props.content += '\n```\n\n**完成**';
    await nextTick();
    assert.equal(view.root.querySelectorAll('pre').length, 1);
    assert.equal(view.root.querySelector('strong').textContent, '完成');
    view.props.content = '    const value = 1;\n\n使用 `<tag>`';
    await nextTick();
    assert.equal(view.root.querySelector('pre code').textContent, 'const value = 1;\n');
    assert.equal(view.root.querySelector('p code').textContent, '<tag>');
  } finally {
    view.dispose();
  }
});

test('raw HTML, event handlers and remote image syntax cannot create executable or network-loading nodes', () => {
  const attack =
    '<script>alert(1)</script>\n\n<img src="https://example.com/pixel" onerror="alert(1)">\n\n<iframe src="https://example.com"></iframe>\n\n![说明](https://example.com/tracker.png)\n\n![payload](data:image/svg+xml;base64,AAA)\n\n```html\n<svg onload=alert(1)>\n```';
  const view = mount(attack);
  try {
    assert.equal(view.root.querySelector('script,img,iframe,svg,object,embed,style,link'), null);
    assert.equal(view.root.querySelector('[src],[onerror],[onload],[style]'), null);
    assert.match(view.root.textContent, /<script>alert\(1\)<\/script>/);
    assert.match(view.root.querySelector('.markdown-image').textContent, /说明/);
  } finally {
    view.dispose();
  }
});

test('only explicit web/mail links can navigate and all external links isolate opener and referrer', () => {
  const view = mount(
    '[文档](https://example.com/docs "说明") [邮件](mailto:help@example.com)\n\n[x](javascript:alert%281%29) [x](JaVaScRiPt:alert%281%29) [x](jav&#x61;script:alert%281%29) [x](data:text/html,test) [x](file:///etc/passwd) [x](//example.com) [x](/api/command) [x](vscode://file/test)',
  );
  try {
    const links = [...view.root.querySelectorAll('a')];
    assert.equal(links.length, 2);
    assert.equal(links[0].getAttribute('href'), 'https://example.com/docs');
    assert.equal(links[0].title, '说明');
    for (const link of links) {
      assert.equal(link.target, '_blank');
      assert.equal(link.rel, 'noopener noreferrer');
      assert.equal(link.getAttribute('referrerpolicy'), 'no-referrer');
    }
    for (const value of [
      'javascript:alert(1)',
      'java\nscript:alert(1)',
      '\nhttps://example.com',
      'file:///tmp/a',
      '//example.com',
      '/api/command',
      'data:text/html,x',
      'https://',
    ])
      assert.equal(safeMarkdownHref(value), undefined, value);
  } finally {
    view.dispose();
  }
});

test('raw text and typed user reference labels remain literal; assistant replies use Markdown', async () => {
  const view = mount(
    {
      role: 'user',
      content: '# literal **text** <img src=x>',
      composer_document: {
        version: 1,
        parts: [
          { type: 'text', text: '# literal **text** ' },
          {
            type: 'reference',
            kind: 'milestone',
            project_id: 'A',
            id: 'M1',
            label: '<strong>原始标签</strong>',
          },
        ],
      },
    },
    MessageContent,
  );
  try {
    assert.equal(view.root.querySelector('h1,strong,img'), null);
    assert.equal(
      view.root.querySelector('.message-reference').textContent,
      '#<strong>原始标签</strong>',
    );
    assert.match(view.root.textContent, /# literal \*\*text\*\*/);
    view.props.message = { role: 'assistant', content: '# Markdown\n\n**格式**' };
    await nextTick();
    assert.equal(view.root.querySelector('h1').textContent, 'Markdown');
    assert.equal(view.root.querySelector('strong').textContent, '格式');
  } finally {
    view.dispose();
  }
});

test('compact text extraction follows Markdown tokens without stripping literal code or URL punctuation', () => {
  assert.equal(
    markdownTextPreview('# Heading\n\n**bold** and _emphasis_\n\n- first\n- second'),
    'Heading bold and emphasis first second',
  );
  assert.equal(
    markdownTextPreview(
      '## Use `#tag **literal** a_b | x` with <https://example.com/a_b?q=1#frag>',
    ),
    'Use #tag **literal** a_b | x with https://example.com/a_b?q=1#frag',
  );
  assert.equal(
    markdownTextPreview('```ts\nconst path = "https://example.com/a_b#frag"; // **literal**\n'),
    'const path = "https://example.com/a_b#frag"; // **literal**',
  );
  assert.equal(
    markdownTextPreview('[文档](https://example.com) ![an *image*](https://example.com/a.png)'),
    '文档 图片：an image',
  );
  assert.equal(markdownTextPreview('| A | B |\n| --- | --- |\n| C | D |'), 'A B C D');
  assert.equal(
    markdownTextPreview('\\# escaped &amp; <script>alert(1)</script>'),
    '# escaped & <script>alert(1)</script>',
  );
});
