import type { Point } from '../composables/useGraphLayout';

export interface Box extends Point {
  id: string;
  width: number;
  height: number;
}
export function separateBoxes(boxes: Box[]): Box[] {
  const placed: Box[] = [];
  for (const original of boxes) {
    const box = { ...original };
    while (
      placed.some(
        (b) =>
          box.x < b.x + b.width + 28 &&
          box.x + box.width + 28 > b.x &&
          box.y < b.y + b.height + 32 &&
          box.y + box.height + 32 > b.y,
      )
    )
      box.y += 182;
    placed.push(box);
  }
  return placed;
}

/** Orthogonal visibility grid for manually positioned nodes; no segment crosses a box. */
export function routeAroundBoxes(start: Point, end: Point, boxes: Box[]): Point[] {
  const xs = [
    ...new Set([start.x, end.x, ...boxes.flatMap((b) => [b.x - 24, b.x + b.width + 24])]),
  ].sort((a, b) => a - b);
  const ys = [
    ...new Set([start.y, end.y, ...boxes.flatMap((b) => [b.y - 24, b.y + b.height + 24])]),
  ].sort((a, b) => a - b);
  const key = (x: number, y: number) => y * xs.length + x;
  const point = (id: number) => ({ x: xs[id % xs.length], y: ys[Math.floor(id / xs.length)] });
  const inside = (p: Point) =>
    boxes.some(
      (b) =>
        p.x > b.x + 0.01 &&
        p.x < b.x + b.width - 0.01 &&
        p.y > b.y + 0.01 &&
        p.y < b.y + b.height - 0.01,
    );
  const clear = (a: Point, b: Point) =>
    !boxes.some((r) =>
      a.x === b.x
        ? a.x > r.x + 0.01 &&
          a.x < r.x + r.width - 0.01 &&
          Math.max(a.y, b.y) > r.y + 0.01 &&
          Math.min(a.y, b.y) < r.y + r.height - 0.01
        : a.y > r.y + 0.01 &&
          a.y < r.y + r.height - 0.01 &&
          Math.max(a.x, b.x) > r.x + 0.01 &&
          Math.min(a.x, b.x) < r.x + r.width - 0.01,
    );
  const source = key(xs.indexOf(start.x), ys.indexOf(start.y)),
    target = key(xs.indexOf(end.x), ys.indexOf(end.y));
  const costs = new Map([[source, 0]]),
    previous = new Map<number, number>(),
    open = new Set([source]);
  const estimate = (id: number) => {
    const p = point(id);
    return (costs.get(id) ?? Infinity) + Math.abs(p.x - end.x) + Math.abs(p.y - end.y);
  };
  while (open.size) {
    let current = -1,
      best = Infinity;
    for (const id of open) {
      const cost = estimate(id);
      if (cost < best) {
        best = cost;
        current = id;
      }
    }
    if (current === target) {
      const result = [end];
      while (previous.has(current)) {
        current = previous.get(current)!;
        result.unshift(point(current));
      }
      return result;
    }
    open.delete(current);
    const x = current % xs.length,
      y = Math.floor(current / xs.length),
      a = point(current);
    for (const [nx, ny] of [
      [x - 1, y],
      [x + 1, y],
      [x, y - 1],
      [x, y + 1],
    ]) {
      if (nx < 0 || ny < 0 || nx >= xs.length || ny >= ys.length) continue;
      const next = key(nx, ny),
        b = point(next);
      if (inside(b) || !clear(a, b)) continue;
      const cost = costs.get(current)! + Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
      if (cost < (costs.get(next) ?? Infinity)) {
        costs.set(next, cost);
        previous.set(next, current);
        open.add(next);
      }
    }
  }
  return []; // Never draw a fabricated path through a node.
}

/** Keep curves clear of nodes and group headings, including return/self edges. */
export function routeArchitectureEdge(start: Point, end: Point, boxes: Box[]): Point[] {
  const clearance = 28;
  const obstacles = boxes.map((b) => ({
    ...b,
    x: b.x - clearance,
    y: b.y - clearance,
    width: b.width + 2 * clearance,
    height: b.height + 2 * clearance,
  }));
  const exit = { x: start.x + clearance + 24, y: start.y };
  const entry = { x: end.x - clearance - 24, y: end.y };
  const middle = routeAroundBoxes(exit, entry, obstacles);
  return middle.length ? [start, exit, ...middle.slice(1, -1), entry, end] : [];
}

/** Place labels on free route segments, reserving space for earlier labels. */
export function architectureLabels(routes: { route: Point[]; label: string }[], obstacles: Box[]) {
  const occupied = [...obstacles];
  return routes.map(({ route, label }, index) => {
    const width = Math.max(
      36,
      [...label].reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? 12 : 7), 18),
    );
    const segments = route
      .slice(1)
      .map((b, i) => ({ a: route[i], b }))
      .sort(
        (s, t) =>
          Math.hypot(t.b.x - t.a.x, t.b.y - t.a.y) - Math.hypot(s.b.x - s.a.x, s.b.y - s.a.y),
      );
    for (const { a, b } of segments) {
      for (const ratio of [0.5, 0.25, 0.75, 0.1, 0.9]) {
        const center = { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio };
        const box = {
          id: `label:${index}`,
          x: center.x - width / 2,
          y: center.y - 15,
          width,
          height: 30,
        };
        if (
          occupied.some(
            (r) =>
              box.x < r.x + r.width + 8 &&
              box.x + box.width + 8 > r.x &&
              box.y < r.y + r.height + 8 &&
              box.y + box.height + 8 > r.y,
          )
        )
          continue;
        occupied.push(box);
        return center;
      }
    }
    return null; // Crowded labels remain available on hover and in the component passport.
  });
}
