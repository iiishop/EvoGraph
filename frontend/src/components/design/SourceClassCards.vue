<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import type { SourceClassModel, SourceClassMember, SourceRelation } from '../../types';
import { sourceLocationLabel, sourceRelationLabels } from '../../lib/sourceClassModel';
type Selection = { classId: string; memberId: string };
const props = defineProps<{ model: SourceClassModel; selection?: Selection }>();
const emit = defineEmits<{ source: []; selection: [value: Selection] }>();
const selectedClassId = ref(''),
  selectedMemberId = ref('');
const canvas = ref<HTMLElement>();
const paths = ref<{ id: string; path: string; x: number; y: number; relation: SourceRelation }[]>(
  [],
);
const canvasSize = ref({ width: 1, height: 1 });
const markerId = `source-arrow-${useId().replace(/[^a-z0-9_-]/gi, '')}`;
let observer: ResizeObserver | undefined;
let frame = 0;
let generation = 0;
let disposed = false;
const selectedClass = computed(() =>
  props.model.classes.find((item) => item.id === selectedClassId.value),
);
const selectedMember = computed(() =>
  selectedClass.value?.members.find((item) => item.id === selectedMemberId.value),
);
const activeNodeIds = computed(
  () =>
    new Set(
      selectedClass.value ? [selectedClass.value.id, ...selectedClass.value.package_ids] : [],
    ),
);
const relations = computed(() =>
  props.model.relations.filter(
    (item) =>
      !selectedClass.value ||
      activeNodeIds.value.has(item.source) ||
      activeNodeIds.value.has(item.target),
  ),
);
const groups = computed(() =>
  props.model.packages.map((item) => ({
    ...item,
    classes: props.model.classes.filter((node) => node.package_ids[0] === item.id),
    sharedClasses: props.model.classes.filter(
      (node) => node.package_ids[0] !== item.id && node.package_ids.includes(item.id),
    ),
  })),
);
const nodeNames = computed(
  () =>
    new Map([
      ...props.model.classes.map((item) => [item.id, item.name] as const),
      ...props.model.packages.map((item) => [item.id, `package ${item.label}`] as const),
      ...props.model.boundaries.map((item) => [item.id, item.label] as const),
    ]),
);
const visibility = (member: SourceClassMember) =>
  ({ public: '+ 公开', protected: '# 保护', private: '− 私有', unspecified: '~ 未标注' })[
    member.visibility
  ];
