import type { Milestone } from '../types';

export function layout(milestones: Milestone[]) {
  const byId = new Map(milestones.map((m) => [m.id, m]));
  const downstream = new Map<string, number>();
  function distance(id: string): number {
    if (downstream.has(id)) return downstream.get(id)!;
    downstream.set(id, 0);
    const children = milestones.filter((m) => m.dependencies.includes(id));
    const value = children.length ? 1 + Math.max(...children.map((m) => distance(m.id))) : 0;
    downstream.set(id, value);
    return value;
  }
  milestones.forEach((m) => distance(m.id));
  const remaining = new Set(byId.keys());
  const placed = new Set<string>();
  const positions = new Map<string, { x: number; y: number }>();
  let column = 0;
  // Two lanes keep small plans readable. Only edges, not columns, imply dependency.
  while (remaining.size) {
    const ready = [...remaining]
      .filter((id) => byId.get(id)!.dependencies.every((dep) => placed.has(dep)))
      .sort((a, b) => downstream.get(b)! - downstream.get(a)! || a.localeCompare(b))
      .slice(0, 2);
    if (!ready.length) break;
    ready.forEach((id, row) => {
      positions.set(id, {
        x: column * 285 + 20,
        y: row * 165 + (ready.length === 1 ? 82.5 : 0) + 24,
      });
      remaining.delete(id);
      placed.add(id);
    });
    column++;
  }
  return positions;
}
