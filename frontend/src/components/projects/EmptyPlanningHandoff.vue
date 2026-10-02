<script setup lang="ts">
import { ArrowDown, GitBranch } from 'lucide-vue-next';
defineProps<{ description: string; providerAvailable: boolean }>();
defineEmits<{ compose: [] }>();
</script>
<template>
  <section class="empty-state empty-planning-handoff" aria-labelledby="empty-planning-title">
    <span class="empty-icon"><GitBranch :size="30" /></span>
    <span class="project-entry-eyebrow">从一个目标开始</span>
    <h3 id="empty-planning-title">下一步，你想先做成什么？</h3>
    <p v-if="description" class="saved-project-description">
      <span>项目描述已保存</span>{{ description }}
    </p>
    <p v-else>项目已准备好，还没有里程碑。可以先说目标、使用场景或希望完成的一小步。</p>
    <p>
      在下方同一个输入框继续描述想法、补充约束或调整方向。<br />需要时再用 # 引用节点、@ 引用资料。
    </p>
    <button type="button" class="button secondary" @click="$emit('compose')">
      <ArrowDown :size="15" />写下第一步
    </button>
    <small>{{
      providerAvailable
        ? '由你点击发送后开始规划；不会自动发送项目描述'
        : '可以先写下草稿；发送前在设置中配置 Provider'
    }}</small>
  </section>
</template>
