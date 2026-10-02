import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
});
for (const key of [
  'window',
  'document',
  'Document',
  'navigator',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'KeyboardEvent',
  'MutationObserver',
  'DOMRect',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
// jsdom has no layout; this test checks the installed Vue Flow DOM/event contract.
const ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.ResizeObserver = ResizeObserver;
dom.window.ResizeObserver = ResizeObserver;
const { createApp, h, nextTick, ref } = await import('vue');
const { VueFlow } = await import('@vue-flow/core');
const source = readFileSync(
  new URL('../../frontend/src/lib/milestoneInteraction.ts', import.meta.url),
  'utf8',
);
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { activateMilestoneKey } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);
const flush = async () => {
  await nextTick();
  await new Promise((resolve) => setTimeout(resolve, 20));
  await nextTick();
};

test('installed Vue Flow wrapper activates app selection through native canvas capture for Enter and Space', async () => {
  const root = document.createElement('div');
  document.body.append(root);
  const selected = ref(null),
    activated = [];
  const app = createApp({
    render: () =>
      h('div', [
        h(
          'div',
          {
            class: 'graph-canvas',
            onKeydownCapture: (event) =>
              activateMilestoneKey(event, ['A', 'B'], (id) => {
                selected.value = id;
                activated.push(id);
              }),
          },
          [
            h(VueFlow, {
              id: 'keyboard-contract',
              nodes: ['A', 'B'].map((id, i) => ({
                id,
                label: id,
                position: { x: i * 300, y: 0 },
                selected: selected.value === id,
              })),
              nodesDraggable: false,
              deleteKeyCode: null,
              fitViewOnInit: false,
            }),
          ],
        ),
        h('textarea', { class: 'outside-composer' }),
      ]),
  });
  app.mount(root);
  try {
    await flush();
    const a = root.querySelector('.vue-flow__node[data-id="A"]'),
      b = root.querySelector('.vue-flow__node[data-id="B"]');
    assert.ok(a);
    assert.ok(b);
    assert.equal(a.tabIndex, 0);
    a.focus();
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    a.dispatchEvent(enter);
    await flush();
    assert.equal(enter.defaultPrevented, true);
    assert.equal(selected.value, 'A');
    assert.ok(a.classList.contains('selected'));
    b.focus();
    const space = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    b.dispatchEvent(space);
    await flush();
    assert.equal(space.defaultPrevented, true);
    assert.equal(selected.value, 'B');
    assert.ok(b.classList.contains('selected'));
    const input = document.createElement('textarea');
    a.append(input);
    for (const [target, extra] of [
      [input, {}],
      [root.querySelector('.outside-composer'), {}],
      [a, { isComposing: true }],
      [a, { repeat: true }],
      [a, { ctrlKey: true }],
    ]) {
      target.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...extra }),
      );
    }
    await flush();
    assert.deepEqual(activated, ['A', 'B']);
    const unknown = document.createElement('div');
    unknown.className = 'vue-flow__node';
    unknown.dataset.id = 'unknown';
    root.querySelector('.graph-canvas').append(unknown);
    unknown.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    );
    assert.deepEqual(activated, ['A', 'B']);
  } finally {
    app.unmount();
    root.remove();
  }
});

test('production graph wires the native canvas handler without unsupported node event attributes', () => {
  const graph = readFileSync(
    new URL('../../frontend/src/components/graph/MilestoneGraph.vue', import.meta.url),
    'utf8',
  );
  assert.match(graph, /class="graph-canvas" @keydown.capture="activateNodeKey"/);
  assert.match(graph, /function activateNodeKey\(event: KeyboardEvent\)/);
  assert.match(graph, /selectNode\(id\);\s*locate\(id\);/);
  assert.doesNotMatch(graph, /domAttributes|onKeydownCapture/);
});
