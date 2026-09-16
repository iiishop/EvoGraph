<script setup lang="ts">
import { ref, watch } from 'vue';
import type { Obligation } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
const props = defineProps<{
  obligation: Obligation;
  projectId: string;
  milestoneId: string;
}>();
const { state, perform } = useWorkspace();
const note = ref(props.obligation.note),
  editing = ref(false);
watch(
  () => props.obligation.note,
  (value) => {
    note.value = value;
  },
);
async function save(resolved: boolean) {
  const result = await perform(
    'milestone.obligation',
    {
      project_id: props.projectId,
      milestone_id: props.milestoneId,
      obligation_id: props.obligation.id,
      resolved,
      note: note.value,
    },
    '调查记录已保存',
  );
  if (result) editing.value = false;
}
</script>
<template>
  <div class="obligation">
    <button class="obligation-toggle" @click="editing = !editing">
      <span class="check-box" :class="{ checked: obligation.resolved }">{{
        obligation.resolved ? '✓' : ''
      }}</span
      ><span>{{ obligation.label }}</span
      ><small>{{ obligation.resolved ? '已记录' : '待调查' }}</small>
    </button>
    <div v-if="editing" class="obligation-edit">
      <textarea
        v-model="note"
        rows="3"
        placeholder="记录检查了什么、发现了什么，以及相关文件或证据…"
      /><button
        class="button small primary"
        :disabled="state.busy || !note.trim()"
        @click="save(true)"
      >
        记录调查结论</button
      ><button
        v-if="obligation.resolved"
        class="text-button"
        :disabled="state.busy"
        @click="save(false)"
      >
        重新打开
      </button>
    </div>
  </div>
</template>
