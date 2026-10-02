import { surfaceMotionUrl } from './helpers/surface-motion-fixtures.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { parse, compileScript } from '@vue/compiler-sfc';
import {
  moduleUrl,
  source,
  compile,
  browseStoreUrl,
  browseModelUrl,
  rolesUrl,
} from './helpers/architecture-fixtures.mjs';
import { composerDocumentUrl } from './helpers/composer-fixtures.mjs';

const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement'])
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
const { createApp, h, nextTick } = await import('vue');
const vue = import.meta.resolve('vue');
const tick = async () => {
  await nextTick();
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
const project = (id, created_at = `original-${id}`, revision = 0) => ({
  id,
  name: id,
  description: `Description ${id}`,
  repository: '',
  is_demo: false,
  archived: false,
  acceptance: { passed: 0, total: 0, achieved: false },
  created_at,
  revision,
  milestones: [{ id: `${id}-node`, title: 'A planning node' }],
  source_milestones: [],
  architectures: [],
  messages: [],
});
let sequence = 0;
async function harness(realViews = false) {
  const id = ++sequence;
  const apiUrl = moduleUrl(`export const env = { handler: null };
    export const command = (action, params = {}) => env.handler(action, params);
    export const readyTransport = async () => {}; // ${id}`);
  const draftsUrl = moduleUrl(
    compile(source('composables/useAgentDrafts.ts'))
      .replace("from 'vue'", `from ${JSON.stringify(vue)}`)
      .replace("from '../lib/composerDocument'", `from ${JSON.stringify(composerDocumentUrl)}`) +
      `\n// ${id}`,
  );
  const workflowUrl = moduleUrl(
    compile(source('composables/useWorkflowDrafts.ts')).replace(
      "from 'vue'",
      `from ${JSON.stringify(vue)}`,
    ) + `\n// ${id}`,
  );
  const storeImports = {
    vue,
    '../api/client': apiUrl,
    './useAgentDrafts': draftsUrl,
    './useWorkflowDrafts': workflowUrl,
    './useArchitectureBrowse': browseStoreUrl,
    './useNotifications': moduleUrl('export const useNotifications = () => ({ push() {} });'),
  };
  const replace = (code, imports) =>
    code.replace(/from (['"])([^'"]+)\1/g, (_, quote, name) => {
      assert.ok(imports[name], `Unexpected dependency ${name}`);
      return `from ${JSON.stringify(imports[name])}`;
    });
  const workspaceUrl = moduleUrl(
    replace(compile(source('composables/useWorkspace.ts')), storeImports),
  );
  const { useWorkspace } = await import(workspaceUrl);
  const workspace = useWorkspace();
  const records = new Map(['A', 'B', 'C'].map((id) => [id, project(id)]));
  const handlers = {};
  const { env: api } = await import(apiUrl);
  api.handler = async (action, params) => {
    if (handlers[action]) return handlers[action](params);
    if (action === 'projects.list') return [...records.values()];
    if (action === 'settings.get') return { provider: { config: { model: 'test' } } };
    if (action === 'projects.bootstrap') return;
    if (action === 'projects.get') {
      if (!records.has(params.project_id)) throw new Error('Project unavailable');
      return structuredClone(records.get(params.project_id));
    }
    if (action === 'projects.delete') {
      records.delete(params.project_id);
      return { deleted: true };
    }
    if (action === 'projects.list_archived') return [];
    if (action === 'projects.restore_archived') {
      const restored = !records.has(params.project_id);
      const value = records.get(params.project_id) ?? project(params.project_id);
      records.set(value.id, value);
      return { project: structuredClone(value), restored };
    }
    if (action === 'projects.restore') {
      const restored = project(params.project_id);
      records.set(restored.id, restored);
      return restored;
    }
    throw new Error(`Unexpected command ${action}`);
  };
  const saved = new Map([['evograph.project', 'A']]);
  globalThis.localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
  };
  if (realViews) {
    for (const record of records.values())
      Object.assign(record, {
        readiness: {},
        baselines: [],
        research: [],
        uml_diagrams: [],
        attachments: [],
        milestones: Array.from({ length: 32 }, (_, i) => ({
          id: `${record.id}-node-${i + 1}`,
          title: `Slice ${i + 1}`,
          dependencies: [],
          dependency_reasons: {},
          scope: [],
        })),
        source_diagram: {
          id: 'source',
          nodes: [
            { id: 'source-one', label: 'Source module', role: 'backend' },
            { id: 'source-two', label: 'Source data', role: 'database' },
          ],
          edges: [],
          groups: [],
        },
        architectures: [
          {
            number: 1,
            diagram: {
              id: 'target',
              nodes: [{ id: 'target-one', label: 'Target module' }],
              edges: [],
              groups: [],
            },
            technologies: [],
            decisions: [],
            research_ids: [],
          },
        ],
      });
  }
  await workspace.init();
  const agentUrl = moduleUrl(`import { reactive } from ${JSON.stringify(vue)};
    export const env = { free: [], resume: [], locates: [], mounted: [], callbacks: null };
    export const agent = { state: reactive({ projectId: 'A', follow: {}, view: 'graph', running: false, updates: {}, navigationTick: 0, pulse: 7, focusId: 'A-node-20', diagramId: 'old-diagram' }),
      freeView: id => { env.free.push(id); agent.state.follow[id] = false; },
      resumeFollow: id => { env.resume.push(id); agent.state.follow[id] = true; agent.state.pulse++; } };
    export const useAgent = () => agent; // ${id}`);
  const viewsUrl = moduleUrl(`import { h, markRaw } from ${JSON.stringify(vue)};
    import { env } from ${JSON.stringify(agentUrl)};
    export const workspaceViews = ['graph', 'architecture', 'design', 'evidence', 'activity'].map(id => ({ id, label: id, icon: markRaw({render(){return null;}}), component: markRaw({ setup(_, { expose }) {
      env.mounted.push(id); expose({ locate: id => env.locates.push(id), fit() {}, reset() {} });
      return () => h('div', { 'data-view': id });
    } }) }));`);
  const stub = moduleUrl('export default { inheritAttrs: false, render() { return null; } };');
  const imports = {
    '../../composables/useSurfaceMotion': surfaceMotionUrl,
    './composables/useSurfaceMotion': surfaceMotionUrl,
    vue,
    'lucide-vue-next': import.meta.resolve('lucide-vue-next'),
    '../../composables/useAgent': agentUrl,
    '../../composables/useWorkspace': workspaceUrl,
    '../../lib/workspaceViews': viewsUrl,
    './WorkspaceHeader.vue': stub,
    '../graph/MilestoneGraph.vue': stub,
    '../graph/MilestoneFinder.vue': stub,
    '../graph/MilestoneInspector.vue': stub,
    '../graph/SourceInspector.vue': stub,
    '../agent/AgentDock.vue':
      moduleUrl(`import { h, getCurrentInstance } from ${JSON.stringify(vue)};
      import { env } from ${JSON.stringify(agentUrl)};
      export default { props: ['project'], emits: ['resume', 'locate'], setup(props, { emit }) {
        const instance = getCurrentInstance();
        return () => { env.callbacks = { resume: instance.vnode.props.onResume, locate: instance.vnode.props.onLocate };
          return h('aside', {}, [h('button', { class: 'resume', onClick: () => emit('resume') }, 'Resume'), h('button', { class: 'locate', onClick: () => emit('locate', props.project.id + '-node') }, 'Locate')]); };
      } };`),
  };
  const component = async (path) => {
    const { descriptor } = parse(source(path));
    const url = moduleUrl(
      replace(
        compile(compileScript(descriptor, { id: path, inlineTemplate: true }).content),
        imports,
      ),
    );
    return { url, view: (await import(url)).default };
  };
  let flows = null;
  const originals = {};
  const frames = new Map();
  if (realViews) {
    for (const name of ['requestAnimationFrame', 'cancelAnimationFrame', 'ResizeObserver'])
      originals[name] = globalThis[name];
    let frameId = 0;
    globalThis.requestAnimationFrame = (callback) => {
      const id = ++frameId;
      frames.set(id, callback);
      return id;
    };
    globalThis.cancelAnimationFrame = (id) => frames.delete(id);
    globalThis.ResizeObserver = class {
      constructor(callback) {
        this.callback = callback;
      }
      observe(node) {
        this.callback([{ contentRect: { width: node.clientWidth, height: node.clientHeight } }]);
      }
      disconnect() {}
    };
    originals.clientWidth = Object.getOwnPropertyDescriptor(
      dom.window.HTMLElement.prototype,
      'clientWidth',
    );
    originals.clientHeight = Object.getOwnPropertyDescriptor(
      dom.window.HTMLElement.prototype,
      'clientHeight',
    );
    Object.defineProperty(dom.window.HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 880,
    });
    Object.defineProperty(dom.window.HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get: () => 320,
    });
    const flowUrl = moduleUrl(`import { h, ref } from ${JSON.stringify(vue)};
      export const flows = [];
      export const MarkerType = { ArrowClosed: 'arrow' };
      export const useVueFlow = id => {
        const flow = { id, attrs: null, viewport: { x: 0, y: 0, zoom: 1 }, fits: [], centers: [], cameras: [] }; flows.push(flow);
        return { dimensions: ref({ width: 880, height: 320 }), getViewport: () => ({ ...flow.viewport }),
          setViewport: async (value, options) => { flow.cameras.push([value, options]); flow.viewport = { ...value }; return true; },
          fitView: async options => { flow.fits.push(options); return true; },
          setCenter: async (...args) => { flow.centers.push(args); return true; },
          updateNodeInternals() {}, findNode: id => flow.attrs?.nodes.find(node => node.id === id) };
      };
      export const VueFlow = { inheritAttrs: false, setup(_, { attrs }) { const flow = flows.findLast(item => item.id === attrs.id); flow.attrs = attrs; if (attrs['default-viewport']) flow.viewport = { ...attrs['default-viewport'] }; return () => h('div', { 'data-flow': attrs.id }); } }; // ${id}`);
    ({ flows } = await import(flowUrl));
    Object.assign(imports, {
      '@vue-flow/core': flowUrl,
      '@vue-flow/background': moduleUrl('export const Background = { render() { return null; } };'),
      '@vue-flow/controls': moduleUrl('export const Controls = { render() { return null; } };'),
      '../../lib/architectureRoles': rolesUrl,
      '../../lib/architectureBrowse': browseModelUrl,
      '../../composables/useArchitectureBrowse': browseStoreUrl,
      '../../lib/milestoneViewport': moduleUrl(compile(source('lib/milestoneViewport.ts'))),
      '../../lib/milestoneInteraction': moduleUrl(compile(source('lib/milestoneInteraction.ts'))),
      '../../lib/edgeKinds': moduleUrl(
        'export const edgeKinds = []; export const edgeKind = () => ({ color: "blue" });',
      ),
      '../../lib/graphGeometry': moduleUrl(
        'export const separateBoxes = boxes => boxes; export const routeAroundBoxes = () => []; export const routeArchitectureEdge = () => []; export const architectureLabels = () => [];',
      ),
      '../../lib/architectureLayout': moduleUrl(
        'export const architectureGroupBounds = () => null;',
      ),
      '../../lib/layoutArchitecture': moduleUrl(
        'export const layoutArchitecture = async diagram => new Map(diagram.nodes.map((node, i) => [node.id, { x: i * 346, y: 0 }]));',
      ),
      '../../composables/useGraphLayout': moduleUrl(
        'export const edgeId = (a,b) => a+":"+b; export const layout = async nodes => ({ direction: "RIGHT", routes: new Map(), positions: new Map(nodes.map((node,i) => [node.id, { x: (i % 8)*346, y: Math.floor(i/8)*350 }])) });',
      ),
      // Class/UML providers and routing actions are deliberately inert under the pause.
      '../../composables/useScopedClassDetail':
        moduleUrl(`import { ref } from ${JSON.stringify(vue)};
        export const MAX_DETAIL_COMPONENTS = 3, MAX_DETAIL_FILES = 12, matchingScopedDesigns = () => [], classDetailGuidance = () => '';
        const unused = () => { throw new Error('Paused class detail must never execute'); };
        export const useScopedClassDetail = () => ({ componentIds: ref([]), filePaths: ref(null), open: ref(false), loading: ref(false), result: ref(null), error: ref(''), select: unused, toggle: unused, selectFiles: unused, toggleFile: unused, close: unused, show: unused, reload: unused, showSaved: unused });`),
    });
    for (const name of [
      './MilestoneNode.vue',
      './GraphEdge.vue',
      './ArchitectureNode.vue',
      './ArchitectureGroup.vue',
      '../graph/GraphEdge.vue',
      './DiagramImage.vue',
      './ArchitectureQuality.vue',
      './ComponentPassport.vue',
      './UmlView.vue',
    ])
      imports[name] = stub;
    imports['../projects/EmptyPlanningHandoff.vue'] = (
      await component('components/projects/EmptyPlanningHandoff.vue')
    ).url;
    const graph = await component('components/graph/MilestoneGraph.vue');
    imports['./DiagramView.vue'] = (await component('components/design/DiagramView.vue')).url;
    imports['./ArchitectureBrowser.vue'] = (
      await component('components/design/ArchitectureBrowser.vue')
    ).url;
    const architecture = await component('components/design/ArchitecturePanel.vue');
    imports['../../lib/workspaceViews'] = moduleUrl(`import { markRaw } from ${JSON.stringify(vue)};
      import Graph from ${JSON.stringify(graph.url)}; import Architecture from ${JSON.stringify(architecture.url)};
      export const workspaceViews = ['graph', 'architecture', 'design', 'evidence', 'activity'].map(id => ({ id, label: id, icon: markRaw({ render(){ return null; } }), component: markRaw(id === 'graph' ? Graph : id === 'architecture' ? Architecture : { render(){ return null; } }) }));`);
  }
  imports['../graph/GraphToolbar.vue'] = (await component('components/graph/GraphToolbar.vue')).url;
  const Workspace = (await component('components/workspace/ProjectWorkspace.vue')).view;
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp({
    setup() {
      return () =>
        workspace.state.page === 'projects' && workspace.state.project
          ? h(Workspace, { project: workspace.state.project, key: workspace.state.project.id })
          : h('div', { class: 'settings' });
    },
  });
  app.mount(root);
  const { agent, env } = await import(agentUrl);
  const tab = () => root.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim();
  const choose = async (id) => {
    [...root.querySelectorAll('[role="tab"]')]
      .find((button) => button.textContent.startsWith(id))
      .click();
    await tick();
  };
  return {
    workspace,
    flows,
    browsing: (await import(browseStoreUrl)).architectureBrowse,
    flush: async () => {
      for (let i = 0; i < 12; i++) {
        await nextTick();
        const callbacks = [...frames.values()];
        frames.clear();
        for (const callback of callbacks) await callback();
      }
    },
    records,
    handlers,
    agent,
    env,
    root,
    saved,
    tab,
    choose,
    dispose() {
      app.unmount();
      root.remove();
      if (realViews) {
        for (const name of ['requestAnimationFrame', 'cancelAnimationFrame', 'ResizeObserver']) {
          if (originals[name] === undefined) delete globalThis[name];
          else globalThis[name] = originals[name];
        }
        for (const name of ['clientWidth', 'clientHeight']) {
          if (originals[name])
            Object.defineProperty(dom.window.HTMLElement.prototype, name, originals[name]);
          else delete dom.window.HTMLElement.prototype[name];
        }
      }
    },
  };
}

