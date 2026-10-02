import { reactive, readonly } from 'vue';
import { truncate } from '../lib/changeSummary';
const items = reactive<{ id: number; message: string; group?: string }[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let sequence = 0;
function dismiss(id: number) {
  const index = items.findIndex((n) => n.id === id);
  if (index >= 0) items.splice(index, 1);
  clearTimeout(timers.get(id));
  timers.delete(id);
}
function push(message: string, group?: string) {
  if (!message) return;
  // One stable live update per project. Repeated graph edits update its text,
  // rather than stacking over the composer or restarting its enter transition.
  const previous = group ? items.find((item) => item.group === group) : undefined;
  const id = previous?.id ?? ++sequence;
  if (previous) previous.message = truncate(message);
  else items.push({ id, message: truncate(message), ...(group ? { group } : {}) });
  clearTimeout(timers.get(id));
  timers.set(
    id,
    setTimeout(() => dismiss(id), 5000),
  );
}
export function useNotifications() {
  return { items: readonly(items), push, dismiss };
}
