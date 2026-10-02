import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript, registerTS } from '@vue/compiler-sfc';
import ts from 'typescript';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
});
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, nextTick, reactive } = await import('vue');
registerTS(() => ts);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const filename = new URL(
  '../../frontend/src/components/design/ArchitectureEdge.vue',
  import.meta.url,
);
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname });
const imports = {
  vue: import.meta.resolve('vue'),
  '@vue-flow/core': import.meta.resolve('@vue-flow/core'),
  '../../lib/architectureRouting': moduleUrl(
    compile(
      readFileSync(
        new URL('../../frontend/src/lib/architectureRouting.ts', import.meta.url),
        'utf8',
      ),
    ),
  ),
};
const code = compile(
  compileScript(descriptor, { id: 'edge-regression', inlineTemplate: true }).content,
).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
  assert.ok(imports[name], `Unexpected dependency ${name}`);
  return `from ${JSON.stringify(imports[name])}`;
});
const ArchitectureEdge = (await import(moduleUrl(code))).default;

function mount(label = '完整的架构关系说明') {
  const props = reactive({
    id: 'edge',
    source: 'a',
    target: 'b',
    type: 'architecture',
    sourceNode: {},
    targetNode: {},
    sourcePosition: 'right',
    targetPosition: 'left',
    sourceX: 0,
    sourceY: 0,
    targetX: 200,
    targetY: 0,
    selected: false,
    updatable: false,
    animated: false,
    markerStart: '',
    markerEnd: '',
    events: {},
    label,
    data: {
      route: [
        { x: 0, y: 0 },
        { x: 200, y: 0 },
      ],
      labelPosition: { x: 100, y: -17, width: 80, height: 24, text: '完整…' },
    },
    style: { opacity: 1 },
  });
  const root = document.createElement('div');
  document.body.append(root);
  // The SVG ancestor is essential: a bare body Teleport inherits this namespace
  // unless the component crosses a foreignObject HTML boundary first.
  const app = createApp({ render: () => h('svg', {}, [h(ArchitectureEdge, props)]) });
  app.mount(root);
  return {
    props,
    root,
    hover(x = 30, y = 30) {
      root
        .querySelector('.architecture-edge')
        .dispatchEvent(new window.MouseEvent('mouseenter', { clientX: x, clientY: y }));
    },
    leave() {
      root.querySelector('.architecture-edge').dispatchEvent(new window.MouseEvent('mouseleave'));
    },
    dispose() {
      app.unmount();
      root.remove();
    },
  };
}
const tooltip = () => document.querySelector('.architecture-edge-tooltip');

test('an actual SVG architecture edge teleports a real HTML tooltip with safe full text', async () => {
  const label = '<img src=x onerror=alert(1)>完整关系';
  const view = mount(label);
  try {
    assert.equal(tooltip(), null);
    view.hover(1000, 750);
    await nextTick();
    assert.ok(tooltip() instanceof HTMLElement);
    assert.equal(tooltip().namespaceURI, 'http://www.w3.org/1999/xhtml');
    assert.equal(tooltip().parentElement, document.body);
    assert.equal(tooltip().getAttribute('role'), 'tooltip');
    assert.equal(tooltip().textContent, label);
    assert.equal(tooltip().querySelector('img,script,iframe'), null);
    assert.equal(tooltip().style.left, `${window.innerWidth - 368}px`);
    assert.equal(tooltip().style.top, `${window.innerHeight - 128}px`);
    view.props.label = '更新后的完整关系';
    await nextTick();
    assert.equal(tooltip().textContent, view.props.label);
  } finally {
    view.dispose();
  }
  assert.equal(tooltip(), null, 'unmount removes the body-level tooltip');
});

test('repeated hover/leave never leaves a detached tooltip and clamps top-left placement', async () => {
  const view = mount();
  try {
    for (let i = 0; i < 5; i++) {
      view.hover(-50, -50);
      await nextTick();
      assert.equal(document.querySelectorAll('.architecture-edge-tooltip').length, 1);
      assert.equal(tooltip().style.left, '8px');
      assert.equal(tooltip().style.top, '8px');
      view.leave();
      await nextTick();
      assert.equal(tooltip(), null);
    }
    view.hover();
    await nextTick();
  } finally {
    view.dispose();
  }
  assert.equal(tooltip(), null);
});