const headerParts = (value: string) => value.split(/(?=[(<])/u);
const boundaryKind = { import: '导入引用', base: '基类 / 接口引用', architecture: '架构邻居' };
const boundaryReason: Record<string, string> = {
  unresolved: '未解析到所选文件内的唯一声明',
  ambiguous: '同名声明存在歧义，未选择任一实现',
  external_import: '范围外导入，未展开实现',
  architecture_neighbor: '所选架构的邻接模块，未展开实现',
};
const relationNote = (relation: SourceRelation) =>
  relation.kind === 'architecture'
    ? `${relation.origin === 'design' ? 'DESIGN 设计版本' : 'SRC 架构'}的模块级连线，不代表类调用`
    : relation.kind === 'import'
      ? '模块级静态导入，不代表类或方法调用'
      : relation.resolution === 'selected'
        ? '同文件唯一声明的语法名称匹配；不证明运行时绑定'
        : '只保留源码中的基类 / 接口写法，未解析其实现';
function select(classId: string, memberId = '') {
  selectedClassId.value = classId;
  selectedMemberId.value = memberId;
  emit('selection', { classId, memberId });
}
watch(
  () => props.selection,
  (value) => {
    if (!value) return;
    const item = props.model.classes.find((item) => item.id === value.classId);
    selectedClassId.value = item?.id ?? '';
    selectedMemberId.value = item?.members.find((member) => member.id === value.memberId)?.id ?? '';
  },
  { immediate: true, flush: 'sync' },
);
function clearSelection() {
  select('');
}
function measure() {
  if (disposed) return;
  const current = generation;
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    if (disposed || current !== generation || !canvas.value) return;
    frame = 0;
    const bounds = canvas.value.getBoundingClientRect();
    const nodes = new Map<string, DOMRect>();
    for (const element of canvas.value.querySelectorAll<HTMLElement>('[data-semantic-id]'))
      nodes.set(element.dataset.semanticId!, element.getBoundingClientRect());
    canvasSize.value = {
      // Flow bounds exclude this absolute SVG and prevent measurement feedback.
      width: Math.max(1, Math.ceil(bounds.width)),
      height: Math.max(1, Math.ceil(bounds.height)),
    };
    paths.value = relations.value.flatMap((relation, index) => {
      const from = nodes.get(relation.source),
        to = nodes.get(relation.target);
      if (!from || !to) return [];
      const sameColumn = Math.abs(from.left - to.left) < 80 || from.right > to.left;
      const x1 = from.right - bounds.left,
        y1 = from.top - bounds.top + Math.min(from.height / 2, 44);
      const x2 = (sameColumn ? to.right : to.left) - bounds.left,
        y2 = to.top - bounds.top + Math.min(to.height / 2, 44);
      const bend = sameColumn ? Math.max(x1, x2) + 24 + (index % 3) * 8 : (x1 + x2) / 2;
      return [
        {
          id: relation.id,
          relation,
          path: `M${x1} ${y1} C${bend} ${y1},${bend} ${y2},${x2} ${y2}`,
          x: bend,
          y: (y1 + y2) / 2 - 7,
        },
      ];
    });
  });
}
async function observe(reveal = false) {
  const current = ++generation;
  cancelAnimationFrame(frame);
  await nextTick();
  if (disposed || current !== generation) return;
  observer?.disconnect();
  if (canvas.value) {
    observer?.observe(canvas.value);
    for (const item of canvas.value.querySelectorAll('[data-semantic-id]')) observer?.observe(item);
  }
  measure();
  if (reveal && selectedClassId.value) {
    const target = selectedMemberId.value
      ? [...(canvas.value?.querySelectorAll<HTMLElement>('[data-source-member]') ?? [])].find(
          (item) => item.dataset.sourceMember === selectedMemberId.value,
        )
      : [...(canvas.value?.querySelectorAll<HTMLElement>('[data-semantic-id]') ?? [])].find(
          (item) => item.dataset.semanticId === selectedClassId.value,
        );
    target?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
  }
}
watch(
  () => props.model,
  () => {
    clearSelection();
    paths.value = [];
    void observe();
  },
  { flush: 'sync' },
);
watch(relations, measure);
onMounted(() => {
  observer = new ResizeObserver(measure);
  void observe(true);
});
onBeforeUnmount(() => {
  disposed = true;
  generation++;
  observer?.disconnect();
  cancelAnimationFrame(frame);
});
</script>
<template>
  <section class="source-class-view" aria-label="源码语义类结构">
    <div class="source-class-summary">
      <span>SRC · {{ model.classes.length }} 个声明 · {{ model.files.length }} 个文件</span>
      <span>基线 {{ model.baseline_id }} · 只读局部提取</span>
    </div>
    <div class="source-class-layout">
      <div class="source-class-main">
        <div
          class="source-class-scroll"
          tabindex="0"
          aria-label="类声明与边界画布，可滚动"
          @scroll.passive="measure"
        >
          <div
            ref="canvas"
            class="source-class-canvas"
            :class="{ 'has-boundaries': model.boundaries.length }"
          >
            <svg
              class="source-class-edges"
              :width="canvasSize.width"
              :height="canvasSize.height"
              aria-hidden="true"
            >
              <defs>
                <marker
                  :id="markerId"
                  markerWidth="9"
                  markerHeight="9"
                  refX="8"
                  refY="4.5"
                  orient="auto"
                >
                  <path d="M1 1 L8 4.5 L1 8" />
                </marker>
                <marker
                  :id="`${markerId}-type`"
                  markerWidth="10"
                  markerHeight="10"
                  refX="9"
                  refY="5"
                  orient="auto"
                >
                  <path class="source-type-arrow" d="M1 1 L9 5 L1 9 Z" />
                </marker>
              </defs>
              <g
                v-for="edge in paths"
                :key="edge.id"
                :class="['source-class-edge', edge.relation.kind]"
              >
                <path
                  :d="edge.path"
                  :marker-end="`url(#${markerId}${['extends', 'implements'].includes(edge.relation.kind) ? '-type' : ''})`"
                />
                <text :x="edge.x" :y="edge.y" text-anchor="middle">
                  {{ sourceRelationLabels[edge.relation.kind] }}
                </text>
              </g>
            </svg>
            <div class="source-class-packages">
              <section
                v-for="group in groups"
                :key="group.id"
                class="source-class-package"
                :data-semantic-id="group.id"
              >
                <div class="source-package-heading">
                  <code>package {{ group.label }}</code
                  ><small>局部模块范围</small>
                </div>
                <p v-if="!group.classes.length" class="source-empty-members">
                  {{
                    group.sharedClasses.length
                      ? `共享源码声明已在前一模块显示：${group.sharedClasses.map((item) => item.name).join('、')}`
                      : '此模块没有独立类声明，仍保留模块级关系'
                  }}
                </p>
                <article
                  v-for="item in group.classes"
                  :key="item.id"
                  class="source-class-card"
                  :class="{ selected: selectedClassId === item.id }"
                  :data-semantic-id="item.id"
                >
                  <button
                    class="source-class-heading"
                    :aria-pressed="selectedClassId === item.id && !selectedMemberId"
                    @click="select(item.id)"
                  >
                    <strong
                      ><template
                        v-for="(part, index) in headerParts(item.declaration ?? item.name)"
                        :key="index"
                        ><wbr v-if="index" />{{ part }}</template
                      ></strong
                    ><small>SRC {{ item.kind }} · {{ item.language }} · 静态声明</small>
                  </button>
                  <section
                    v-for="kind in ['field', 'method'] as const"
                    :key="kind"
                    class="source-member-group"
                  >
                    <h4>{{ kind === 'field' ? 'ATTRIBUTES / 属性' : 'METHODS / 方法' }}</h4>
                    <p
                      v-if="!item.members.some((member) => member.kind === kind)"
                      class="source-empty-members"
                    >
                      未提取到{{ kind === 'field' ? '属性' : '方法' }}声明
                    </p>
                    <button
                      v-for="member in item.members.filter((member) => member.kind === kind)"
                      :key="member.id"
                      class="source-class-member"
                      :data-source-member="member.id"
                      :class="{ selected: selectedMemberId === member.id }"
                      :aria-pressed="selectedMemberId === member.id"
                      :aria-label="`${item.name} · ${member.text}`"
                      @click="select(item.id, member.id)"
                    >
                      <code>{{ member.text }}</code
                      ><span aria-hidden="true">↗</span>
                    </button>
                  </section>
                  <div class="source-class-location">{{ sourceLocationLabel(item.location) }}</div>
                </article>
              </section>
            </div>
            <aside
              v-if="model.boundaries.length"
              class="source-class-boundaries"
              aria-label="范围外边界"
            >
              <div
                v-for="item in model.boundaries"
                :key="item.id"
                class="source-class-boundary"
                :data-semantic-id="item.id"
              >
                <span class="source-boundary-icon" aria-hidden="true">{{
                  item.kind === 'import' ? '↪' : '◇'
                }}</span>
                <div>
                  <strong>{{ item.label }}</strong
                  ><small
                    >{{ item.origin === 'design' ? 'DESIGN' : 'SRC' }} ·
                    {{ boundaryKind[item.kind] }} · 范围外</small
                  >
                  <p>{{ boundaryReason[item.reason ?? ''] ?? '仅保留引用，不读取或展开实现' }}</p>
                </div>
              </div>
            </aside>
          </div>
        </div>
        <div class="source-relation-legend" aria-label="关系图例">
          <span>—▷ 继承声明</span><span>╌▷ 实现声明</span><span>╌→ 包级导入 / 架构关系</span>
        </div>
        <section class="source-class-relations" aria-label="可核验关系">
          <div class="source-relation-heading">
            <h4>
              {{ selectedClass ? `${selectedClass.name} 与所属模块的关系` : '范围内的显式关系' }} ·
              {{ relations.length }}
            </h4>
            <button v-if="selectedClass" class="text-button" @click="clearSelection">
              查看全部关系
            </button>
          </div>
          <p v-if="!relations.length" class="source-empty-members">
            此范围未提取到显式关系；不会从字段类型推断关联或调用
          </p>
          <ol v-else>
            <li v-for="relation in relations" :key="relation.id">
              <div class="source-relation-pair">
                <code>{{ nodeNames.get(relation.source) }}</code
                ><span>→</span><code>{{ nodeNames.get(relation.target) }}</code>
              </div>
              <small
                >{{ relation.origin === 'design' ? 'DESIGN' : 'SRC' }} ·
                {{ sourceRelationLabels[relation.kind]
                }}{{ relation.label ? ` · ${relation.label}` : '' }}</small
              >
              <p>
                {{ relationNote(relation)
                }}<template v-if="relation.location">
                  · {{ sourceLocationLabel(relation.location) }}</template
                >
              </p>
            </li>
          </ol>
        </section>
      </div>
      <aside class="source-member-detail" aria-label="类与成员详情">
        <template v-if="selectedClass">
          <div class="source-detail-heading">
            <small
              >已选择 ·
              {{
                selectedMember ? (selectedMember.kind === 'method' ? '类方法' : '类属性') : '类声明'
              }}</small
            ><button class="icon-button" aria-label="清除成员选择" @click="clearSelection">
              ×
            </button>
          </div>
          <h3>{{ selectedMember?.name ?? selectedClass.name }}</h3>
          <p v-if="selectedMember" class="source-detail-class">{{ selectedClass.name }}</p>
          <template v-if="selectedMember">
            <h4>完整声明</h4>
            <pre>{{ selectedMember.text }}</pre>
            <h4>可见性标记</h4>
            <p>
              {{ visibility(selectedMember)
              }}{{ selectedClass.language === 'python' ? '（命名约定）' : '' }}
            </p>
            <template v-if="selectedMember.qualifiers.length"
              ><h4>识别的限定符</h4>
              <p>{{ selectedMember.qualifiers.join(' · ') }}</p></template
            >
          </template>
          <template v-if="!selectedMember && selectedClass.declaration">
            <h4>完整声明</h4>
            <pre>{{ selectedClass.declaration }}</pre>
          </template>
          <h4>来源</h4>
          <code class="source-detail-location">{{
            sourceLocationLabel(selectedMember?.location ?? selectedClass.location)
          }}</code>
          <p>SRC · {{ selectedClass.language }} 静态语法提取</p>
        </template>
        <template v-else
          ><small>成员与来源</small>
          <h3>选择类或成员</h3>
          <p>完整签名连续保留在类卡片中。选择声明可核对来源、可见性标记和语法限定符。</p></template
        >
        <div class="source-detail-limits">
          <h4>解析边界</h4>
          <p>
            只增强已识别的声明；不推断字段类型关联、调用或运行时行为。Python 的 + / −
            是命名约定，不是运行时访问控制。
          </p>
          <p>默认值和初始化内容保持隐藏，不显示常量内容。</p>
          <button class="button secondary" @click="emit('source')">查看 PlantUML 源码 ↗</button>
        </div>
      </aside>
    </div>
  </section>
