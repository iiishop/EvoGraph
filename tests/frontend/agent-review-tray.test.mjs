import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc';
import ts from 'typescript';

// Mounted behavior with explicit geometry/WAAPI stubs. These tests are not native pixel proof.
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
});
for (const key of [
  'window',
  'document',
  'Document',
  'Node',
  'Element',
  'HTMLElement',
  'SVGElement',
  'KeyboardEvent',
  'MouseEvent',
  'Event',
  'DOMRect',
])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
let viewport = { width: 1050, height: 720 };
for (const [key, field] of [
  ['innerWidth', 'width'],
  ['innerHeight', 'height'],
])
  Object.defineProperty(window, key, { configurable: true, get: () => viewport[field] });
const mediaListeners = new Set();
const media = {
  matches: false,
  addEventListener(_, listener) {
    mediaListeners.add(listener);
  },
  removeEventListener(_, listener) {
    mediaListeners.delete(listener);
  },
  change(value) {
    this.matches = value;
    for (const listener of mediaListeners) listener({ matches: value });
  },
};
window.matchMedia = () => media;
const observers = new Set();
globalThis.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.nodes = new Set();
    observers.add(this);
  }
  observe(node) {
    this.nodes.add(node);
  }
  disconnect() {
    observers.delete(this);
  }
};
let nextFrame = 1;
const frames = new Map();
globalThis.requestAnimationFrame = (callback) => {
  const id = nextFrame++;
  frames.set(id, callback);
  return id;
};
globalThis.cancelAnimationFrame = (id) => frames.delete(id);
const runFrames = () => {
  for (const [id, callback] of [...frames]) {
    frames.delete(id);
    callback();
  }
};
const animations = [];
const effects = new WeakMap();
const operations = [];
HTMLElement.prototype.animate = function (keyframes, options) {
  const element = this;
  const animation = {
    element,
    keyframes,
    options,
    progress: 0,
    onfinish: null,
    cancelled: false,
    cancel() {
      operations.push('cancel');
      this.cancelled = true;
      if (effects.get(element) === this) effects.delete(element);
    },
    finish() {
      this.progress = 1;
      this.onfinish?.();
    },
  };
  effects.set(element, animation);
  animations.push(animation);
  return animation;
};
const actualStyle = window.getComputedStyle.bind(window);
globalThis.getComputedStyle = (element) => {
  const style = actualStyle(element);
  const effect = effects.get(element);
  const [from, to] = effect?.keyframes ?? [];
  return new Proxy(style, {
    get(target, key) {
      if (key === 'opacity')
        return effect
          ? String(from.opacity + (to.opacity - from.opacity) * effect.progress)
          : target.opacity || '1';
      return Reflect.get(target, key);
    },
  });
};
const baseline = () => ({
  row: { left: 100, top: 566, width: 800, height: 52 },
  dock: { left: 88, top: 520, width: 824, height: 190 },
});
let geometry = baseline();
let reads = 0;
const rect = ({ left, top, width, height }) => new DOMRect(left, top, width, height);
const parseTransform = (value) => {
  const match = value.match(
    /translate\(([-\d.e]+)px, ([-\d.e]+)px\) scale\(([-\d.e]+), ([-\d.e]+)\)/,
  );
  assert.ok(match, value);
  return match.slice(1).map(Number);
};
function assertRectNear(actual, expected) {
  for (const key of ['left', 'top', 'width', 'height'])
    assert.ok(
      Math.abs(actual[key] - expected[key]) < 0.000001,
      `${key}: ${actual[key]} != ${expected[key]}`,
    );
}
function transformed(base, transform) {
  const [x, y, sx, sy] = parseTransform(transform);
  return {
    left: base.left + x,
    top: base.top + y,
    width: base.width * sx,
    height: base.height * sy,
  };
}
function tileSlot(element) {
  const slot = element.matches('.review-tile-slot')
    ? element
    : element.closest('.review-tile-slot');
  const siblings = [...slot.parentElement.querySelectorAll('.review-tile-slot')];
  const width = (geometry.row.width - (siblings.length - 1) * 8) / siblings.length;
  return { ...geometry.row, left: geometry.row.left + siblings.indexOf(slot) * (width + 8), width };
}
HTMLElement.prototype.getBoundingClientRect = function () {
  reads++;
  if (this.classList.contains('agent-dock')) return rect(geometry.dock);
  if (this.classList.contains('agent-review-tray')) return rect(geometry.row);
  if (this.classList.contains('review-tile-slot')) return rect(tileSlot(this));
  if (this.classList.contains('review-tile'))
    return rect(this.classList.contains('is-expanded') ? geometry.row : tileSlot(this));
  if (this.matches('.review-surface-fill, .review-surface-heading')) {
    operations.push('visual-read');
    const base = this.closest('.agent-review-surface').getBoundingClientRect().toJSON();
    const effect = effects.get(this);
    if (!effect) return rect(base);
    const convert = (value) =>
      value.includes('scale')
        ? transformed(base, value)
        : {
            ...base,
            left: base.left + Number(value.match(/translate\(([-\d.e]+)px/)[1]),
            top: base.top + Number(value.match(/, ([-\d.e]+)px/)[1]),
          };
    const start = convert(effect.keyframes[0].transform);
    const end = convert(effect.keyframes[1].transform);
    return rect(
      Object.fromEntries(
        ['left', 'top', 'width', 'height'].map((key) => [
          key,
          start[key] + (end[key] - start[key]) * effect.progress,
        ]),
      ),
    );
  }
  if (this.matches('.review-tile-fill, .review-tile-copy')) {
    operations.push('visual-read');
    const button = this.closest('.review-tile');
    let base = button.classList.contains('is-expanded') ? { ...geometry.row } : tileSlot(this);
    if (this.classList.contains('review-tile-copy'))
      base = { left: base.left + 12, top: base.top + 7, width: base.width - 24, height: 38 };
    const effect = effects.get(this);
    if (!effect) return rect(base);
    const convert = (value) =>
      value.includes('scale')
        ? transformed(base, value)
        : { ...base, left: base.left + Number(value.match(/translateX\(([-\d.e]+)px\)/)[1]) };
    const start = convert(effect.keyframes[0].transform);
    const end = convert(effect.keyframes[1].transform);
    return rect(
      Object.fromEntries(
        Object.keys(base).map((key) => [
          key,
          start[key] + (end[key] - start[key]) * effect.progress,
        ]),
      ),
    );
  }
  if (this.classList.contains('agent-review-surface')) {
    if (this.parentElement.style.display === 'none') return new DOMRect();
    return new DOMRect(
      parseFloat(this.style.left),
      parseFloat(this.style.top) + parseFloat(this.parentElement.style.top),
      parseFloat(this.style.width),
      parseFloat(this.style.height),
    );
  }
  if (this.classList.contains('canvas')) return new DOMRect(88, 80, 824, 440);
  return new DOMRect(100, 630, 800, 38);
};
const { createApp, h, nextTick, reactive } = await import('vue');
const vue = import.meta.resolve('vue');
const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const transpile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const summaryModule = url(transpile(source('lib/turnSummary.ts')));
const composerModule = url(transpile(source('lib/composerDocument.ts')));
async function component(name, imports) {
  const path = `components/agent/${name}.vue`;
  const { descriptor } = parse(source(path));
  const id = `data-v-${name.toLowerCase()}`;
  const moduleUrl = url(
    transpile(compileScript(descriptor, { id, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, q, imported) => {
        assert.ok(imports[imported], `Unexpected import ${imported}`);
        return `from ${JSON.stringify(imports[imported])}`;
      },
    ),
  );
  const value = (await import(moduleUrl)).default;
  value.__scopeId = id;
  for (const block of descriptor.styles) {
    const style = document.createElement('style');
    style.textContent = compileStyle({
      source: block.content,
      filename: path,
      id,
      scoped: true,
    }).code;
    document.head.append(style);
  }
  return { value, moduleUrl };
}
const receipt = await component('AgentTurnSummary', {
  vue,
  '../../lib/turnSummary': summaryModule,
});
const message = await component('MessageContent', {
  vue,
  '../../lib/composerDocument': composerModule,
});
const { value: Tray } = await component('AgentReviewTray', {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  './AgentTurnSummary.vue': receipt.moduleUrl,
  './MessageContent.vue': message.moduleUrl,
  '../../lib/turnSummary': summaryModule,
});
const summary = () => ({
  version: 1,
  turn_id: 'T1',
  status: 'stopped',
  history_warning: '这轮对话未完整保存',
  changed: true,
  before_revision: 2,
  after_revision: 3,
  changes: {
    milestones: {
      added: [{ id: 'A', title: 'Available A', fields: [] }],
      updated: [{ id: 'missing', title: 'Missing history', fields: ['scope'] }],
      removed: [{ id: 'gone', title: 'Removed history', fields: [] }],
    },
    dependencies: {
      added: [
        { source: 'A', target: 'B', reason: 'Stored dependency reason', type: 'verification' },
      ],
      updated: [],
      removed: [],
    },
    target: null,
    architecture: { before_revision: 1, after_revision: 2 },
    other: ['attachments'],
  },
});
const project = () => ({
  id: 'P1',
  milestones: [
    { id: 'A', title: 'A' },
    { id: 'B', title: 'B' },
  ],
  source_milestones: [],
  messages: [
    { id: '1', role: 'user', content: 'Earlier request', created_at: '2026-10-01' },
    { id: '2', role: 'assistant', content: 'Earlier saved answer', created_at: '2026-10-01' },
    { id: '3', role: 'assistant', content: 'Latest saved answer', created_at: '2026-10-02' },
  ],
});
async function mount(overrides = {}) {
  viewport = { width: 1050, height: 720 };
  geometry = baseline();
  animations.length = 0;
  operations.length = 0;
  media.matches = false;
  const props = reactive({
    project: project(),
    summary: summary(),
    running: false,
    disabled: false,
    ...overrides,
  });
  const locates = [];
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    render: () =>
      h('main', [
        h('div', { class: 'canvas' }, 'Canvas'),
        h('section', { class: 'agent-dock' }, [
          h('button', { class: 'question' }, 'Question'),
          h(Tray, { ...props, onLocate: (id) => locates.push(id) }),
          h('input', { class: 'composer' }),
          h('button', { class: 'stop' }, 'Stop'),
        ]),
        h('button', { class: 'outside' }, 'Outside'),
      ]),
  });
  app.mount(root);
  await nextTick();
  return {
    root,
    props,
    locates,
    tiles: () => [...root.querySelectorAll('.review-tile')],
    surface: () => document.querySelector('.agent-review-surface'),
    layer: () => document.querySelector('.agent-review-layer'),
    dispose() {
      app.unmount();
      root.remove();
    },
  };
}
const flush = async () => {
  await nextTick();
  await nextTick();
};
async function click(element, pointer = false) {
  element.focus();
  element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: pointer ? 1 : 0 }));
  await flush();
}
const visible = (view) => view.layer().style.display !== 'none';
const tileMotion = () =>
  animations.filter((animation) => animation.element.matches('.review-surface-fill')).at(-1);
