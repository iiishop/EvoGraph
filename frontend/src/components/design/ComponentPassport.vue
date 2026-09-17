<script setup lang="ts">
import { X } from 'lucide-vue-next';
import type { Diagram, Project } from '../../types';
import { architectureRole } from '../../lib/architectureRoles';
defineProps<{
  node: Diagram['nodes'][number];
  diagram: Diagram;
  project: Project;
  source: boolean;
}>();
defineEmits<{ close: []; select: [id: string] }>();
</script>
<template>
  <aside class="component-passport">
    <header>
      <span :style="{ color: architectureRole(node.role).color }">{{
        architectureRole(node.role).label
      }}</span
      ><button class="icon-button" aria-label="关闭组件详情" @click="$emit('close')">
        <X :size="18" />
      </button>
    </header>
    <h3>{{ node.label }}</h3>
    <p>{{ node.description }}</p>
    <h4>{{ source ? 'SRC 源码依据' : '引用源码' }}</h4>
    <code v-for="path in node.source_refs" :key="path" class="scope-path">{{ path }}</code>
    <p v-if="!node.source_refs?.length" class="muted">设计组件，尚未关联源码依据。</p>
    <h4>直接关系</h4>
    <button
      v-for="(edge, i) in diagram.edges.filter((e) => e.source === node.id || e.target === node.id)"
      :key="i"
      class="relationship-row"
      @click="$emit('select', edge.source === node.id ? edge.target : edge.source)"
    >
      <span
        >{{ edge.source === node.id ? '指向' : '来自' }}
        {{
          diagram.nodes.find((n) => n.id === (edge.source === node.id ? edge.target : edge.source))
            ?.label
        }}</span
      ><small>{{ edge.label }}</small>
    </button>
    <h4>关联交付</h4>
    <p
      v-for="m in project.milestones.filter((m) => m.architecture_components.includes(node.id))"
      :key="m.id"
    >
      {{ m.id }} {{ m.title }}
    </p>
    <p v-if="source" class="muted">
      静态源码观察不等同于功能验收。导入关系也不代表里程碑执行依赖。
    </p>
  </aside>
</template>
