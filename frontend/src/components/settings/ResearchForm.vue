<script setup lang="ts">
import { inject, onMounted, onUnmounted } from 'vue';
import { command } from '../../api/client';
import {
  settingsCommandKey,
  settingsBusyReason,
  queuedSettingsCommand,
  useResearchSettings,
} from '../../composables/useSettingsForms';
import { useWorkspace } from '../../composables/useWorkspace';
const { state, reserveSettingsOperation } = useWorkspace();
const form = useResearchSettings(
  inject(settingsCommandKey, queuedSettingsCommand(command, reserveSettingsOperation)),
  () => state.busy,
);
const {
  settings,
  blocked,
  loading,
  loadError,
  provider,
  config,
  apiKey,
  clearKey,
  vision,
  descriptor,
  savedKey,
  searchDirty,
  visionDirty,
  searchStatus,
  visionStatus,
  load,
  saveSearch,
  saveVision,
} = form;
onMounted(load);
onUnmounted(form.dispose);
</script>
<template>
  <div class="settings-tool-stack">
    <div v-if="loading || loadError" class="settings-load-state">
      <p v-if="loading" role="status">正在读取工具设置…</p>
      <template v-else
        ><p class="settings-feedback error" role="alert">{{ loadError }}</p>
        <button type="button" class="button secondary" @click="load">
          重新读取工具设置
        </button></template
      >
    </div>
    <form
      class="settings-card search-settings-form"
      aria-label="网页搜索设置"
      :aria-busy="searchStatus.busy"
      @submit.prevent="saveSearch"
    >
      <div class="settings-card-heading">
        <div>
          <h2>网页搜索</h2>
          <p>选择搜索服务。网页读取独立可用。</p>
        </div>
        <span v-if="settings" class="settings-status-tag" :class="{ dirty: searchDirty }">{{
          searchDirty ? '未保存' : '已保存'
        }}</span>
      </div>
      <fieldset :disabled="searchStatus.busy || !settings">
        <label
          >搜索服务<select v-model="provider" aria-label="搜索服务">
            <option value="">关闭搜索（仍可读取网页）</option>
            <option v-for="item in settings?.providers" :key="item.id" :value="item.id">
              {{ item.name }}
            </option></select
          ><small>{{ descriptor?.description || '关闭搜索不影响公开网页读取。' }}</small></label
        >
        <label v-for="field in descriptor?.fields" :key="`${provider}-${field.key}`"
          >{{ field.label }}
          <input
            v-model="config[field.key]"
            :required="field.required"
            :placeholder="field.placeholder"
            autocomplete="off"
          />
        </label>
        <label v-if="descriptor?.secret_label"
          >{{ descriptor.secret_label }}
          <input
            v-model="apiKey"
            type="password"
            autocomplete="new-password"
            aria-label="搜索 API Key"
            :disabled="clearKey"
            :placeholder="savedKey ? '已保存；留空保留' : '输入 API Key'"
          />
          <small>密钥保存在系统凭据库。更改搜索服务或服务配置会清空输入的密钥。</small>
        </label>
        <label v-if="savedKey && descriptor?.secret_label" class="settings-checkbox"
          ><input v-model="clearKey" type="checkbox" />移除当前搜索服务已保存的密钥</label
        >
      </fieldset>
      <p class="settings-capability-note">
        基础搜索可使用无需密钥的 Bing。仅访问公开页面，不执行网页脚本。
      </p>
      <p v-if="blocked && !searchStatus.busy" class="settings-feedback waiting" role="status">
        {{ settingsBusyReason }}
      </p>
      <p v-if="searchStatus.error" class="settings-feedback error" role="alert">
        {{ searchStatus.error }}
      </p>
      <footer class="settings-card-footer">
        <p class="settings-save-state" role="status">
          {{
            searchStatus.busy
              ? '正在保存搜索设置…'
              : searchDirty
                ? '尚有未保存的搜索设置'
                : searchStatus.message || '本分区独立保存'
          }}
        </p>
        <button class="button primary" :disabled="blocked || searchStatus.busy || !settings">
          {{ searchStatus.busy ? '保存中…' : '保存搜索设置' }}
        </button>
      </footer>
    </form>
    <form
      class="settings-card vision-settings-form"
      aria-label="视觉检查设置"
      :aria-busy="visionStatus.busy"
      @submit.prevent="saveVision"
    >
      <div class="settings-card-heading">
        <div>
          <h2>视觉检查</h2>
          <p>声明当前模型是否支持图像理解。</p>
        </div>
        <span v-if="settings" class="settings-status-tag" :class="{ dirty: visionDirty }">{{
          visionDirty ? '未保存' : '已保存'
        }}</span>
      </div>
      <fieldset :disabled="visionStatus.busy || !settings">
        <label class="settings-switch-row"
          ><span>启用截图视觉审阅<small>需要当前模型具有图像理解能力</small></span>
          <input v-model="vision" type="checkbox" role="switch" aria-label="启用截图视觉审阅" />
        </label>
      </fieldset>
      <p class="settings-vision-note">
        不会自动截图；视觉审阅不等于交互测试通过。可在输入区附加真实界面截图后要求检查。
      </p>
      <p v-if="blocked && !visionStatus.busy" class="settings-feedback waiting" role="status">
        {{ settingsBusyReason }}
      </p>
      <p v-if="visionStatus.error" class="settings-feedback error" role="alert">
        {{ visionStatus.error }}
      </p>
      <footer class="settings-card-footer">
        <p class="settings-save-state" role="status">
          {{
            visionStatus.busy
              ? '正在保存视觉设置…'
              : visionDirty
                ? '尚有未保存的视觉设置'
                : visionStatus.message || '本分区独立保存'
          }}
        </p>
        <button class="button primary" :disabled="blocked || visionStatus.busy || !settings">
          {{ visionStatus.busy ? '保存中…' : '保存视觉设置' }}
        </button>
      </footer>
    </form>
  </div>
</template>
