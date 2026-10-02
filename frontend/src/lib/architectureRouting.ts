import type { Diagram } from '../types';
import type { Box } from './graphGeometry';
import type { ArchitecturePoint as Point } from './architectureLayout';

export type ArchitectureSide = 'left' | 'right' | 'top' | 'bottom';
export interface ArchitecturePort {
  id: string;
  x: number;
  y: number;
  edges: number[];
  side: ArchitectureSide;
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
const PORT_OFFSET = 12;
const SIDES: ArchitectureSide[] = ['left', 'right', 'top', 'bottom'];
const NORMAL: Record<ArchitectureSide, Point> = {
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
  top: { x: 0, y: -1 },
  bottom: { x: 0, y: 1 },
};
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
  // Infer each terminal axis from its reserved segment. Top/bottom ports
  // must not be pulled sideways when Vue Flow measures their outer anchor.
  const sourceHorizontal = route[0].y === route[1].y;
  const last = route.length - 1;
  const targetHorizontal = route[last].y === route[last - 1].y;
  if (points.length > 2) {
    if (sourceHorizontal) points[1].y = start.y;
    else points[1].x = start.x;
    if (targetHorizontal) points[last - 1].y = end.y;
    else points[last - 1].x = end.x;
  } else if (sourceHorizontal && Math.abs(start.y - end.y) > EPSILON) {
    const x = (start.x + end.x) / 2;
    return [start, { x, y: start.y }, { x, y: end.y }, end];
  } else if (!sourceHorizontal && Math.abs(start.x - end.x) > EPSILON) {
    const y = (start.y + end.y) / 2;
    return [start, { x: start.x, y }, { x: end.x, y }, end];
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
        // Prefer a nearby parallel corridor over weaving through several
        // existing relations. This is a soft penalty, not a forced perimeter:
        // a long outside detour still costs more than a local crossing.
        cost += 90;
    }
  }
  return cost;
}

function* findRouteSteps(
  start: Point,
  end: Point,
  obstacles: Box[],
  reserved: Segment[],
  sourceSide: ArchitectureSide,
  targetSide: ArchitectureSide,
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
  const sourceAxis = NORMAL[sourceSide].x ? 0 : 1;
  const targetAxis = NORMAL[targetSide].x ? 0 : 1;
  const costs = new Map<number, number>([[source * 2 + sourceAxis, 0]]);
  const previous = new Map<number, number>();
  const penalties = new Map<string, number>();
  const queue = new Queue();
  queue.push({ id: source * 2 + sourceAxis, cost: distance(start, end) });
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
      // Keep the shared trunk intact: no immediate reversal back toward the
      // node, and no arrival from behind an input trunk.
      const delta = { x: b.x - a.x, y: b.y - a.y };
      if (cell === source && delta.x * NORMAL[sourceSide].x + delta.y * NORMAL[sourceSide].y < 0)
        continue;
      if (next === target && delta.x * NORMAL[targetSide].x + delta.y * NORMAL[targetSide].y > 0)
        continue;
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
        (next === target && axis !== targetAxis ? 28 : 0);
      const state = next * 2 + axis;
      if (nextCost >= (costs.get(state) ?? Infinity)) continue;
      costs.set(state, nextCost);
      previous.set(state, item.id);
      queue.push({ id: state, cost: nextCost + distance(b, end) });
    }
  }
  return [];
}

/** Fixed coordinates keep ports stable as edges are added, removed or reordered. */
function portPosition(box: Box, side: ArchitectureSide, incoming: boolean): Point {
  const sign = side === 'right' || side === 'bottom' ? 1 : -1;
  const offset = (incoming ? -1 : 1) * sign * PORT_OFFSET;
  return side === 'left' || side === 'right'
    ? { x: side === 'right' ? box.width : 0, y: box.height / 2 + offset }
    : { x: box.width / 2 + offset, y: side === 'bottom' ? box.height : 0 };
}

