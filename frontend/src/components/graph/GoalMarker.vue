<script setup lang="ts">
import { computed } from 'vue';
import { Flag } from 'lucide-vue-next';
import type { Project } from '../../types';
import { acceptanceSummary } from '../../lib/acceptance';
const props = defineProps<{ project: Project }>();
const acceptance = computed(() => acceptanceSummary(props.project));
const statement = computed(
  () => props.project.targets.at(-1)?.statement || props.project.description || props.project.name,
);
</script>
<template>
  <div
    class="goal-marker"
    :class="{ achieved: acceptance.achieved, 'needs-target': !acceptance.total }"
    aria-label="最终目标与步骤验收"
  >
    <Flag :size="15" aria-hidden="true" />
    <div class="goal-copy">
      <small>
        最终目标 ·
        {{ !acceptance.total ? '待定义标准' : acceptance.achieved ? '已达成' : '待达成' }}
        · 非 PR 节点
      </small>
      <strong :title="statement">{{ statement }}</strong>
      <p v-if="!acceptance.total" class="goal-guidance">尚未定义最终目标标准</p>
    </div>
    <div class="goal-counts">
      <span title="当前目标版本中已有有效验收证据的标准数 / 所需标准数">
        最终目标 <b>{{ acceptance.passed }}/{{ acceptance.total }}</b>
      </span>
      <span title="当前里程碑中未计入最终目标的标准，仍须通过所在里程碑的验收">
        步骤专属 <b>{{ acceptance.stepOnlyTotal }} 项</b>
      </span>
    </div>
  </div>
</template>
