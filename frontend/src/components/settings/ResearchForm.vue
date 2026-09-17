<script setup lang="ts">
import { onMounted, computed, ref } from 'vue';
import { Globe, Eye } from 'lucide-vue-next';
import { command } from '../../api/client';
import type { ProviderDescriptor } from '../../types';
interface ResearchSettings {
  providers: ProviderDescriptor[];
  saved: { provider: string; config: Record<string, string>; has_key: boolean } | null;
  vision_enabled: boolean;
}
const settings = ref<ResearchSettings>(),
  provider = ref(''),
  config = ref<Record<string, string>>({}),
  apiKey = ref(''),
  vision = ref(false),
  clearKey = ref(false),
  busy = ref(false),
  message = ref(''),
  error = ref('');
const descriptor = computed(() => settings.value?.providers.find((p) => p.id === provider.value));
onMounted(async () => {
  try {
    settings.value = await command<ResearchSettings>('research.settings');
    provider.value = settings.value.saved?.provider ?? '';
    config.value = settings.value.saved?.config ?? {};
    vision.value = settings.value.vision_enabled;
  } catch (e) {
    error.value = String(e);
  }
});
function changed() {
  config.value = Object.fromEntries(descriptor.value?.fields.map((f) => [f.key, f.default]) ?? []);
  apiKey.value = '';
  clearKey.value = false;
}
async function save() {
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    settings.value = await command<ResearchSettings>('research.configure', {
      provider: provider.value,
      config: config.value,
      api_key: apiKey.value,
      clear_key: clearKey.value,
      vision_enabled: vision.value,
    });
    apiKey.value = '';
    clearKey.value = false;
    message.value = '网页搜索与视觉能力设置已保存';
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <form class="research-form" @submit.prevent="save">
    <h2><Globe :size="20" />网页搜索与视觉检查</h2>
    <p>让技术选型有可追溯的网页依据。密钥保存在系统凭据库，查询发送给选定的搜索服务。</p>
    <label
      >搜索服务<select v-model="provider" @change="changed">
        <option value="">未启用</option>
        <option v-for="p in settings?.providers" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select></label
    >
    <p v-if="descriptor" class="muted">{{ descriptor.description }}</p>
    <label v-for="field in descriptor?.fields" :key="field.key"
      >{{ field.label
      }}<input
        v-model="config[field.key]"
        :required="field.required"
        :placeholder="field.placeholder"
    /></label>
    <label v-if="descriptor?.secret_label"
      >{{ descriptor.secret_label
      }}<input
        v-model="apiKey"
        type="password"
        autocomplete="new-password"
        :placeholder="
          settings?.saved?.has_key && settings.saved.provider === provider
            ? '已保存；留空保留'
            : '输入 API Key'
        "
    /></label>
    <label v-if="descriptor?.secret_label" class="checkbox-label"
      ><input v-model="clearKey" type="checkbox" />清除已保存密钥</label
    >
    <label class="checkbox-label"
      ><input v-model="vision" type="checkbox" /><Eye
        :size="17"
      />当前模型支持图像理解，启用截图视觉审阅</label
    ><small
      >在输入区附加真实界面截图，再要求 Agent
      视觉检查。此开关声明模型能力；不会自动截屏，也不把视觉审阅当成交互测试通过。</small
    >
    <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <button class="button primary" :disabled="busy || !settings">
      {{ busy ? '保存中…' : '保存工具设置' }}
    </button>
  </form>
</template>