function chooseSide(
  box: Box,
  neighbor: Box,
  incoming: boolean,
  obstacles: Box[],
): ArchitectureSide {
  const dx = (neighbor.x + neighbor.width / 2 - box.x - box.width / 2) / box.width;
  const dy = (neighbor.y + neighbor.height / 2 - box.y - box.height / 2) / box.height;
  const magnitude = Math.hypot(dx, dy) || 1;
  // Direction is based on geometry rather than edge order. A header or nearby
  // component can veto a face before any handle is allocated there.
  return SIDES.map((side, order) => {
    const local = portPosition(box, side, incoming);
    const start = { x: box.x + local.x, y: box.y + local.y };
    const normal = NORMAL[side];
    const exit = { x: start.x + normal.x * STUB, y: start.y + normal.y * STUB };
    const blocked = obstacles.some(
      (other) => other.id !== box.id && architectureSegmentHitsBox(start, exit, other),
    );
    const alignment =
      box.id === neighbor.id
        ? side === 'right'
          ? 1
          : 0
        : (normal.x * dx + normal.y * dy) / magnitude;
    return { side, order, score: (blocked ? 1e9 : 0) - alignment * 1000 };
  }).sort((a, b) => a.score - b.score || a.order - b.order)[0].side;
}

/** Each face has one input and one output trunk; every relation keeps its own full path. */
function* architectureRoutingSteps(
  diagram: Pick<Diagram, 'edges'>,
  boxes: Box[],
  headers: Box[] = [],
) {
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const ports = new Map<string, { incoming: ArchitecturePort[]; outgoing: ArchitecturePort[] }>(
    boxes.map((box) => [box.id, { incoming: [], outgoing: [] }]),
  );
  const obstacles = [
    ...boxes.map((b) => expand(b, CLEARANCE)),
    ...headers.map((b) => expand(b, 6)),
  ];
  const sides = diagram.edges.map((edge) => {
    const source = byId.get(edge.source),
      target = byId.get(edge.target);
    return {
      source:
        source && target
          ? chooseSide(source, target, false, obstacles)
          : ('right' as ArchitectureSide),
      target:
        source && target
          ? chooseSide(target, source, true, obstacles)
          : ('left' as ArchitectureSide),
    };
  });
  const routes: ArchitectureRoute[] = diagram.edges.map((_, index) => ({
    route: [],
    sourceHandle: `out:${sides[index].source}`,
    targetHandle: `in:${sides[index].target}`,
  }));
  for (const box of boxes) {
    for (const side of SIDES) {
      for (const incoming of [true, false]) {
        const indices = diagram.edges.flatMap((edge, index) =>
          (incoming ? edge.target : edge.source) === box.id &&
          (incoming ? sides[index].target : sides[index].source) === side
            ? [index]
            : [],
        );
        if (!indices.length) continue;
        ports.get(box.id)![incoming ? 'incoming' : 'outgoing'].push({
          id: `${incoming ? 'in' : 'out'}:${side}`,
          ...portPosition(box, side, incoming),
          edges: indices,
          side,
        });
      }
    }
  }
  const reserved: Segment[] = [];
  const work = diagram.edges
    .map((edge, index) => {
      const source = byId.get(edge.source),
        target = byId.get(edge.target);
      if (!source || !target) return { index, start: null, end: null, span: Infinity, key: '' };
      const sourcePort = portPosition(source, sides[index].source, false);
      const targetPort = portPosition(target, sides[index].target, true);
      const start = { x: source.x + sourcePort.x, y: source.y + sourcePort.y };
      const end = { x: target.x + targetPort.x, y: target.y + targetPort.y };
      return {
        index,
        start,
        end,
        span: distance(start, end),
        key: JSON.stringify([edge.source, edge.target, edge.label]),
      };
    })
    .sort((a, b) => a.span - b.span || a.key.localeCompare(b.key) || a.index - b.index);
  for (const { index, start, end } of work) {
    if (!start || !end) continue;
    const sourceNormal = NORMAL[sides[index].source],
      targetNormal = NORMAL[sides[index].target];
    const exit = { x: start.x + sourceNormal.x * STUB, y: start.y + sourceNormal.y * STUB };
    const entry = { x: end.x + targetNormal.x * STUB, y: end.y + targetNormal.y * STUB };
    const middle = yield* findRouteSteps(
      exit,
      entry,
      obstacles,
      reserved,
      sides[index].source,
      sides[index].target,
    );
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