const readerMotion = () =>
  animations.filter((animation) => animation.element.matches('.review-surface-content')).at(-1);
const finish = async () => {
  tileMotion().finish();
  await flush();
};
const close = (view) => view.surface().querySelector('.review-close');
const assertAboveDock = (view, animation) => {
  for (const progress of [0, 0.2, 0.5, 0.8, 1]) {
    animation.progress = progress;
    const value = view.surface().getBoundingClientRect();
    assert.ok(value.top >= 12 - 0.001);
    assert.ok(value.bottom <= geometry.dock.top - 10 + 0.001, `${value.bottom} crosses dock`);
    assert.ok(value.left >= 12 - 0.001 && value.right <= viewport.width - 12 + 0.001);
  }
};

test('two fixed-height half tiles stay in layout while a full-width body-teleported reader opens', async () => {
  const view = await mount();
  try {
    const [reply, receiptButton] = view.tiles();
    const strip = view.root.querySelector('.agent-review-tray');
    const controls = ['.canvas', '.question', '.composer', '.stop'].map((query) =>
      view.root.querySelector(query),
    );
    const boxes = controls.map((element) => element.getBoundingClientRect().toJSON());
    assert.equal(getComputedStyle(strip).height, '52px');
    assert.equal(getComputedStyle(strip).gridTemplateColumns, 'minmax(0, 1fr) minmax(0, 1fr)');
    assert.equal(reply.getBoundingClientRect().width, receiptButton.getBoundingClientRect().width);
    await click(reply);
    assert.equal(view.layer().parentElement, document.body);
    assert.equal(getComputedStyle(view.layer()).position, 'fixed');
    assert.equal(view.surface().getBoundingClientRect().width, 800);
    assert.equal(view.surface().getBoundingClientRect().bottom, 510);
    assert.equal(view.tiles()[0], reply);
    assert.equal(view.tiles()[1], receiptButton);
    assert.deepEqual(
      controls.map((element) => element.getBoundingClientRect().toJSON()),
      boxes,
    );
    assert.equal(view.surface().getAttribute('role'), 'dialog');
    assert.equal(view.surface().getAttribute('aria-modal'), 'false');
    assert.equal(view.surface().getAttribute('aria-labelledby'), reply.id);
    assert.equal(reply.getAttribute('aria-controls'), view.surface().id);
    assert.equal(reply.getAttribute('aria-expanded'), 'true');
    assert.equal(receiptButton.getAttribute('aria-expanded'), 'false');
    assert.equal(receiptButton.hasAttribute('inert'), true);
    assert.equal(reply.hasAttribute('inert'), false);
    assert.equal(view.surface().hasAttribute('inert'), false);
    assert.ok(
      controls.every((control) => !control.closest('[inert]')),
      'background controls are never inert',
    );
    assert.equal(reply.getBoundingClientRect().width, 800);
    assert.equal(receiptButton.parentElement.getBoundingClientRect().width, 396);
    assert.equal(animations.length, 0, 'keyboard activation is instant');
  } finally {
    view.dispose();
  }
});