</template>
<style scoped>
.source-class-view {
  min-width: 0;
  color: var(--text, #34405b);
}
.source-class-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding: 14px 0;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.source-class-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 250px;
  gap: 18px;
  align-items: start;
}
.source-class-main {
  min-width: 0;
}
.source-class-scroll {
  max-height: 590px;
  overflow: auto;
  border: 1px solid #dce1ed;
  border-radius: 18px;
  background: linear-gradient(125deg, #eeeefa77, #e8f5f077);
}
.source-class-canvas {
  position: relative;
  min-width: 425px;
  padding: 20px 36px 20px 16px;
  display: grid;
  grid-template-columns: minmax(345px, 1fr);
  align-items: start;
  gap: 48px;
}
.source-class-canvas.has-boundaries {
  min-width: 740px;
  grid-template-columns: minmax(420px, 1fr) 220px;
}
.source-class-edges {
  position: absolute;
  inset: 0;
  z-index: 1;
  overflow: visible;
  pointer-events: none;
}
.source-class-edges path {
  fill: none;
  stroke: #9d8bba;
  stroke-width: 1.5;
}
.source-class-edges .source-type-arrow {
  fill: #f0f2f7;
}
.source-class-edge:not(.extends) > path {
  stroke-dasharray: 5 4;
}
.source-class-edge text {
  fill: #69788e;
  font-size: 10px;
  paint-order: stroke;
  stroke: #eef2f7;
  stroke-width: 5px;
  stroke-linejoin: round;
}
.source-class-packages {
  display: grid;
  gap: 24px;
  min-width: 0;
}
.source-class-package {
  border: 1px solid #dbd5eb;
  border-radius: 17px;
  padding: 17px;
  background: #ffffff28;
  min-width: 0;
}
.source-package-heading {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: space-between;
  margin: 2px 0 18px;
  color: #7b6e98;
  font-size: 12px;
}
.source-package-heading small {
  color: var(--muted);
}
.source-class-card {
  position: relative;
  z-index: 2;
  background: #fff;
  border: 1px solid #d0cbe0;
  border-radius: 13px;
  overflow: hidden;
  margin-top: 16px;
  box-shadow: 0 8px 20px #65718a07;
}
.source-class-card.selected {
  border-color: #ada0cf;
}
.source-class-heading {
  display: block;
  width: 100%;
  border: 0;
  text-align: left;
  padding: 18px;
  background: #f0edf9;
  color: #5b507a;
  cursor: pointer;
}
.source-class-heading strong {
  display: block;
  font:
    600 15px/1.5 ui-monospace,
    monospace;
  overflow-wrap: normal;
  word-break: normal;
  overflow-x: auto;
}
.source-class-heading small {
  display: block;
  margin-top: 8px;
  color: #8a7ca6;
  font-size: 10px;
}
.source-member-group h4 {
  padding: 14px 16px 6px;
  margin: 0;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: #8590a5;
}
.source-member-group + .source-member-group {
  border-top: 1px solid #e9ecf3;
}
.source-class-member {
  display: flex;
  align-items: start;
  gap: 10px;
  width: 100%;
  border: 0;
  border-top: 1px solid #f1f1f6;
  padding: 12px 16px;
  text-align: left;
  background: #fff;
  color: #52617c;
  cursor: pointer;
}
.source-class-member code {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    11px/1.9 ui-monospace,
    monospace;
  min-width: 0;
  flex: 1;
}
.source-class-member span {
  font-size: 11px;
  color: #9d8cb7;
}
.source-class-member .source-member-visibility {
  padding: 3px 5px;
  border-radius: 4px;
  color: #5b8978;
  background: #eaf4ef;
  font:
    9px/1.8 ui-monospace,
    monospace;
  white-space: nowrap;
  flex-shrink: 0;
}
.source-class-member .source-member-visibility.private {
  color: #8b71a7;
  background: #f2edf8;
}
.source-class-member .source-member-visibility.protected {
  color: #997d46;
  background: #f8f2e6;
}
.source-class-member:hover,
.source-class-member.selected {
  background: #f4f2f8;
}
.source-class-heading:focus-visible,
.source-class-member:focus-visible {
  outline: 2px solid #9180bd;
  outline-offset: -3px;
}
.source-empty-members {
  margin: 0;
  padding: 10px 16px 14px;
  color: #9299a9;
  font-size: 11px;
  line-height: 1.7;
}
.source-class-location {
  border-top: 1px solid #e8eaf2;
  padding: 11px 16px;
  color: #8b94a7;
  font:
    10px/1.7 ui-monospace,
    monospace;
  overflow-wrap: anywhere;
  background: #fbfbfe;
}
.source-class-boundaries {
  position: sticky;
  top: 16px;
  align-self: start;
  display: grid;
  gap: 20px;
  padding-top: 55px;
}
.source-class-boundary {
  position: relative;
  z-index: 2;
  display: flex;
  gap: 11px;
  align-items: start;
  padding: 16px;
  border: 1px solid #d8dfeb;
  border-radius: 13px;
  background: #ffffffed;
  box-shadow: 0 5px 14px #65738e08;
}
.source-boundary-icon {
  background: #efedf6;
  color: #9582af;
  border-radius: 8px;
  padding: 6px 9px;
  flex-shrink: 0;
}
.source-class-boundary strong {
  display: block;
  overflow-wrap: anywhere;
  font:
    12px/1.7 ui-monospace,
    monospace;
}
.source-class-boundary small,
.source-class-boundary p {
  display: block;
  font-size: 10px;
  line-height: 1.7;
  color: #8a95a8;
  margin: 5px 0 0;
}
.source-relation-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 13px 4px;
  color: #7d8799;
  font-size: 10px;
}
.source-class-relations {
  border: 1px solid #e2e4ef;
  border-radius: 12px;
  padding: 13px 16px;
  background: #ffffffa0;
}
.source-relation-heading {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
.source-relation-heading h4 {
  margin: 0;
  font-size: 11px;
}
.source-class-relations ol {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  max-height: 220px;
  overflow: auto;
}
.source-class-relations li {
  padding: 11px 0;
  border-top: 1px solid #e9ebf3;
}
.source-relation-pair {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 11px;
  line-height: 1.7;
}
.source-relation-pair code {
  overflow-wrap: anywhere;
}
.source-class-relations small,
.source-class-relations p {
  display: block;
  font-size: 10px;
  line-height: 1.7;
  color: #8590a5;
  margin: 4px 0 0;
}
.source-member-detail {
  min-width: 0;
  padding: 20px;
  background: #fff;
  border: 1px solid #e4e4ef;
  border-radius: 18px;
}
.source-detail-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.source-member-detail small {
  color: #8b94a7;
  font-size: 10px;
}
.source-member-detail h3 {
  font:
    600 19px/1.6 ui-monospace,
    monospace;
  margin: 12px 0;
  overflow-wrap: anywhere;
}
.source-member-detail h4 {
  font-size: 11px;
  margin: 22px 0 10px;
}
.source-member-detail p {
  font-size: 11px;
  color: #7c8a9f;
  line-height: 1.9;
  margin: 7px 0;
}
.source-member-detail pre {
  padding: 12px;
  border-radius: 8px;
  background: #f0edf9;
  color: #736294;
  font:
    11px/1.9 ui-monospace,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.source-detail-location {
  font:
    11px/1.9 ui-monospace,
    monospace;
  overflow-wrap: anywhere;
}
.source-member-detail .source-detail-class {
  color: #8b72b8;
}
.source-detail-limits {
  border-top: 1px solid #e3e5ef;
  margin-top: 22px;
}
.source-detail-limits .button {
  margin-top: 12px;
  font-size: 11px;
}
@media (max-width: 980px) {
  .source-class-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .source-member-detail {
    padding: 16px;
  }
  .source-class-scroll {
    max-height: 460px;
  }
}
</style>