test('real workspace restores all five tabs across Settings and independent project remounts without navigation replay', async () => {
  const h = await harness();
  try {
    for (const view of ['graph', 'architecture', 'design', 'evidence', 'activity']) {
      await h.choose(view);
      const before = JSON.stringify(h.agent.state);
      const mounted = h.env.mounted.length;
      h.workspace.setPage('settings');
      await tick();
      h.workspace.setPage('projects');
      await tick();
      assert.ok(h.tab().startsWith(view));
      assert.deepEqual(
        h.env.mounted.slice(mounted),
        [view],
        'Remount must not briefly mount graph first',
      );
      assert.equal(JSON.stringify(h.agent.state), before);
      assert.deepEqual(h.env.locates, []);
    }
    await h.choose('architecture');
    await h.workspace.selectProject('B');
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    await h.choose('evidence');
    await h.workspace.selectProject('A');
    await tick();
    assert.equal(h.tab(), 'architecture');
    await h.workspace.selectProject('B');
    await tick();
    assert.equal(h.tab(), 'evidence');
    assert.deepEqual([...h.saved.keys()], ['evograph.project'], 'Tabs remain session-only');
  } finally {
    h.dispose();
  }
});

test('offscreen Agent navigation is not replayed; later mounted events and explicit Resume still work', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    h.agent.state.follow.A = true;
    h.workspace.setPage('settings');
    await tick();
    Object.assign(h.agent.state, {
      view: 'evidence',
      focusId: 'offscreen',
      diagramId: 'offscreen',
    });
    h.agent.state.navigationTick++;
    await tick();
    const before = JSON.stringify(h.agent.state);
    h.workspace.setPage('projects');
    await tick();
    assert.equal(h.tab(), 'architecture');
    assert.equal(JSON.stringify(h.agent.state), before);
    assert.deepEqual(h.env.locates, []);
    h.agent.state.view = 'activity';
    h.agent.state.navigationTick++;
    await tick();
    assert.equal(h.tab(), 'activity');
    await h.choose('design');
    h.agent.state.view = 'evidence';
    h.agent.state.navigationTick++;
    await tick();
    assert.equal(h.tab(), 'design', 'Explicit tab choice disables follow');
    h.root.querySelector('.resume').click();
    await tick();
    assert.equal(h.tab(), 'evidence');
    assert.deepEqual(h.env.resume, ['A']);
    assert.equal(h.agent.state.pulse, 8, 'Only explicit Resume changes the Agent pulse');
  } finally {
    h.dispose();
  }
});

