import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const load = async (file) =>
  import(
    'data:text/javascript;base64,' +
      Buffer.from(
        ts
          .transpileModule(
            readFileSync(new URL(`../../frontend/src/lib/${file}`, import.meta.url), 'utf8'),
            { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
          )
          .outputText.replaceAll(
            'elkjs/lib/elk.bundled.js',
            pathToFileURL(require.resolve('elkjs/lib/elk.bundled.js')).href,
          ),
      ).toString('base64')
  );
const {
  routeArchitecture,
  routeArchitectureAsync,
  anchorArchitectureRoute,
  placeArchitectureLabels,
  roundedArchitecturePath,
  architectureDrawingBounds,
  architectureSegmentHitsBox,
} = await load('architectureRouting.ts');
const { layoutArchitecture } = await load('layoutArchitecture.ts');
const { architectureGroupBounds } = await load('architectureLayout.ts');
const fixture = JSON.parse(
  readFileSync(new URL('../fixtures/architecture-routing.json', import.meta.url), 'utf8'),
);
const rectanglesOverlap = (a, b) =>
  a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
const length = (points) =>
  points
    .slice(1)
    .reduce((total, p, i) => total + Math.abs(p.x - points[i].x) + Math.abs(p.y - points[i].y), 0);
const allSegments = (route) => route.slice(1).map((b, i) => [route[i], b]);
async function geometry(diagram) {
  const positions = await layoutArchitecture(diagram);
  const boxes = [...positions].map(([id, point]) => ({ id, ...point, width: 236, height: 150 }));
  const groups = diagram.groups.map((group) => architectureGroupBounds(positions, group));
  const headers = groups.map((group) => ({ id: `header:${group.id}`, ...group.header }));
  return { boxes, groups, headers, ...routeArchitecture(diagram, boxes, headers) };
}
function verifyGeometry(diagram, result) {
  assert.equal(result.routes.length, diagram.edges.length);
  result.routes.forEach(({ route, sourceHandle, targetHandle }, index) => {
    const edge = diagram.edges[index];
    const source = result.boxes.find((box) => box.id === edge.source),
      target = result.boxes.find((box) => box.id === edge.target);
    assert.ok(
      route.length >= 2,
      `edge ${index} ${edge.source} → ${edge.target} must not disappear`,
    );
    const outgoing = result.ports.get(source.id).outgoing.find((p) => p.id === sourceHandle);
    const incoming = result.ports.get(target.id).incoming.find((p) => p.id === targetHandle);
    assert.deepEqual(route[0], {
      x: source.x + (outgoing.side === 'right' ? source.width : 0),
      y: source.y + outgoing.y,
    });
    assert.deepEqual(route.at(-1), {
      x: target.x + (incoming.side === 'right' ? target.width : 0),
      y: target.y + incoming.y,
    });
    assert.ok(
      (route[1].x - route[0].x) * (outgoing.side === 'right' ? 1 : -1) > 0,
      'source leaves its actual chosen side port',
    );
    assert.ok(
      (route.at(-1).x - route.at(-2).x) * (incoming.side === 'left' ? 1 : -1) > 0,
      'arrow enters its actual chosen side port',
    );
    for (const [a, b] of allSegments(route)) {
      assert.ok(a.x === b.x || a.y === b.y, 'reserved route must be orthogonal');
      for (const box of [...result.boxes, ...result.headers])
        assert.equal(
          architectureSegmentHitsBox(a, b, box),
          false,
          `edge ${index} crosses ${box.id}`,
        );
    }
  });
}

test('screenshot-inspired synthetic fixture preserves all directed edges, groups and port origins', async () => {
  const unchanged = JSON.stringify(fixture);
  const result = await geometry(fixture);
  verifyGeometry(fixture, result);
  assert.equal(JSON.stringify(fixture), unchanged);
  assert.equal(result.boxes.length, 8);
  assert.equal(result.routes.length, 14);
  result.groups.forEach((group, index) => {
    for (const box of result.boxes)
      if (!fixture.groups[index].member_node_ids.includes(box.id))
        assert.equal(rectanglesOverlap(group, box), false);
  });
});

test('parallel, opposite and self edges have separate actual handles and visible routes', () => {
  const boxes = [
    { id: 'a', x: 0, y: 0, width: 236, height: 150 },
    { id: 'b', x: 400, y: 0, width: 236, height: 150 },
  ];
  const diagram = {
    edges: [
      { source: 'a', target: 'b' },
      { source: 'a', target: 'b' },
      { source: 'b', target: 'a' },
      { source: 'b', target: 'b' },
    ],
  };
  const result = { boxes, headers: [], ...routeArchitecture(diagram, boxes) };
  verifyGeometry(diagram, result);
  assert.equal(new Set(result.routes.map(({ route }) => JSON.stringify(route))).size, 4);
  for (const nodePorts of result.ports.values())
    for (const side of ['incoming', 'outgoing'])
      assert.equal(
        new Set(nodePorts[side].map((p) => `${p.side}:${p.y}`)).size,
        nodePorts[side].length,
      );
  assert.ok(
    length(result.routes[2].route) < 1100,
    'return edge uses a nearby corridor, not a distant perimeter',
  );
});

test('disconnected members reserve a traversable corridor instead of squeezing return paths outside the diagram', async () => {
  const result = await geometry(fixture);
  for (const ids of [
    ['kernel', 'runtime'],
    ['engine', 'llm'],
  ]) {
    const [a, b] = ids
      .map((id) => result.boxes.find((box) => box.id === id))
      .sort((a, b) => a.y - b.y);
    assert.ok(b.y - (a.y + a.height) >= 72);
  }
});

test('dense grouped graph routes every edge, including cycles and boundary returns', async () => {
  const nodes = Array.from({ length: 16 }, (_, i) => ({ id: `n${i}` }));
  const groups = Array.from({ length: 4 }, (_, g) => ({
    id: `g${g}`,
    member_node_ids: nodes.slice(g * 4, g * 4 + 4).map((n) => n.id),
  }));
  const edges = nodes.flatMap((node, i) =>
    [1, 4, 7].map((offset) => ({
      source: node.id,
      target: nodes[(i + offset) % nodes.length].id,
      label: `Relation ${i}/${offset}`,
    })),
  );
  const diagram = { nodes, groups, edges };
  const result = await geometry(diagram);
  verifyGeometry(diagram, result);
  assert.equal(result.routes.length, 48);
});

test('labels use measured text, prioritize selected relations, and never cover nodes or edge segments', async () => {
  const result = await geometry(fixture);
  let measured = 0;
  const items = result.routes.map(({ route }, i) => ({
    route,
    label: fixture.edges[i].label,
    priority: i === 12 ? 1 : 0,
  }));
  const labels = placeArchitectureLabels(items, [...result.boxes, ...result.headers], (text) => {
    measured++;
    return [...text].length * 11;
  });
  assert.ok(measured > 0);
  assert.ok(labels.filter(Boolean).length >= 8, 'normal-sized graph keeps useful labels visible');
  const boxes = labels.flatMap((label, i) =>
    label
      ? [
          {
            id: `label:${i}`,
            x: label.x - label.width / 2,
            y: label.y - label.height / 2,
            width: label.width,
            height: label.height,
          },
        ]
      : [],
  );
  for (const [i, box] of boxes.entries()) {
    for (const other of [...result.boxes, ...result.headers, ...boxes.slice(i + 1)])
      assert.equal(rectanglesOverlap(box, other), false);
    for (const { route } of result.routes)
      for (const [a, b] of allSegments(route))
        assert.equal(architectureSegmentHitsBox(a, b, box), false);
  }
  const route = [
    { x: 0, y: 0 },
    { x: 250, y: 0 },
  ];
  const crowded = [
    { route, label: 'first' },
    { route, label: 'selected', priority: 1 },
  ];
  const labels2 = placeArchitectureLabels(crowded, [], () => 180);
  assert.ok(labels2[1]);
  assert.equal(labels2[1].y, -17);
});

test('rounded path stays in its reserved corridor and fit bounds include returns and label extents', async () => {
  assert.equal(
    roundedArchitecturePath([
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 50, y: 100 },
    ]),
    'M 0 0 L 42 0 Q 50 0 50 8 L 50 100',
  );
  const result = await geometry(fixture);
  const labels = placeArchitectureLabels(
    result.routes.map((r, i) => ({ ...r, label: fixture.edges[i].label })),
    [...result.boxes, ...result.headers],
  );
  const bounds = architectureDrawingBounds(
    [...result.boxes, ...result.groups],
    result.routes,
    labels,
  );
  for (const { route } of result.routes)
    for (const p of route)
      assert.ok(
        p.x >= bounds.x &&
          p.y >= bounds.y &&
          p.x <= bounds.x + bounds.width &&
          p.y <= bounds.y + bounds.height,
      );
  for (const label of labels.filter(Boolean))
    assert.ok(
      label.x - label.width / 2 >= bounds.x && label.x + label.width / 2 <= bounds.x + bounds.width,
    );
});