test('edge hover and keyboard focus identify endpoints and preserve the entire reserved path', async () => {
  const view = mount('calls');
  const previews = [];
  try {
    view.props.data = {
      ...view.props.data,
      index: 1,
      description: 'Caller → Service：calls',
      onPreview: (index) => previews.push(index),
      route: [
        { x: 0, y: 0 },
        { x: 40, y: 0 },
        { x: 40, y: 80 },
        { x: 160, y: 80 },
        { x: 160, y: 0 },
        { x: 200, y: 0 },
      ],
    };
    await nextTick();
    const edge = view.root.querySelector('.architecture-edge');
    const completePath = view.root.querySelector('.vue-flow__edge-path').getAttribute('d');
    assert.ok(completePath.startsWith('M 0 0'));
    assert.ok(completePath.endsWith('L 200 0'));
    view.hover();
    await nextTick();
    assert.equal(previews.at(-1), 1);
    assert.equal(tooltip().textContent, 'Caller → Service：calls');
    assert.equal(edge.getAttribute('aria-label'), 'Caller → Service：calls');
    view.props.style = { opacity: 1, strokeWidth: 3.2 };
    view.props.data.previewed = true;
    await nextTick();
    assert.ok(edge.classList.contains('is-edge-preview'));
    assert.equal(view.root.querySelector('.vue-flow__edge-path').getAttribute('d'), completePath);
    assert.equal(
      view.root.querySelector('.vue-flow__edge-interaction').getAttribute('d'),
      completePath,
    );
    view.leave();
    await nextTick();
    assert.equal(previews.at(-1), null);
    assert.equal(tooltip(), null);
    edge.dispatchEvent(new window.FocusEvent('focus'));
    await nextTick();
    assert.equal(previews.at(-1), 1);
    assert.ok(tooltip());
    edge.dispatchEvent(new window.FocusEvent('blur'));
    await nextTick();
    assert.equal(previews.at(-1), null);
    assert.equal(tooltip(), null);
  } finally {
    view.dispose();
  }
});

test('shared-port picker previews each relation and bridges pointer travel without changing paths', async () => {
  const view = mount('calls');
  const previews = [];
  try {
    view.props.data = {
      ...view.props.data,
      index: 0,
      previewIndex: null,
      previewPeers: [
        { index: 0, description: 'Caller → Service：calls' },
        { index: 2, description: 'Caller → Cache：reads' },
      ],
      onPreview(index) {
        previews.push(index);
        view.props.data.previewIndex = index;
      },
    };
    await nextTick();
    const path = view.root.querySelector('.vue-flow__edge-path').getAttribute('d');
    view.hover();
    await nextTick();
    assert.equal(tooltip().getAttribute('role'), 'dialog');
    view.leave();
    tooltip().dispatchEvent(new window.MouseEvent('mouseenter'));
    await new Promise((resolve) => setTimeout(resolve, 180));
    await nextTick();
    assert.ok(tooltip(), 'the shared picker remains open after pointer travel');
    const choices = tooltip().querySelectorAll('button');
    assert.equal(choices.length, 2);
    choices[1].dispatchEvent(new window.MouseEvent('mouseenter'));
    await nextTick();
    assert.equal(previews.at(-1), 2);
    assert.equal(choices[1].getAttribute('aria-pressed'), 'true');
    assert.equal(view.root.querySelector('.vue-flow__edge-path').getAttribute('d'), path);
    tooltip().dispatchEvent(new window.MouseEvent('mouseleave'));
    await new Promise((resolve) => setTimeout(resolve, 180));
    await nextTick();
    assert.equal(tooltip(), null);
    assert.equal(previews.at(-1), null);
    const edge = view.root.querySelector('.architecture-edge');
    edge.dispatchEvent(new window.FocusEvent('focus'));
    await nextTick();
    edge.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await nextTick();
    assert.equal(previews.at(-1), 2);
    edge.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();
    assert.equal(tooltip(), null);
    assert.equal(previews.at(-1), null);
  } finally {
    view.dispose();
  }
});

test('routing replacement and unmount clear transient previews and body-level pickers', async () => {
  const view = mount();
  const previews = [];
  view.props.data.index = 0;
  view.props.data.previewEnabled = true;
  view.props.data.onPreview = (index) => previews.push(index);
  await nextTick();
  view.hover();
  await nextTick();
  assert.ok(tooltip());
  view.props.data.previewEnabled = false;
  await nextTick();
  assert.equal(tooltip(), null);
  assert.equal(previews.at(-1), null);
  view.hover();
  await nextTick();
  assert.equal(tooltip(), null, 'old geometry cannot reopen previews while arranging');
  view.props.data.previewEnabled = true;
  await nextTick();
  view.hover();
  await nextTick();
  assert.ok(tooltip());
  view.dispose();
  assert.equal(tooltip(), null);
  assert.equal(previews.at(-1), null);
});

test('moving preview ownership clears the old focused tooltip and keyboard can reopen it', async () => {
  const view = mount();
  const previews = [];
  try {
    view.props.data = {
      ...view.props.data,
      index: 0,
      previewOwned: true,
      previewIndex: 0,
      previewPeers: [
        { index: 0, description: 'A → B：first' },
        { index: 1, description: 'A → C：second' },
      ],
      onPreview(index) {
        previews.push(index);
        view.props.data.previewIndex = index;
      },
    };
    await nextTick();
    const edge = view.root.querySelector('.architecture-edge');
    edge.dispatchEvent(new window.FocusEvent('focus'));
    await nextTick();
    assert.ok(tooltip());
    view.props.data.previewOwned = false;
    await nextTick();
    assert.equal(tooltip(), null, 'only the current preview owner keeps a picker');
    assert.equal(previews.at(-1), null);
    edge.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await nextTick();
    assert.ok(tooltip(), 'the still-focused edge can resume keyboard preview');
    assert.equal(previews.at(-1), 1);
  } finally {
    view.dispose();
  }
});
