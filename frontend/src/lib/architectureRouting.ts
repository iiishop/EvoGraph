import type { Diagram } from '../types';
import type { Box } from './graphGeometry';
import type { ArchitecturePoint as Point } from './architectureLayout';

export interface ArchitecturePort {
  id: string;
  y: number;
  edge: number;
  side: 'left' | 'right';
}
export interface ArchitectureRoute {
  route: Point[];
  sourceHandle: string;
  targetHandle: string;
}
const EPSILON = 0.01;
const CLEARANCE = 16;
const LANE_GAP = 12;
const STUB = 24;
const distance = (a: Point, b: Point) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
const expand = (b: Box, amount: number): Box => ({
  ...b,
  x: b.x - amount,
  y: b.y - amount,
  width: b.width + amount * 2,
  height: b.height + amount * 2,
});
const overlap = (a: Box, b: Box) =>
  a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
export const architectureSegmentHitsBox = (a: Point, b: Point, box: Box) =>
  a.x === b.x
    ? a.x > box.x + EPSILON &&
      a.x < box.x + box.width - EPSILON &&
      Math.max(a.y, b.y) > box.y + EPSILON &&
      Math.min(a.y, b.y) < box.y + box.height - EPSILON
    : a.y > box.y + EPSILON &&
      a.y < box.y + box.height - EPSILON &&
      Math.max(a.x, b.x) > box.x + EPSILON &&
      Math.min(a.x, b.x) < box.x + box.width - EPSILON;

export function simplifyArchitectureRoute(input: Point[]): Point[] {
  const unique = input.filter((point, i) => !i || distance(point, input[i - 1]) > EPSILON);
  return unique.filter(
    (point, i) =>
      !i ||
      i === unique.length - 1 ||
      !(
        (unique[i - 1].x === point.x && unique[i + 1].x === point.x) ||
        (unique[i - 1].y === point.y && unique[i + 1].y === point.y)
      ),
  );
}

/** Align the reserved corridor with Vue Flow's measured handle outer anchors. */
export function anchorArchitectureRoute(route: Point[], start: Point, end: Point): Point[] {
  if (route.length < 2) return [];
  const points = route.map((point) => ({ ...point }));
  points[0] = start;
  points[points.length - 1] = end;
  if (points.length > 2) {
    points[1].y = start.y;
    points[points.length - 2].y = end.y;
  } else if (Math.abs(start.y - end.y) > EPSILON) {
    const x = (start.x + end.x) / 2;
    return [start, { x, y: start.y }, { x, y: end.y }, end];
  }
  return points;
}

/** Small circular corners stay inside the reserved orthogonal corridor. */
export function roundedArchitecturePath(input: Point[], radius = 8): string {
  const points = simplifyArchitectureRoute(input);
  if (points.length < 2) return '';
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const before = points[i - 1],
      point = points[i],
      after = points[i + 1];
    const r = Math.min(radius, distance(before, point) / 2, distance(point, after) / 2);
    const approach = {
      x: point.x + Math.sign(before.x - point.x) * r,
      y: point.y + Math.sign(before.y - point.y) * r,
    };
    const exit = {
      x: point.x + Math.sign(after.x - point.x) * r,
      y: point.y + Math.sign(after.y - point.y) * r,
    };
    path += ` L ${approach.x} ${approach.y} Q ${point.x} ${point.y} ${exit.x} ${exit.y}`;
  }
  const end = points[points.length - 1];
  return `${path} L ${end.x} ${end.y}`;
}

type Segment = { a: Point; b: Point };
const segments = (route: Point[]): Segment[] => route.slice(1).map((b, i) => ({ a: route[i], b }));

// Direction is part of the search state: a shorter route with many turns is
// not necessarily the clearest route. Sharing a lane costs more than crossing it.
class Queue {
  items: { id: number; cost: number }[] = [];
  push(item: { id: number; cost: number }) {
    let i = this.items.length;
    this.items.push(item);
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.items[parent].cost <= item.cost) break;
      this.items[i] = this.items[parent];
      i = parent;
    }
    this.items[i] = item;
  }
  pop() {
    const first = this.items[0],
      last = this.items.pop()!;
    if (this.items.length) {
      let i = 0;
      while (i * 2 + 1 < this.items.length) {
        let child = i * 2 + 1;
        if (child + 1 < this.items.length && this.items[child + 1].cost < this.items[child].cost)
          child++;
        if (this.items[child].cost >= last.cost) break;
        this.items[i] = this.items[child];
        i = child;
      }
      this.items[i] = last;
    }
    return first;
  }
}