test('long labels in the standard gap truncate visually and retain their full source text', () => {
  const label = '执行请求（Action Executor 端口）';
  const item = {
    route: [
      { x: 236, y: 75 },
      { x: 386, y: 75 },
    ],
    label,
    priority: 1,
  };
  const labels = placeArchitectureLabels(
    [item],
    [
      { id: 'a', x: 0, y: 0, width: 236, height: 150 },
      { id: 'b', x: 386, y: 0, width: 236, height: 150 },
    ],
  );
  assert.ok(labels[0]);
  assert.ok(labels[0].text.endsWith('…'));
  assert.equal(item.label, label);
});

test('renderer anchors its first and last segments to measured Vue Flow ports', () => {
  const reserved = [
    { x: 236, y: 75 },
    { x: 260, y: 75 },
    { x: 260, y: 250 },
    { x: 376, y: 250 },
    { x: 376, y: 175 },
    { x: 400, y: 175 },
  ];
  const actual = anchorArchitectureRoute(reserved, { x: 238.5, y: 76 }, { x: 397.5, y: 176 });
  assert.deepEqual(actual[0], { x: 238.5, y: 76 });
  assert.deepEqual(actual.at(-1), { x: 397.5, y: 176 });
  assert.equal(actual[1].y, 76);
  assert.equal(actual.at(-2).y, 176);
  assert.deepEqual(reserved[0], { x: 236, y: 75 });
  for (const [a, b] of allSegments(actual)) assert.ok(a.x === b.x || a.y === b.y);
});