test('full saved history and factual expanded receipt remain mounted through switches and preserve scroll', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0]);
    const history = view.surface().querySelector('.review-history');
    const messages = [...history.querySelectorAll('.message-content')];
    assert.deepEqual(
      messages.map((item) => item.textContent),
      ['Earlier request', 'Earlier saved answer', 'Latest saved answer'],
    );
    assert.ok(view.surface().querySelector('header').textContent.includes('对话记录'));
    history.scrollTop = 83;
    await click(close(view));
    await click(view.tiles()[1]);
    const details = view.surface().querySelector('.agent-turn-summary');
    assert.equal(details.open, true);
    assert.equal(getComputedStyle(details.querySelector(':scope > summary')).display, 'none');
    assert.match(view.surface().textContent, /已停止/);
    assert.match(details.textContent, /这轮对话未完整保存/);
    assert.match(details.textContent, /Stored dependency reason/);
    assert.match(details.textContent, /Missing history/);
    assert.match(details.textContent, /Removed history/);
    assert.match(details.textContent, /A1 → A2/);
    assert.match(details.textContent, /项目资料/);
    for (const button of details.querySelectorAll('.turn-node-link')) {
      if (!visible(view)) await click(view.tiles()[1]);
      button.click();
      await flush();
      assert.equal(visible(view), false, 'locating closes the reader');
      assert.notEqual(
        document.activeElement,
        view.tiles()[1],
        'locate never restores trigger focus',
      );
    }
    assert.deepEqual(view.locates, ['A', 'A', 'B']);
    assert.equal(getComputedStyle(view.surface().querySelector('.review-reader')).overflow, 'auto');
    assert.equal(
      getComputedStyle(details.querySelector('.turn-summary-scroll')).overflow,
      'visible',
    );
    await click(view.tiles()[0]);
    assert.equal(view.surface().querySelector('.review-history'), history);
    assert.equal(history.scrollTop, 83);
    assert.deepEqual([...history.querySelectorAll('.message-content')], messages);
  } finally {
    view.dispose();
  }
});