test('mounted follow events obey project, known-view and explicit-tab intent boundaries', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    h.agent.state.follow.A = true;
    Object.assign(h.agent.state, { projectId: 'B', view: 'design' });
    h.agent.state.navigationTick++;
    await tick();
    assert.equal(h.tab(), 'architecture');
    Object.assign(h.agent.state, { projectId: 'A', view: 'invalid-view' });
    h.agent.state.navigationTick++;
    await tick();
    assert.equal(h.tab(), 'architecture');
    h.agent.state.view = 'activity';
    h.agent.state.navigationTick++;
    // A user choice in the same Vue flush remains the last explicit intent.
    [...h.root.querySelectorAll('[role="tab"]')]
      .find((button) => button.textContent === 'evidence')
      .click();
    await tick();
    assert.equal(h.tab(), 'evidence');
  } finally {
    h.dispose();
  }
});

test('receipt locate deliberately chooses graph and selects only the requested node', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    h.root.querySelector('.locate').click();
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    assert.equal(h.workspace.state.selectedId, 'A-node');
    assert.deepEqual(h.env.locates, ['A-node']);
    h.workspace.setPage('settings');
    await tick();
    h.workspace.setPage('projects');
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    assert.deepEqual(h.env.locates, ['A-node'], 'Settings return never replays locate');
  } finally {
    h.dispose();
  }
});

