import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { resolveLayoutImports } from './helpers/layout-module.mjs';
const load = async (file) =>
  import(
    'data:text/javascript;base64,' +
      Buffer.from(
        resolveLayoutImports(
          ts.transpileModule(
            readFileSync(new URL(`../../frontend/src/lib/${file}`, import.meta.url), 'utf8'),
            { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
          ).outputText,
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
      x: source.x + outgoing.x,
      y: source.y + outgoing.y,
    });
    assert.deepEqual(route.at(-1), {
      x: target.x + incoming.x,
      y: target.y + incoming.y,
    });
    const normals = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] };
    const dot = (a, b, side) => (b.x - a.x) * normals[side][0] + (b.y - a.y) * normals[side][1];
    assert.ok(dot(route[0], route[1], outgoing.side) > 0, 'source leaves its chosen face');
    assert.ok(dot(route.at(-1), route.at(-2), incoming.side) > 0, 'arrow enters its chosen face');
    assert.ok(outgoing.edges.includes(index));
    assert.ok(incoming.edges.includes(index));
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

test('parallel edges share directional face handles while opposite and self paths remain distinct', () => {
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
  assert.equal(result.routes[0].sourceHandle, result.routes[1].sourceHandle);
  assert.equal(result.routes[0].targetHandle, result.routes[1].targetHandle);
  assert.notEqual(result.routes[0].sourceHandle, result.routes[2].targetHandle);
  for (const nodePorts of result.ports.values())
    for (const side of ['incoming', 'outgoing'])
      assert.equal(
        new Set(nodePorts[side].map((p) => `${p.side}:${p.x}:${p.y}`)).size,
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
    assert.equal(new Set(all.map((p) => `${p.side}:${p.x}:${p.y}`)).size, all.length);
  }
});

test('all four faces have at most one input and output and unused faces are absent', () => {
  const boxes = [
    { id: 'center', x: 400, y: 400, width: 236, height: 150 },
    { id: 'left', x: 0, y: 400, width: 236, height: 150 },
    { id: 'right', x: 800, y: 400, width: 236, height: 150 },
    { id: 'top', x: 400, y: 0, width: 236, height: 150 },
    { id: 'bottom', x: 400, y: 800, width: 236, height: 150 },
    { id: 'unused', x: 1200, y: 1200, width: 236, height: 150 },
  ];
  const edges = boxes.slice(1, 5).flatMap((box) => [
    { source: 'center', target: box.id, label: `out ${box.id}` },
    { source: 'center', target: box.id, label: `out duplicate ${box.id}` },
    { source: box.id, target: 'center', label: `in ${box.id}` },
  ]);
  const diagram = { edges };
  const result = { boxes, headers: [], ...routeArchitecture(diagram, boxes) };
  verifyGeometry(diagram, result);
  const center = result.ports.get('center');
  assert.equal(center.incoming.length, 4);
  assert.equal(center.outgoing.length, 4);
  assert.deepEqual(result.ports.get('unused'), { incoming: [], outgoing: [] });
  for (const ports of result.ports.values()) {
    for (const direction of ['incoming', 'outgoing']) {
      assert.ok(ports[direction].length <= 4);
      assert.equal(new Set(ports[direction].map((p) => p.side)).size, ports[direction].length);
    }
    assert.equal(
      new Set([...ports.incoming, ...ports.outgoing].map((p) => `${p.x}:${p.y}`)).size,
      ports.incoming.length + ports.outgoing.length,
    );
  }
  for (const port of center.outgoing) {
    assert.equal(port.edges.length, 2);
    const [a, b] = port.edges.map((index) => result.routes[index].route);
    assert.deepEqual(a[0], b[0]);
    // Every branch shares a short outward trunk before its independently routed body.
    const n = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] }[port.side];
    for (const r of [a, b]) assert.ok((r[1].x - r[0].x) * n[0] + (r[1].y - r[0].y) * n[1] >= 24);
  }
});

test('face selection avoids obstructed group headers and remains stable after edge reorder', () => {
  const boxes = [
    { id: 'a', x: 400, y: 400, width: 236, height: 150 },
    { id: 'b', x: 400, y: 0, width: 236, height: 150 },
    { id: 'c', x: 800, y: 400, width: 236, height: 150 },
  ];
  const headers = [{ id: 'header:a', x: 372, y: 316, width: 292, height: 66 }];
  const edges = [
    { source: 'a', target: 'b', label: 'up' },
    { source: 'a', target: 'c', label: 'right' },
    { source: 'b', target: 'a', label: 'down' },
  ];
  const result = { boxes, headers, ...routeArchitecture({ edges }, boxes, headers) };
  verifyGeometry({ edges }, result);
  assert.notEqual(result.routes[0].sourceHandle, 'out:top');
  const reversed = routeArchitecture({ edges: [...edges].reverse() }, boxes, headers);
  assert.deepEqual(result.routes, [...reversed.routes].reverse());
  for (const [id, ports] of result.ports) {
    for (const direction of ['incoming', 'outgoing'])
      assert.deepEqual(
        ports[direction].map(({ edges, ...port }) => port),
        reversed.ports.get(id)[direction].map(({ edges, ...port }) => port),
      );
  }
});

test('measured top/bottom handles preserve orthogonal terminal axes without mutating routes', () => {
  const reserved = [
    { x: 106, y: 150 },
    { x: 106, y: 174 },
    { x: 300, y: 174 },
    { x: 300, y: 250 },
    { x: 518, y: 250 },
    { x: 518, y: 300 },
  ];
  const source = { x: 107, y: 152.5 },
    target = { x: 519, y: 297.5 };
  const original = JSON.stringify(reserved);
  const route = anchorArchitectureRoute(reserved, source, target);
  assert.deepEqual(route[0], source);
  assert.deepEqual(route.at(-1), target);
  for (const [a, b] of allSegments(route)) assert.ok(a.x === b.x || a.y === b.y);
  assert.equal(JSON.stringify(reserved), original);
  const straight = anchorArchitectureRoute(
    [
      { x: 0, y: 0 },
      { x: 0, y: 100 },
    ],
    { x: 1, y: 2 },
    { x: 2, y: 98 },
  );
  for (const [a, b] of allSegments(straight)) assert.ok(a.x === b.x || a.y === b.y);
});

test('busy central routing uses nearby parallel corridors without unnecessary outside detours', async () => {
  const result = await geometry(fixture);
  const segments = result.routes.flatMap(({ route }, index) =>
    allSegments(route).map(([a, b]) => ({ a, b, index })),
  );
  let crossings = 0;
  for (const [index, a] of segments.entries()) {
    for (const b of segments.slice(index + 1)) {
      if (a.index === b.index || (a.a.x === a.b.x) === (b.a.x === b.b.x)) continue;
      const [v, h] = a.a.x === a.b.x ? [a, b] : [b, a];
      if (
        v.a.x > Math.min(h.a.x, h.b.x) &&
        v.a.x < Math.max(h.a.x, h.b.x) &&
        h.a.y > Math.min(v.a.y, v.b.y) &&
        h.a.y < Math.max(v.a.y, v.b.y)
      )
        crossings++;
    }
  }
  assert.ok(crossings <= 10, 'avoid the original crowded 13-crossing central weave');
  assert.ok(
    result.routes.reduce((sum, item) => sum + length(item.route), 0) < 11000,
    'cleaner corridors must not produce giant perimeter loops',
  );
});