test('pointer card grows from selected half into floating reader with unscaled content', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[1], true);
    const entry = tileMotion();
    const base = boxFrom(view.surface());
    assert.equal(entry.options.duration, 280);
    assert.equal(entry.options.easing, 'cubic-bezier(0.77, 0, 0.175, 1)');
    assert.deepEqual(Object.keys(entry.keyframes[0]).sort(), ['opacity', 'transform']);
    assertRectNear(transformed(base, entry.keyframes[0].transform), {
      left: 504,
      top: 566,
      width: 396,
      height: 52,
    });
    assertRectNear(transformed(base, entry.keyframes[1].transform), base);
    assert.deepEqual(Object.keys(readerMotion().keyframes[0]).sort(), ['clipPath', 'opacity']);
    assert.ok(animations.every((animation) => animation.options.duration < 300));
    assert.ok(
      animations
        .filter(
          (animation) => !animation.element.matches('.review-surface-fill, .review-tile-fill'),
        )
        .every((animation) =>
          animation.keyframes.every((frame) => !String(frame.transform).includes('scale')),
        ),
      'text never scales',
    );
    for (const progress of [0, 0.25, 0.5, 0.75, 1]) {
      entry.progress = progress;
      const value = entry.element.getBoundingClientRect();
      assert.ok(value.width >= 396 && value.width <= 800);
      assert.ok(value.height >= 52 - 0.001 && value.height <= 360 + 0.001);
      assert.ok(value.bottom <= geometry.row.top + geometry.row.height + 0.001);
    }
    assertAboveDock(view, entry);
    await finish();
    await click(close(view), true);
    assert.equal(document.activeElement, view.tiles()[1]);
    const exit = tileMotion();
    assert.equal(exit.options.duration, 230);
    assertRectNear(transformed(base, exit.keyframes[1].transform), {
      left: 504,
      top: 566,
      width: 396,
      height: 52,
    });
    await finish();
    assert.equal(visible(view), false);
  } finally {
    view.dispose();
  }
});
const boxFrom = (element) => {
  const { left, top, width, height } = element.getBoundingClientRect();
  return { left, top, width, height };
};

