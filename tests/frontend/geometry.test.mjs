import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const require = createRequire(import.meta.url);
async function load(path) {
  const code = ts
    .transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replaceAll(
      'elkjs/lib/elk.bundled.js',
      pathToFileURL(require.resolve('elkjs/lib/elk.bundled.js')).href,
    );
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const { separateBoxes, routeAroundBoxes } = await load('../../frontend/src/lib/graphGeometry.ts');
const { layout } = await load('../../frontend/src/composables/useGraphLayout.ts');
const { architectureGroupBounds, packArchitectureGroups } = await load(
  '../../frontend/src/lib/architectureLayout.ts',
);
const overlaps = (a, b) =>
  a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

test('shared architecture routes reserve distinct labels without covering nodes', async () => {
  const { architectureLabels } = await load('../../frontend/src/lib/graphGeometry.ts');
  const route = [{ x: 0, y: 0 }, { x: 600, y: 0 }];
  const labels = architectureLabels(Array.from({ length: 3 }, () => ({ route, label: '请求' })), []);
  assert.equal(labels.filter(Boolean).length, 3);
  for (let i = 0; i < labels.length; i++)
    for (let j = i + 1; j < labels.length; j++)
      assert.ok(Math.abs(labels[i].x - labels[j].x) > 50);
  assert.deepEqual(architectureLabels([{ route, label: '请求' }], [{ id: 'occupied', x: -10, y: -30, width: 620, height: 60 }]), [null]);
});

test('dragging onto another node produces non-overlapping positions', () => {
  const boxes = separateBoxes(
    ['a', 'b', 'c'].map((id) => ({ id, x: 50, y: 50, width: 236, height: 150 })),
  );
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) assert.equal(overlaps(boxes[i], boxes[j]), false);
});

test('manual long edge routes around intervening milestone', () => {
  const boxes = [
    { id: 'a', x: 0, y: 0, width: 236, height: 150 },
    { id: 'b', x: 320, y: 0, width: 236, height: 150 },
    { id: 'c', x: 640, y: 0, width: 236, height: 150 },
  ];
  const route = routeAroundBoxes({ x: 236, y: 75 }, { x: 640, y: 75 }, boxes);
  assert.ok(route.length > 2);
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1],
      b = route[i];
    for (const r of boxes) {
      const crossing =
        a.x === b.x
          ? a.x > r.x &&
            a.x < r.x + r.width &&
            Math.max(a.y, b.y) > r.y &&
            Math.min(a.y, b.y) < r.y + r.height
          : a.y > r.y &&
            a.y < r.y + r.height &&
            Math.max(a.x, b.x) > r.x &&
            Math.min(a.x, b.x) < r.x + r.width;
      assert.equal(crossing, false);
    }
  }
});

test('branch and merge layout has unique positions and forward prerequisites', async () => {
  const graph = [
    { id: 'A', dependencies: [] },
    { id: 'B', dependencies: ['A'] },
    { id: 'C', dependencies: ['A'] },
    { id: 'D', dependencies: ['B', 'C'] },
    { id: 'E', dependencies: ['A', 'D'] },
  ];
  const result = await layout(graph);
  const boxes = [...result.positions].map(([id, p]) => ({ id, ...p, width: 236, height: 150 }));
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) assert.equal(overlaps(boxes[i], boxes[j]), false);
  for (const node of graph)
    for (const dep of node.dependencies)
      assert.ok(result.positions.get(node.id).x > result.positions.get(dep).x);
  assert.equal(result.routes.size, 6);
});

test('architecture groups are packed apart and keep their title strips clear', () => {
  const positions = new Map([
    ['client-a', { x: 0, y: 140 }],
    ['client-b', { x: 40, y: 320 }],
    ['server-a', { x: 80, y: 150 }],
    ['server-b', { x: 120, y: 330 }],
    ['loose', { x: 120, y: 0 }],
  ]);
  const groups = [
    {
      id: 'client',
      label: '客户端',
      description: '入口与交互',
      kind: 'client',
      member_node_ids: ['client-a', 'client-b'],
    },
    {
      id: 'server',
      label: '服务端',
      description: '领域服务',
      kind: 'server',
      member_node_ids: ['server-a', 'server-b'],
    },
  ];
  const packed = packArchitectureGroups(positions, groups);
  const bounds = groups.map((group) => architectureGroupBounds(packed, group));
  assert.ok(bounds.every(Boolean));
  for (let i = 0; i < bounds.length; i++)
    for (let j = i + 1; j < bounds.length; j++) {
      assert.equal(overlaps(bounds[i], bounds[j]), false);
    }
  for (const [index, group] of groups.entries()) {
    const frame = bounds[index];
    for (const [id, point] of packed) {
      if (group.member_node_ids.includes(id)) continue;
      assert.equal(overlaps(frame.header, { ...point, width: 236, height: 150 }), false);
    }
  }
  // The pass clones positions so a reactive layout map is never mutated.
  assert.equal(positions.get('client-a').x, 0);
});

