<script setup lang="ts">
import { X, Check } from 'lucide-vue-next';
import { useNotifications } from '../../composables/useNotifications';
const { items, dismiss } = useNotifications();
</script>
<template>
  <div
    class="notification-stack"
    aria-live="polite"
    aria-relevant="additions"
    aria-label="更新通知"
  >
    <TransitionGroup name="notification"
      ><div v-for="item in items" :key="item.id" class="update-notification">
        <Check :size="16" aria-hidden="true" />
        <p>{{ item.message }}</p>
        <button class="icon-button" aria-label="关闭更新通知" @click="dismiss(item.id)">
          <X :size="14" />
        </button></div
    ></TransitionGroup>
  </div>
</template>
