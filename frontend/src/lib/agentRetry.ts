import type { DraftRequest, FailedDraft } from '../composables/useAgentDrafts';
import type { Project } from '../types';
import {
  trimComposerDocument,
  normalizeComposerDocument,
  renderComposerDocument,
} from './composerDocument';

type RetryPlan =
  | { kind: 'ready'; request: DraftRequest }
  | { kind: 'blocked'; message: string; differentQuestion?: boolean };

export async function waitForRetryRead<T>(read: Promise<T>, timeoutMs = 30000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      read,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error('读取当前项目超时，原请求仍保留，请稍后重试')),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

// The failed draft is canonical user input. Only the outgoing request receives
// recovery context, so another failed retry cannot accumulate wrapper text.
export function planAgentRetry(
  failure: FailedDraft,
  current: Pick<Project, 'question' | 'milestones'>,
): RetryPlan {
  const question = current.question;
  if (question && question.id !== failure.questionId) {
    return {
      kind: 'blocked',
      message: '当前有新的待确认问题，原请求已保留。请先回答当前问题，不会复用旧回答。',
      differentQuestion: true,
    };
  }
  const document = failure.composerDocument
    ? trimComposerDocument(failure.composerDocument)
    : undefined;
  const request: DraftRequest = {
    text: document ? renderComposerDocument(document) : failure.text.trim(),
    ...(document ? { composerDocument: document } : {}),
    questionId: question?.id,
    verificationMilestone: question
      ? (question.verification_milestone ?? undefined)
      : (failure.verificationMilestone ?? failure.question?.verification_milestone ?? undefined),
  };
  if (
    request.verificationMilestone &&
    !current.milestones.some((milestone) => milestone.id === request.verificationMilestone)
  ) {
    return {
      kind: 'blocked',
      message: `原回答关联的验收里程碑 ${request.verificationMilestone} 已不存在，未发送。原请求仍保留，请先确认当前要处理的里程碑。`,
    };
  }
  if (failure.questionId && !question && document) {
    const prefix = [
      '上次回答后的处理未完成。请基于当前已保存的项目状态继续剩余工作，保留已经提交的修改，不要重放或重复已完成的操作。',
      '原问题（恢复上下文）：',
      JSON.stringify({
        id: failure.questionId,
        prompt: failure.question?.prompt ?? '原问题内容未保留，请结合项目对话记录理解。',
        context: failure.question?.context ?? '',
      }),
      '原回答：',
      '',
    ].join('\n');
    request.composerDocument = normalizeComposerDocument({
      version: 1,
      parts: [{ type: 'text', text: prefix }, ...document.parts],
    });
    request.text = renderComposerDocument(request.composerDocument);
  } else if (failure.questionId && !question) {
    request.text = [
      '上次回答后的处理未完成。请基于当前已保存的项目状态继续剩余工作，保留已经提交的修改，不要重放或重复已完成的操作。',
      '原问题与回答（恢复上下文）：',
      JSON.stringify({
        question: {
          id: failure.questionId,
          prompt: failure.question?.prompt ?? '原问题内容未保留，请结合项目对话记录理解。',
          context: failure.question?.context ?? '',
        },
        answer: failure.text.trim(),
      }),
    ].join('\n');
  }
  if (request.text.length > 16000) {
    return {
      kind: 'blocked',
      message:
        '原问题与回答加上恢复说明超过 16000 字符，未发送。原请求仍保留，请缩短或重新组织回答后发送。',
    };
  }
  return { kind: 'ready', request };
}