function laneCost(a: Point, b: Point, reserved: Segment[]) {
  let cost = 0;
  const horizontal = a.y === b.y;
  for (const s of reserved) {
    const otherHorizontal = s.a.y === s.b.y;
    if (horizontal === otherHorizontal) {
      const gap = horizontal ? Math.abs(a.y - s.a.y) : Math.abs(a.x - s.a.x);
      if (gap >= LANE_GAP - EPSILON) continue;
      const amount = horizontal
        ? Math.min(Math.max(a.x, b.x), Math.max(s.a.x, s.b.x)) -
          Math.max(Math.min(a.x, b.x), Math.min(s.a.x, s.b.x))
        : Math.min(Math.max(a.y, b.y), Math.max(s.a.y, s.b.y)) -
          Math.max(Math.min(a.y, b.y), Math.min(s.a.y, s.b.y));
      if (amount > 0) cost += amount * 5 * (1 - gap / LANE_GAP);
    } else {
      const h = horizontal ? { a, b } : s,
        v = horizontal ? s : { a, b };
      if (
        v.a.x > Math.min(h.a.x, h.b.x) &&
        v.a.x <= Math.max(h.a.x, h.b.x) &&
        h.a.y > Math.min(v.a.y, v.b.y) &&
        h.a.y <= Math.max(v.a.y, v.b.y)
      )
        cost += 60;
    }
  }
  return cost;
}

function* findRouteSteps(
  start: Point,
  end: Point,
  obstacles: Box[],
  reserved: Segment[],
): Generator<void, Point[]> {
  const xs = [
    ...new Set([
      start.x,
      end.x,
      ...obstacles.flatMap((b) =>
        [0, LANE_GAP, LANE_GAP * 2].flatMap((offset) => [
          b.x - 8 - offset,
          b.x + b.width + 8 + offset,
        ]),
      ),
    ]),
  ].sort((a, b) => a - b);
  const ys = [
    ...new Set([
      start.y,
      end.y,
      ...obstacles.flatMap((b) =>
        [0, LANE_GAP, LANE_GAP * 2].flatMap((offset) => [
          b.y - 8 - offset,
          b.y + b.height + 8 + offset,
        ]),
      ),
    ]),
  ].sort((a, b) => a - b);
  const point = (id: number) => ({ x: xs[id % xs.length], y: ys[Math.floor(id / xs.length)] });
  const source = ys.indexOf(start.y) * xs.length + xs.indexOf(start.x);
  const target = ys.indexOf(end.y) * xs.length + xs.indexOf(end.x);
  const costs = new Map<number, number>([[source * 2, 0]]);
  const previous = new Map<number, number>();
  const penalties = new Map<string, number>();
  const queue = new Queue();
  queue.push({ id: source * 2, cost: distance(start, end) });
  let expansions = 0;
  while (queue.items.length) {
    // Yield inside a large search as well as between edges. This keeps both
    // manual input and stale-generation cancellation responsive at 40/100.
    if (++expansions % 64 === 0) yield;
    const item = queue.pop(),
      cell = Math.floor(item.id / 2),
      direction = item.id % 2;
    const a = point(cell),
      cost = costs.get(item.id)!;
    if (item.cost > cost + distance(a, end) + EPSILON) continue;
    if (cell === target) {
      const result = [end];
      let current = item.id;
      while (previous.has(current)) {
        current = previous.get(current)!;
        result.unshift(point(Math.floor(current / 2)));
      }
      return simplifyArchitectureRoute(result);
    }
    const x = cell % xs.length,
      y = Math.floor(cell / xs.length);
    for (const [nx, ny, axis] of [
      [x - 1, y, 0],
      [x + 1, y, 0],
      [x, y - 1, 1],
      [x, y + 1, 1],
    ]) {
      if (nx < 0 || ny < 0 || nx >= xs.length || ny >= ys.length) continue;
      const next = ny * xs.length + nx,
        b = point(next);
      const key = cell < next ? `${cell}:${next}` : `${next}:${cell}`;
      let penalty = penalties.get(key);
      if (penalty === undefined) {
        penalty = obstacles.some((box) => architectureSegmentHitsBox(a, b, box))
          ? Infinity
          : laneCost(a, b, reserved);
        penalties.set(key, penalty);
      }
      const nextCost =
        cost +
        distance(a, b) +
        penalty +
        (direction === axis ? 0 : 28) +
        (next === target && axis !== 0 ? 28 : 0);
      const state = next * 2 + axis;
      if (nextCost >= (costs.get(state) ?? Infinity)) continue;
      costs.set(state, nextCost);
      previous.set(state, item.id);
      queue.push({ id: state, cost: nextCost + distance(b, end) });
    }
  }
  return [];
}

