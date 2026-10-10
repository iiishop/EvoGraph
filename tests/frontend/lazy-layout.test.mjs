import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { compile, moduleUrl, resolveLayoutImports } from './helpers/layout-module.mjs';

const source = (path) =>
  readFileSync(new URL(`../../frontend/src/${path}`, import.meta.url), 'utf8');
let harnessId = 0;
async function harness() {
  const stateUrl = moduleUrl(`export const state = {
    loads: 0, constructions: 0, importFailure: null, constructorFailure: null,
    layout: async graph => graph,
  }; // ${++harnessId}`);
  const { state } = await import(stateUrl);
  const elkUrl = moduleUrl(`import { state } from ${JSON.stringify(stateUrl)};
    state.loads++;
    if (state.importFailure) throw state.importFailure;
    export default class ELK {
      constructor() {
        this.id = ++state.constructions;
        if (state.constructorFailure) throw state.constructorFailure;
      }
      layout(graph, options) { return state.layout(graph, options, this.id); }
    }`);
  const load = (path) => import(moduleUrl(resolveLayoutImports(compile(source(path)), elkUrl)));
  const [{ createLazyElk }, { layout }, { layoutArchitecture }] = await Promise.all([
    load('lib/lazyElk.ts'),
    load('composables/useGraphLayout.ts'),
    load('lib/layoutArchitecture.ts'),
  ]);
  return { state, createLazyElk, layout, layoutArchitecture };
}

test('importing layout modules and laying out empty graphs never loads or constructs ELK', async () => {
  const { state, layout, layoutArchitecture } = await harness();
  assert.equal(state.loads, 0);
  assert.equal(state.constructions, 0);
  const [milestones, architecture] = await Promise.all([
    layout([]),
    layoutArchitecture({ nodes: [], edges: [], groups: [] }),
  ]);
  assert.deepEqual(milestones, { direction: 'RIGHT', positions: new Map(), routes: new Map() });
  assert.deepEqual(architecture, new Map());
  assert.equal(state.loads, 0);
  assert.equal(state.constructions, 0);
});

test('concurrent cold requests share initialization, keep separate engines, and do not serialize layouts', async () => {
  const { state, createLazyElk } = await harness();
  const calls = [];
  let started;
  const allStarted = new Promise((resolve) => {
    started = resolve;
  });
  state.layout = (graph, options, engine) =>
    new Promise((resolve) => {
      calls.push({ graph, options, engine, resolve });
      if (calls.length === 3) started();
    });
  const first = createLazyElk(),
    second = createLazyElk();
  const options = { layoutOptions: { 'elk.direction': 'DOWN' } };
  const a = first.layout({ id: 'a' }, options);
  const b = first.layout({ id: 'b' });
  const c = second.layout({ id: 'c' });
  await allStarted;
  assert.equal(state.loads, 1);
  assert.equal(state.constructions, 2);
  assert.equal(calls[0].engine, calls[1].engine);
  assert.notEqual(calls[0].engine, calls[2].engine);
  assert.equal(calls[0].options, options);
  calls[1].resolve({ id: 'b', x: 20 });
  assert.deepEqual(await b, { id: 'b', x: 20 });
  calls[2].resolve({ id: 'c', x: 30 });
  calls[0].resolve({ id: 'a', x: 10 });
  assert.deepEqual(await Promise.all([a, c]), [
    { id: 'a', x: 10 },
    { id: 'c', x: 30 },
  ]);
  state.layout = async (graph) => graph;
  assert.deepEqual(await first.layout({ id: 'later' }), { id: 'later' });
  assert.equal(state.constructions, 2);
});

