import type { Diagram } from '../types';

/** Geometry shared by the architecture canvas and its group frames. */
export const ARCHITECTURE_NODE_WIDTH = 236;
export const ARCHITECTURE_NODE_HEIGHT = 150;
export const ARCHITECTURE_GROUP_PADDING = 28;
// Includes the title, optional two-line description, and breathing room before
// the first component so group metadata cannot be covered by a node.
export const ARCHITECTURE_GROUP_HEADER = 84;
export const ARCHITECTURE_GROUP_GAP = 72;

export interface ArchitecturePoint {
  x: number;
  y: number;
}

export interface ArchitectureGroupBounds {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  memberMinX: number;
  memberMinY: number;
  memberMaxX: number;
  memberMaxY: number;
  /** The top strip is deliberately kept free of component nodes. */
  header: { x: number; y: number; width: number; height: number };
}

type ArchitectureGroup = NonNullable<Diagram['groups']>[number];

const memberPoints = (positions: Map<string, ArchitecturePoint>, group: ArchitectureGroup) =>
  group.member_node_ids
    .map((id) => positions.get(id))
    .filter((point): point is ArchitecturePoint => Boolean(point));

const rectanglesOverlap = (
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

export function architectureGroupBounds(
  positions: Map<string, ArchitecturePoint>,
  group: ArchitectureGroup,
): ArchitectureGroupBounds | null {
  const members = memberPoints(positions, group);
  if (!members.length) return null;

  const memberMinX = Math.min(...members.map((point) => point.x));
  const memberMinY = Math.min(...members.map((point) => point.y));
  const memberMaxX = Math.max(...members.map((point) => point.x + ARCHITECTURE_NODE_WIDTH));
  const memberMaxY = Math.max(...members.map((point) => point.y + ARCHITECTURE_NODE_HEIGHT));
  const x = memberMinX - ARCHITECTURE_GROUP_PADDING;
  const y = memberMinY - ARCHITECTURE_GROUP_HEADER;
  const width = memberMaxX - memberMinX + ARCHITECTURE_GROUP_PADDING * 2;
  const height = memberMaxY - memberMinY + ARCHITECTURE_GROUP_HEADER + ARCHITECTURE_GROUP_PADDING;

  return {
    id: group.id,
    x,
    y,
    width,
    height,
    memberMinX,
    memberMinY,
    memberMaxX,
    memberMaxY,
    header: {
      x: x + 12,
      y: y + 10,
      width: Math.max(0, width - 24),
      height: ARCHITECTURE_GROUP_HEADER - 18,
    },
  };
}

/**
 * Packs semantic groups into disjoint horizontal lanes after ELK has laid out
 * the nodes. ELK remains responsible for dependency order; this pass only
 * translates complete groups, preserving the relative positions of members.
 */
export function packArchitectureGroups(
  positions: Map<string, ArchitecturePoint>,
  groups: ArchitectureGroup[],
): Map<string, ArchitecturePoint> {
  const next = new Map([...positions].map(([id, point]) => [id, { ...point }]));
  const records = groups
    .map((group) => ({ group, bounds: architectureGroupBounds(next, group) }))
    .filter((record): record is { group: ArchitectureGroup; bounds: ArchitectureGroupBounds } =>
      Boolean(record.bounds),
    )
    .sort(
      (a, b) =>
        a.bounds.x - b.bounds.x || a.bounds.y - b.bounds.y || a.group.id.localeCompare(b.group.id),
    );

  let cursor = Number.NEGATIVE_INFINITY;
  for (const record of records) {
    let shiftX = Math.max(0, cursor - record.bounds.x);
    const memberIds = new Set(record.group.member_node_ids);
    const otherNodes = [...next]
      .filter(([id]) => !memberIds.has(id))
      .map(([, point]) => ({
        x: point.x,
        y: point.y,
        width: ARCHITECTURE_NODE_WIDTH,
        height: ARCHITECTURE_NODE_HEIGHT,
      }));

    // Keep the whole title strip clear of every non-member node, including
    // nodes that are intentionally outside a semantic group.
    let candidate = { ...record.bounds.header, x: record.bounds.header.x + shiftX };
    let moved = true;
    while (moved) {
      moved = false;
      for (const node of otherNodes) {
        if (!rectanglesOverlap(candidate, node)) continue;
        const required = node.x + node.width + ARCHITECTURE_GROUP_GAP - candidate.x;
        if (required > 0) {
          shiftX += required;
          candidate = { ...candidate, x: candidate.x + required };
          moved = true;
        }
      }
    }
    if (shiftX > 0) {
      for (const memberId of record.group.member_node_ids) {
        const point = next.get(memberId);
        if (point) next.set(memberId, { x: point.x + shiftX, y: point.y });
      }
      record.bounds = architectureGroupBounds(next, record.group)!;
    }
    cursor = record.bounds.x + record.bounds.width + ARCHITECTURE_GROUP_GAP;
  }
  return next;
}