test('deletion and restore issue fresh tab leases even when id and created_at are reused', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    const old = h.workspace.bindWorkspaceTab(h.workspace.state.project);
    await h.workspace.deleteProject(h.workspace.state.project);
    await tick();
    old.tab.value = 'activity';
    assert.equal(h.workspace.state.project, null);
    await h.workspace.undoDelete();
    await tick();
    const restored = h.workspace.bindWorkspaceTab(h.workspace.state.project);
    assert.notEqual(restored.key, old.key);
    assert.ok(h.tab().startsWith('graph'));
    old.tab.value = 'evidence';
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    await h.choose('design');
    assert.equal(old.tab.value, 'graph');
    assert.equal(restored.tab.value, 'design');
  } finally {
    h.dispose();
  }
});

test('accepted new incarnation and authoritative removal cannot inherit cached tabs or stale writes', async () => {
  const h = await harness();
  try {
    await h.choose('activity');
    const old = h.workspace.bindWorkspaceTab(h.workspace.state.project);
    h.records.set('A', project('A', 'recreated-A'));
    await h.workspace.selectProject('A');
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    old.tab.value = 'architecture';
    await tick();
    assert.ok(h.tab().startsWith('graph'));
    await h.choose('design');
    await h.workspace.selectProject('B');
    await tick();
    h.records.delete('A');
    await h.workspace.refresh();
    h.records.set('A', project('A', 'recreated-A'));
    await h.workspace.selectProject('A');
    await tick();
    assert.ok(h.tab().startsWith('graph'));
  } finally {
    h.dispose();
  }
});