test('rapid open/close/reopen and switching start from the current visual before cancellation', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0], true);
    const entry = tileMotion();
    entry.progress = 0.4;
    const entryFinish = entry.onfinish;
    const current = entry.element.getBoundingClientRect().toJSON();
    operations.length = 0;
    await click(view.tiles()[0], true);
    assert.ok(operations.indexOf('visual-read') < operations.indexOf('cancel'));
    assert.equal(entry.cancelled, true);
    const exit = tileMotion();
    const base = boxFrom(view.surface());
    assertRectNear(transformed(base, exit.keyframes[0].transform), {
      left: current.left,
      top: current.top,
      width: current.width,
      height: current.height,
    });
    exit.progress = 0.3;
    const exiting = exit.element.getBoundingClientRect().toJSON();
    await click(view.tiles()[0], true);
    const switched = tileMotion();
    assert.equal(exit.cancelled, true);
    assertRectNear(transformed(base, switched.keyframes[0].transform), {
      left: exiting.left,
      top: exiting.top,
      width: exiting.width,
      height: exiting.height,
    });
    entryFinish();
    await flush();
    assert.equal(visible(view), true, 'stale completion cannot hide new content');
    assert.equal(view.tiles()[0].getAttribute('aria-expanded'), 'true');
    assert.equal(view.tiles()[1].getAttribute('aria-expanded'), 'false');
    assert.equal(view.surface().getAttribute('aria-labelledby'), view.tiles()[0].id);
    assertAboveDock(view, switched);
    await finish();
    assert.equal(document.activeElement, close(view));
    await click(close(view));
    await click(view.tiles()[1], true);
    assert.equal(view.tiles()[0].hasAttribute('inert'), true);
    assert.equal(view.tiles()[1].hasAttribute('inert'), false);
    assert.equal(view.surface().hasAttribute('inert'), false);
    assert.equal(view.surface().getAttribute('aria-labelledby'), view.tiles()[1].id);
  } finally {
    view.dispose();
  }
});