/** All edges share a routing plan, while their declared endpoints and order remain unchanged. */
function* architectureRoutingSteps(
  diagram: Pick<Diagram, 'edges'>,
  boxes: Box[],
  headers: Box[] = [],
) {
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const ports = new Map<string, { incoming: ArchitecturePort[]; outgoing: ArchitecturePort[] }>(
    boxes.map((box) => [box.id, { incoming: [], outgoing: [] }]),
  );
  const routes: ArchitectureRoute[] = diagram.edges.map((_, index) => ({
    route: [],
    sourceHandle: `out:${index}`,
    targetHandle: `in:${index}`,
  }));
  const sides = diagram.edges.map((edge) => {
    const source = byId.get(edge.source),
      target = byId.get(edge.target);
    const backward = Boolean(source && target && target.x + target.width <= source.x);
    return {
      source: backward ? ('left' as const) : ('right' as const),
      target: backward ? ('right' as const) : ('left' as const),
    };
  });
  for (const box of boxes) {
    const incident = diagram.edges.flatMap((edge, index) => [
      ...(edge.source === box.id
        ? [{ index, incoming: false, neighbor: byId.get(edge.target), side: sides[index].source }]
        : []),
      ...(edge.target === box.id
        ? [{ index, incoming: true, neighbor: byId.get(edge.source), side: sides[index].target }]
        : []),
    ]);
    for (const side of ['left', 'right'] as const) {
      const ordered = incident
        .filter((port) => port.side === side)
        .sort(
          (a, b) =>
            (a.neighbor?.y ?? 0) - (b.neighbor?.y ?? 0) ||
            (a.neighbor?.x ?? 0) - (b.neighbor?.x ?? 0) ||
            Number(a.incoming) - Number(b.incoming) ||
            a.index - b.index,
        );
      ordered.forEach((port, rank) => {
        ports.get(box.id)![port.incoming ? 'incoming' : 'outgoing'].push({
          id: port.incoming ? routes[port.index].targetHandle : routes[port.index].sourceHandle,
          y: 24 + ((box.height - 48) * (rank + 1)) / (ordered.length + 1),
          edge: port.index,
          side,
        });
      });
    }
  }
  const obstacles = [
    ...boxes.map((b) => expand(b, CLEARANCE)),
    ...headers.map((b) => expand(b, 6)),
  ];
  const reserved: Segment[] = [];
  const work = diagram.edges
    .map((edge, index) => {
      const source = byId.get(edge.source),
        target = byId.get(edge.target);
      if (!source || !target) return { index, start: null, end: null, span: Infinity };
      const start = {
        x: source.x + (sides[index].source === 'right' ? source.width : 0),
        y: source.y + ports.get(source.id)!.outgoing.find((p) => p.edge === index)!.y,
      };
      const end = {
        x: target.x + (sides[index].target === 'right' ? target.width : 0),
        y: target.y + ports.get(target.id)!.incoming.find((p) => p.edge === index)!.y,
      };
      return { index, start, end, span: distance(start, end) };
    })
    .sort((a, b) => a.span - b.span || a.index - b.index);
  for (const { index, start, end } of work) {
    if (!start || !end) continue;
    const exit = { x: start.x + (sides[index].source === 'right' ? STUB : -STUB), y: start.y },
      entry = { x: end.x + (sides[index].target === 'right' ? STUB : -STUB), y: end.y };
    const middle = yield* findRouteSteps(exit, entry, obstacles, reserved);
    // Never silently substitute a line through a component on failure.
    if (!middle.length) continue;
    routes[index].route = simplifyArchitectureRoute([start, exit, ...middle, entry, end]);
    reserved.push(...segments(routes[index].route));
    yield;
  }
  return { routes, ports };
}

/** Deterministic synchronous entry point for geometry checks and small offline renders. */
export function routeArchitecture(
  diagram: Pick<Diagram, 'edges'>,
  boxes: Box[],
  headers: Box[] = [],
) {
  const steps = architectureRoutingSteps(diagram, boxes, headers);
  let step = steps.next();
  while (!step.done) step = steps.next();
  return step.value;
}