test('Settings-hidden and different-project leases cannot update tab; equal-incarnation refresh preserves it', async () => {
  const h = await harness();
  try {
    await h.choose('evidence');
    const lease = h.workspace.bindWorkspaceTab(h.workspace.state.project);
    h.workspace.setPage('settings');
    await tick();
    lease.tab.value = 'activity';
    h.workspace.setPage('projects');
    await tick();
    assert.equal(h.tab(), 'evidence');
    h.records.set('A', project('A', 'original-A', 4));
    await h.workspace.refresh();
    await tick();
    assert.equal(h.tab(), 'evidence');
    await h.workspace.selectProject('B');
    await tick();
    lease.tab.value = 'design';
    await h.workspace.selectProject('A');
    await tick();
    assert.equal(h.tab(), 'evidence');
  } finally {
    h.dispose();
  }
});

test('late child callbacks cannot navigate or focus a remounted or recreated project incarnation', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    const unmounted = h.env.callbacks;
    h.workspace.setPage('settings');
    // Reject callbacks as soon as page intent changes, before Vue unmounts the view.
    unmounted.resume();
    unmounted.locate('A-node');
    await tick();
    unmounted.resume();
    unmounted.locate('A-node');
    h.workspace.setPage('projects');
    await tick();
    assert.equal(h.tab(), 'architecture');
    assert.deepEqual(h.env.resume, []);
    assert.deepEqual(h.env.locates, []);
    const oldIncarnation = h.env.callbacks;
    h.records.set('A', project('A', 'fresh-incarnation'));
    await h.workspace.selectProject('A');
    await tick();
    await h.choose('evidence');
    oldIncarnation.resume();
    oldIncarnation.locate('A-node');
    await tick();
    assert.equal(h.tab(), 'evidence');
    assert.equal(h.workspace.state.selectedId, null);
    assert.deepEqual(h.env.resume, []);
    assert.deepEqual(h.env.locates, []);
  } finally {
    h.dispose();
  }
});

