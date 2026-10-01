import type { Milestone } from '../types';

type SearchableMilestone = Pick<Milestone, 'id' | 'title' | 'scope' | 'origin'>;
export function findMilestones<T extends SearchableMilestone>(milestones: T[], query: string): T[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return milestones;
  const terms = needle.split(/\s+/u);
  return milestones
    .map((milestone, index) => {
      const id = milestone.id.toLocaleLowerCase();
      const title = milestone.title.toLocaleLowerCase();
      const haystack =
        `${id} ${title} ${milestone.scope.join(' ')} ${milestone.origin === 'source' ? 'SRC 源码观察' : '计划'}`.toLocaleLowerCase();
      const rank =
        id === needle
          ? 0
          : id.startsWith(needle)
            ? 1
            : title === needle
              ? 2
              : title.startsWith(needle)
                ? 3
                : 4;
      return { milestone, index, rank, matches: terms.every((term) => haystack.includes(term)) };
    })
    .filter((item) => item.matches)
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.milestone);
}
