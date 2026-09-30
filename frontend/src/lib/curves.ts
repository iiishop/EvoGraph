import type { Point } from '../composables/useGraphLayout';

/** Sample the M/C-only paths emitted by our shared edge renderer. */
export function sampleCubicPath(path: string): Point[] {
  const values = (path.match(/-?\d*\.?\d+(?:e[+-]?\d+)?/gi) ?? []).map(Number);
  const samples: Point[] = [];
  let start = { x: values[0], y: values[1] };
  for (let i = 2; i + 5 < values.length; i += 6) {
    const [ax, ay, bx, by, x, y] = values.slice(i, i + 6);
    const length =
      Math.hypot(ax - start.x, ay - start.y) +
      Math.hypot(bx - ax, by - ay) +
      Math.hypot(x - bx, y - by);
    const steps = Math.max(16, Math.ceil(length / 4));
    for (let j = 0; j <= steps; j++) {
      const t = j / steps,
        u = 1 - t;
      samples.push({
        x: u ** 3 * start.x + 3 * u ** 2 * t * ax + 3 * u * t ** 2 * bx + t ** 3 * x,
        y: u ** 3 * start.y + 3 * u ** 2 * t * ay + 3 * u * t ** 2 * by + t ** 3 * y,
      });
    }
    start = { x, y };
  }
  return samples;
}

/** ELK SPLINES emits control points for successive cubic Bézier segments. */
export function splinePath(points: Point[]): string {
  if (points.length < 2) return '';
  if ((points.length - 1) % 3 !== 0) return smoothWaypoints(points);
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i += 3) {
    const a = points[i],
      b = points[i + 1],
      end = points[i + 2];
    path += ` C ${a.x} ${a.y}, ${b.x} ${b.y}, ${end.x} ${end.y}`;
  }
  return path;
}

/** Continuous curves through an obstacle-free route, with capped tangent overshoot. */
export function smoothWaypoints(input: Point[]): string {
  const points = input.filter(
    (p, i) =>
      i === 0 ||
      i === input.length - 1 ||
      !(
        (input[i - 1].x === p.x && input[i + 1].x === p.x) ||
        (input[i - 1].y === p.y && input[i + 1].y === p.y)
      ),
  );
  if (points.length < 2) return '';
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p = points[i],
      q = points[i + 1],
      before = points[Math.max(0, i - 1)],
      after = points[Math.min(points.length - 1, i + 2)];
    const tangent = (dx: number, dy: number) => {
      const scale = Math.min(1 / 5, 20 / Math.max(1, Math.hypot(dx, dy)));
      return { x: dx * scale, y: dy * scale };
    };
    const a = tangent(q.x - before.x, q.y - before.y),
      b = tangent(after.x - p.x, after.y - p.y);
    path += ` C ${p.x + a.x} ${p.y + a.y}, ${q.x - b.x} ${q.y - b.y}, ${q.x} ${q.y}`;
  }
  return path;
}