test('Escape and Close restore the correct trigger; owned IME and prevented Escape do nothing', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0]);
    assert.equal(document.activeElement, close(view));
    const composing = new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
      isComposing: true,
    });
    close(view).dispatchEvent(composing);
    await flush();
    assert.equal(visible(view), true);
    assert.equal(composing.defaultPrevented, false);
    const prevented = new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    });
    prevented.preventDefault();
    close(view).dispatchEvent(prevented);
    await flush();
    assert.equal(visible(view), true);
    close(view).dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    await flush();
    assert.equal(visible(view), false);
    assert.equal(document.activeElement, view.tiles()[0]);
    await click(view.tiles()[1]);
    await click(close(view));
    assert.equal(visible(view), false);
    assert.equal(document.activeElement, view.tiles()[1]);
    assert.equal(animations.length, 0);
  } finally {
    view.dispose();
  }
});

test('outside pointer dismisses without preventing, trapping or stealing focus from question/composer/Stop', async () => {
  const view = await mount();
  try {
    for (const query of ['.question', '.composer', '.stop', '.outside']) {
      await click(view.tiles()[0]);
      const target = view.root.querySelector(query);
      const event = new MouseEvent('pointerdown', { bubbles: true, cancelable: true });
      target.dispatchEvent(event);
      target.focus();
      await flush();
      assert.equal(event.defaultPrevented, false);
      assert.equal(document.activeElement, target);
      await finish();
      assert.equal(document.activeElement, target);
      assert.equal(visible(view), false);
    }
    await click(view.tiles()[0]);
    const composer = view.root.querySelector('.composer');
    composer.focus();
    composer.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    );
    assert.equal(document.activeElement, composer, 'the dialog has no focus trap');
  } finally {
    view.dispose();
  }
});

test('reduced motion is instant before opening and immediately settles active opening/closing effects', async () => {
  const view = await mount();
  try {
    media.matches = true;
    await click(view.tiles()[0], true);
    assert.equal(animations.length, 0);
    await click(close(view), true);
    assert.equal(visible(view), false);
    media.matches = false;
    await click(view.tiles()[0], true);
    const opening = tileMotion();
    opening.progress = 0.2;
    media.change(true);
    await flush();
    assert.equal(opening.cancelled, true);
    assert.equal(view.surface().getBoundingClientRect().width, 800);
    assert.equal(visible(view), true);
    media.change(false);
    await click(close(view), true);
    const closing = tileMotion();
    closing.progress = 0.2;
    media.change(true);
    await flush();
    assert.equal(closing.cancelled, true);
    assert.equal(visible(view), false);
  } finally {
    view.dispose();
  }
  assert.equal(mediaListeners.size, 0);
});

test('availability, project change and dock collapse close only invalid readers and reset project scroll', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[1]);
    view.props.running = true;
    await flush();
    assert.equal(visible(view), false);
    assert.equal(view.tiles().length, 1);
    assert.equal(view.tiles()[0].getBoundingClientRect().width, 800);
    await click(view.tiles()[0]);
    view.props.project.messages.push({
      id: '4',
      role: 'assistant',
      content: 'Live saved content',
      created_at: '',
    });
    await flush();
    assert.equal(visible(view), true, 'history stays readable during a running turn');
    assert.match(view.surface().textContent, /Live saved content/);
    const reader = view.surface().querySelector('.review-history');
    reader.scrollTop = 90;
    view.props.project = { ...project(), id: 'P2' };
    await flush();
    assert.equal(visible(view), false);
    assert.equal(reader.scrollTop, 0);
    await click(view.tiles()[0]);
    view.props.disabled = true;
    await flush();
    assert.equal(visible(view), false);
    assert.equal(view.tiles()[0].disabled, true);
    view.props.disabled = false;
    view.props.running = false;
    await flush();
    await click(view.tiles()[1]);
    view.props.summary = null;
    await flush();
    assert.equal(visible(view), false);
  } finally {
    view.dispose();
  }
});