/** Time-slice route search so a dense valid architecture cannot monopolize the UI thread. */
export async function routeArchitectureAsync(
  diagram: Pick<Diagram, 'edges'>,
  boxes: Box[],
  headers: Box[] = [],
  cancelled: () => boolean = () => false,
  pause: () => Promise<void> = () => new Promise((resolve) => setTimeout(resolve, 0)),
) {
  const steps = architectureRoutingSteps(diagram, boxes, headers);
  let sinceYield = performance.now();
  while (!cancelled()) {
    const step = steps.next();
    if (step.done) return step.value;
    if (performance.now() - sinceYield >= 12) {
      await pause();
      sinceYield = performance.now();
    }
  }
  return null;
}

export interface ArchitectureLabel extends Point {
  width: number;
  height: number;
  text: string;
}
const fallbackWidth = (text: string) =>
  [...text].reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? 11 : 6.5), 0);

/** Reserve labels against nodes, headers, other labels AND every unrelated route. */
export function placeArchitectureLabels(
  items: { route: Point[]; label: string; priority?: number }[],
  obstacles: Box[],
  measure: (text: string) => number = fallbackWidth,
): (ArchitectureLabel | null)[] {
  const occupied = obstacles.map((b) => expand(b, 5));
  const result: (ArchitectureLabel | null)[] = items.map(() => null);
  const edgeSegments = items.flatMap((item, index) =>
    segments(item.route).map((s) => ({ ...s, index })),
  );
  const ordered = items
    .map((item, index) => ({ ...item, index }))
    .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0) || a.index - b.index);
  for (const item of ordered) {
    if (!item.label.trim()) continue;
    // Horizontal runs read naturally. Vertical runs are a fallback, with the
    // label placed beside the line rather than obscuring another edge.
    const candidates = segments(item.route).sort((s, t) => {
      const score = (v: Segment) => distance(v.a, v.b) + (v.a.y === v.b.y ? 1000 : 0);
      return score(t) - score(s);
    });
    outer: for (const { a, b } of candidates) {
      const horizontal = a.y === b.y;
      const height = 24;
      if (distance(a, b) < (horizontal ? 72 : height + 24)) continue;
      const maxTextWidth = horizontal ? Math.min(230, distance(a, b) - 40) : 230;
      let text = item.label;
      while (measure(text) > maxTextWidth && [...text].length > 1)
        text = [...text].slice(0, -2).join('') + '…';
      const width = Math.ceil(measure(text)) + 16;
      for (const ratio of [0.5, 0.3, 0.7, 0.15, 0.85]) {
        for (const side of [-1, 1]) {
          const center = {
            x: a.x + (b.x - a.x) * ratio + (horizontal ? 0 : side * (width / 2 + 7)),
            y: a.y + (b.y - a.y) * ratio + (horizontal ? side * (height / 2 + 5) : 0),
          };
          const box: Box = {
            id: `label:${item.index}`,
            x: center.x - width / 2,
            y: center.y - height / 2,
            width,
            height,
          };
          if (
            occupied.some((other) => overlap(expand(box, 4), other)) ||
            edgeSegments.some((s) => architectureSegmentHitsBox(s.a, s.b, expand(box, 3)))
          )
            continue;
          result[item.index] = { ...center, width, height, text };
          occupied.push(box);
          break outer;
        }
      }
    }
  }
  return result;
}

export function architectureDrawingBounds(
  boxes: Box[],
  routes: ArchitectureRoute[],
  labels: (ArchitectureLabel | null)[] = [],
) {
  const rectangles = [
    ...boxes,
    ...routes.flatMap(({ route }) => route.map((p) => ({ ...p, width: 0, height: 0 }))),
    ...labels.flatMap((label) =>
      label
        ? [
            {
              x: label.x - label.width / 2,
              y: label.y - label.height / 2,
              width: label.width,
              height: label.height,
            },
          ]
        : [],
    ),
  ];
  if (!rectangles.length) return null;
  const x = Math.min(...rectangles.map((b) => b.x)),
    y = Math.min(...rectangles.map((b) => b.y));
  return {
    x: x - 12,
    y: y - 12,
    width: Math.max(...rectangles.map((b) => b.x + b.width)) - x + 24,
    height: Math.max(...rectangles.map((b) => b.y + b.height)) - y + 24,
  };
}
