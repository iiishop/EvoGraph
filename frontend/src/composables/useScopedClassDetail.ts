import { onScopeDispose, ref, shallowRef, watch } from 'vue';
import { command } from '../api/client';
import type { ClassDetailResult, UmlDiagram } from '../types';
import { validSourceClassModel } from '../lib/sourceClassModel';

export const MAX_DETAIL_COMPONENTS = 3;
export const MAX_DETAIL_FILES = 12;
export interface ClassDetailContext {
  key: string;
  projectId: string;
  baselineId?: string;
  architectureRevision: number;
  componentIds: string[];
  componentFiles: Record<string, string[]>;
}
export interface ClassDetailRequest {
  project_id: string;
  component_ids: string[];
  architecture_revision: number;
  file_paths?: string[];
}
type LoadDetail = (request: ClassDetailRequest) => Promise<ClassDetailResult>;

/** A bounded, explicitly requested drilldown. Closing it never changes the overview. */
export function useScopedClassDetail(
  context: () => ClassDetailContext,
  load: LoadDetail = (request) => command('architecture.class_detail', request),
) {
  const componentIds = ref<string[]>([]);
  const filePaths = ref<string[] | null>(null);
  const open = ref(false);
  const loading = ref(false);
  const result = shallowRef<ClassDetailResult | null>(null);
  const error = ref('');
  let sequence = 0;

  function invalidate() {
    sequence++;
    loading.value = false;
    result.value = null;
    error.value = '';
    open.value = false;
  }
  watch(
    () => context().key,
    () => {
      componentIds.value = [];
      invalidate();
    },
    { flush: 'sync' },
  );
  watch(
    componentIds,
    () => {
      filePaths.value = null;
      invalidate();
    },
    { flush: 'sync' },
  );
  watch(filePaths, invalidate, { flush: 'sync' });
  onScopeDispose(() => sequence++);

  function select(ids: string[]) {
    const available = new Set(context().componentIds);
    const next = [...new Set(ids)];
    if (next.length > MAX_DETAIL_COMPONENTS || next.some((id) => !available.has(id))) return false;
    if (next.join('\0') !== componentIds.value.join('\0')) componentIds.value = next;
    return true;
  }
  function toggle(id: string) {
    return select(
      componentIds.value.includes(id)
        ? componentIds.value.filter((item) => item !== id)
        : [...componentIds.value, id],
    );
  }
  function selectFiles(paths: string[] | null) {
    if (paths === null) {
      filePaths.value = null;
      return true;
    }
    const available = new Set(
      componentIds.value.flatMap((id) => context().componentFiles[id] ?? []),
    );
    const next = [...new Set(paths)];
    if (next.length > MAX_DETAIL_FILES || next.some((path) => !available.has(path))) return false;
    filePaths.value = next;
    return true;
  }
  function toggleFile(path: string) {
    const selected = filePaths.value ?? [];
    return selectFiles(
      selected.includes(path) ? selected.filter((item) => item !== path) : [...selected, path],
    );
  }
  function close() {
    sequence++;
    open.value = false;
    loading.value = false;
  }
  async function reload() {
    if (loading.value) return;
    sequence++;
    result.value = null;
    error.value = '';
    await show();
  }
  function showSaved() {
    sequence++;
    loading.value = false;
    open.value = true;
  }
  async function show() {
    if (!componentIds.value.length || filePaths.value?.length === 0 || loading.value) return;
    open.value = true;
    if (result.value) return;
    const current = ++sequence;
    const selected = [...componentIds.value];
    const { projectId, baselineId, architectureRevision, componentFiles } = context();
    const requestedFiles = [
      ...new Set(filePaths.value ?? selected.flatMap((id) => componentFiles[id] ?? [])),
    ].sort();
    loading.value = true;
    error.value = '';
    try {
      const detail = await load({
        project_id: projectId,
        component_ids: selected,
        architecture_revision: architectureRevision,
        ...(filePaths.value === null ? {} : { file_paths: [...filePaths.value] }),
      });
      if (current !== sequence) return;
      const returnedIds = [...new Set(detail.component_ids)].sort();
      if (
        returnedIds.join('\0') !== [...selected].sort().join('\0') ||
        (detail.diagram?.architecture_revision !== undefined &&
          detail.diagram.architecture_revision !== architectureRevision)
      ) {
        throw new Error('局部结构与当前模块范围不一致，请返回总览后重试。');
      }
      if (detail.semantic) {
        const semantic = detail.semantic;
        if (
          semantic.project_id !== projectId ||
          semantic.architecture_revision !== architectureRevision ||
          (baselineId !== undefined && semantic.baseline_id !== baselineId) ||
          !Array.isArray(semantic.component_ids) ||
          !Array.isArray(semantic.files) ||
          [...new Set(semantic.component_ids)].sort().join('\0') !==
            [...selected].sort().join('\0') ||
          [...new Set(semantic.files)].sort().join('\0') !== requestedFiles.join('\0') ||
          [...new Set(detail.files)].sort().join('\0') !== requestedFiles.join('\0')
        )
          throw new Error('源码语义结果与当前项目、基线或文件范围不一致，请刷新后重试。');
        if (!validSourceClassModel(semantic)) {
          detail.semantic = undefined;
          detail.limitations = [
            ...detail.limitations,
            '语义结构格式无法核验，保留标准 PlantUML 图与源码。',
          ];
        }
      }
      if (detail.status === 'ready' && !detail.diagram && !detail.image) {
        throw new Error('局部结构未返回可显示的图，请重试。');
      }
      result.value = detail;
    } catch (e) {
      if (current === sequence) error.value = e instanceof Error ? e.message : String(e);
    } finally {
      if (current === sequence) loading.value = false;
    }
  }
  return {
    componentIds,
    filePaths,
    open,
    loading,
    result,
    error,
    select,
    toggle,
    selectFiles,
    toggleFile,
    close,
    show,
    reload,
    showSaved,
  };
}

export function classDetailGuidance(status: ClassDetailResult['status']) {
  return {
    ready: '',
    empty: '所选源码中未发现可展示的类。可返回总览，选择包含类定义的模块；这不代表模块没有功能。',
    unmapped:
      '当前无法验证所选模块的源码依据。请按上方原因刷新基线或补充组件的源码映射后重试；不会按名称猜测实现。',
    too_large:
      '此范围仍然过大。请返回总览，减少所选模块，或用「缩小到文件」选择 1–12 个已关联文件。',
    unsupported: '当前模块的语言或结构暂不支持类级提取。可继续查看模块职责、关系与源码依据。',
  }[status];
}

/** Only the latest saved DESIGN revision for this exact local architecture scope is visible. */
export function matchingScopedDesigns(diagrams: UmlDiagram[], ids: string[], revision: number) {
  if (!ids.length || ids.length > MAX_DETAIL_COMPONENTS) return [];
  const selected = [...new Set(ids)].sort().join('\0');
  const latest = new Map<string, UmlDiagram>();
  for (const diagram of diagrams) {
    if (!latest.has(diagram.id) || diagram.revision >= latest.get(diagram.id)!.revision)
      latest.set(diagram.id, diagram);
  }
  return [...latest.values()].filter(
    (diagram) =>
      diagram.kind === 'class' &&
      diagram.origin !== 'source' &&
      diagram.architecture_revision === revision &&
      diagram.component_ids?.length &&
      [...new Set(diagram.component_ids)].sort().join('\0') === selected,
  );
}