test('no-reply and receipt-only cases are factual; neither trigger exists when data is absent', async () => {
  const view = await mount({
    project: {
      ...project(),
      messages: [{ id: 'u', role: 'user', content: 'Still waiting', created_at: '' }],
    },
    summary: null,
  });
  try {
    assert.equal(view.tiles().length, 1);
    assert.match(view.tiles()[0].textContent, /对话记录/);
    assert.doesNotMatch(view.tiles()[0].textContent, /本轮回复/);
    await click(view.tiles()[0]);
    assert.match(view.surface().textContent, /Still waiting/);
    view.props.project.messages = [];
    view.props.summary = summary();
    await flush();
    assert.equal(visible(view), false);
    assert.equal(view.tiles().length, 1);
    assert.match(view.tiles()[0].textContent, /最近变更/);
    await click(view.tiles()[0]);
    assert.equal(view.surface().querySelector('.agent-turn-summary').open, true);
    view.props.summary = null;
    await flush();
    assert.equal(view.tiles().length, 0);
    assert.equal(view.root.querySelector('.agent-review-tray').style.display, 'none');
    assert.equal(visible(view), false);
  } finally {
    view.dispose();
  }
});

test('resize/scroll snap safely within viewport, coalesce reads, ignore internal scroll, and clean up', async () => {
  const view = await mount();
  await click(view.tiles()[0], true);
  const opening = tileMotion();
  opening.progress = 0.3;
  const composer = view.root.querySelector('.composer');
  composer.focus();
  viewport = { width: 600, height: 720 };
  geometry.row = { left: -20, top: 450, width: 800, height: 52 };
  geometry.dock.top = 400;
  for (let i = 0; i < 20; i++) window.dispatchEvent(new Event('resize'));
  assert.equal(frames.size, 1);
  const beforeReads = reads;
  runFrames();
  await flush();
  assert.equal(reads - beforeReads, 3, 'one bounded placement measurement per coalesced event');
  assert.equal(opening.cancelled, true);
  const placed = view.surface().getBoundingClientRect();
  assert.equal(placed.left, 12);
  assert.equal(placed.right, 588);
  assert.equal(placed.bottom, 390);
  assert.equal(document.activeElement, composer);
  view.surface().querySelector('.review-history').dispatchEvent(new Event('scroll'));
  assert.equal(frames.size, 0);
  document.dispatchEvent(new Event('scroll'));
  assert.equal(frames.size, 1);
  runFrames();
  await flush();
  const observer = [...observers][0];
  assert.ok([...observer.nodes].every((node) => node.matches('.agent-dock, .agent-review-tray')));
  observer.callback();
  assert.equal(frames.size, 0, 'ResizeObserver snaps placement before paint');
  document.dispatchEvent(new Event('scroll'));
  assert.equal(frames.size, 1);
  view.dispose();
  assert.equal(frames.size, 0);
  assert.equal(observers.size, 0);
  assert.equal(mediaListeners.size, 0);
  assert.equal(document.querySelector('.agent-review-layer'), null);
  window.dispatchEvent(new Event('resize'));
  assert.equal(frames.size, 0);
});

test('same-id incarnation/owner changes and new questions cancel stale motion without stealing focus', async () => {
  const view = await mount({ owner: 1 });
  try {
    for (const mutate of [
      () => {
        view.props.project.created_at = '2026-10-02T08:00:00Z';
      },
      () => {
        view.props.owner = 2;
      },
      () => {
        view.props.project.question = { id: 'new-question' };
      },
    ]) {
      await click(view.tiles()[0], true);
      const pending = tileMotion();
      const staleCompletion = pending.onfinish;
      const composer = view.root.querySelector('.composer');
      composer.focus();
      mutate();
      await flush();
      assert.equal(pending.cancelled, true);
      assert.equal(visible(view), false);
      staleCompletion();
      await flush();
      assert.equal(visible(view), false);
      assert.equal(document.activeElement, composer);
      assert.ok(view.tiles().every((button) => !button.hasAttribute('inert')));
      assert.equal(view.surface().hasAttribute('inert'), true);
    }
  } finally {
    view.dispose();
  }
});

