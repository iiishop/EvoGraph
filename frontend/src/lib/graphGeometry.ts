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
