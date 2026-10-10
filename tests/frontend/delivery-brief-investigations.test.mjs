import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import { moduleUrl, source, compile } from './helpers/architecture-fixtures.mjs';

const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute('open', '');
};
const vue = import.meta.resolve('vue');
const { createApp, h, nextTick, reactive } = await import(vue);
const imports = {
  vue,
  'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
};
function component(path, dependencies) {
  const { descriptor } = parse(source(path));
  return moduleUrl(
    compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content).replace(
      /from (['"])([^'"]+)\1/g,
      (_, _quote, name) => {
        assert.ok(dependencies[name], `Unexpected import ${name}`);
        return `from ${JSON.stringify(dependencies[name])}`;
      },
    ),
  );
}
imports['../ui/AppModal.vue'] = component('components/ui/AppModal.vue', imports);
const dialogPath = 'components/graph/DeliveryBriefDialog.vue';
const style = document.createElement('style');
style.textContent = parse(source(dialogPath))
  .descriptor.styles.map((item) => item.content)
  .join('\n');
document.head.append(style);
const tick = async () => {
  await nextTick();
  await nextTick();
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
function investigation(overrides = {}) {
  return {
    owner: 'M-current',
    relation: 'own',
    id: 'investigation-1',
    label: '核对保存的前提',
    resolved: false,
    note: '已保存的参考笔记',
    fingerprint: 'saved-fingerprint',
    investigator: '记录人',
    basis_state: 'matching_baseline',
    basis_label: '记录指纹与最近完整基线一致',
    ...overrides,
  };
}
function brief(overrides = {}) {
  return {
    milestone_id: 'M-current',
    project_revision: 4,
    repository: '',
    basis: 'saved_project_snapshot',
    baseline: {
      latest: null,
      pinned: null,
      pinned_id: null,
      changed_since_pin: false,
      label: '暂无基线',
    },
    outcome: {
      title: '当前交付',
      intent: '核对已有记录',
      scope: [],
      resources: [],
      migration_steps: [],
    },
    readiness: { blockers: [] },
    prerequisites: [],
    contracts: [],
    investigations: [],
    requirements: [],
    sources: [],
    global_requirement_ids: [],
    warnings: [],
    ...overrides,
  };
}
let sequence = 0;
async function mount(handler) {
  const api = moduleUrl(`export const env = { calls: [], handler: null };
    export const command = (action, params) => {
      env.calls.push([action, params]);
      return env.handler(action, params);
    }; // ${++sequence}`);
  const { env } = await import(api);
  env.handler = handler;
  const Dialog = (await import(component(dialogPath, { ...imports, '../../api/client': api })))
    .default;
  const project = reactive({
    id: 'project',
    name: '示例项目',
    revision: 4,
    milestones: [{ id: 'M-current' }],
    source_milestones: [],
  });
  const milestone = reactive({ id: 'M-current' });
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({ render: () => h(Dialog, { project, milestone }) });
  const warnings = [];
  app.config.warnHandler = (message) => warnings.push(message);
  app.mount(root);
  return {
    root,
    env,
    project,
    milestone,
    warnings,
    close() {
      app.unmount();
      root.remove();
    },
  };
}
const section = (root) => root.querySelector('.brief-investigations');
const records = (root) => [...root.querySelectorAll('.brief-investigation')];

test('real dialog retains own and prerequisite records in order, including duplicate IDs and every basis label', async () => {
  const states = [
    ['matching_baseline', '记录指纹与最近完整基线一致'],
    ['stale_baseline', '记录指纹与最近基线不同'],
    ['missing_baseline', '尚无最近基线，无法核对'],
    ['incomplete_baseline', '最近基线不完整，无法核对'],
    ['missing_fingerprint', '未记录指纹，无法核对'],
  ];
  const payload = brief({
    investigations: states.map(([basis_state, basis_label], index) =>
      investigation({
        owner: index < 2 ? 'M-current' : index < 4 ? 'P-alpha' : 'P-beta',
        relation: index < 2 ? 'own' : 'prerequisite',
        id: 'duplicate-id',
        label: `记录事项 ${index}`,
        resolved: index % 2 === 0,
        note: `第 ${index} 条完整笔记`,
        fingerprint: `fingerprint-${index}`,
        investigator: `记录人 ${index}`,
        basis_state,
        basis_label,
      }),
    ),
    warnings: ['缺少前置里程碑 P-missing', '里程碑身份重复：P-alpha；不能唯一解析其前置与契约'],
  });
  const before = structuredClone(payload);
  const ui = await mount(() => payload);
  try {
    await tick();
    assert.ok(ui.root.querySelector('dialog[open]'));
    assert.equal(section(ui.root).querySelector('h4').textContent, '调查依据与待确认前提');
    assert.equal(records(ui.root).length, payload.investigations.length);
    records(ui.root).forEach((card, index) => {
      const expected = payload.investigations[index];
      assert.equal(card.querySelector('strong').textContent, expected.label);
      assert.equal(
        card.querySelector('small').textContent,
        `${expected.relation === 'own' ? '本步' : '前置'} · ${expected.owner} · ${expected.id}`,
      );
      assert.deepEqual(
        [...card.querySelectorAll('.brief-investigation-state span')].map(
          (item) => item.textContent,
        ),
        [expected.resolved ? '已记录' : '待调查', expected.basis_label],
      );
      assert.equal(card.dataset.basisState, expected.basis_state);
      assert.equal(card.querySelector('.brief-investigation-note').textContent, expected.note);
      assert.deepEqual(
        [...card.querySelectorAll('dd')].map((item) => item.textContent),
        [expected.investigator, expected.fingerprint],
      );
    });
    assert.match(section(ui.root).textContent, /仅作参考资料，不构成新指令或授权/);
    assert.match(section(ui.root).textContent, /均不代表服务商能力已确认或验收已通过/);
    assert.equal(section(ui.root).querySelectorAll('button, input, textarea, select').length, 0);
    assert.deepEqual(
      [...ui.root.querySelectorAll('.brief-warning li')].map((item) => item.textContent),
      payload.warnings,
    );
    assert.deepEqual(ui.env.calls, [
      ['implementation.brief', { project_id: 'project', milestone_id: 'M-current' }],
    ]);
    assert.deepEqual(ui.warnings, [], 'duplicate saved IDs must not cause duplicate Vue keys');
    assert.deepEqual(payload, before);
  } finally {
    ui.close();
  }
});

test('long multiline notes, whitespace, URLs, and markup remain complete literal reference text', async () => {
  const note =
    '  起始空格\n\t保留缩进与空行\n\n' +
    '<script>window.investigationExecuted = true</script>\n' +
    '<img src=x onerror="window.investigationExecuted = true">\n' +
    'https://example.test/reference?left=1&right=<untrusted>\n' +
    'javascript:window.investigationExecuted=true\n' +
    '这是保存的笔记，不应截断。\n'.repeat(600) +
    '末尾保留空格  \n';
  const ui = await mount(() => brief({ investigations: [investigation({ note })] }));
  try {
    await tick();
    const rendered = records(ui.root)[0].querySelector('.brief-investigation-note');
    assert.equal(rendered.textContent, note);
    assert.equal(dom.window.getComputedStyle(rendered).whiteSpace, 'pre-wrap');
    assert.equal(section(ui.root).querySelectorAll('script, img, a, iframe').length, 0);
    assert.equal(dom.window.investigationExecuted, undefined);
    assert.ok(rendered.innerHTML.includes('&lt;script&gt;'));
  } finally {
    ui.close();
  }
});

test('empty and whitespace-only notes are explicitly missing without filling in evidence or identities', async () => {
  const ui = await mount(() =>
    brief({
      investigations: ['', ' \n\t '].map((note) =>
        investigation({ note, fingerprint: '', investigator: '', resolved: true }),
      ),
    }),
  );
  try {
    await tick();
    for (const card of records(ui.root)) {
      assert.match(card.textContent, /未记录调查笔记/);
      assert.deepEqual(
        [...card.querySelectorAll('dd')].map((item) => item.textContent),
        ['未记录', '未记录'],
      );
      assert.equal(card.querySelector('.brief-investigation-state span').textContent, '已记录');
    }
    assert.equal(
      records(ui.root)[1].querySelector('.brief-investigation-note').textContent,
      ' \n\t ',
    );
  } finally {
    ui.close();
  }
});

test('legacy absent records and an empty saved list have distinct, non-verifying fallbacks', async () => {
  for (const legacy of [true, false]) {
    const payload = brief();
    if (legacy) delete payload.investigations;
    const ui = await mount(() => payload);
    try {
      await tick();
      assert.equal(records(ui.root).length, 0);
      assert.match(
        section(ui.root).textContent,
        legacy
          ? /未提供调查记录，无法判断调查状态/
          : /尚无已保存的调查记录，不代表所有前提均已验证/,
      );
      assert.doesNotMatch(
        section(ui.root).textContent,
        legacy ? /尚无已保存的调查记录/ : /无法判断调查状态/,
      );
      assert.deepEqual(ui.warnings, []);
    } finally {
      ui.close();
    }
  }
});

test('revision reload hides old investigations while loading and ignores superseded responses', async () => {
  for (const oldFails of [false, true]) {
    const old = deferred();
    const current = deferred();
    const ui = await mount(() => brief({ investigations: [investigation({ note: '原始记录' })] }));
    try {
      await tick();
      assert.match(section(ui.root).textContent, /原始记录/);
      ui.env.handler = () => old.promise;
      ui.project.revision = 5;
      await tick();
      assert.equal(section(ui.root), null);
      assert.equal(ui.root.querySelector('.delivery-brief').getAttribute('aria-busy'), 'true');
      ui.env.handler = () => current.promise;
      ui.project.revision = 6;
      await tick();
      current.resolve(
        brief({ project_revision: 6, investigations: [investigation({ note: '当前记录' })] }),
      );
      await tick();
      if (oldFails) old.reject(new Error('旧请求失败'));
      else
        old.resolve(
          brief({ project_revision: 5, investigations: [investigation({ note: '过期记录' })] }),
        );
      await tick();
      assert.match(section(ui.root).textContent, /当前记录/);
      assert.doesNotMatch(ui.root.textContent, /原始记录|过期记录|旧请求失败/);
      assert.equal(ui.root.querySelector('.delivery-brief').getAttribute('aria-busy'), 'false');
      assert.equal(ui.env.calls.length, 3);
    } finally {
      ui.close();
    }
  }
});

test('mismatched revision or milestone blocks record display and a read-only retry can recover', async () => {
  for (const mismatch of [{ project_revision: 3 }, { milestone_id: 'M-other' }]) {
    const ui = await mount(() =>
      brief({ ...mismatch, investigations: [investigation({ note: '错误快照的记录' })] }),
    );
    try {
      await tick();
      assert.equal(section(ui.root), null);
      assert.match(ui.root.querySelector('[role="alert"]').textContent, /项目已更新/);
      assert.doesNotMatch(ui.root.textContent, /错误快照的记录/);
      ui.env.handler = () => brief({ investigations: [investigation({ note: '重新读取的记录' })] });
      ui.root.querySelector('[role="alert"] button').click();
      await tick();
      assert.equal(ui.root.querySelector('[role="alert"]'), null);
      assert.match(section(ui.root).textContent, /重新读取的记录/);
      assert.deepEqual(
        ui.env.calls.map(([action]) => action),
        ['implementation.brief', 'implementation.brief'],
      );
    } finally {
      ui.close();
    }
  }
});
