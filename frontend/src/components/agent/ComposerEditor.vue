<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { Hash, FileText, X } from 'lucide-vue-next';
import type { SuggestionProps } from '@tiptap/suggestion';
import type { ComposerDocument, ComposerReferencePart, ReferenceItem } from '../../types';
import { createComposerEditor, matchingReferences } from '../../lib/composerEditor';
import {
  fromEditorDocument,
  toEditorDocument,
  referenceKey,
  referencePrefix,
  referenceKindLabels,
} from '../../lib/composerDocument';
const props = defineProps<{
  modelValue: ComposerDocument;
  projectId: string;
  catalog: ReferenceItem[] | null;
  explicitAttachmentIds: string[];
  disabled: boolean;
  placeholder: string;
  inputLabel: string;
  catalogError?: string;
  catalogWarnings?: string[];
}>();
const emit = defineEmits<{
  'update:modelValue': [document: ComposerDocument];
  submit: [];
  error: [message: string];
  requestCatalog: [];
}>();
const surface = ref<HTMLElement>();
const list = ref<HTMLElement>();
const detail = ref<HTMLElement>();
const controller = shallowRef<ReturnType<typeof createComposerEditor>>();
const suggestion = shallowRef<{ trigger: '#' | '@'; props: SuggestionProps<ReferenceItem> } | null>(
  null,
);
const activeIndex = ref(0);
const popupStyle = ref({ left: '12px', top: '12px', width: '340px' });
const inspected = shallowRef<ComposerReferencePart | null>(null);
const inspectStyle = ref({ left: '12px', top: '12px' });
let disposed = false;
const items = computed(() =>
  suggestion.value
    ? matchingReferences(props.catalog, suggestion.value.trigger, suggestion.value.props.query)
    : [],
);
const currentReference = computed(() =>
  inspected.value
    ? props.catalog?.find((item) => referenceKey(item) === referenceKey(inspected.value!))
    : undefined,
);
const invalidReference = computed(() =>
  Boolean(inspected.value && props.catalog !== null && !currentReference.value),
);
function positionPopup() {
  const rect = suggestion.value?.props.clientRect?.();
  if (!rect) return;
  const width = Math.min(380, window.innerWidth - 24);
  const height = Math.min(310, window.innerHeight - 24);
  popupStyle.value = {
    left: `${Math.min(Math.max(12, rect.left), window.innerWidth - width - 12)}px`,
    top: `${rect.bottom + height + 10 <= window.innerHeight ? rect.bottom + 7 : Math.max(12, rect.top - height - 7)}px`,
    width: `${width}px`,
  };
}
function closeSuggestions() {
  controller.value?.closeSuggestions();
}
function closeInspection() {
  inspected.value = null;
  controller.value?.editor.commands.focus();
}
function outsideInspection(event: Event) {
  if (!inspected.value || !(event.target instanceof Element)) return;
  if (!detail.value?.contains(event.target) && !event.target.closest('.composer-reference'))
    inspected.value = null;
}
function choose(item: ReferenceItem) {
  const active = suggestion.value;
  if (active && !props.disabled) active.props.command(item);
}
function syncAria() {
  const dom = controller.value?.editor.view.dom;
  if (!dom) return;
  dom.setAttribute('aria-expanded', String(Boolean(suggestion.value)));
  if (suggestion.value) {
    dom.setAttribute('aria-controls', 'composer-reference-options');
    if (items.value[activeIndex.value])
      dom.setAttribute('aria-activedescendant', `composer-reference-${activeIndex.value}`);
    else dom.removeAttribute('aria-activedescendant');
  } else {
    dom.removeAttribute('aria-controls');
    dom.removeAttribute('aria-activedescendant');
  }
}
watch([items, activeIndex], async () => {
  if (activeIndex.value >= items.value.length) activeIndex.value = 0;
  syncAria();
  await nextTick();
  list.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
});
watch(
  () => props.catalog,
  () => {
    controller.value?.refreshReferences();
    syncAria();
  },
);
watch(
  () => props.modelValue,
  (document) => {
    const current = controller.value?.editor;
    if (
      !current ||
      JSON.stringify(
        fromEditorDocument(current.getJSON() as ReturnType<typeof toEditorDocument>),
      ) === JSON.stringify(document)
    )
      return;
    // Only an external draft restore/clear replaces content. A normal editor
    // transaction echoes through v-model without resetting selection or history.
    controller.value?.replaceDocument(document);
    inspected.value = null;
    closeSuggestions();
  },
  { deep: true },
);
watch(
  () => props.disabled,
  (value) => {
    controller.value?.editor.setEditable(!value, false);
    if (value) {
      closeSuggestions();
      inspected.value = null;
    }
  },
);
watch(
  () => [props.inputLabel, props.placeholder],
  () => {
    const dom = controller.value?.editor.view.dom;
    dom?.setAttribute('aria-label', props.inputLabel);
    dom?.setAttribute('data-placeholder', props.placeholder);
  },
);
onMounted(() => {
  if (!surface.value) return;
  controller.value = createComposerEditor({
    element: surface.value,
    document: props.modelValue,
    projectId: () => props.projectId,
    catalog: () => props.catalog,
    attachments: () => props.explicitAttachmentIds,
    editable: !props.disabled,
    ariaLabel: props.inputLabel,
    placeholder: props.placeholder,
    onChange: (document) => {
      if (!disposed) {
        inspected.value = null;
        emit('update:modelValue', document);
        emit('error', '');
      }
    },
    onSubmit: () => {
      if (!disposed) emit('submit');
    },
    onError: (message) => {
      if (!disposed) emit('error', message);
    },
    onRequestCatalog: () => emit('requestCatalog'),
    onSuggestion: (trigger, state) => {
      if (disposed) return;
      const changed =
        suggestion.value?.props.query !== state.query || suggestion.value?.trigger !== trigger;
      suggestion.value = { trigger, props: state };
      if (changed) activeIndex.value = 0;
      inspected.value = null;
      positionPopup();
      syncAria();
    },
    onSuggestionKey: ({ event }) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeSuggestions();
        return true;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (items.value.length)
          activeIndex.value =
            (activeIndex.value + (event.key === 'ArrowDown' ? 1 : -1) + items.value.length) %
            items.value.length;
        return true;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        const item = items.value[activeIndex.value];
        if (item) choose(item);
        return true;
      }
      return false;
    },
    onCloseSuggestion: () => {
      suggestion.value = null;
      syncAria();
    },
    onEscape: () => {
      if (!inspected.value) return false;
      closeInspection();
      return true;
    },
    onInspect: (reference, rect) => {
      closeSuggestions();
      inspected.value = reference;
      inspectStyle.value = {
        left: `${Math.min(Math.max(12, rect.left), window.innerWidth - 340)}px`,
        top: `${Math.max(12, rect.top - 170)}px`,
      };
    },
  });
  window.addEventListener('resize', positionPopup);
  document.addEventListener('pointerdown', outsideInspection);
});
onBeforeUnmount(() => {
  disposed = true;
  window.removeEventListener('resize', positionPopup);
  document.removeEventListener('pointerdown', outsideInspection);
  controller.value?.editor.destroy();
});
defineExpose({ focus: () => controller.value?.editor.commands.focus(), closeSuggestions });
</script>
<template>
  <div class="composer-editor"><div ref="surface" /></div>
  <Teleport to="body">
    <div
      v-if="suggestion"
      class="composer-suggestions"
      :style="popupStyle"
      @mousedown.prevent
      @keydown.stop
    >
      <header>
        <strong>{{ suggestion.trigger === '#' ? '项目对象' : '项目资料' }}</strong
        ><small>{{ items.length }} 个匹配</small>
      </header>
      <div
        id="composer-reference-options"
        ref="list"
        role="listbox"
        aria-label="选择行内引用"
        class="composer-suggestion-list"
      >
        <button
          v-for="(item, index) in items"
          :id="`composer-reference-${index}`"
          :key="referenceKey(item)"
          type="button"
          role="option"
          :aria-selected="index === activeIndex"
          :class="{ active: index === activeIndex }"
          @mouseenter="activeIndex = index"
          @click="choose(item)"
        >
          <FileText v-if="referencePrefix(item.kind) === '@'" :size="17" aria-hidden="true" /><Hash
            v-else
            :size="17"
            aria-hidden="true"
          />
          <span
            ><strong>{{ item.label }}</strong
            ><small>{{ referenceKindLabels[item.kind] }} · {{ item.path || item.id }}</small></span
          >
        </button>
        <p v-if="!catalog">
          {{ catalogError || '正在读取引用…'
          }}<button
            v-if="catalogError"
            type="button"
            class="text-button"
            @click="$emit('requestCatalog')"
          >
            重试
          </button>
        </p>
        <p v-else-if="!items.length">没有匹配项，继续输入或按 Esc 关闭</p>
      </div>
      <p v-for="warning in catalogWarnings" :key="warning" class="reference-catalog-warning">
        {{ warning }}
      </p>
      <footer>↑ ↓ 选择 · Enter 插入 <span>Esc 关闭</span></footer>
    </div>
    <div
      v-if="inspected"
      ref="detail"
      class="composer-reference-detail"
      :style="inspectStyle"
      role="dialog"
      aria-label="行内引用详情"
      @keydown.esc.stop.prevent="closeInspection"
    >
      <header>
        <strong>{{ currentReference?.label || inspected.label }}</strong
        ><button
          type="button"
          class="icon-button"
          aria-label="关闭引用详情"
          @click="closeInspection"
        >
          <X :size="15" />
        </button>
      </header>
      <p>{{ referenceKindLabels[inspected.kind] }}</p>
      <code>{{ currentReference?.path || inspected.id }}</code>
      <p v-if="invalidReference" class="reference-invalid-note">
        引用已失效。请在输入框中删除或重新选择，草稿仍然保留。
      </p>
      <small v-else>这是对话上下文，Agent 仍会判断整体需要调整的内容。</small>
    </div>
  </Teleport>
</template>
