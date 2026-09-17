import type { Point } from '../composables/useGraphLayout';

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
