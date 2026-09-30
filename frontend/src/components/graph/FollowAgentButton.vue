<script setup lang="ts">
import { Focus } from 'lucide-vue-next';
defineProps<{ following?: boolean }>();
defineEmits<{ resume: [] }>();
</script>
<template>
  <button class="follow-agent" @click="$emit('resume')">
    <Focus :size="15" />{{ following ? '正在跟随 Agent' : '恢复跟随 Agent' }}
  </button>
</template>
