import { computed, reactive, watch } from 'vue';
import type { Diagram, Project } from '../types';
import { validArchitectureViewport } from '../lib/architectureBrowse';
import type { ArchitectureRelation, ArchitectureViewport } from '../lib/architectureBrowse';

type BrowseProject = Pick<Project, 'id'> &
  Partial<Pick<Project, 'created_at' | 'architectures' | 'source_diagram'>>;
export interface ArchitectureBrowseView {
  key: string;
  query: string;
  focusedId: string;
  role: string;
  relation: ArchitectureRelation;
  viewport: ArchitectureViewport | null;
  nodeIds: string[];
}
interface BrowseProjectState {
  incarnation: string;
  key: string;
  view: string;
  views: Map<string, ArchitectureBrowseView>;
}
const initialView = (project: BrowseProject) =>
  project.architectures?.length ? 'current' : 'source';
const viewDiagram = (project: BrowseProject, view: string): Diagram | null | undefined =>
  view === 'source'
    ? project.source_diagram
    : view === 'current'
      ? project.architectures?.at(-1)?.diagram
      : project.architectures?.find((item) => `revision:${item.number}` === view)?.diagram;

/** Session-only navigation state. No source result or evidence is stored here. */
export function createArchitectureBrowse() {
  const projects = reactive(new Map<string, BrowseProjectState>());
  const discarded = new Set<string>();
  const retiredIncarnations = new Map<string, Set<string>>();
  let sequence = 0;
  function entry(project: BrowseProject) {
    if (discarded.has(project.id)) return undefined;
    const incarnation = project.created_at ?? '';
    if (retiredIncarnations.get(project.id)?.has(incarnation)) return undefined;
    let saved = projects.get(project.id);
    if (!saved || saved.incarnation !== incarnation) {
      if (saved) {
        if (!retiredIncarnations.has(project.id)) retiredIncarnations.set(project.id, new Set());
        retiredIncarnations.get(project.id)!.add(saved.incarnation);
      }
      projects.set(project.id, {
        incarnation,
        key: `${project.id}:${++sequence}`,
        view: initialView(project),
        views: new Map(),
      });
      saved = projects.get(project.id)!;
    }
    return saved;
  }
  function reconcile(project: BrowseProject) {
    const saved = entry(project);
    if (!saved) return;
    const validViews = new Set([
      'current',
      'source',
      ...(project.architectures ?? []).map((item) => `revision:${item.number}`),
    ]);
    if (!validViews.has(saved.view)) saved.view = initialView(project);
    for (const [view, state] of saved.views) {
      if (!validViews.has(view)) {
        saved.views.delete(view);
        continue;
      }
      const nodes = viewDiagram(project, view)?.nodes ?? [];
      const ids = new Set(nodes.map((node) => node.id));
      if (!ids.has(state.focusedId)) {
        state.focusedId = '';
        state.relation = 'all';
      }
      if (state.role !== 'all' && !nodes.some((node) => (node.role ?? 'backend') === state.role))
        state.role = 'all';
      // A replacement graph with no shared components has no meaningful previous camera.
      if (state.nodeIds.length && !state.nodeIds.some((id) => ids.has(id))) {
        state.viewport = null;
        state.key = `${saved.key}:${view}:${++sequence}`;
      }
      state.nodeIds = [...ids];
    }
  }
  function viewState(project: BrowseProject) {
    const saved = entry(project);
    if (!saved) return undefined;
    if (!saved.views.has(saved.view))
      saved.views.set(saved.view, {
        key: `${saved.key}:${saved.view}`,
        query: '',
        focusedId: '',
        role: 'all',
        relation: 'all',
        viewport: null,
        nodeIds: viewDiagram(project, saved.view)?.nodes.map((node) => node.id) ?? [],
      });
    return saved.views.get(saved.view)!;
  }
  function discard(id: string) {
    projects.delete(id);
    discarded.add(id);
  }
  return {
    entry,
    viewState,
    reconcile,
    discard,
    activate(project: BrowseProject) {
      discarded.delete(project.id);
      retiredIncarnations.get(project.id)?.delete(project.created_at ?? '');
      reconcile(project);
    },
    retain(ids: string[]) {
      for (const id of projects.keys()) if (!ids.includes(id)) discard(id);
    },
    rememberViewport(project: BrowseProject, key: string, viewport: ArchitectureViewport) {
      const saved = projects.get(project.id);
      if (saved?.incarnation !== (project.created_at ?? '') || !validArchitectureViewport(viewport))
        return;
      const state = saved.views.get(saved.view);
      if (state?.key === key) state.viewport = { ...viewport };
    },
  };
}
export const architectureBrowse = createArchitectureBrowse();

export function useArchitectureBrowse(project: () => Project) {
  // Observe topology, not the complete project: streamed messages and evidence can grow
  // independently and must never be traversed by this synchronous navigation watcher.
  watch(
    () => {
      const value = project();
      return {
        id: value.id,
        created_at: value.created_at,
        architectures: value.architectures,
        source_diagram: value.source_diagram,
      };
    },
    (value) => architectureBrowse.reconcile(value),
    { immediate: true, flush: 'sync', deep: true },
  );
  const state = computed(() => architectureBrowse.viewState(project()));
  const view = computed({
    get: () => architectureBrowse.entry(project())?.view ?? initialView(project()),
    set: (value) => {
      const saved = architectureBrowse.entry(project());
      if (saved) saved.view = value;
    },
  });
  function field<T extends 'query' | 'focusedId' | 'role' | 'relation'>(
    key: T,
    fallback: ArchitectureBrowseView[T],
  ) {
    return computed({
      get: () => state.value?.[key] ?? fallback,
      set: (value) => {
        if (state.value) state.value[key] = value;
      },
    });
  }
  return {
    view,
    query: field('query', ''),
    focusedId: field('focusedId', ''),
    role: field('role', 'all'),
    relation: field('relation', 'all'),
    key: computed(() => state.value?.key ?? ''),
    viewport: computed(() => state.value?.viewport ?? null),
    rememberViewport: ({ key, viewport }: { key: string; viewport: ArchitectureViewport }) =>
      architectureBrowse.rememberViewport(project(), key, viewport),
  };
}
