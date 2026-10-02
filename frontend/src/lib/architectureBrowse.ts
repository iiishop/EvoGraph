import type { Diagram } from '../types';
import { architectureRole } from './architectureRoles';

export type ArchitectureRelation = 'all' | 'upstream' | 'downstream';
export type ArchitectureViewport = { x: number; y: number; zoom: number };

// Explicit full-fit must cover tall/wide bounded diagrams; fresh entry uses a
// separate readable zoom policy instead of forcing this overview scale.
export const ARCHITECTURE_MIN_ZOOM = 0.005;

export function validArchitectureViewport(value: unknown): value is ArchitectureViewport {
  if (!value || typeof value !== 'object') return false;
  const { x, y, zoom } = value as ArchitectureViewport;
  return [x, y, zoom].every(Number.isFinite) && zoom >= ARCHITECTURE_MIN_ZOOM && zoom <= 1.8;
}

/** Search only ranks existing components; it never changes the architecture model. */
export function findArchitectureNodes(nodes: Diagram['nodes'], query: string, role = 'all') {
  const needle = query.trim().toLocaleLowerCase();
  const terms = needle.split(/\s+/u).filter(Boolean);
  return nodes
    .map((node, index) => {
      const id = node.id.toLocaleLowerCase();
      const label = node.label.toLocaleLowerCase();
      const haystack = [
        id,
        label,
        node.description,
        node.role,
        architectureRole(node.role).label,
        ...(node.source_refs ?? []),
      ]
        .join(' ')
        .toLocaleLowerCase();
      return {
        node,
        index,
        rank: !needle
          ? 0
          : id === needle
            ? 0
            : id.startsWith(needle)
              ? 1
              : label === needle
                ? 2
                : label.startsWith(needle)
                  ? 3
                  : 4,
        matches:
          (role === 'all' || (node.role ?? 'backend') === role) &&
          terms.every((term) => haystack.includes(term)),
      };
    })
    .filter((item) => item.matches)
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.node);
}

/** Directed reachability uses only declared edges, including cycles and boundary components. */
export function architectureVisibleIds(
  diagram: Diagram,
  query = '',
  role = 'all',
  focusedId = '',
  relation: string = 'all',
) {
  const matches = findArchitectureNodes(diagram.nodes, query, role).map((node) => node.id);
  if (!focusedId || relation === 'all') return new Set(matches);
  const related = new Set([focusedId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of diagram.edges) {
      const from = relation === 'upstream' ? edge.target : edge.source;
      const to = relation === 'upstream' ? edge.source : edge.target;
      if (related.has(from) && !related.has(to)) {
        related.add(to);
        changed = true;
      }
    }
  }
  return new Set(matches.filter((id) => related.has(id)));
}