test('layout consumers keep distinct engines and snapshot their inputs before the cold import', async () => {
  const { state, layout, layoutArchitecture } = await harness();
  const calls = [];
  state.layout = async (graph, options, engine) => {
    calls.push({ graph, engine });
    return graph;
  };
  const milestones = [
    { id: 'a', dependencies: [] },
    { id: 'b', dependencies: ['a'] },
  ];
  const diagram = {
    nodes: [{ id: 'left' }, { id: 'right' }],
    edges: [{ source: 'left', target: 'right' }],
  };
  const milestoneResult = layout(milestones);
  const architectureResult = layoutArchitecture(diagram);
  milestones[0].id = 'changed';
  milestones[1].dependencies.push('changed');
  await Promise.all([milestoneResult, architectureResult]);
  assert.equal(state.loads, 1);
  assert.equal(state.constructions, 2);
  const milestoneCall = calls.find((call) => call.graph.id === 'root');
  const interiors = calls.filter((call) => call.graph.id.startsWith('single:'));
  const outer = calls.find((call) => call.graph.id === 'architecture');
  assert.deepEqual(
    milestoneCall.graph.children.map((node) => node.id),
    ['a', 'b'],
  );
  assert.deepEqual(
    milestoneCall.graph.edges.map((edge) => edge.id),
    ['["a","b"]'],
  );
  assert.equal(interiors.length, 2);
  assert.ok(interiors.every((call) => call.engine === outer.engine));
  assert.notEqual(milestoneCall.engine, outer.engine);
});

test('initialization failures reach every waiting request and a later request can retry construction', async () => {
  const { state, createLazyElk } = await harness();
  const failure = new Error('engine initialization failed');
  state.constructorFailure = failure;
  const engine = createLazyElk();
  const results = await Promise.allSettled([
    engine.layout({ id: 'a' }),
    engine.layout({ id: 'b' }),
  ]);
  assert.ok(results.every((result) => result.status === 'rejected' && result.reason === failure));
  assert.equal(state.constructions, 1);
  state.constructorFailure = null;
  assert.deepEqual(await engine.layout({ id: 'retry' }), { id: 'retry' });
  assert.equal(state.constructions, 2);
});

test('import and individual layout failures propagate without an empty or fallback layout', async () => {
  const failedImport = await harness();
  const importFailure = new Error('chunk unavailable');
  failedImport.state.importFailure = importFailure;
  await assert.rejects(
    failedImport.layout([{ id: 'a', dependencies: [] }]),
    (error) => error === importFailure,
  );
  assert.equal(failedImport.state.constructions, 0);

  const { state, createLazyElk } = await harness();
  const failure = new Error('invalid graph');
  state.layout = async (graph) => {
    if (graph.id === 'invalid') throw failure;
    return graph;
  };
  const engine = createLazyElk();
  await assert.rejects(engine.layout({ id: 'invalid' }), (error) => error === failure);
  assert.deepEqual(await engine.layout({ id: 'valid' }), { id: 'valid' });
  assert.equal(state.constructions, 1);
});

test('real ELK layouts remain isolated under concurrent cold calls and recover after invalid input', async () => {
  const load = (path) => import(moduleUrl(resolveLayoutImports(compile(source(path)))));
  const [{ layout }, { layoutArchitecture }] = await Promise.all([
    load('composables/useGraphLayout.ts'),
    load('lib/layoutArchitecture.ts'),
  ]);
  const milestones = [
    { id: 'a', dependencies: [] },
    { id: 'b', dependencies: ['a'] },
    { id: 'c', dependencies: ['a'] },
    { id: 'd', dependencies: ['b', 'c'] },
  ];
  const diagram = JSON.parse(
    readFileSync(new URL('../fixtures/architecture-routing.json', import.meta.url), 'utf8'),
  );
  const original = JSON.stringify({ milestones, diagram });
  const concurrent = await Promise.all([
    layout(milestones),
    layoutArchitecture(diagram),
    layout(milestones),
    layoutArchitecture(diagram),
  ]);
  assert.deepEqual(concurrent[0], concurrent[2]);
  assert.deepEqual(concurrent[1], concurrent[3]);
  assert.deepEqual(await layout(milestones), concurrent[0]);
  assert.deepEqual(await layoutArchitecture(diagram), concurrent[1]);
  assert.equal(JSON.stringify({ milestones, diagram }), original);
  await assert.rejects(layout([{ id: 'a', dependencies: ['missing'] }]), /missing/);
  assert.deepEqual(await layout(milestones), concurrent[0]);
});
