import { onMounted, onUnmounted, type Ref } from 'vue';
import gsap from 'gsap';

export function useEntrance(element: Ref<HTMLElement | undefined>) {
  let context: gsap.Context | undefined;
  onMounted(() => {
    if (!element.value || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    context = gsap.context(() => {
      gsap.from(element.value!, { opacity: 0, y: 9, duration: 0.35, ease: 'power2.out' });
    }, element.value);
  });
  onUnmounted(() => context?.revert());
}
