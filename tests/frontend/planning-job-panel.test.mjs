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
export const env = { calls: [], continuation: async()=>{}, cancellation: async()=>{}, refresh: async()=>{} };
export const workspace = reactive({busy:false,error:''});
export const agent = reactive({running:false,projectId:'P1',cancellingJobId:''});
export const useWorkspace=()=>({state:workspace,refresh:()=>env.refresh(),setError:error=>workspace.error=error});
export const useAgent=()=>({state:agent,
  continuePlanningJob:async(...args)=>{env.calls.push(['continue',...args]);return env.continuation();},
  cancelPlanningJob:async(...args)=>{env.calls.push(['cancel',...args]);agent.cancellingJobId=args[1];return env.cancellation();}
});`);
const { env, workspace, agent } = await import(stateUrl);
const imports = {
  vue,
  '../../composables/useWorkspace': stateUrl,
  '../../composables/useAgent': stateUrl,
};
const code = compile(
  compileScript(parse(source('components/agent/PlanningJobPanel.vue')).descriptor, {
    id: 'planning-job',
    inlineTemplate: true,
  }).content,
).replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => `from ${JSON.stringify(imports[name])}`);
const Panel = (await import(moduleUrl(code))).default;
const tick = async () => {
  await nextTick();
  await nextTick();
};
function mount(overrides = {}) {
  workspace.busy = false;
  workspace.error = '';
  agent.running = false;
  agent.cancellingJobId = '';
  env.calls.length = 0;
  env.continuation = async () => {};
  env.cancellation = async () => {};
  env.refresh = async () => {};
  const job = reactive({
    id: 'job-1',
    write_version: 4,
    status: 'paused',
    source_id: 'source-1',
    phase_id: 'phase-1',
    phase_number: 1,
    stop_reason: 'phase_budget_boundary',
    can_continue: true,
    authorization_needed: false,
    continue_pins: { record_hash: 'retained-checkpoint' },
    limits: { max_phases: 2, max_calls: 10, max_input_bytes: 786432 },
    spend: { calls: 5, input_bytes: 390000, tokens: 0, usage_complete: false },
    phase_spend: { calls: 5, input_bytes: 390000 },
    phase_limits: { max_calls: 5, max_total_input_bytes: 393216 },
    progress: {
      retained_checkpoints: 3,
      new_checkpoints: 2,
      completed_units: 5,
      runnable_units: 2,
      held_units: 1,
      remaining_units: 3,
      remaining_call_lower_bound: null,
    },
    ...overrides,
  });
  let authorizations = 0;
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    render: () => h(Panel, { projectId: 'P1', job, onAuthorize: () => authorizations++ }),
  });
  app.mount(root);
  return {
    root,
    job,
    authorizations: () => authorizations,
    close: () => {
      app.unmount();
      root.remove();
    },
  };
}
const button = (root, text) =>
  [...root.querySelectorAll('button')].find((node) => node.textContent.trim() === text);

test('planning job panel shows saved progress, two distinct budgets and honest unreported usage', async () => {
  const ui = mount();
  try {
    const text = ui.root.textContent;
    assert.match(text, /保留 3 · 新增 2/);
    assert.match(text, /已完成 5 · 可执行 2 · 暂缓 1 · 剩余 3/);
    assert.match(text, /规划传输的生成单元进度，不代表编码里程碑完成/);
    assert.match(text, /阶段 1 \/ 2 · 本阶段调用 5 \/ 5/);
    assert.match(text, /累计调用 5 \/ 已授权 10 · 累计输入 390,000 \/ 已授权 786,432 B/);
    assert.match(text, /Token 用量未完整报告/);
    assert.match(text, /剩余调用数下界：未知/);
    assert.match(text, /phase_budget_boundary/);
    ui.job.spend = { calls: 6, input_bytes: 410000, tokens: 1823, usage_complete: true };
    ui.job.progress.remaining_call_lower_bound = 3;
    await tick();
    assert.match(ui.root.textContent, /已报告 token：1,823/);
    assert.doesNotMatch(ui.root.textContent, /Token 用量未完整报告/);
    assert.match(ui.root.textContent, /剩余调用数下界：3/);
  } finally {
    ui.close();
  }
});

test('collapsed technical details keep the outcome and actions visible without starting requests', async () => {
  const ui = mount();
  try {
    const details = ui.root.querySelector('details');
    const summary = ui.root.querySelector('.planning-job-summary');
    const actions = ui.root.querySelector('.planning-job-actions');
    assert.equal(details.open, false);
    assert.match(summary.textContent, /本阶段额度已用完/);
    assert.match(summary.textContent, /保留 3 · 新增 2/);
    assert.match(summary.textContent, /不代表代码实现或验收完成/);
    assert.equal(summary.getAttribute('aria-live'), 'polite');
    assert.doesNotMatch(summary.textContent, /job-1|source-1|phase_budget_boundary|生成单元|token|390,000/i);
    assert.equal(details.contains(summary), false);
    assert.equal(details.contains(actions), false);
    assert.ok(button(actions, '在已授权额度内继续'));
    assert.ok(button(actions, '取消作业'));
    details.querySelector('summary').click();
    await tick();
    assert.equal(details.open, true);
    assert.match(details.textContent, /job-1.*source-1/);
    assert.match(details.textContent, /原因代码：phase_budget_boundary/);
    details.querySelector('summary').click();
    await tick();
    assert.equal(details.open, false);
    assert.equal(env.calls.length, 0);
    ui.job.status = 'applied';
    ui.job.stop_reason = 'applied';
    ui.job.can_continue = false;
    await tick();
    assert.match(summary.textContent, /方案已应用/);
    assert.equal(button(actions, '在已授权额度内继续'), undefined);
    assert.equal(button(actions, '取消作业'), undefined);
    assert.equal(env.calls.length, 0);
  } finally {
    ui.close();
  }
});

test('planning job panel repeats neither continued input nor continuation clicks', async () => {
  const ui = mount();
  let release;
  try {
    env.continuation = () =>
      new Promise((resolve) => {
        release = resolve;
      });
    const continueButton = button(ui.root, '在已授权额度内继续');
    continueButton.click();
    continueButton.click();
    await tick();
    assert.equal(env.calls.length, 1);
    assert.deepEqual(env.calls[0], [
      'continue',
      'P1',
      'job-1',
      { record_hash: 'retained-checkpoint' },
    ]);
    assert.equal(button(ui.root, '在已授权额度内继续').disabled, true);
    release();
    await tick();
    ui.job.authorization_needed = true;
    ui.job.can_continue = false;
    ui.job.stop_reason = 'aggregate_call_limit';
    ui.job.status = 'stopped';
    await tick();
    assert.equal(button(ui.root, '在已授权额度内继续'), undefined);
    button(ui.root, '查看新作业授权').click();
    await tick();
    assert.equal(ui.authorizations(), 1);
    assert.equal(env.calls.length, 1, 'authorization link itself must never send');
    assert.match(ui.root.textContent, /需要新授权；不会自动增加额度/);
    assert.match(ui.root.querySelector('.planning-job-summary').textContent, /已达总调用上限/);
    assert.match(ui.root.querySelector('details').textContent, /原因代码：aggregate_call_limit/);
  } finally {
    release?.();
    ui.close();
  }
});

test('planning job panel permits cancellation during a busy provider stream and disables duplicate clicks', async () => {
  const ui = mount({ status: 'running', can_continue: false, continue_pins: null });
  let release;
  try {
    agent.running = true;
    workspace.busy = true;
    env.cancellation = () =>
      new Promise((resolve) => {
        release = resolve;
      });
    await tick();
    const cancel = button(ui.root, '取消作业');
    assert.equal(cancel.disabled, false);
    cancel.click();
    cancel.click();
    await tick();
    assert.deepEqual(env.calls, [['cancel', 'P1', 'job-1']]);
    assert.equal(button(ui.root, '正在取消…').disabled, true);
    ui.job.status = 'cancelled';
    ui.job.stop_reason = 'cancelled';
    release();
    await tick();
    assert.equal(button(ui.root, '取消作业'), undefined);
    assert.equal(button(ui.root, '在已授权额度内继续'), undefined);
    assert.match(ui.root.textContent, /有界规划作业 · 已取消/);
  } finally {
    release?.();
    ui.close();
  }
});

test('planning job panel restored from a project snapshot refreshes without repeating any job operation', async () => {
  const ui = mount();
  try {
    let reads = 0;
    env.refresh = async () => {
      reads++;
      ui.job.status = 'applied';
      ui.job.can_continue = false;
      ui.job.stop_reason = null;
    };
    button(ui.root, '刷新状态').click();
    await tick();
    assert.equal(reads, 1);
    assert.equal(env.calls.length, 0);
    assert.match(ui.root.textContent, /方案已应用/);
    assert.equal(button(ui.root, '在已授权额度内继续'), undefined);
    env.refresh = async () => {
      throw new Error('refresh offline');
    };
    button(ui.root, '刷新状态').click();
    await tick();
    assert.equal(workspace.error, 'refresh offline');
    assert.match(ui.root.textContent, /方案已应用/);
  } finally {
    ui.close();
  }
});

test('planning job controls override global full-width inputs and retain scrollable compact panels', () => {
  const dock = parse(source('components/agent/AgentDock.vue')).descriptor;
  const panel = parse(source('components/agent/PlanningJobPanel.vue')).descriptor;
  const styles = document.createElement('style');
  styles.textContent =
    source('styles/base.css') +
    dock.styles.map((style) => style.content).join('\n') +
    panel.styles.map((style) => style.content).join('\n');
  document.head.append(styles);
  const fixture = document.createElement('div');
  fixture.innerHTML =
    '<div class="planning-job-authorization"><label class="planning-job-opt-in"><input type="checkbox"><span>确认本次有界规划作业</span></label><fieldset></fieldset></div><section class="planning-job-panel"><div class="planning-job-heading"></div><div class="planning-job-summary"></div><div class="planning-job-actions"></div><details class="planning-job-details"><div class="planning-job-progress"></div></details></section>';
  document.body.append(fixture);
  try {
    const style = (selector) => window.getComputedStyle(fixture.querySelector(selector));
    assert.equal(style('input').width, '16px');
    assert.equal(style('input').height, '16px');
    assert.equal(style('input').padding, '0px');
    assert.equal(style('input').flexBasis, '16px');
    assert.equal(parseFloat(style('span').minWidth), 0);
    assert.equal(parseFloat(style('fieldset').minWidth), 0);
    assert.equal(style('.planning-job-authorization').overflowY, 'auto');
    assert.equal(style('.planning-job-authorization').minHeight, '24px');
    assert.equal(style('.planning-job-panel').minHeight, '112px');
    assert.equal(style('.planning-job-panel').overflow, 'hidden');
    assert.equal(parseFloat(style('.planning-job-progress').minHeight), 0);
    assert.equal(style('.planning-job-details').overflowY, 'auto');
    assert.equal(style('.planning-job-summary').flexShrink, '0');
    assert.equal(style('.planning-job-heading').flexShrink, '0');
    assert.equal(style('.planning-job-actions').flexShrink, '0');
    assert.match(dock.template.content, /<span>本次使用有界规划作业/);
    assert.match(dock.template.content, /<span\s*>\s*确认按以上总额度/);
  } finally {
    fixture.remove();
    styles.remove();
  }
});
