<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue';
import { ChevronUp, X } from 'lucide-vue-next';
import MessageContent from './MessageContent.vue';
import { markdownTextPreview } from './MarkdownContent';
import AgentTurnSummary from './AgentTurnSummary.vue';
import {
  parseTurnSummary,
  readTurnReceipt,
  selectTurnReceipt,
  turnSummaryHeadline,
  turnSummaryStatus,
  type TurnReceiptSelection,
} from '../../lib/turnSummary';
import type { SavedRequestSelection } from '../../composables/useAgentDrafts';
import type { Message, Project, TurnSummary } from '../../types';

const props = defineProps<{
  project: Project;
  summary: TurnSummary | null;
  running: boolean;
  disabled?: boolean;
  owner?: number;
  reuseBlocker?: string;
  reuseError?: string;
}>();
const emit = defineEmits<{ locate: [id: string]; reuse: [selection: SavedRequestSelection] }>();
type Review = 'reply' | 'receipt';
type HistoryRow = {
  message: Message;
  selection: SavedRequestSelection | null;
  reuse: (event: MouseEvent) => void;
};
type Box = { left: number; top: number; width: number; height: number };
type Placement = { frame: Box; tile: Box; row: Box; ceiling: number; floor: number };
const uid = `agent-review-${useId()}`;
const strip = ref<HTMLElement>();
const replyTrigger = ref<HTMLButtonElement>();
const receiptTrigger = ref<HTMLButtonElement>();
const layer = ref<HTMLElement>();
const surface = ref<HTMLElement>();
const history = ref<HTMLElement>();
const receiptReader = ref<HTMLElement>();
const surfaceFill = ref<HTMLElement>();
const surfaceContent = ref<HTMLElement>();
const surfaceHeading = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();
const active = ref<Review | null>(null);
const historySession = ref(0);
// The original controls/readers stay mounted. Closing keeps presentation until motion ends.
const shown = ref<Review | null>(null);
const tileShown = ref<Review | null>(null);
const placement = ref<Placement | null>(null);
const selectedReceipt = ref<TurnReceiptSelection | null>(null);
let receiptOrigin: HTMLElement | undefined;
let resetReceiptScroll = false;
const selectedRead = computed(() =>
  selectedReceipt.value ? readTurnReceipt(props.project, selectedReceipt.value) : null,
);
const readingSummary = computed(() =>
  selectedRead.value ? selectedRead.value.summary : props.running ? null : props.summary,
);
const receiptTitle = computed(() => (selectedReceipt.value ? '历史变更' : '最近变更'));
const receiptContext = computed(() => {
  const selected = selectedReceipt.value;
  if (!selected) return '';
  const saved = parseTurnSummary(selected.detail);
  const date = new Date(selected.createdAt);
  const time = Number.isNaN(date.valueOf())
    ? '时间未记录'
    : `${date
        .toISOString()
        .replace('T', ' ')
        .replace(/\.\d{3}Z$/, '')} UTC`;
  return saved
    ? `${time} · 回合 ${saved.turn_id} · 版本 ${saved.before_revision} → ${saved.after_revision}`
    : `${time} · 事件 ${selected.eventId} · 回合与版本信息不可用`;
});
const hasReply = computed(() => props.project.messages.length > 0);
const hasReceipt = computed(() =>
  Boolean(selectedReceipt.value || (props.summary && !props.running)),
);
const latestReply = computed(() =>
  props.project.messages.filter((message) => message.role === 'assistant').at(-1),
);
const replyPreview = computed(() => {
  const text = markdownTextPreview(latestReply.value?.content ?? '');
  if (props.running) return '正在处理 · 查看已保存的对话';
  if (props.project.messages.at(-1)?.role === 'user')
    return `${props.summary ? turnSummaryStatus(props.summary) : '尚无新回复'} · 查看已保存的对话`;
  return text ? `最近保存的回复 · ${text.slice(0, 160)}` : '查看已发送的请求';
});
const reuseBlocked = computed(() =>
  props.project.question
    ? '请先完成当前问题，再将历史请求用作新草稿。'
    : (props.reuseBlocker ?? ''),
);
const savedUserMessage = (message: Message, projectId: string) =>
  message.role === 'user' &&
  Boolean(message.id) &&
  (message.project_id == null || message.project_id === projectId);