test('real ArchitecturePanel and DiagramView preserve source browsing/camera across offscreen navigation, then accept live follow and Resume', async () => {
  const h = await harness(true);
  try {
    await h.flush();
    await h.choose('architecture');
    await h.flush();
    const sourceButton = () =>
      [...h.root.querySelectorAll('.architecture-tabs button')].find((button) =>
        button.textContent.includes('SRC'),
      );
    sourceButton().click();
    await h.flush();
    const project = h.workspace.state.project;
    const saved = h.browsing.viewState(project);
    Object.assign(saved, {
      query: 'Source',
      focusedId: 'source-two',
      role: 'database',
      relation: 'upstream',
    });
    await h.flush();
    const sourceFlow = h.flows.at(-1);
    const camera = { x: -142, y: 83, zoom: 0.73 };
    sourceFlow.attrs.onViewportChange(camera);
    await h.flush();
    h.workspace.setPage('settings');
    await tick();
    Object.assign(h.agent.state, {
      view: 'architecture',
      diagramKind: 'architecture',
      diagramId: 'target',
      focusId: 'offscreen',
    });
    h.agent.state.follow.A = true;
    h.agent.state.navigationTick++;
    h.agent.state.pulse++;
    await tick();
    h.workspace.setPage('projects');
    await h.flush();
    assert.equal(h.tab(), 'architecture');
    assert.equal(h.browsing.entry(project).view, 'source');
    assert.equal(sourceButton().getAttribute('aria-pressed'), 'true');
    assert.equal(h.browsing.viewState(project).query, 'Source');
    assert.equal(h.browsing.viewState(project).focusedId, 'source-two');
    assert.equal(h.browsing.viewState(project).relation, 'upstream');
    const restoredFlow = h.flows.at(-1);
    assert.deepEqual(restoredFlow.attrs['default-viewport'], camera);
    assert.equal(restoredFlow.fits.length, 0);
    assert.equal(
      restoredFlow.centers.length,
      0,
      'An offscreen focus pulse must not fit or locate the restored camera',
    );
    h.agent.state.navigationTick++;
    await h.flush();
    assert.equal(
      h.browsing.entry(project).view,
      'current',
      'A later live mounted navigation event remains intentional',
    );
    sourceButton().click();
    await h.flush();
    h.root.querySelector('.resume').click();
    await h.flush();
    assert.equal(h.browsing.entry(project).view, 'current');
    assert.deepEqual(h.env.resume, ['A']);
  } finally {
    h.dispose();
  }
});

