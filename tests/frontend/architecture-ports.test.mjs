import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript, registerTS } from '@vue/compiler-sfc';
import ts from 'typescript';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, nextTick, reactive } = await import('vue');
registerTS(() => ts);
const moduleUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
const vue = import.meta.resolve('vue');
const filename = new URL(
  '../../frontend/src/components/design/ArchitectureNode.vue',
  import.meta.url,
);
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname });
const imports = {
  vue,
  '@vue-flow/core': moduleUrl(`import { h } from ${JSON.stringify(vue)};
    export const Position = { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' };
    export const Handle = {
      props: ['id', 'type', 'position'],
      setup(props, { attrs }) { return () => h('div', { ...attrs, 'data-handleid': props.id, 'data-position': props.position, 'data-type': props.type }); }
    };`),
  '../../lib/architectureRoles': moduleUrl(
    compile(
      readFileSync(new URL('../../frontend/src/lib/architectureRoles.ts', import.meta.url), 'utf8'),
    ),
  ),
};
const code = compile(
  compileScript(descriptor, { id: 'ports-regression', inlineTemplate: true }).content,
).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
  assert.ok(imports[name], `Unexpected dependency ${name}`);
  return `from ${JSON.stringify(imports[name])}`;
});
const ArchitectureNode = (await import(moduleUrl(code))).default;
function mount(data) {
  const props = reactive({
    data: { id: 'component', label: 'Component', ...data },
    selected: false,
  });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(ArchitectureNode, props) });
  app.mount(root);
  return {
    props,
    root,
    dispose() {
      app.unmount();
      root.remove();
    },
  };
}

test('grouped architecture ports position incoming and outgoing handles on all four faces', async () => {
  const faces = [
    { side: 'left', x: 0, y: 75 },
    { side: 'right', x: 236, y: 75 },
    { side: 'top', x: 118, y: 0 },
    { side: 'bottom', x: 118, y: 150 },
  ];
  const ports = {
    incoming: faces.map((face, index) => ({
      ...face,
      id: `in:${face.side}`,
      edges: [index, index + 4],
    })),
    outgoing: faces.map((face, index) => ({ ...face, id: `out:${face.side}`, edges: [index + 8] })),
  };
  const view = mount({ ports });
  try {
    assert.equal(view.root.querySelectorAll('[data-handleid]').length, 8);
    for (const [kind, type] of [
      ['incoming', 'target'],
      ['outgoing', 'source'],
    ]) {
      for (const port of ports[kind]) {
        const element = view.root.querySelector(`[data-handleid="${port.id}"]`);
        assert.equal(element.dataset.position, port.side);
        assert.equal(element.dataset.type, type);
        assert.equal(element.style.left, `${port.x}px`);
        assert.equal(element.style.top, `${port.y}px`);
        assert.equal(element.style.right, 'auto');
        assert.equal(element.style.bottom, 'auto');
        assert.equal(element.style.transform, 'translate(-50%, -50%)');
        assert.ok(element.classList.contains(`architecture-port-${kind}`));
        assert.ok(element.title.includes(`${port.edges.length} 条关系`));
      }
    }
    view.props.data.previewed = true;
    view.props.data.previewHandles = ['in:top', 'out:bottom'];
    await nextTick();
    assert.equal(view.root.querySelectorAll('.architecture-port.is-edge-preview').length, 2);
    assert.ok(view.root.querySelector('.architecture-node').classList.contains('is-edge-preview'));
    view.props.data.previewHandles = [];
    view.props.data.previewed = false;
    await nextTick();
    assert.equal(view.root.querySelectorAll('.is-edge-preview').length, 0);
  } finally {
    view.dispose();
  }
});

test('unused architecture handles are absent, including isolated and not-yet-routed components', async () => {
  const view = mount({});
  try {
    assert.equal(view.root.querySelectorAll('[data-handleid]').length, 0);
    view.props.data.ports = {
      incoming: [],
      outgoing: [{ id: 'out:top', side: 'top', x: 118, y: 0, edges: [0, 1, 2] }],
    };
    await nextTick();
    assert.equal(view.root.querySelectorAll('[data-handleid]').length, 1);
    assert.equal(view.root.querySelector('[data-handleid]').dataset.handleid, 'out:top');
    view.props.data.ports = { incoming: [], outgoing: [] };
    await nextTick();
    assert.equal(view.root.querySelectorAll('[data-handleid]').length, 0);
  } finally {
    view.dispose();
  }
});