test('open/reopen removes boolean inert instead of leaving a false attribute, and hidden sibling cannot activate', async () => {
  const view = await mount();
  try {
    for (let i = 0; i < 5; i++) {
      const selected = view.tiles()[i % 2];
      const sibling = view.tiles()[1 - (i % 2)];
      await click(selected);
      assert.equal(view.surface().hasAttribute('inert'), false);
      assert.equal(selected.hasAttribute('inert'), false);
      assert.equal(sibling.hasAttribute('inert'), true);
      const label = view.surface().getAttribute('aria-labelledby');
      sibling.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
      await flush();
      assert.equal(view.surface().getAttribute('aria-labelledby'), label);
      await click(close(view));
      assert.equal(view.surface().hasAttribute('inert'), true);
      assert.ok(view.tiles().every((button) => !button.hasAttribute('inert')));
      assert.equal(selected.getBoundingClientRect().width, 396);
    }
  } finally {
    view.dispose();
  }
});

test('narrow receipt preview prioritizes factual outcome/history warning and pending reply is not stale-result copy', async () => {
  const view = await mount();
  try {
    for (const [status, label] of [
      ['failed', '本轮未完成'],
      ['stopped', '已停止'],
      ['waiting', '等待你的回答'],
    ]) {
      view.props.summary.status = status;
      await flush();
      assert.match(
        view.tiles()[1].querySelector('.review-tile-status').textContent,
        new RegExp(label),
      );
      assert.equal(
        view.tiles()[1].querySelector('.review-tile-preview').textContent,
        '对话未完整保存',
      );
      assert.equal(
        getComputedStyle(view.tiles()[1].querySelector('.review-tile-status')).flexShrink,
        '0',
      );
    }
    view.props.summary = null;
    view.props.project.messages.push({
      id: 'u4',
      role: 'user',
      content: 'New request after earlier reply',
      created_at: '',
    });
    await flush();
    assert.match(view.tiles()[0].textContent, /尚无新回复/);
    assert.doesNotMatch(view.tiles()[0].textContent, /Latest saved answer/);
    view.props.running = true;
    await flush();
    assert.match(view.tiles()[0].textContent, /正在处理/);
  } finally {
    view.dispose();
  }
});

test('resize with no safe reading space restores a surviving trigger only for focus inside the reader', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0]);
    assert.equal(document.activeElement, close(view));
    geometry.dock.top = 70;
    window.dispatchEvent(new Event('resize'));
    runFrames();
    await flush();
    assert.equal(visible(view), false);
    assert.equal(document.activeElement, view.tiles()[0]);
    assert.equal(view.surface().contains(document.activeElement), false);
  } finally {
    view.dispose();
  }
});

test('resize with no reading space never pulls existing focus away from a background control', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0]);
    const composer = view.root.querySelector('.composer');
    composer.focus();
    geometry.dock.top = 70;
    window.dispatchEvent(new Event('resize'));
    runFrames();
    await flush();
    assert.equal(visible(view), false);
    assert.equal(document.activeElement, composer);
  } finally {
    view.dispose();
  }
});

test('unchanged observer/scroll notifications cannot cancel a live visible morph', async () => {
  const view = await mount();
  try {
    await click(view.tiles()[0], true);
    const entry = tileMotion();
    entry.progress = 0.4;
    for (const observer of observers) observer.callback();
    document.dispatchEvent(new Event('scroll'));
    runFrames();
    await flush();
    assert.equal(entry.cancelled, false);
    assert.equal(entry.progress, 0.4);
    await finish();
    assert.equal(visible(view), true);
  } finally {
    view.dispose();
  }
});
