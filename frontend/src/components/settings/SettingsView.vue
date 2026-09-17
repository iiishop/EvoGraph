<script setup lang="ts">
import { ChevronRight, Settings2, Database, ShieldCheck } from 'lucide-vue-next';
import ProviderForm from './ProviderForm.vue';
import ResearchForm from './ResearchForm.vue';
</script>
<template>
  <main class="settings-view">
    <div class="breadcrumb">
      <Settings2 :size="14" /> 工作空间 <ChevronRight :size="13" /><span>设置</span>
    </div>
    <h1>工作空间设置</h1>
    <p class="settings-intro">按你的方式连接模型，让规划留在自己的工作流中。</p>
    <div class="settings-layout">
      <div class="settings-section-nav">
        <span class="active"><Settings2 :size="15" />Provider</span>
        <p>当前版本使用一个工作空间级模型配置。</p>
      </div>
      <div class="settings-content">
        <ProviderForm />
        <ResearchForm />
        <div class="settings-note">
          <Database :size="20" />
          <div>
            <h4>项目在本地，证据可追溯</h4>
            <p>目标、规划、对话与验证历史存储在 SQLite。关闭应用后，下次打开仍可继续。</p>
          </div>
        </div>
        <div class="settings-note">
          <ShieldCheck :size="20" />
          <div>
            <h4>图上直接演化，代码独立执行</h4>
            <p>
              Agent
              通过工具实时修改当前图，缺少信息时会向你提问。仓库代码不会自动修改，验收仍由你发起。
            </p>
          </div>
        </div>
      </div>
    </div>
  </main>
</template>
