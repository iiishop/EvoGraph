<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue';
import { ArrowLeft, ChevronRight } from 'lucide-vue-next';
import { command } from '../../api/client';
import { queuedSettingsCommand, settingsCommandKey } from '../../composables/useSettingsForms';
import { useWorkspace } from '../../composables/useWorkspace';
import { useSurfaceMotion } from '../../composables/useSurfaceMotion';
import ProviderForm from './ProviderForm.vue';
import ResearchForm from './ResearchForm.vue';
const { setPage, reserveSettingsOperation } = useWorkspace();
const category = ref<'model' | 'tools'>('model');
const categorySurface = ref<HTMLElement>();
const categoryMotion = useSurfaceMotion();
watch(category, () => categoryMotion.reveal(categorySurface.value), { flush: 'post' });
const title = computed(() => (category.value === 'model' ? '模型服务' : '搜索与工具'));
provide(settingsCommandKey, queuedSettingsCommand(command, reserveSettingsOperation));
</script>
<template>
  <main class="settings-view">
    <header class="settings-topbar">
      <strong>EvoGraph</strong>
      <div class="settings-breadcrumb" aria-label="当前位置">
        工作空间 <span>/</span> 设置 <span>/</span> <span>{{ title }}</span>
      </div>
      <button type="button" class="button secondary" @click="setPage('projects')">
        <ArrowLeft :size="15" />返回项目
      </button>
    </header>
    <div class="settings-shell">
      <nav class="settings-category-nav" aria-label="设置分类">
        <h2>设置</h2>
        <button
          type="button"
          :class="{ active: category === 'model' }"
          :aria-current="category === 'model' ? 'page' : undefined"
          aria-controls="model-settings"
          @click="category = 'model'"
        >
          模型服务<ChevronRight :size="15" />
        </button>
        <button
          type="button"
          :class="{ active: category === 'tools' }"
          :aria-current="category === 'tools' ? 'page' : undefined"
          aria-controls="tool-settings"
          @click="category = 'tools'"
        >
          搜索与工具<ChevronRight :size="15" />
        </button>
        <button type="button" disabled>外观与交互<small>规划中</small></button>
        <button type="button" disabled>项目与存储<small>规划中</small></button>
        <p class="settings-nav-foot">
          分类切换保留本次草稿。<br />离开设置后，未保存的草稿将清除。<br /><br />更多设置将在对应分类中开放。
        </p>
      </nav>
      <div ref="categorySurface" class="settings-page">
        <header class="settings-page-head">
          <p class="settings-eyebrow">WORKSPACE SETTINGS</p>
          <h1>{{ title }}</h1>
          <p>
            {{
              category === 'model'
                ? '配置工作空间使用的模型连接，所有项目共享。'
                : '按能力管理搜索与视觉检查，各分区有独立的保存状态。'
            }}
          </p>
        </header>
        <!-- Keep both panels alive. No draft, including a key, is written to browser storage. -->
        <section
          id="model-settings"
          v-show="category === 'model'"
          class="settings-inner"
          aria-label="模型服务设置"
        >
          <ProviderForm />
          <aside class="settings-context" aria-label="模型设置说明">
            <div>
              <h3>此设置影响哪些地方？</h3>
              <p>项目对话、规划演化与需要模型的分析，使用当前工作空间的模型配置。</p>
            </div>
            <div>
              <h3>会发送哪些信息？</h3>
              <p>
                项目状态、最近对话、有限仓库文件列表与 Agent 选取的源码摘录会发送到该
                Provider。不会自动执行模型返回的命令。
              </p>
            </div>
            <div>
              <h3>保存与测试分开</h3>
              <p>
                保存只更新模型配置。连接测试使用已经保存的连接；未保存的模型、地址与密钥不会用于测试。
              </p>
            </div>
          </aside>
        </section>
        <section
          id="tool-settings"
          v-show="category === 'tools'"
          class="settings-inner"
          aria-label="搜索与工具设置"
        >
          <ResearchForm />
          <aside class="settings-context" aria-label="工具设置说明">
            <div>
              <h3>操作反馈属于当前分区</h3>
              <p>搜索和视觉设置分别保存。一个分区的保存不会提交另一个分区的草稿。</p>
            </div>
            <div>
              <h3>网页读取独立可用</h3>
              <p>关闭搜索不会关闭网页读取。仅读取公开页面，不执行网页脚本。</p>
            </div>
            <div>
              <h3>保留能力边界</h3>
              <p>
                视觉审阅需要支持图像理解的模型与真实截图。它不会自动截图，也不能代替实际交互测试。
              </p>
            </div>
          </aside>
        </section>
      </div>
    </div>
  </main>
</template>
