<script setup lang="ts">
import { onMounted, onUnmounted, ref, useId } from 'vue';
import { X } from 'lucide-vue-next';
const props = defineProps<{ title: string; wide?: boolean; closeDisabled?: boolean }>();
const titleId = `modal-title-${useId()}`;
function close() {
  if (!props.closeDisabled) emit('close');
}
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
let previous: Element | null = null;
onMounted(() => {
  previous = document.activeElement;
  dialog.value?.showModal();
});
onUnmounted(() => {
  if (previous instanceof HTMLElement) previous.focus();
});
</script>
<template>
  <dialog
    ref="dialog"
    class="modal"
    :class="{ wide }"
    :aria-labelledby="titleId"
    @cancel.prevent="close"
    @click="
      (event) => {
        if (event.target === dialog) close();
      }
    "
  >
    <header class="modal-header">
      <h2 :id="titleId">{{ title }}</h2>
      <button class="icon-button" aria-label="关闭" :disabled="closeDisabled" @click="close">
        <X :size="19" />
      </button>
    </header>
    <slot />
  </dialog>
</template>
