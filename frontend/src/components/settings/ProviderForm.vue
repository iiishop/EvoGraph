<script setup lang="ts">
import { inject, onUnmounted } from 'vue';
import { Radio, Save, Eye, EyeOff } from 'lucide-vue-next';
import { command } from '../../api/client';
import {
  settingsCommandKey,
  settingsBusyReason,
  queuedSettingsCommand,
  useModelSettings,
} from '../../composables/useSettingsForms';
import { useWorkspace } from '../../composables/useWorkspace';
const { state, applySettings, reserveSettingsOperation } = useWorkspace();
const form = useModelSettings(
  state.settings,
  inject(settingsCommandKey, queuedSettingsCommand(command, reserveSettingsOperation)),
  applySettings,
  { blocked: () => state.busy, latestSettings: () => state.settings },
);
const {
  settings,
  blocked,
  adapter,
  fields,
  apiKey,
  clearKey,
  showKey,
  descriptor,
  savedKey,
  dirty,
  operation,
  error,
  message,
  tested,
  save,
  test,
} = form;
onUnmounted(form.dispose);
</script>
<template>
  <form
    class="settings-card model-settings-form"
    aria-label="模型连接配置"
    :aria-busy="Boolean(operation)"
    @submit.prevent="save"
  >
    <div class="settings-card-heading">
      <div>
        <h2>连接配置</h2>
        <p>管理模型连接，所有项目共享同一配置。</p>
      </div>
      <span class="settings-status-tag" :class="{ dirty }">{{ dirty ? '未保存' : '已保存' }}</span>
    </div>
    <fieldset :disabled="Boolean(operation) || !settings">
      <label
        >Provider 协议
        <select v-model="adapter" aria-label="Provider 协议">
          <option v-for="item in settings?.adapters" :key="item.id" :value="item.id">
            {{ item.name }}
          </option></select
        ><small>{{ descriptor?.description }}</small>
      </label>
      <label v-for="field in descriptor?.fields" :key="`${adapter}-${field.key}`"
        >{{ field.label }}
        <input
          v-model="fields[field.key]"
          :required="field.required"
          :placeholder="field.placeholder"
          autocomplete="off"
        />
      </label>
      <label
        ><span class="settings-label-line"
          >{{ descriptor?.secret_label || 'API Key' }}<small>本地服务可留空</small></span
        >
        <span class="password-field"
          ><input
            v-model="apiKey"
            :type="showKey ? 'text' : 'password'"
            :disabled="clearKey"
            :placeholder="savedKey ? '已保存在系统凭据库；留空保留' : '输入 API Key'"
            autocomplete="new-password"
            aria-label="模型 API Key"
          />
          <button
            type="button"
            class="icon-button"
            :aria-label="showKey ? '隐藏密钥' : '显示密钥'"
            :aria-pressed="showKey"
            @click="showKey = !showKey"
          >
            <EyeOff v-if="showKey" :size="17" /><Eye v-else :size="17" />
          </button>
        </span>
        <small
          >密钥保存到操作系统凭据库，不写入项目数据库。未保存的密钥只保留在本次设置页面中；更改服务地址会清空输入的密钥。</small
        >
      </label>
      <label v-if="savedKey" class="settings-checkbox"
        ><input v-model="clearKey" type="checkbox" />移除当前连接已保存的密钥</label
      >
    </fieldset>
    <p v-if="blocked && !operation" class="settings-feedback waiting" role="status">
      {{ settingsBusyReason }}
    </p>
    <p v-if="error" class="settings-feedback error" role="alert">{{ error }}</p>
    <p v-if="tested" class="settings-feedback success" role="status">{{ tested }}</p>
    <footer class="settings-card-footer">
      <p class="settings-save-state" role="status">
        {{
          operation === 'saving'
            ? '正在保存模型配置…'
            : operation === 'testing'
              ? '正在测试已保存的连接…'
              : dirty
                ? '尚有未保存的模型配置'
                : message || '当前模型配置已保存'
        }}
      </p>
      <div class="settings-actions">
        <button
          type="button"
          class="button secondary"
          :disabled="blocked || Boolean(operation) || !settings?.provider"
          @click="test"
        >
          <Radio :size="15" />{{ operation === 'testing' ? '测试中…' : '测试已保存的连接' }}
        </button>
        <button class="button primary" :disabled="blocked || Boolean(operation) || !settings">
          <Save :size="15" />{{ operation === 'saving' ? '保存中…' : '保存模型配置' }}
        </button>
      </div>
    </footer>
  </form>
</template>