const { splinePath, smoothWaypoints } = await load('../../frontend/src/lib/curves.ts');
test('automatic and manual routes render cubic curves, never orthogonal line commands', async () => {
  const result = await layout([
    { id: 'A', dependencies: [] },
    { id: 'B', dependencies: ['A'] },
    { id: 'C', dependencies: ['A'] },
    { id: 'D', dependencies: ['B', 'C'] },
  ]);
  for (const points of result.routes.values()) {
    const path = splinePath(points);
    assert.ok(path.includes(' C '));
    assert.equal(/[LHQV]/.test(path), false);
  }
  const manual = smoothWaypoints([
    { x: 0, y: 0 },
    { x: 30, y: 0 },
    { x: 30, y: 100 },
    { x: 100, y: 100 },
  ]);
  assert.ok(manual.includes(' C '));
  assert.equal(/[LHQV]/.test(manual), false);
});

const { fuzzyMatch } = await load('../../frontend/src/lib/referenceMatch.ts');
test('reference search matches ordered non-contiguous characters ignoring case', () => {
  assert.equal(fuzzyMatch('WorldWildWeb.md', 'www'), true);
  assert.equal(fuzzyMatch('src/WorldWildWeb.ts', 'WWW'), true);
  assert.equal(fuzzyMatch('world.md', 'www'), false);
  assert.equal(fuzzyMatch('abc', 'ca'), false);
  assert.equal(fuzzyMatch('设计文档.md', '设档'), true);
});
const { layoutArchitecture } = await load('../../frontend/src/lib/layoutArchitecture.ts');
const { routeArchitectureEdge } = await load('../../frontend/src/lib/graphGeometry.ts');
test('architecture boundaries contain only their members and stay disjoint', async () => {
  const groups = [
    { id: 'client', member_node_ids: ['a', 'b'] },
    { id: 'server', member_node_ids: ['c', 'd'] },
  ];
  const diagram = {
    groups,
    nodes: ['a', 'b', 'c', 'd', 'e'].map((id) => ({ id })),
    edges: [
      { source: 'a', target: 'c' },
      { source: 'b', target: 'd' },
      { source: 'c', target: 'd' },
      { source: 'd', target: 'e' },
    ],
  };
  const positions = await layoutArchitecture(diagram);
  const frames = groups.map((g) => architectureGroupBounds(positions, g));
  assert.equal(overlaps(frames[0], frames[1]), false);
  for (let i = 0; i < groups.length; i++)
    for (const [id, p] of positions) {
      if (!groups[i].member_node_ids.includes(id))
        assert.equal(overlaps(frames[i], { ...p, width: 236, height: 150 }), false);
    }
});
test('architecture forward, return and self routes avoid component interiors', () => {
  const boxes = [
    { id: 'a', x: 0, y: 0, width: 236, height: 150 },
    { id: 'b', x: 400, y: 0, width: 236, height: 150 },
    { id: 'c', x: 800, y: 0, width: 236, height: 150 },
  ];
  for (const [source, target] of [
    [boxes[0], boxes[2]],
    [boxes[2], boxes[0]],
    [boxes[0], boxes[0]],
  ]) {
    const route = routeArchitectureEdge(
      { x: source.x + 236, y: 75 },
      { x: target.x, y: 75 },
      boxes,
    );
    assert.ok(route.length >= 4);
    for (let i = 1; i < route.length; i++) {
      const a = route[i - 1],
        b = route[i];
      for (const box of boxes) {
        const cross =
          a.x === b.x
            ? a.x > box.x &&
              a.x < box.x + box.width &&
              Math.max(a.y, b.y) > box.y &&
              Math.min(a.y, b.y) < box.y + box.height
            : a.y > box.y &&
              a.y < box.y + box.height &&
              Math.max(a.x, b.x) > box.x &&
              Math.min(a.x, b.x) < box.x + box.width;
        assert.equal(cross, false);
      }
    }
  }
});
