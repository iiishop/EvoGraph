import { command } from './client';
import type { TurnSummary } from '../types';

export interface TurnResult {
  turn_id: string;
  pending: boolean;
  summary: TurnSummary | null;
}

export async function waitForTurnResult(
  projectId: string,
  turnId: string,
  read = () => command<TurnResult>('agent.turn_result', { project_id: projectId, turn_id: turnId }),
  pause = () => new Promise<void>((resolve) => setTimeout(resolve, 250)),
  timeoutMs = 30000,
): Promise<TurnResult> {
  // An abort is not a rollback. Bound resynchronization so a lost bridge/network
  // response cannot leave the entire workspace busy indefinitely. A timeout is
  // explicitly unknown, never synthesized as a completed or rolled-back turn.
  let expired = false;
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      expired = true;
      reject(new Error('本轮结果尚未确认，请稍后重新打开项目查看已保存的更改'));
    }, timeoutMs);
  });
  const poll = async () => {
    while (!expired) {
      const result = await read();
      if (result.turn_id !== turnId || (result.summary && result.summary.turn_id !== turnId)) {
        throw new Error('收到其他轮次的结果，请重新打开项目检查');
      }
      if (!result.pending) return result;
      await pause();
    }
    throw new Error('本轮结果尚未确认');
  };
  try {
    return await Promise.race([poll(), timeout]);
  } finally {
    expired = true;
    clearTimeout(timer!);
  }
}

export async function bestEffortRefresh(refresh: () => Promise<void>, timeoutMs = 3000) {
  let timer: ReturnType<typeof setTimeout>;
  try {
    await Promise.race([
      refresh(),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, timeoutMs);
      }),
    ]);
  } catch {
    // A verified terminal outcome remains authoritative if the subsequent
    // best-effort project refresh is temporarily unavailable.
  } finally {
    clearTimeout(timer!);
  }
}