test('dense routing yields to the event loop and cancellation never installs a partial graph', async () => {
  const boxes = Array.from({ length: 16 }, (_, i) => ({
    id: `n${i}`,
    x: (i % 4) * 386,
    y: Math.floor(i / 4) * 260,
    width: 236,
    height: 150,
  }));
  const diagram = {
    edges: boxes.flatMap((box, i) =>
      [1, 4, 7].map((offset) => ({
        source: box.id,
        target: boxes[(i + offset) % boxes.length].id,
      })),
    ),
  };
  let yields = 0;
  const result = await routeArchitectureAsync(
    diagram,
    boxes,
    [],
    () => false,
    async () => {
      yields++;
    },
  );
  assert.ok(yields > 0, 'expensive work must yield');
  verifyGeometry(diagram, { boxes, headers: [], ...result });
  let cancelled = false;
  const stale = await routeArchitectureAsync(
    diagram,
    boxes,
    [],
    () => cancelled,
    async () => {
      cancelled = true;
    },
  );
  assert.equal(stale, null);
});

test('return dependencies use inward-facing side ports and avoid unnecessary perimeter loops', () => {
  const boxes = [
    { id: 'left', x: 0, y: 0, width: 236, height: 150 },
    { id: 'right', x: 386, y: 0, width: 236, height: 150 },
  ];
  const diagram = {
    edges: [
      { source: 'left', target: 'right' },
      { source: 'right', target: 'left' },
    ],
  };
  const result = routeArchitecture(diagram, boxes);
  assert.equal(result.ports.get('right').outgoing[0].side, 'left');
  assert.equal(result.ports.get('left').incoming[0].side, 'right');
  assert.ok(length(result.routes[1].route) < 250);
  for (const node of result.ports.values()) {
    const all = [...node.incoming, ...node.outgoing];
    assert.equal(new Set(all.map((p) => `${p.side}:${p.y}`)).size, all.length);
  }
});
