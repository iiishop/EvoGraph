<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { X } from 'lucide-vue-next';
defineProps<{ title: string; wide?: boolean }>();
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
    @cancel.prevent="emit('close')"
    @click="
      (event) => {
        if (event.target === dialog) emit('close');
      }
    "
  >
    <header class="modal-header">
      <h2>{{ title }}</h2>
      <button class="icon-button" aria-label="关闭" @click="emit('close')">
        <X :size="19" />
      </button>
    </header>
    <slot />
  </dialog>
</template>
