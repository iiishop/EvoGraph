import { computed, reactive } from 'vue';
import { command, CommandError } from '../api/client';
import { useWorkspace } from './useWorkspace';
import type { Project, ProjectCreationOutcome, ProjectRecord } from '../types';

type Fields = { name: string; description: string; repository: string };
type Attempt = Readonly<Fields & { project_id?: string; request_id?: string }>;
type Phase = 'editing' | 'saving' | 'uncertain' | 'confirmed' | 'existing' | 'opening';
type Saved = { outcome: ProjectCreationOutcome['outcome'] | 'updated'; project: ProjectRecord };
type Workspace = ReturnType<typeof useWorkspace>;

function definitelyRejected(error: unknown) {
  return (
    error instanceof CommandError &&
    ['BUSY', 'VALIDATION', 'INVALID_OPERATION', 'CONFLICT', 'UNKNOWN_ACTION'].includes(error.code)
  );
}
const message = (error: unknown) => (error instanceof Error ? error.message : '操作未完成');

function isProjectRecord(value: unknown): value is ProjectRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === 'string' &&
    Boolean(record.id) &&
    typeof record.name === 'string' &&
    typeof record.description === 'string' &&
    typeof record.repository === 'string' &&
    typeof record.created_at === 'string' &&
    typeof record.archived === 'boolean' &&
    Number.isInteger(record.revision) &&
    (record.revision as number) >= 0
  );
}

function isCreationOutcome(value: unknown, requestId: string): value is ProjectCreationOutcome {
  if (!value || typeof value !== 'object') return false;
  const result = value as Record<string, unknown>;
  if (
    typeof result.outcome !== 'string' ||
    !['created', 'reused_request', 'existing_repository'].includes(result.outcome) ||
    !isProjectRecord(result.project)
  )
    return false;
  // An existing-repository resolution deliberately has another request identity.
  // Actual create/replay confirmations must belong to this frozen request.
  return (
    result.outcome === 'existing_repository' ||
    (result.project as ProjectRecord & { creation_key?: unknown }).creation_key === requestId
  );
}

