<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue';
import { ChevronUp, X } from 'lucide-vue-next';
import MessageContent from './MessageContent.vue';
import AgentTurnSummary from './AgentTurnSummary.vue';
import { turnSummaryHeadline, turnSummaryStatus } from '../../lib/turnSummary';
import type { Project, TurnSummary } from '../../types';

const props = defineProps<{
  project: Project;
  summary: TurnSummary | null;
  running: boolean;
  disabled?: boolean;
  owner?: number;
}>();
const emit = defineEmits<{ locate: [id: string] }>();
type Review = 'reply' | 'receipt';
type Box = { left: number; top: number; width: number; height: number };
type Placement = { frame: Box; tile: Box; row: Box; ceiling: number; floor: number };
const uid = `agent-review-${useId()}`;
const strip = ref<HTMLElement>();
const replyTrigger = ref<HTMLButtonElement>();
const receiptTrigger = ref<HTMLButtonElement>();
const layer = ref<HTMLElement>();
const surface = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();
const active = ref<Review | null>(null);
// The original controls/readers stay mounted. Closing keeps presentation until motion ends.
const shown = ref<Review | null>(null);
const tileShown = ref<Review | null>(null);
const placement = ref<Placement | null>(null);
const hasReply = computed(() => props.project.messages.length > 0);
const hasReceipt = computed(() => Boolean(props.summary && !props.running));
const latestReply = computed(() =>
  props.project.messages.filter((message) => message.role === 'assistant').at(-1),
);
const replyPreview = computed(() => {
  const text = latestReply.value?.content.replace(/\s+/g, ' ').trim() ?? '';
  if (props.running) return '正在处理 · 查看已保存的对话';
  if (props.project.messages.at(-1)?.role === 'user')
    return `${props.summary ? turnSummaryStatus(props.summary) : '尚无新回复'} · 查看已保存的对话`;
  return text ? `已保存 · ${text.slice(0, 160)}` : '查看已发送的请求';
});
const trigger = (kind: Review) => (kind === 'reply' ? replyTrigger.value : receiptTrigger.value);
const available = (kind: Review) =>
  !props.disabled && (kind === 'reply' ? hasReply.value : hasReceipt.value);
const layerStyle = computed(() => ({
  top: `${placement.value?.ceiling ?? 0}px`,
  height: `${Math.max(0, (placement.value?.floor ?? 0) - (placement.value?.ceiling ?? 0))}px`,
}));
const surfaceStyle = computed(() => {
  const value = placement.value;
  return {
    left: `${value?.frame.left ?? 0}px`,
    top: `${value ? value.frame.top - value.ceiling : 0}px`,
    width: `${value?.frame.width ?? 0}px`,
    height: `${value?.frame.height ?? 0}px`,
  };
});
let motions: Animation[] = [];
let sequence = 0;
let frame = 0;
let mounted = false;
let media: MediaQueryList | undefined;
let observer: ResizeObserver | undefined;
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
const box = (rect: DOMRect): Box => ({
  left: rect.left,
  top: rect.top,
  width: rect.width,
  height: rect.height,
});