test('real milestone camera and focus ignore offscreen state but honor a later live pulse and Resume before remount', async () => {
  const h = await harness(true);
  try {
    await h.flush();
    h.workspace.setPage('settings');
    await tick();
    Object.assign(h.agent.state, { view: 'graph', focusId: 'A-node-24' });
    h.agent.state.follow.A = true;
    h.agent.state.pulse++;
    h.agent.state.navigationTick++;
    h.workspace.setPage('projects');
    await h.flush();
    const graph = h.flows.at(-1);
    assert.ok(graph.id.startsWith('milestones-'));
    assert.ok(
      graph.viewport.zoom < 0.5,
      'Returning establishes normal overview, not a replayed old Agent focus',
    );
    assert.equal(
      graph.attrs.nodes.some((node) => node.data.agentFocused),
      false,
    );
    h.agent.state.pulse++;
    await h.flush();
    assert.ok(graph.viewport.zoom >= 0.9);
    assert.equal(graph.attrs.nodes.find((node) => node.id === 'A-node-24').data.agentFocused, true);
    await h.choose('evidence');
    h.agent.state.focusId = 'A-node-5';
    h.root.querySelector('.resume').click();
    await h.flush();
    assert.ok(h.tab().startsWith('graph'));
    const resumed = h.flows.at(-1);
    assert.ok(
      resumed.viewport.zoom >= 0.9,
      'Resume issued before graph remount still owns that camera',
    );
    assert.equal(
      resumed.attrs.nodes.find((node) => node.id === 'A-node-5').data.agentFocused,
      true,
    );
    h.records.set('A', { ...structuredClone(h.records.get('A')), created_at: 'new-incarnation' });
    await h.workspace.selectProject('A');
    await h.flush();
    const recreated = h.flows.at(-1);
    assert.notEqual(recreated, resumed, 'A new accepted incarnation remounts the view ownership');
    assert.ok(recreated.viewport.zoom < 0.5);
    assert.equal(
      recreated.attrs.nodes.some((node) => node.data.agentFocused),
      false,
    );
  } finally {
    h.dispose();
  }
});

test('accepted B navigation rejects A callbacks before the component props/unmount flush', async () => {
  const h = await harness();
  try {
    await h.choose('architecture');
    const stale = h.env.callbacks;
    const before = JSON.stringify(h.agent.state);
    // Run immediately inside the accepted state mutation, before Vue can render B.
    const stop = (await import('vue')).watch(
      () => h.workspace.state.project?.id,
      (id) => {
        if (id === 'B') {
          stale.resume();
          stale.locate('A-node');
        }
      },
      { flush: 'sync' },
    );
    await h.workspace.selectProject('B');
    stop();
    await tick();
    assert.equal(h.workspace.state.project.id, 'B');
    assert.equal(h.workspace.state.selectedId, null);
    assert.equal(JSON.stringify(h.agent.state), before);
    assert.deepEqual(h.env.resume, []);
    assert.deepEqual(h.env.locates, []);
  } finally {
    h.dispose();
  }
});

for (const restored of [false, true]) {
  test(`integrated recovery ${restored ? 'changed outcome retires' : 'authoritative no-op retains'} the active tab lease`, async () => {
    const h = await harness();
    try {
      assert.equal(typeof h.workspace.restoreProject, 'function');
      await h.choose('activity');
      const original = h.workspace.state.project;
      const lease = h.workspace.bindWorkspaceTab(original);
      h.handlers['projects.restore_archived'] = () => ({
        project: structuredClone(h.records.get('A')),
        restored,
      });
      assert.equal(await h.workspace.restoreProject(original), true);
      if (restored) await h.workspace.selectProject('A');
      await tick();
      const current = h.workspace.bindWorkspaceTab(h.workspace.state.project);
      if (restored) {
        assert.notEqual(current.key, lease.key);
        assert.ok(h.tab().startsWith('graph'));
        lease.tab.value = 'evidence';
        await tick();
        assert.ok(h.tab().startsWith('graph'));
      } else {
        assert.equal(current.key, lease.key);
        assert.equal(h.tab(), 'activity');
        lease.tab.value = 'evidence';
        await tick();
        assert.equal(h.tab(), 'evidence');
      }
    } finally {
      h.dispose();
    }
  });
}