const historyRows = computed<HistoryRow[]>(() => {
  const projectId = props.project.id;
  const projectCreatedAt = props.project.created_at;
  const owner = props.owner;
  const session = historySession.value;
  return props.project.messages.map((message) => {
    // Capture scope with the rendered row. Old callbacks must never borrow the
    // project, incarnation or owner of a newer render.
    const selection: SavedRequestSelection | null = savedUserMessage(message, projectId)
      ? {
          messageId: message.id,
          projectId,
          projectCreatedAt,
          ...(owner === undefined ? {} : { owner }),
        }
      : null;
    const row: HistoryRow = {
      message,
      selection,
      reuse(event) {
        const origin = event.currentTarget;
        const matches = props.project.messages.filter((saved) => saved.id === selection?.messageId);
        if (
          !mounted ||
          !selection ||
          active.value !== 'reply' ||
          shown.value !== 'reply' ||
          props.disabled ||
          reuseBlocked.value ||
          session !== historySession.value ||
          !historyRows.value.includes(row) ||
          props.project.id !== selection.projectId ||
          props.project.created_at !== selection.projectCreatedAt ||
          props.owner !== selection.owner ||
          matches.length !== 1 ||
          !savedUserMessage(matches[0], selection.projectId) ||
          !(origin instanceof HTMLElement) ||
          !origin.isConnected ||
          !history.value?.contains(origin) ||
          !origin.matches('button.review-reuse') ||
          origin.matches(':disabled') ||
          origin.closest('article')?.dataset.messageId !== selection.messageId
        )
          return;
        emit('reuse', { ...selection });
      },
    };
    return row;
  });
});
const trigger = (kind: Review) => (kind === 'reply' ? replyTrigger.value : receiptTrigger.value);
function composerControl() {
  return strip.value
    ?.closest('.agent-dock')
    ?.querySelector<HTMLElement>('[contenteditable="true"], textarea, input');
}
function focusTrigger(kind: Review) {
  const origin = kind === 'receipt' ? receiptOrigin : undefined;
  const controls = [origin, trigger(kind), replyTrigger.value, composerControl()];
  const control = controls.find(
    (candidate) =>
      candidate?.isConnected &&
      !candidate.matches(':disabled') &&
      !candidate.closest('[inert]') &&
      // A history-only tile disappears as soon as this selection is released.
      !(
        candidate === receiptTrigger.value &&
        selectedReceipt.value &&
        (!props.summary || props.running)
      ),
  );
  control?.focus({ preventScroll: true });
}
function releaseSelection() {
  if (active.value || shown.value) return;
  if (selectedReceipt.value) resetReceiptScroll = true;
  selectedReceipt.value = null;
  receiptOrigin = undefined;
}
async function openReceipt(eventId: string, origin: HTMLElement) {
  const selected = selectTurnReceipt(props.project, eventId);
  if (!mounted || !selected || props.disabled) return;
  reset();
  selectedReceipt.value = selected;
  receiptOrigin = origin;
  const token = sequence;
  await nextTick();
  if (!mounted || token !== sequence || !selectedReceipt.value) return;
  // History rows share this single reader, without morphing the unrelated latest tile.
  await change('receipt', false, false);
}
defineExpose({ openReceipt, closeForDraft: reset });
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
  const floor = Math.min(row.bottom, viewportTop + viewportHeight - 12);
  const width = Math.min(row.width, viewportWidth - 24);
  const readingFloor = Math.min(dock.top - 10, floor);
  const height = Math.min(360, readingFloor - ceiling);
  if (width <= 0 || height < 64) return null;
  const left = clamp(row.left, viewportLeft + 12, viewportLeft + viewportWidth - width - 12);
  return {
    ceiling,
    floor,
    frame: { left, top: readingFloor - height, width, height },
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
  releaseSelection();
}
function clip(rect: Box, base: Box) {
  const top = Math.max(0, rect.top - base.top);
  const right = Math.max(0, base.left + base.width - rect.left - rect.width);
  const bottom = Math.max(0, base.top + base.height - rect.top - rect.height);
  const left = Math.max(0, rect.left - base.left);
  return `inset(${top}px ${right}px ${bottom}px ${left}px round 13px)`;
}
async function change(next: Review | null, animate: boolean, restoreFocus: boolean) {
  if (selectedReceipt.value) animate = false;
  const previous = active.value ?? tileShown.value;
  const kind = next ?? previous;
  if (!kind || (next && !available(next))) return;
  const button = trigger(kind);
  const triggerFill = button?.querySelector<HTMLElement>('.review-tile-fill');
  const triggerCopy = button?.querySelector<HTMLElement>('.review-tile-copy');
  const triggerVisual = triggerFill ? box(triggerFill.getBoundingClientRect()) : null;
  const triggerCopyLeft = triggerCopy?.getBoundingClientRect().left;
  const triggerCopyOpacity = triggerCopy ? Number(getComputedStyle(triggerCopy).opacity) : 1;
  const sibling = trigger(kind === 'reply' ? 'receipt' : 'reply');
  const siblingOpacity = sibling ? Number(getComputedStyle(sibling).opacity) : 0;
  // Read the actual decorative shell BEFORE cancellation. Reversal starts from
  // these pixels, not from either endpoint. The content itself never scales.
  const visual =
    shown.value && surfaceFill.value ? box(surfaceFill.value.getBoundingClientRect()) : null;
  const headingVisual =
    shown.value && surfaceHeading.value ? box(surfaceHeading.value.getBoundingClientRect()) : null;
  const readerOpacity =
    shown.value && surfaceContent.value
      ? Number(getComputedStyle(surfaceContent.value).opacity)
      : 0;
  const measured = measure(kind);
  const token = ++sequence;
  cancelMotion();
  if (!measured) {
    active.value = null;
    shown.value = null;
    tileShown.value = null;
    if (restoreFocus && previous) focusTrigger(previous);
    releaseSelection();
    return;
  }
  const focusAtRequest = document.activeElement;
  active.value = next;
  tileShown.value = kind;
  if (next) shown.value = next;
  placement.value = measured;
  if (restoreFocus && previous) focusTrigger(previous);
  await nextTick();
  if (!mounted || token !== sequence) return;
  if (next === 'receipt' && resetReceiptScroll && receiptReader.value) {
    // A hidden reader has no scroll box in browsers; reset only after it is visible.
    receiptReader.value.scrollTop = 0;
    resetReceiptScroll = false;
  }
  if (next && document.activeElement === focusAtRequest)
    closeButton.value?.focus({ preventScroll: true });
  const fill = surfaceFill.value;
  const content = surfaceContent.value;
  const heading = surfaceHeading.value;
  if (!animate || media?.matches || !fill?.animate || !content || !heading) {
    shown.value = next;
    tileShown.value = next;
    releaseSelection();
    return;
  }
  const options = {
    duration: next ? 280 : 230,
    // On-screen geometry needs a readable middle, rather than finishing most
    // displacement in the first 50 ms of a strong entrance ease-out.
    easing: 'cubic-bezier(0.77, 0, 0.175, 1)',
    fill: 'both' as const,
  };
  const source = visual && visual.width > 0 ? visual : measured.tile;
  const target = next ? measured.frame : measured.tile;
  const tileMotion = fill.animate(
    [
      { transform: transform(source, measured.frame), opacity: 1 },
      { transform: transform(target, measured.frame), opacity: 1 },
    ],
    options,
  );
  const headingSource = headingVisual ?? source;
  const headingTarget = next ? measured.frame : measured.tile;
  const headingMotion = heading.animate(
    [
      {
        transform: `translate(${headingSource.left - measured.frame.left}px, ${headingSource.top - measured.frame.top}px)`,
      },
      {
        transform: `translate(${headingTarget.left - measured.frame.left}px, ${headingTarget.top - measured.frame.top}px)`,
      },
    ],
    options,
  );
  const readerMotion = content.animate(
    [
      { clipPath: clip(source, measured.frame), opacity: readerOpacity },
      { clipPath: clip(target, measured.frame), opacity: next ? 1 : 0 },
    ],
    options,
  );
  const siblingMotion = sibling?.animate(
    [{ opacity: siblingOpacity }, { opacity: next ? 0 : 1 }],
    options,
  );
  const triggerMotions =
    triggerFill && triggerCopy
      ? [
          triggerFill.animate(
            [
              { transform: transform(triggerVisual ?? measured.tile, measured.row), opacity: 1 },
              {
                transform: transform(next ? measured.row : measured.tile, measured.row),
                opacity: 1,
              },
            ],
            options,
          ),
          triggerCopy.animate(
            [
              {
                transform: `translateX(${(triggerCopyLeft ?? measured.tile.left + 12) - measured.row.left - 12}px)`,
                opacity: triggerCopyOpacity,
              },
              {
                transform: `translateX(${next ? 0 : measured.tile.left - measured.row.left}px)`,
                opacity: next ? 1 : 0,
              },
            ],
            options,
          ),
        ]
      : [];
  const owned = [
    ...triggerMotions,
    tileMotion,
    headingMotion,
    readerMotion,
    ...(siblingMotion ? [siblingMotion] : []),
  ];
  motions = owned;
  tileMotion.onfinish = () => {
    if (token !== sequence) return;
    shown.value = active.value;
    tileShown.value = active.value;
    releaseSelection();
    motions = [];
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
  const kind = active.value ?? tileShown.value;
  if (!kind) return;
  const measured = measure(kind);
  if (measured) {
    // ResizeObserver's initial notification or an unrelated scroll must not
    // silently cancel a healthy transition whose anchors have not moved.
    if (JSON.stringify(measured) === JSON.stringify(placement.value)) return;
    settleNow();
    placement.value = measured;
  } else {
    const restore = surface.value?.contains(document.activeElement);
    if (restore && available(kind)) focusTrigger(kind);
    reset();
  }
}
function schedulePosition(event?: Event) {
  if (!shown.value || frame) return;
  if (event?.target instanceof Node && layer.value?.contains(event.target)) return;
  frame = requestAnimationFrame(reposition);
}
function reset() {
  resetReceiptScroll = true;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  active.value = null;
  settleNow();
}
function reduceMotion(event: MediaQueryListEvent) {
  if (event.matches) settleNow();
}
watch(active, () => historySession.value++, { flush: 'sync' });
watch([() => props.project.id, () => props.project.created_at, () => props.owner], async () => {
  const focus = document.activeElement;
  const ownedFocus = selectedReceipt.value && surface.value?.contains(focus);
  reset();
  for (const reader of surface.value?.querySelectorAll<HTMLElement>('.review-reader') ?? [])
    reader.scrollTop = 0;
  const token = sequence;
  await nextTick();
  if (mounted && token === sequence && ownedFocus && document.activeElement === focus)
    composerControl()?.focus({ preventScroll: true });
});
// A persisted snapshot replaces the Project object even when its identity is
// unchanged. Only actual identity changes above own the reader's open/scroll state.
watch(
  () => props.project.messages,
  async () => {
    const reader = history.value;
    if (active.value !== 'reply' || !reader) return;
    // The default pre-flush sees the old DOM, before appended/replaced messages
    // grow it. Reading older content must not follow the latest saved reply.
    const top = reader.scrollTop;
    const following = reader.scrollHeight - reader.clientHeight - top <= 2;
    const token = sequence;
    await nextTick();
    if (!mounted || token !== sequence || active.value !== 'reply' || history.value !== reader)
      return;
    reader.scrollTop = following ? Math.max(0, reader.scrollHeight - reader.clientHeight) : top;
  },
  { deep: true },
);
watch(() => props.project.question?.id, reset, { flush: 'sync' });
watch(
  () => [props.disabled, hasReply.value, hasReceipt.value],
  () => {
    const kind = active.value ?? tileShown.value;
    if (props.disabled || (kind && !available(kind))) reset();
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
    aria-label="对话记录与最近变更"
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
            >对话记录 <ChevronUp :size="13" aria-hidden="true"
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
            ><span>{{ receiptTitle }}</span
            ><span class="review-tile-status">{{
              readingSummary ? turnSummaryStatus(readingSummary) : ''
            }}</span
            ><ChevronUp :size="13" aria-hidden="true"
          /></span>
          <span
            class="review-tile-preview"
            :class="{ 'has-warning': readingSummary?.history_warning }"
            :title="readingSummary?.history_warning"
            >{{
              readingSummary?.history_warning
                ? '对话未完整保存'
                : readingSummary
                  ? turnSummaryHeadline(readingSummary)
                  : (selectedRead?.unavailable ?? '')
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
        :aria-labelledby="selectedReceipt ? `${uid}-heading` : `${uid}-${shown ?? 'reply'}-trigger`"
        :aria-hidden="!active"
        :inert="active ? undefined : true"
      >
        <div ref="surfaceFill" class="review-surface-fill" aria-hidden="true"></div>
        <div ref="surfaceContent" class="review-surface-content">
          <header ref="surfaceHeading" class="review-surface-heading">
            <div>
              <strong :id="`${uid}-heading`">{{
                shown === 'receipt' ? receiptTitle : '对话记录'
              }}</strong>
              <span>{{
                shown === 'receipt'
                  ? readingSummary
                    ? turnSummaryStatus(readingSummary)
                    : '回执不可用'
                  : `${project.name} · ${project.messages.length} 条已保存的请求与回复`
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
            ref="history"
            class="review-reader review-history"
            tabindex="0"
            :aria-label="`${project.name}的对话记录`"
          >
            <p
              v-if="reuseBlocked"
              :id="`${uid}-reuse-blocker`"
              class="review-reuse-note"
              role="status"
            >
              {{ reuseBlocked }}
            </p>
            <p v-if="reuseError" class="review-reuse-note" role="alert">{{ reuseError }}</p>
            <article
              v-for="row in historyRows"
              :key="`${project.id}:${row.message.id}`"
              :class="row.message.role"
              :data-message-id="row.message.id"
            >
              <strong>{{ row.message.role === 'assistant' ? 'EvoGraph' : '你' }}</strong>
              <MessageContent :message="row.message" />
              <button
                v-if="row.selection"
                type="button"
                class="review-reuse"
                :disabled="disabled || Boolean(reuseBlocked)"
                :aria-describedby="reuseBlocked ? `${uid}-reuse-blocker` : undefined"
                @click="row.reuse"
              >
                用作新草稿
              </button>
            </article>
          </div>
          <div
            v-show="shown === 'receipt'"
            ref="receiptReader"
            class="review-reader review-receipt"
            tabindex="0"
            :aria-label="selectedReceipt ? '所选历史规划回执' : '最近已保存的规划变更'"
          >
            <p v-if="selectedReceipt" class="review-receipt-context">{{ receiptContext }}</p>
            <p v-if="selectedRead?.unavailable" role="status">{{ selectedRead.unavailable }}</p>
            <AgentTurnSummary
              v-if="readingSummary"
              :key="`${project.id}:${project.created_at}:${selectedReceipt?.eventId ?? 'latest'}:${readingSummary.turn_id}`"
              :summary="readingSummary"
              :project="project"
              :milestones="[...project.milestones, ...(project.source_milestones ?? [])]"
              :open="true"
              @locate="locate"
            />
          </div>
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
  border-radius: 14px;
  color: var(--ink, #233c4d);
  overflow: visible;
  pointer-events: auto;
  transform-origin: top left;
}
.review-surface-fill {
  position: absolute;
  inset: 0;
  border: 1px solid var(--line, #d8e1e7);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 10px 32px #223d551c;
  box-sizing: border-box;
  transform-origin: top left;
  pointer-events: none;
}
.review-surface-content {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  border-radius: inherit;
  overflow: hidden;
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
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 12px;
}
.review-surface-heading strong {
  font-size: 13px;
}
.review-surface-heading span {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
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
  overflow-wrap: anywhere;
}
.review-history :deep(.message-content:not(.markdown-content)) {
  white-space: pre-wrap;
}
.review-history article.user :deep(.message-content) {
  padding: 8px 10px;
  border-radius: 8px;
  background: #f4f7f9;
}
.review-reuse {
  margin-top: 7px;
  padding: 3px 8px;
  border: 1px solid var(--line, #d8e1e7);
  border-radius: 6px;
  background: #fff;
  color: var(--accent, #376d83);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.review-reuse:disabled {
  color: var(--text-secondary, #617383);
  opacity: 0.65;
  cursor: default;
}
.review-reuse-note {
  margin: 10px 0 0;
  color: var(--text-secondary, #617383);
  font-size: 11px;
}
.review-reuse-note[role='alert'] {
  color: var(--warning, #946627);
}
.review-receipt-context {
  margin: 10px 0 0;
  color: var(--text-secondary, #617383);
  font-size: 11px;
  overflow-wrap: anywhere;
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
.review-reuse:focus-visible,
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
