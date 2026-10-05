import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';
const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const vue = import.meta.resolve('vue');
const { createApp, h, nextTick, reactive } = await import(vue);
const stateUrl = moduleUrl(`import { reactive } from ${JSON.stringify(vue)};
export const workspace = reactive({busy:false});
export const agent = reactive({running:false,projectId:'P1',turnId:''});
export const calls=[];
export const useWorkspace=()=>({state:workspace,perform:async()=>{}});
export const useAgent=()=>({state:agent,recheck:async(...args)=>{ calls.push(args); agent.running=true; workspace.busy=true; }});`);
const { workspace, agent, calls } = await import(stateUrl);
const imports = {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
  '../../lib/planPreview': moduleUrl(compile(source('lib/planPreview.ts'))),
  '../../composables/useWorkspace': stateUrl,
  '../../composables/useAgent': stateUrl,
  '../design/DiagramView.vue': moduleUrl('export default {render:()=>null}'),
  './PlanReviewDisclosure.vue': moduleUrl('export default {render:()=>null}'),
};
const code = compile(
  compileScript(parse(source('components/workspace/PlanCandidatePanel.vue')).descriptor, {
    id: 'recheck',
    inlineTemplate: true,
  }).content,
).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => `from ${JSON.stringify(imports[name])}`);
const Panel = (await import(moduleUrl(code))).default;
const tick = async () => {
  await nextTick();
  await nextTick();
};
function mount() {
  workspace.busy = false;
  agent.running = false;
  agent.turnId = '';
  calls.length = 0;
  const project = {
    id: 'P1',
    revision: 2,
    architectures: [],
    milestones: [],
    behaviors: [],
    plan_contract: { sources: [], requirements: [], bindings: [] },
  };
  const candidate = reactive({
    id: 'saved-candidate',
    revision: 7,
    base_revision: 2,
    status: 'needs_resolution',
    project,
    report: { findings: [] },
    input: 'saved input',
    created_at: '2020-01-01T00:00:00Z',
    generation_progress: { state: 'ready', checkpoint_count: 5 },
    metrics: { provider_calls: 1, tokens: 0, elapsed_seconds: 0 },
    review_recheck: {
      version: 'candidate-review-only/v1',
      candidate_id: 'saved-candidate',
      record_hash: 'pinned',
    },
  });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(Panel, { candidate, canonical: project }) });
  app.mount(root);
  return {
    candidate,
    project,
    root,
    close: () => {
      app.unmount();
      root.remove();
    },
  };
}
const button = (root) =>
  [...root.querySelectorAll('button')].find((b) => b.textContent.trim() === '重新评审');
test('complete candidate exposes exact pinned action and blocks double clicks', async () => {
  const ui = mount();
  try {
    const action = button(ui.root);
    assert.ok(action);
    action.click();
    action.click();
    await tick();
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'P1');
    assert.equal(calls[0][1].record_hash, 'pinned');
    assert.equal(button(ui.root), undefined);
  } finally {
    ui.close();
  }
});
test('stale incomplete terminal question and active candidates never expose recheck', async () => {
  for (const change of [
    (c) => (c.status = 'stale'),
    (c) => (c.generation_progress.state = 'staged'),
    (c) => (c.status = 'applied'),
    (c) => (c.base_revision = 1),
    (c) => (c.review_recheck = null),
    () => (agent.running = true),
  ]) {
    const ui = mount();
    try {
      change(ui.candidate);
      await tick();
      assert.equal(button(ui.root), undefined);
    } finally {
      ui.close();
    }
  }
});
test('same candidate new turn uses new review elapsed time and live status', async () => {
  const ui = mount();
  try {
    ui.candidate.status = 'reviewing';
    ui.candidate.review_attempt = {
      id: 'new-review-turn',
      started_at: new Date().toISOString(),
      closed_at: null,
      write_version: 2,
    };
    agent.running = true;
    agent.turnId = 'new-review-turn';
    workspace.busy = true;
    await tick();
    assert.match(ui.root.textContent, /独立重新评审.*new-review-turn/s);
    assert.match(ui.root.textContent, /已等待约\s*0 秒/);
    assert.ok(ui.root.querySelector('.spinning'));
  } finally {
    ui.close();
  }
});
