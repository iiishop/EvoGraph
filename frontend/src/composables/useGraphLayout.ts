import type { ElkNode } from 'elkjs/lib/elk.bundled.js';
import ELK from 'elkjs/lib/elk.bundled.js';
import type { Milestone } from '../types';
const elk = new ELK();
export const NODE_WIDTH = 236,
  NODE_HEIGHT = 150;
export interface Point {
  x: number;
  y: number;
}
export interface LayoutResult {
  direction: 'RIGHT' | 'DOWN';
  positions: Map<string, Point>;
  routes: Map<string, Point[]>;
}
export const edgeId = (source: string, target: string) => JSON.stringify([source, target]);
export async function layout(
  milestones: Pick<Milestone, 'id' | 'dependencies'>[],
): Promise<LayoutResult> {
  const direction = 'RIGHT' as const;
  const vertical = false;
  const result = await elk.layout<ElkNode>({
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': direction,
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.spacing.nodeNode': '70',
      'elk.layered.spacing.nodeNodeBetweenLayers': '110',
      'elk.layered.spacing.edgeNodeBetweenLayers': '32',
      'elk.spacing.edgeNode': '30',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.padding': '[top=60,left=30,bottom=50,right=30]',
    },
    children: milestones.map((m) => ({
      id: m.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' },
      ports: [
        { id: m.id + '-in', x: vertical ? 118 : 0, y: vertical ? 0 : 75, width: 0, height: 0 },
        {
          id: m.id + '-out',
          x: vertical ? 118 : NODE_WIDTH,
          y: vertical ? NODE_HEIGHT : 75,
          width: 0,
          height: 0,
        },
      ],
    })),
    edges: milestones.flatMap((m) =>
      m.dependencies.map((dep) => ({
        id: edgeId(dep, m.id),
        sources: [dep + '-out'],
        targets: [m.id + '-in'],
      })),
    ),
  });
  return {
    direction,
    positions: new Map(result.children?.map((n) => [n.id, { x: n.x ?? 0, y: n.y ?? 0 }]) ?? []),
    routes: new Map(
      result.edges?.map((e) => [
        e.id,
        e.sections?.flatMap((s) => [s.startPoint, ...(s.bendPoints ?? []), s.endPoint]) ?? [],
      ]) ?? [],
    ),
  };
}
