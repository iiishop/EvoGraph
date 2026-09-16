<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { KeyRound, Radio, Save, Eye, EyeOff } from 'lucide-vue-next';
import { useWorkspace } from '../../composables/useWorkspace';
const { state, perform } = useWorkspace();
const adapter = ref(state.settings?.provider?.adapter ?? state.settings?.adapters[0]?.id ?? '');
const fields = ref<Record<string, string>>({}),
  apiKey = ref(''),
  showKey = ref(false),
  clearKey = ref(false),
  tested = ref('');
const descriptor = computed(() => state.settings?.adapters.find((a) => a.id === adapter.value));
function populate() {
  const existing = state.settings?.provider;
  fields.value = Object.fromEntries(
    (descriptor.value?.fields ?? []).map((f) => [
      f.key,
      existing?.adapter === adapter.value ? (existing.config[f.key] ?? f.default) : f.default,
    ]),
  );
  apiKey.value = '';
  tested.value = '';
  clearKey.value = false;
}
watch(adapter, populate, { immediate: true });
async function save() {
  const result = await perform(
    'settings.save',
    {
      adapter: adapter.value,
      config: fields.value,
      api_key: apiKey.value,
      clear_key: clearKey.value,
    },
    'Provider 已保存',
  );
  if (result) {
    apiKey.value = '';
    clearKey.value = false;
    tested.value = '';
  }
}
async function test() {
  tested.value = '';
  const result = await perform<{ message: string }>('settings.test');
  if (result) tested.value = '连接成功 · ' + result.message;
}
</script>
<template>
  <form class="provider-form" @submit.prevent="save">
    <div class="settings-card-heading">
      <span class="settings-icon"><Radio :size="20" /></span>
      <div>
        <h3>模型服务</h3>
        <p>连接你自己的模型，让 Agent 理解目标与项目上下文。</p>
      </div>
      <span class="subtle-tag">{{ state.settings?.provider ? '已配置' : '未配置' }}</span>
    </div>
    <div class="provider-fields">
      <label
        >Provider 协议<select v-model="adapter">
          <option v-for="a in state.settings?.adapters" :key="a.id" :value="a.id">
            {{ a.name }}
          </option></select
        ><small>{{ descriptor?.description }}</small></label
      ><label v-for="field in descriptor?.fields" :key="`${adapter}-${field.key}`"
        >{{ field.label
        }}<input
          v-model="fields[field.key]"
          :required="field.required"
          :placeholder="field.placeholder"
          autocomplete="off" /></label
      ><label
        ><span
          ><KeyRound :size="14" />{{ descriptor?.secret_label ?? 'API Key'
          }}<span class="optional">本地服务可留空</span></span
        >
        <div class="password-field">
          <input
            v-model="apiKey"
            :type="showKey ? 'text' : 'password'"
            :placeholder="
              state.settings?.provider?.has_key
                ? '已保存在系统凭据库；留空保持现有密钥'
                : '输入 API Key'
            "
            autocomplete="new-password"
          /><button
            type="button"
            class="icon-button"
            :aria-label="showKey ? '隐藏密钥' : '显示密钥'"
            @click="showKey = !showKey"
          >
            <EyeOff v-if="showKey" :size="16" /><Eye v-else :size="16" />
          </button>
        </div>
        <small>密钥保存在操作系统凭据库，不写入项目数据库。</small></label
      ><label v-if="state.settings?.provider?.has_key" class="checkbox-label"
        ><input v-model="clearKey" type="checkbox" />移除已保存的密钥</label
      >
      <p class="privacy-note">
        发送消息时，当前项目状态、最近对话、有限的仓库文件列表与 Agent 选取的源码摘录会发送到此
        Provider。不会自动执行模型返回的命令。
      </p>
      <p v-if="tested" class="connection-result" role="status">{{ tested }}</p>
    </div>
    <footer class="settings-card-footer">
      <button
        type="button"
        class="button secondary"
        :disabled="state.busy || !state.settings?.provider"
        @click="test"
      >
        <Radio :size="15" />测试已保存的连接</button
      ><button class="button primary" :disabled="state.busy"><Save :size="15" />保存配置</button>
    </footer>
  </form>
</template>