function measure(kind: Review): Placement | null {
  const anchor = strip.value;
  const slot = trigger(kind)?.parentElement;
  if (!anchor || !slot) return null;
  // Three bounded placement reads. Neither the graph nor the reader is observed/resized.
  const row = anchor.getBoundingClientRect();
  const tile = slot.getBoundingClientRect();
  const dock = anchor.closest('.agent-dock')?.getBoundingClientRect() ?? row;
  const viewport = window.visualViewport;
  const viewportLeft = viewport?.offsetLeft ?? 0;
  const viewportTop = viewport?.offsetTop ?? 0;
  const viewportWidth = viewport?.width ?? window.innerWidth;
  const viewportHeight = viewport?.height ?? window.innerHeight;
  const ceiling = viewportTop + 12;
  const floor = Math.min(dock.top - 10, viewportTop + viewportHeight - 12);
  const width = Math.min(row.width, viewportWidth - 24);
  const height = Math.min(360, floor - ceiling);
  if (width <= 0 || height < 64) return null;
  const left = clamp(row.left, viewportLeft + 12, viewportLeft + viewportWidth - width - 12);
  return {
    ceiling,
    floor,
    frame: { left, top: floor - height, width, height },
    row: box(row),
    tile: box(tile),
  };
}
function cancelMotion() {
  for (const animation of motions) {
    animation.onfinish = null;
    animation.cancel();
  }
  motions = [];
}
function transform(rect: Box, base: Box) {
  return `translate(${rect.left - base.left}px, ${rect.top - base.top}px) scale(${rect.width / base.width}, ${rect.height / base.height})`;
}
function settleNow() {
  sequence++;
  cancelMotion();
  shown.value = active.value;
  tileShown.value = active.value;
}
async function change(next: Review | null, animate: boolean, restoreFocus: boolean) {
  const previous = active.value ?? tileShown.value;
  const kind = next ?? previous;
  if (!kind || (next && !available(next))) return;
  const button = trigger(kind);
  const fill = button?.querySelector<HTMLElement>('.review-tile-fill');
  const copy = button?.querySelector<HTMLElement>('.review-tile-copy');
  const sibling = trigger(kind === 'reply' ? 'receipt' : 'reply');
  const siblingOpacity = sibling ? Number(getComputedStyle(sibling).opacity) : 0;
  // Sample actual tile visuals BEFORE cancelling an interrupted effect. Reading text never scales.
  const visual = fill ? box(fill.getBoundingClientRect()) : null;
  const copyLeft = copy?.getBoundingClientRect().left;
  const copyOpacity = copy ? Number(getComputedStyle(copy).opacity) : 1;
  const readerOpacity =
    shown.value && surface.value ? Number(getComputedStyle(surface.value).opacity) : 0;
  const measured = measure(kind);
  const token = ++sequence;
  cancelMotion();
  if (!measured) {
    active.value = null;
    shown.value = null;
    tileShown.value = null;
    return;
  }
  const focusAtRequest = document.activeElement;
  active.value = next;
  tileShown.value = kind;
  if (next) shown.value = next;
  placement.value = measured;
  if (restoreFocus && previous) {
    const control = trigger(previous);
    if (control?.isConnected && !control.disabled) control.focus({ preventScroll: true });
  }
  await nextTick();
  if (!mounted || token !== sequence) return;
  if (next && document.activeElement === focusAtRequest)
    closeButton.value?.focus({ preventScroll: true });
  const element = surface.value;
  if (!animate || media?.matches || !element?.animate || !fill || !copy) {
    shown.value = next;
    tileShown.value = next;
    return;
  }
  const options = {
    duration: next ? 220 : 180,
    easing: 'cubic-bezier(0.23, 1, 0.32, 1)',
    fill: 'both' as const,
  };
  const source = visual && visual.width > 0 ? visual : measured.tile;
  const target = next ? measured.row : measured.tile;
  const tileMotion = fill.animate(
    [
      { transform: transform(source, measured.row), opacity: 1 },
      { transform: transform(target, measured.row), opacity: 1 },
    ],
    options,
  );
  // Counterpart label moves at its normal size; only the decorative card surface changes scale.
  const copyMotion = copy.animate(
    [
      {
        transform: `translateX(${(copyLeft ?? measured.tile.left + 12) - measured.row.left - 12}px)`,
        opacity: copyOpacity,
      },
      {
        transform: `translateX(${next ? 0 : measured.tile.left - measured.row.left}px)`,
        opacity: next ? 1 : 0,
      },
    ],
    options,
  );
  const readerMotion = element.animate(
    [{ opacity: readerOpacity }, { opacity: next ? 1 : 0 }],
    options,
  );
  const siblingMotion = sibling?.animate(
    [{ opacity: siblingOpacity }, { opacity: next ? 0 : 1 }],
    options,
  );
  const owned = [tileMotion, copyMotion, readerMotion, ...(siblingMotion ? [siblingMotion] : [])];
  motions = owned;
  tileMotion.onfinish = () => {
    if (token !== sequence) return;
    shown.value = active.value;
    tileShown.value = active.value;
    motions = [];
    // Apply collapsed DOM geometry/visibility before removing filled effects.
    void nextTick(() => {
      for (const animation of owned) animation.cancel();
    });
  };
}
function toggle(kind: Review, event: MouseEvent) {
  // The faded sibling is inert; this guard also rejects synthetic activation of it.
  if (tileShown.value && tileShown.value !== kind) return;
  void change(active.value === kind ? null : kind, event.detail > 0, active.value === kind);
}
function close(event: MouseEvent) {
  void change(null, event.detail > 0, true);
}
function escape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || !active.value)
    return;
  event.preventDefault();
  void change(null, false, true);
}
function outside(event: PointerEvent) {
  const target = event.target;
  if (
    !active.value ||
    !(target instanceof Node) ||
    strip.value?.contains(target) ||
    layer.value?.contains(target)
  )
    return;
  // Never prevent this event or restore focus; the clicked control owns it.
  void change(null, true, false);
}
function locate(id: string) {
  emit('locate', id);
  reset();
}
function reposition() {
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  const kind = active.value;
  settleNow();
  if (!kind) return;
  const measured = measure(kind);
  if (measured) placement.value = measured;
  else {
    // A viewport constraint may retire the reader without a user dismissal. Return only
    // its own focus to the still-owned trigger; background focus remains untouched.
    const restore = surface.value?.contains(document.activeElement);
    const button = trigger(kind);
    reset();
    if (restore && available(kind) && button?.isConnected && !button.disabled)
      button.focus({ preventScroll: true });
  }
}
function schedulePosition(event?: Event) {
  if (!shown.value || frame) return;
  if (event?.target instanceof Node && layer.value?.contains(event.target)) return;
  frame = requestAnimationFrame(reposition);
}
function reset() {
  active.value = null;
  settleNow();
}
function reduceMotion(event: MediaQueryListEvent) {
  if (event.matches) settleNow();
}
watch(
  () => [props.project.id, props.project.created_at, props.owner],
  () => {
    reset();
    for (const reader of surface.value?.querySelectorAll<HTMLElement>('.review-reader') ?? [])
      reader.scrollTop = 0;
  },
);
watch(() => props.project.question?.id, reset, { flush: 'sync' });
watch(
  () => [props.disabled, hasReply.value, hasReceipt.value],
  () => {
    const kind = active.value ?? tileShown.value;
    if (kind && !available(kind)) reset();
  },
  { flush: 'sync' },
);
onMounted(() => {
  mounted = true;
  media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  media?.addEventListener('change', reduceMotion);
  document.addEventListener('keydown', escape);
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('scroll', schedulePosition, true);
  window.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('scroll', schedulePosition);
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      if (shown.value) reposition();
    });
    if (strip.value) observer.observe(strip.value);
    const dock = strip.value?.closest('.agent-dock');
    if (dock) observer.observe(dock);
  }
});
onUnmounted(() => {
  mounted = false;
  reset();
  if (frame) cancelAnimationFrame(frame);
  observer?.disconnect();
  media?.removeEventListener('change', reduceMotion);
  document.removeEventListener('keydown', escape);
  document.removeEventListener('pointerdown', outside, true);
  document.removeEventListener('scroll', schedulePosition, true);
  window.removeEventListener('resize', schedulePosition);
  window.visualViewport?.removeEventListener('resize', schedulePosition);
  window.visualViewport?.removeEventListener('scroll', schedulePosition);
});
</script>

