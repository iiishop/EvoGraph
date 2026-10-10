import type { ElkNode } from 'elkjs/lib/elk.bundled.js';
import { createLazyElk } from './lazyElk';
import type { Diagram } from '../types';

const elk = createLazyElk();
const options = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.spacing.nodeNode': '96',
  // Disconnected members still need a real routing corridor between cards.
  'elk.spacing.componentComponent': '96',
  'elk.layered.spacing.nodeNodeBetweenLayers': '150',
  'elk.padding': '[top=100,left=48,bottom=48,right=48]',
};

/** Layout the contents first, then arrange semantic boundaries by their relations. */
export async function layoutArchitecture(diagram: Diagram) {
  if (!diagram.nodes.length && !diagram.edges.length && !diagram.groups?.length)
    return new Map<string, { x: number; y: number }>();
  const assigned = new Set(diagram.groups?.flatMap((g) => g.member_node_ids) ?? []);
  const groups = [
    ...(diagram.groups ?? []).map((g) => ({ id: `group:${g.id}`, ids: g.member_node_ids })),
    ...diagram.nodes
      .filter((n) => !assigned.has(n.id))
      .map((n) => ({ id: `single:${n.id}`, ids: [n.id] })),
  ];
  const owner = new Map(groups.flatMap((g) => g.ids.map((id) => [id, g.id] as const)));
  const interiors = await Promise.all(
    groups.map((g) =>
      elk.layout<ElkNode>({
        id: g.id,
        layoutOptions: options,
        children: g.ids
          .filter((id) => diagram.nodes.some((n) => n.id === id))
          .map((id) => ({ id, width: 236, height: 150 })),
        edges: diagram.edges
          .filter((e) => owner.get(e.source) === g.id && owner.get(e.target) === g.id)
          .map((e, i) => ({ id: `inner:${i}`, sources: [e.source], targets: [e.target] })),
      }),
    ),
  );
  const outer = await elk.layout<ElkNode>({
    id: 'architecture',
    layoutOptions: { ...options, 'elk.spacing.nodeNode': '100' },
    children: interiors.map((g) => ({ id: g.id, width: g.width, height: g.height })),
    edges: diagram.edges
      .filter((e) => owner.get(e.source) !== owner.get(e.target))
      .map((e, i) => ({
        id: `outer:${i}`,
        sources: [owner.get(e.source)!],
        targets: [owner.get(e.target)!],
      })),
  });
  const positions = new Map<string, { x: number; y: number }>();
  for (const group of interiors) {
    const placement = outer.children!.find((g) => g.id === group.id)!;
    for (const node of group.children ?? [])
      positions.set(node.id, {
        x: (placement.x ?? 0) + (node.x ?? 0),
        y: (placement.y ?? 0) + (node.y ?? 0),
      });
  }
  return positions;
}
