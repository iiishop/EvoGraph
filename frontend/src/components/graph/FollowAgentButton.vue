<script setup lang="ts">
import { Focus } from 'lucide-vue-next';
defineEmits<{ resume: [] }>();
</script>
<template>
  <button class="follow-agent" @click="$emit('resume')"><Focus :size="15" />恢复跟随 Agent</button>
</template>