<template>
  <div
    v-show="!disabled && (hasReply || hasReceipt)"
    ref="strip"
    class="agent-review-tray"
    :class="{ 'is-single': !hasReply || !hasReceipt }"
    role="group"
    aria-label="本轮回复与最近变更"
  >
    <div v-if="hasReply" class="review-tile-slot">
      <button
        :id="`${uid}-reply-trigger`"
        ref="replyTrigger"
        type="button"
        class="review-tile"
        :class="{
          'is-expanded': tileShown === 'reply',
          'is-muted': tileShown && tileShown !== 'reply',
        }"
        :inert="tileShown && tileShown !== 'reply' ? true : undefined"
        :disabled="disabled"
        :aria-expanded="active === 'reply'"
        :aria-controls="`${uid}-surface`"
        @click="toggle('reply', $event)"
      >
        <span class="review-tile-fill" aria-hidden="true"></span>
        <span class="review-tile-copy">
          <span class="review-tile-title"
            >{{ latestReply ? '本轮回复' : '对话记录' }} <ChevronUp :size="13" aria-hidden="true"
          /></span>
          <span class="review-tile-preview">{{ replyPreview }}</span>
        </span>
      </button>
    </div>
    <div v-if="hasReceipt" class="review-tile-slot">
      <button
        :id="`${uid}-receipt-trigger`"
        ref="receiptTrigger"
        type="button"
        class="review-tile"
        :class="{
          'is-expanded': tileShown === 'receipt',
          'is-muted': tileShown && tileShown !== 'receipt',
        }"
        :inert="tileShown && tileShown !== 'receipt' ? true : undefined"
        :disabled="disabled"
        :aria-expanded="active === 'receipt'"
        :aria-controls="`${uid}-surface`"
        @click="toggle('receipt', $event)"
      >
        <span class="review-tile-fill" aria-hidden="true"></span>
        <span class="review-tile-copy">
          <span class="review-tile-title"
            ><span>最近变更</span
            ><span class="review-tile-status">{{ summary ? turnSummaryStatus(summary) : '' }}</span
            ><ChevronUp :size="13" aria-hidden="true"
          /></span>
          <span
            class="review-tile-preview"
            :class="{ 'has-warning': summary?.history_warning }"
            :title="summary?.history_warning"
            >{{
              summary?.history_warning
                ? '对话未完整保存'
                : summary
                  ? turnSummaryHeadline(summary)
                  : ''
            }}</span
          >
        </span>
      </button>
    </div>
  </div>
  <Teleport to="body">
    <div v-show="shown" ref="layer" class="agent-review-layer" :style="layerStyle">
      <section
        :id="`${uid}-surface`"
        ref="surface"
        class="agent-review-surface"
        :style="surfaceStyle"
        role="dialog"
        aria-modal="false"
        :aria-labelledby="`${uid}-${shown ?? 'reply'}-trigger`"
        :aria-hidden="!active"
        :inert="active ? undefined : true"
      >
        <header class="review-surface-heading">
          <div>
            <strong>{{ shown === 'receipt' ? '最近变更' : '对话记录' }}</strong>
            <span>{{
              shown === 'receipt' && summary ? turnSummaryStatus(summary) : '已保存的请求与回复'
            }}</span>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="review-close"
            aria-label="收起阅读面板"
            @click="close"
          >
            <X :size="16" aria-hidden="true" />
          </button>
        </header>
        <div
          v-show="shown === 'reply'"
          class="review-reader review-history"
          tabindex="0"
          aria-label="项目对话记录"
        >
          <article v-for="message in project.messages" :key="message.id" :class="message.role">
            <strong>{{ message.role === 'assistant' ? 'EvoGraph' : '你' }}</strong>
            <MessageContent :message="message" />
          </article>
        </div>
        <div
          v-show="shown === 'receipt'"
          class="review-reader review-receipt"
          tabindex="0"
          aria-label="最近已保存的规划变更"
        >
          <AgentTurnSummary
            v-if="summary && !running"
            :summary="summary"
            :milestones="[...project.milestones, ...(project.source_milestones ?? [])]"
            :open="true"
            @locate="locate"
          />
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.agent-review-tray {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
  flex: 0 0 52px;
  height: 52px;
  min-height: 52px;
  min-width: 0;
}
.agent-review-tray.is-single {
  grid-template-columns: minmax(0, 1fr);
}
.review-tile {
  position: relative;
  display: block;
  width: 100%;
  min-width: 0;
  height: 52px;
  padding: 7px 12px;
  border: 0;
  border-radius: 11px;
  background: transparent;
  overflow: hidden;
  color: var(--ink, #233c4d);
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.review-tile-slot {
  min-width: 0;
  height: 52px;
}
.review-tile.is-expanded {
  position: absolute;
  inset: 0;
  width: 100%;
  z-index: 2;
}
.review-tile.is-muted {
  opacity: 0;
  pointer-events: none;
}
.review-tile-fill {
  position: absolute;
  inset: 0;
  border: 1px solid var(--line, #d8e1e7);
  border-radius: 11px;
  background: #ffffffed;
  transform-origin: top left;
  box-sizing: border-box;
}
.review-tile-copy {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.review-tile-status {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 500;
  color: var(--warning, #946627);
}
.review-tile-preview.has-warning {
  color: var(--warning, #946627);
}
.review-tile[aria-expanded='true'] .review-tile-fill {
  border-color: var(--accent, #376d83);
  background: #f2f7f9;
}
.review-tile-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  font-weight: 650;
}
.review-tile-preview {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--text-secondary, #617383);
  font-size: 11px;
  line-height: 1.4;
}
.agent-review-layer {
  position: fixed;
  left: 0;
  right: 0;
  z-index: 110;
  overflow: hidden;
  pointer-events: none;
}
.agent-review-surface {
  position: absolute;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
  border: 1px solid var(--line, #d8e1e7);
  border-radius: 14px;
  background: #fff;
  color: var(--ink, #233c4d);
  box-shadow: 0 10px 32px #223d551c;
  overflow: hidden;
  pointer-events: auto;
  transform-origin: top left;
}
.agent-review-surface[aria-hidden='true'] {
  pointer-events: none;
}
.review-surface-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: 0 0 auto;
  min-height: 46px;
  padding: 6px 12px 6px 16px;
  border-bottom: 1px solid var(--line, #d8e1e7);
}
.review-surface-heading > div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 12px;
}
.review-surface-heading strong {
  font-size: 13px;
}
.review-surface-heading span {
  color: var(--text-secondary, #617383);
  font-size: 11px;
}
.review-close {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 8px;
  background: #eef3f5;
  color: inherit;
  cursor: pointer;
}
.review-reader {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  overflow-wrap: anywhere;
  padding: 4px 16px 14px;
  font-size: 12px;
  line-height: 1.65;
}
.review-history article {
  padding: 12px 0;
}
.review-history article + article {
  border-top: 1px solid var(--line, #d8e1e7);
}
.review-history article > strong {
  color: var(--accent, #376d83);
  font-size: 11px;
}
.review-history :deep(.message-content) {
  margin: 5px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.review-history article.user :deep(.message-content) {
  padding: 8px 10px;
  border-radius: 8px;
  background: #f4f7f9;
}
.review-receipt :deep(.agent-turn-summary) {
  margin: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.review-receipt :deep(.agent-turn-summary > summary) {
  display: none;
}
.review-receipt :deep(.turn-summary-scroll) {
  max-height: none;
  overflow: visible;
  padding: 10px 0 0;
}
.review-tile:focus-visible,
.review-close:focus-visible,
.review-reader:focus-visible {
  outline: 2px solid var(--accent, #376d83);
  outline-offset: -3px;
}
@media (hover: hover) and (pointer: fine) {
  .review-tile:hover .review-tile-fill,
  .review-close:hover {
    background: #edf4f7;
  }
}
</style>
