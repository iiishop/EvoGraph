/** Native capture on our canvas, using Vue Flow's rendered focusable node wrapper. */
export function activateMilestoneKey(
  event: KeyboardEvent,
  milestoneIds: readonly string[],
  activate: (id: string) => void,
) {
  const target = event.target;
  if (
    !(target instanceof HTMLElement) ||
    !(event.currentTarget instanceof HTMLElement) ||
    !event.currentTarget.contains(target) ||
    !target.classList.contains('vue-flow__node') ||
    event.isComposing ||
    event.repeat ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    !['Enter', ' '].includes(event.key)
  )
    return false;
  const id = target.dataset.id;
  if (!id || !milestoneIds.includes(id)) return false;
  event.preventDefault();
  event.stopPropagation();
  activate(id);
  return true;
}
