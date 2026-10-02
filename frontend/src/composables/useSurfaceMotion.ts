import { onBeforeUnmount, onMounted } from 'vue';

type Direction = 'up' | 'left' | 'right';
type Run = { animation: Animation; finish: () => void };
const ease = 'cubic-bezier(0.23, 1, 0.32, 1)';
const offset = { up: 'translateY(14px)', left: 'translateX(-18px)', right: 'translateX(22px)' };

/** Occasional surface changes only. Reading updates and camera commands never call this. */
export function useSurfaceMotion(direction: Direction = 'up') {
  const running = new Map<HTMLElement, Run>();
  const interrupted = new WeakMap<HTMLElement, { opacity: string; transform: string }>();
  let keyboard = false;
  let disposed = false;
  let media: MediaQueryList | undefined;
  const pointerInput = () => (keyboard = false);
  const keyboardInput = () => {
    keyboard = true;
    finishAll();
  };
  function finishAll() {
    for (const run of [...running.values()]) run.finish();
  }
  const preferenceChanged = () => finishAll();
  onMounted(() => {
    media =
      typeof window === 'undefined'
        ? undefined
        : window.matchMedia?.('(prefers-reduced-motion: reduce)');
    media?.addEventListener?.('change', preferenceChanged);
    if (typeof document !== 'undefined')
      document.addEventListener?.('pointerdown', pointerInput, true);
    if (typeof document !== 'undefined')
      document.addEventListener?.('keydown', keyboardInput, true);
  });
  onBeforeUnmount(() => {
    disposed = true;
    finishAll();
    media?.removeEventListener?.('change', preferenceChanged);
    if (typeof document !== 'undefined')
      document.removeEventListener?.('pointerdown', pointerInput, true);
    if (typeof document !== 'undefined')
      document.removeEventListener?.('keydown', keyboardInput, true);
  });
  function run(element: Element, entering: boolean, done: () => void = () => {}) {
    const el = element as HTMLElement;
    const previous = running.get(el);
    // Read the currently presented frame before cancelling: a quick reversal
    // continues where the user saw it, rather than replaying from the edge.
    const current = previous && window.getComputedStyle?.(el);
    const from = current
      ? { opacity: current.opacity, transform: current.transform }
      : interrupted.get(el);
    interrupted.delete(el);
    if (previous) {
      previous.animation.onfinish = null;
      previous.animation.cancel();
      running.delete(el);
    }
    if (disposed || keyboard || !el.animate) {
      done();
      return;
    }
    const reduced = media?.matches ?? false;
    const hidden = reduced ? { opacity: 0 } : { opacity: 0, transform: offset[direction] };
    const visible = reduced ? { opacity: 1 } : { opacity: 1, transform: 'none' };
    const start = from ? (reduced ? { opacity: from.opacity } : from) : entering ? hidden : visible;
    play(el, [start, entering ? visible : hidden], reduced ? 100 : entering ? 220 : 180, done);
  }
  function play(el: HTMLElement, frames: Keyframe[], duration: number, done: () => void) {
    const animation = el.animate(frames, { duration, easing: ease });
    const entry: Run = {
      animation,
      finish() {
        if (running.get(el) !== entry) return;
        running.delete(el);
        animation.onfinish = null;
        animation.cancel();
        done();
      },
    };
    running.set(el, entry);
    animation.onfinish = entry.finish;
  }
  return {
    move(element: HTMLElement, previousLeft: number) {
      const previous = running.get(element);
      if (previous) {
        previous.animation.onfinish = null;
        previous.animation.cancel();
        running.delete(element);
      }
      if (disposed || keyboard || media?.matches || !element.animate) return;
      const distance = previousLeft - element.getBoundingClientRect().left;
      if (Math.abs(distance) < 1) return;
      play(
        element,
        [{ transform: `translateX(${distance}px)` }, { transform: 'none' }],
        220,
        () => {},
      );
    },
    finish(element: Element | null | undefined) {
      if (element) running.get(element as HTMLElement)?.finish();
    },
    reveal(element: Element | null | undefined) {
      if (element) run(element, true);
    },
    enter(element: Element, done: () => void) {
      (element as HTMLElement).inert = false;
      run(element, true, done);
    },
    leave(element: Element, done: () => void) {
      (element as HTMLElement).inert = true;
      run(element, false, done);
    },
    cancel(element: Element) {
      const el = element as HTMLElement;
      const entry = running.get(el);
      if (!entry) return;
      const current = window.getComputedStyle(el);
      interrupted.set(el, { opacity: current.opacity, transform: current.transform });
      entry.animation.onfinish = null;
      entry.animation.cancel();
      running.delete(element as HTMLElement);
    },
  };
}
