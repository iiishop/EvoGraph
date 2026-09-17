import { reactive, readonly } from 'vue';
import { truncate } from '../lib/changeSummary';
const items = reactive<{ id: number; message: string }[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let sequence = 0;
function dismiss(id: number) {
  const index = items.findIndex((n) => n.id === id);
  if (index >= 0) items.splice(index, 1);
  clearTimeout(timers.get(id));
  timers.delete(id);
}
function push(message: string) {
  if (!message) return;
  const id = ++sequence;
  items.push({ id, message: truncate(message) });
  timers.set(
    id,
    setTimeout(() => dismiss(id), 5000),
  );
}
export function useNotifications() {
  return { items: readonly(items), push, dismiss };
}