// A session is independent of the component lifetime. Closing an unresolved
// request and reopening must never acquire a new creation key or lose its result.
export function createProjectFormSession(workspace: Workspace, project?: Project) {
  const form = reactive({
    fields: {
      name: project?.name ?? '',
      description: project?.description ?? '',
      repository: project?.repository ?? '',
    },
    phase: 'editing' as Phase,
    saved: null as Saved | null,
    error: '',
    warning: '',
    opened: false,
    working: false,
    updateRead: null as ProjectRecord | null,
  });
  let attempt: Attempt | null = null;
  const repositoryLocked = Boolean(
    project?.baselines.length ||
    project?.milestones.some(
      (milestone) =>
        milestone.lease_active ||
        ['IN_PROGRESS', 'REVALIDATION_REQUIRED'].includes(milestone.status),
    ),
  );
  const pending = computed(() => form.working);
  const editable = computed(() => form.phase === 'editing');

  async function openKnown(token?: number) {
    if (!form.saved || form.saved.project.archived) return false;
    if (token !== undefined && !workspace.navigationIsCurrent(token)) {
      form.warning = '项目已保存；你已切换位置，未自动跳转。可在这里打开它。';
      return false;
    }
    const previous = form.saved.outcome === 'existing_repository' ? 'existing' : 'confirmed';
    form.phase = 'opening';
    const result = await workspace.selectProject(form.saved.project.id);
    form.phase = previous;
    form.opened = result === 'accepted';
    if (result === 'failed')
      form.warning = '项目已确认，但暂时无法打开。重试只会读取这个项目，不会再次保存。';
    if (result === 'superseded')
      form.warning = '你已切换位置，未覆盖新的导航。可在这里重新打开项目。';
    return form.opened;
  }

  async function submit() {
    if (pending.value || form.saved || (!attempt && !form.fields.name.trim())) return false;
    if (form.phase !== 'editing' && form.phase !== 'uncertain') return false;
    // Updates have no idempotency key. A lost response is resolved through a
    // read, never by replaying that mutation and manufacturing another event.
    if (form.phase === 'uncertain' && project) return false;
    const recoveringUnknown = form.phase === 'uncertain';
    const release = workspace.reserveProjectOperation();
    if (!release) return false;
    form.working = true;
    const navigation = workspace.navigationToken();
    if (!attempt)
      attempt = Object.freeze({
        ...(project ? { project_id: project.id } : { request_id: crypto.randomUUID() }),
        name: form.fields.name.trim(),
        description: form.fields.description,
        repository: repositoryLocked ? project!.repository : form.fields.repository.trim(),
      });
    form.phase = 'saving';
    form.error = '';
    form.warning = '';
    try {
      try {
        if (project) {
          const result = await command<unknown>('projects.update', attempt);
          if (!isProjectRecord(result) || result.id !== project.id)
            throw new Error('未收到完整的保存确认，请先核对当前项目。');
          form.saved = { outcome: 'updated', project: result };
        } else {
          const result = await command<unknown>('projects.create_with_outcome', attempt);
          if (!isCreationOutcome(result, attempt.request_id!))
            throw new Error('未收到完整的创建确认，请用原请求重试。');
          form.saved = result;
        }
      } catch (error) {
        form.error = message(error);
        // A rejection of this retry cannot establish the outcome of an
        // earlier lost response. Retain that original identity until resolved.
        if (definitelyRejected(error) && !recoveringUnknown) {
          form.phase = 'editing';
          attempt = null;
        } else {
          form.phase = 'uncertain';
          form.warning = project
            ? '尚未收到保存确认。先读取当前项目核对结果，避免重复修改。'
            : '尚未收到创建确认。提交内容已锁定；重试会使用原内容和同一个请求编号，不会另建一份。';
        }
        return false;
      }
      form.phase = form.saved.outcome === 'existing_repository' ? 'existing' : 'confirmed';
      workspace.invalidateProjectReads();
      if (form.phase === 'existing' || form.saved.project.archived) return false;
      // The write is already confirmed. Nothing below may make it editable or
      // send another create/update, regardless of refresh or navigation errors.
      try {
        await workspace.refresh();
      } catch {
        form.warning = '项目已保存，但项目列表或工作空间暂时未同步。可以直接重试打开已保存项目。';
        return false;
      }
      return await openKnown(navigation);
    } finally {
      form.working = false;
      release();
    }
  }

  async function open() {
    if (pending.value || !form.saved) return false;
    const release = workspace.reserveProjectOperation();
    if (!release) return false;
    form.working = true;
    form.error = '';
    form.warning = '';
    try {
      const accepted = await openKnown();
      if (accepted) {
        // A successful open is sufficient; the optional list refresh must not
        // reclassify it. The selected snapshot remains a real projects.get view.
        await workspace.refresh().catch(() => {});
      }
      return accepted;
    } finally {
      form.working = false;
      release();
    }
  }

  async function checkUpdate() {
    if (!project || form.phase !== 'uncertain' || !attempt) return;
    const release = workspace.reserveProjectOperation();
    if (!release) return;
    form.working = true;
    form.phase = 'opening';
    form.error = '';
    try {
      const current = await command<unknown>('projects.get', { project_id: project.id });
      if (!isProjectRecord(current) || current.id !== project.id)
        throw new Error('未收到当前项目的完整内容，请重试读取。');
      form.updateRead = current;
      if (
        current.name === attempt.name &&
        current.description === attempt.description &&
        current.repository === attempt.repository
      ) {
        form.saved = { outcome: 'updated', project: current };
        form.phase = 'confirmed';
        form.warning = '已核对：当前保存的名称、描述和仓库与提交内容一致。';
      } else {
        form.phase = 'uncertain';
        form.warning =
          '已读取当前内容，与上次提交不完全一致。原填写内容仍保留；可以继续编辑后重新保存。';
      }
    } catch (error) {
      form.phase = 'uncertain';
      form.error = message(error);
    } finally {
      form.working = false;
      release();
    }
  }

  function continueEditing() {
    if (form.phase !== 'existing' && !(form.phase === 'uncertain' && form.updateRead)) return;
    form.saved = null;
    form.updateRead = null;
    form.phase = 'editing';
    form.error = '';
    form.warning = '';
    attempt = null;
  }

  return { form, pending, editable, repositoryLocked, submit, open, checkUpdate, continueEditing };
}

type Session = ReturnType<typeof createProjectFormSession>;
const sessions = new Map<string, Session>();
export function useProjectForm(project?: Project) {
  const key = project ? `edit:${project.id}` : 'create';
  if (!sessions.has(key)) sessions.set(key, createProjectFormSession(useWorkspace(), project));
  const session = sessions.get(key)!;
  return {
    ...session,
    close: (finish = false) => {
      if (session.pending.value) return false;
      if (
        finish ||
        session.form.phase === 'editing' ||
        session.form.phase === 'existing' ||
        session.form.opened
      )
        if (sessions.get(key) === session) sessions.delete(key);
      return true;
    },
  };
}
