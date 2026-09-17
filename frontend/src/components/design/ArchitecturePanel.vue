<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Search, Network, Maximize2 } from 'lucide-vue-next';
import type { Project } from '../../types';
import DiagramView from './DiagramView.vue';
import ComponentPassport from './ComponentPassport.vue';
import UmlView from './UmlView.vue';
import { architectureRole } from '../../lib/architectureRoles';
const props = defineProps<{ project: Project }>();
const view = ref('current'),
  query = ref(''),
  focusedId = ref(''),
  relation = ref('all');
const graph = ref<InstanceType<typeof DiagramView>>();
watch(
  () => props.project.architectures.length,
  () => {
    view.value = props.project.architectures.length ? 'current' : 'source';
  },
  { immediate: true },
);
const architecture = computed(() =>
  view.value === 'current'
    ? props.project.architectures.at(-1)
    : view.value === 'source' || view.value === 'class'
      ? null
      : props.project.architectures[Number(view.value)],
);
const classDiagram = computed(() =>
  props.project.uml_diagrams?.filter((d) => d.kind === 'class').at(-1),
);
const historyOpen = ref(false);
const diagram = computed(() =>
  view.value === 'source' ? props.project.source_diagram : architecture.value?.diagram,
);
const selected = computed(() => diagram.value?.nodes.find((n) => n.id === focusedId.value));
const roles = computed(() => [
  ...new Set(diagram.value?.nodes.map((n) => n.role ?? 'backend') ?? []),
]);
const sources = computed(() =>
  props.project.research.filter((r) => architecture.value?.research_ids?.includes(r.id)),
);
watch(view, () => {
  focusedId.value = '';
  relation.value = 'all';
  query.value = '';
});
defineExpose({ fit: () => graph.value?.fit(), reset: () => graph.value?.reset() });
</script>
<template>
  <section class="architecture-workbench">
    <header class="architecture-heading">
      <div>
        <h2><Network :size="21" />系统架构</h2>
        <p>
          {{
            view === 'source'
              ? '从代码出发，理解现有系统边界'
              : '持续维护组件边界、技术选型与设计依据'
          }}
        </p>
      </div>
      <button
        class="button secondary"
        :aria-expanded="historyOpen"
        @click="historyOpen = !historyOpen"
      >
        版本记录 {{ project.architectures.length }}
      </button>
    </header>
    <nav class="architecture-tabs" aria-label="架构视图">
      <button
        v-for="item in [
          { id: 'current', label: '当前架构' },
          { id: 'source', label: 'SRC 源码现状' },
          { id: 'class', label: 'UML 类图' },
        ]"
        :key="item.id"
        :aria-pressed="view === item.id"
        @click="view = item.id"
      >
        {{ item.label }}
      </button>
    </nav>
    <div v-if="historyOpen" class="version-track">
      <button
        v-for="(a, i) in project.architectures"
        :key="a.number"
        :aria-pressed="
          view === String(i) || (view === 'current' && i === project.architectures.length - 1)
        "
        @click="view = String(i)"
      >
        <strong>A{{ a.number }}</strong
        ><span>{{ a.summary }}</span
        ><small>{{ i === project.architectures.length - 1 ? '当前版本' : '历史快照' }}</small>
      </button>
      <p v-if="!project.architectures.length">尚无架构版本</p>
    </div>
    <template v-if="view === 'class'"
      ><UmlView v-if="classDiagram" :diagram="classDiagram" :project-id="project.id" />
      <div v-else class="empty-state">
        <h3>持续维护一张代码架构总览</h3>
        <p>在下方要求 Agent 阅读源码并绘制类图。按职责分包，保留关键签名、数据契约与中文说明。</p>
      </div></template
    >
    <template v-else-if="diagram">
      <p v-if="view !== 'source' && view !== 'current'" class="history-banner">
        正在查看 A{{ architecture?.number }} 快照。<button
          class="text-button"
          @click="view = 'current'"
        >
          回到当前架构
        </button>
      </p>
      <div class="architecture-tools">
        <label class="component-search"
          ><Search :size="16" /><input
            v-model="query"
            aria-label="搜索架构组件"
            placeholder="搜索组件与职责"
        /></label>
        <select v-model="focusedId" aria-label="选择架构组件">
          <option value="">全部组件</option>
          <option v-for="node in diagram.nodes" :key="node.id" :value="node.id">
            {{ node.label }}
          </option>
        </select>
        <select v-model="relation" :disabled="!focusedId" aria-label="关系范围">
          <option value="all">全部关系</option>
          <option value="upstream">上游关系</option>
          <option value="downstream">下游关系</option>
        </select>
        <button class="icon-button" aria-label="适应架构画布" @click="graph?.fit()">
          <Maximize2 :size="17" />
        </button>
      </div>
      <div class="architecture-legend">
        <span v-for="role in roles" :key="role"
          ><i :style="{ background: architectureRole(role).color }" />{{
            architectureRole(role).label
          }}</span
        ><small>{{ diagram.nodes.length }} 个组件 / {{ diagram.edges.length }} 条关系</small>
      </div>
      <div class="architecture-stage">
        <DiagramView
          :key="view"
          ref="graph"
          :diagram="diagram"
          :source="view === 'source'"
          :query="query"
          :focused-id="focusedId"
          :relation="relation"
          @select="focusedId = $event"
        />
        <ComponentPassport
          v-if="selected"
          :node="selected"
          :diagram="diagram"
          :project="project"
          :source="view === 'source'"
          @close="focusedId = ''"
          @select="focusedId = $event"
        />
      </div>
      <p v-if="view === 'source'" class="source-summary">{{ project.source_summary }}</p>
      <details v-if="architecture" class="architecture-foundation">
        <summary>
          技术选型、决策与来源
          <span
            >{{ architecture.technologies.length }} 项选型 / {{ sources.length }} 条网页依据</span
          >
        </summary>
        <p>{{ architecture.summary }}</p>
        <div class="technology-grid">
          <article v-for="tech in architecture.technologies" :key="tech.area">
            <small>{{ tech.area }}</small>
            <h3>{{ tech.choice }}</h3>
            <p>{{ tech.rationale }}</p>
          </article>
        </div>
        <ul class="decision-list">
          <li v-for="decision in architecture.decisions" :key="decision">{{ decision }}</li>
        </ul>
        <a
          v-for="source in sources"
          :key="source.id"
          :href="source.url"
          target="_blank"
          rel="noopener noreferrer"
          class="research-link"
          >{{ source.title }}<small>{{ new Date(source.created_at).toLocaleString() }}</small></a
        >
        <p v-if="!sources.length" class="muted">
          尚无网页依据；可在下方要求 Agent 搜索并补充选型依据。
        </p>
      </details>
    </template>
    <div v-else class="empty-state">
      <Network :size="36" />
      <h3>{{ view === 'source' ? '读取基线，自动建立源码视图' : '让架构先于实现' }}</h3>
      <p>
        {{
          view === 'source'
            ? '在顶部读取基线后，将显示带 SRC 标识的目录组件和静态导入关系。'
            : '在下方说明系统边界与技术约束，Agent 将维护架构与选型依据。'
        }}
      </p>
    </div>
  </section>
</template>
