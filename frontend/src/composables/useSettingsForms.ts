import { computed, reactive, ref, watch, type InjectionKey } from 'vue';
import type { ProviderDescriptor, Settings } from '../types';

export type SettingsCommand = <T>(action: string, params?: object) => Promise<T>;
export const settingsCommandKey: InjectionKey<SettingsCommand> = Symbol('settingsCommand');
export interface ResearchSettings {
  providers: ProviderDescriptor[];
  saved: { provider: string; config: Record<string, string>; has_key: boolean } | null;
  vision_enabled: boolean;
}

// Ordering survives leaving and reopening settings. No credential is persisted:
// queued payloads only live in these in-memory request closures until completion.
let pendingSettingsCommand: Promise<unknown> = Promise.resolve();
export const settingsBusyReason = 'Agent 或工作空间操作进行中，完成后可保存或测试；仍可编辑草稿。';
export function queuedSettingsCommand(
  command: SettingsCommand,
  reserve?: () => (() => void) | null,
): SettingsCommand {
  return <T>(action: string, params?: object) => {
    const mutation = action !== 'research.settings' && action !== 'settings.get';
    const release = mutation ? reserve?.() : undefined;
    if (mutation && reserve && !release) return Promise.reject(new Error(settingsBusyReason));
    const result = pendingSettingsCommand.then(() => command<T>(action, params));
    const settled = result.finally(() => release?.());
    pendingSettingsCommand = settled.catch(() => {});
    return settled;
  };
}
export interface ModelSettingsOptions {
  blocked?: () => boolean;
  latestSettings?: () => Settings | null;
}
const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : '操作未完成，请重试';
const cleanConfig = (config: Record<string, string>) =>
  Object.fromEntries(
    Object.keys(config)
      .sort()
      .map((key) => [key, config[key].trim()]),
  );
const sameConfig = (a: Record<string, string>, b: Record<string, string>) =>
  JSON.stringify(cleanConfig(a)) === JSON.stringify(cleanConfig(b));
const endpoint = (url = '') => url.trim().replace(/\/+$/, '');
const defaults = (descriptor?: ProviderDescriptor) =>
  Object.fromEntries((descriptor?.fields ?? []).map((field) => [field.key, field.default]));

export function useModelSettings(
  initial: Settings | null,
  command: SettingsCommand,
  applySettings: (settings: Settings) => void,
  options: ModelSettingsOptions = {},
) {
  const blocked = computed(() => options.blocked?.() ?? false);
  const settings = ref(initial);
  const adapter = ref(initial?.provider?.adapter ?? initial?.adapters[0]?.id ?? '');
  const fields = ref<Record<string, string>>({});
  const apiKey = ref(''),
    clearKey = ref(false),
    showKey = ref(false);
  const operation = ref<'saving' | 'testing' | ''>('');
  const error = ref(''),
    message = ref(''),
    tested = ref('');
  const descriptor = computed(() =>
    settings.value?.adapters.find((item) => item.id === adapter.value),
  );
  const savedKey = computed(() =>
    Boolean(
      settings.value?.provider?.has_key &&
      settings.value.provider.adapter === adapter.value &&
      endpoint(settings.value.provider.config.base_url) === endpoint(fields.value.base_url),
    ),
  );
  const dirty = computed(() => {
    const saved = settings.value?.provider;
    return (
      !saved ||
      saved.adapter !== adapter.value ||
      !sameConfig(saved.config, fields.value) ||
      Boolean(apiKey.value) ||
      clearKey.value
    );
  });
  watch(
    adapter,
    () => {
      const saved = settings.value?.provider;
      fields.value =
        saved?.adapter === adapter.value
          ? { ...defaults(descriptor.value), ...saved.config }
          : defaults(descriptor.value);
      apiKey.value = '';
      clearKey.value = false;
      showKey.value = false;
      error.value = '';
      message.value = '';
    },
    { immediate: true, flush: 'sync' },
  );
  // A newly entered secret must never follow a changed destination accidentally.
  watch(
    () => endpoint(fields.value.base_url),
    () => {
      apiKey.value = '';
      clearKey.value = false;
      showKey.value = false;
    },
    { flush: 'sync' },
  );
  watch(
    clearKey,
    (clear) => {
      if (clear) apiKey.value = '';
    },
    { flush: 'sync' },
  );
  let disposed = false;
  let adoptedDraft = { adapter: adapter.value, config: { ...fields.value } };
  if (options.latestSettings)
    watch(
      options.latestSettings,
      (latest) => {
        if (!latest || disposed || latest === settings.value) return;
        const pristine =
          !operation.value &&
          adapter.value === adoptedDraft.adapter &&
          sameConfig(fields.value, adoptedDraft.config) &&
          !apiKey.value &&
          !clearKey.value;
        settings.value = latest;
        tested.value = '';
        if (pristine) {
          adapter.value = latest.provider?.adapter ?? latest.adapters[0]?.id ?? '';
          fields.value = { ...defaults(descriptor.value), ...latest.provider?.config };
          adoptedDraft = { adapter: adapter.value, config: { ...fields.value } };
        }
        if (!savedKey.value) clearKey.value = false;
      },
      { flush: 'sync' },
    );
  async function save() {
    if (blocked.value || operation.value || !settings.value || disposed) return;
    operation.value = 'saving';
    error.value = '';
    message.value = '';
    tested.value = '';
    try {
      const result = await command<Settings>('settings.save', {
        adapter: adapter.value,
        config: { ...fields.value },
        api_key: apiKey.value,
        clear_key: savedKey.value && clearKey.value,
      });
      applySettings(result);
      if (disposed) return;
      settings.value = result;
      fields.value = { ...result.provider!.config };
      adoptedDraft = { adapter: adapter.value, config: { ...fields.value } };
      apiKey.value = '';
      clearKey.value = false;
      showKey.value = false;
      message.value = '模型配置已保存';
    } catch (reason) {
      if (!disposed) error.value = messageOf(reason);
    } finally {
      operation.value = '';
    }
  }
  async function test() {
    if (blocked.value || operation.value || !settings.value?.provider || disposed) return;
    operation.value = 'testing';
    error.value = '';
    tested.value = '';
    try {
      const result = await command<{ message: string }>('settings.test');
      if (!disposed) tested.value = '已保存的连接可用 · ' + result.message;
    } catch (reason) {
      if (!disposed) error.value = messageOf(reason);
    } finally {
      operation.value = '';
    }
  }
  function dispose() {
    disposed = true;
    apiKey.value = '';
    clearKey.value = false;
    showKey.value = false;
  }
  return {
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
    dispose,
  };
}

export function useResearchSettings(
  command: SettingsCommand,
  isBlocked: () => boolean = () => false,
) {
  const blocked = computed(isBlocked);
  const settings = ref<ResearchSettings>();
  const loading = ref(false),
    loadError = ref('');
  const provider = ref(''),
    config = ref<Record<string, string>>({});
  const apiKey = ref(''),
    clearKey = ref(false),
    vision = ref(false);
  const searchStatus = reactive({ busy: false, error: '', message: '' });
  const visionStatus = reactive({ busy: false, error: '', message: '' });
  const descriptor = computed(() =>
    settings.value?.providers.find((item) => item.id === provider.value),
  );
  const savedKey = computed(() =>
    Boolean(
      settings.value?.saved?.has_key &&
      settings.value.saved.provider === provider.value &&
      sameConfig(settings.value.saved.config, config.value),
    ),
  );
  const searchDirty = computed(() => {
    if (!settings.value) return false;
    const saved = settings.value.saved;
    return (
      (saved?.provider ?? '') !== provider.value ||
      !sameConfig(saved?.config ?? {}, config.value) ||
      Boolean(apiKey.value) ||
      clearKey.value
    );
  });
  const visionDirty = computed(() =>
    Boolean(settings.value && settings.value.vision_enabled !== vision.value),
  );
  watch(
    provider,
    () => {
      const saved = settings.value?.saved;
      config.value =
        saved?.provider === provider.value
          ? { ...defaults(descriptor.value), ...saved.config }
          : defaults(descriptor.value);
      apiKey.value = '';
      clearKey.value = false;
      searchStatus.error = '';
      searchStatus.message = '';
    },
    { flush: 'sync' },
  );
  watch(
    () => JSON.stringify(cleanConfig(config.value)),
    () => {
      apiKey.value = '';
      clearKey.value = false;
    },
    { flush: 'sync' },
  );
  watch(
    clearKey,
    (clear) => {
      if (clear) apiKey.value = '';
    },
    { flush: 'sync' },
  );
  let disposed = false;
  async function load() {
    if (loading.value || settings.value || disposed) return;
    loading.value = true;
    loadError.value = '';
    try {
      const result = await command<ResearchSettings>('research.settings');
      if (disposed) return;
      settings.value = result;
      provider.value = result.saved?.provider ?? '';
      config.value = { ...defaults(descriptor.value), ...result.saved?.config };
      vision.value = result.vision_enabled;
    } catch (reason) {
      if (!disposed) loadError.value = messageOf(reason);
    } finally {
      loading.value = false;
    }
  }
  async function saveSearch() {
    if (blocked.value || searchStatus.busy || !settings.value || disposed) return;
    searchStatus.busy = true;
    searchStatus.error = '';
    searchStatus.message = '';
    try {
      const result = await command<ResearchSettings>('research.configure_search', {
        provider: provider.value,
        config: { ...config.value },
        api_key: apiKey.value,
        clear_key: savedKey.value && clearKey.value,
      });
      if (disposed) return;
      // Only this section's baseline changes, even if another response is older.
      settings.value.saved = result.saved;
      config.value = { ...defaults(descriptor.value), ...result.saved?.config };
      apiKey.value = '';
      clearKey.value = false;
      searchStatus.message = '搜索设置已保存';
    } catch (reason) {
      if (!disposed) searchStatus.error = messageOf(reason);
    } finally {
      searchStatus.busy = false;
    }
  }
  async function saveVision() {
    if (blocked.value || visionStatus.busy || !settings.value || disposed) return;
    visionStatus.busy = true;
    visionStatus.error = '';
    visionStatus.message = '';
    try {
      const result = await command<ResearchSettings>('research.configure_vision', {
        vision_enabled: vision.value,
      });
      if (disposed) return;
      settings.value.vision_enabled = result.vision_enabled;
      visionStatus.message = '视觉设置已保存';
    } catch (reason) {
      if (!disposed) visionStatus.error = messageOf(reason);
    } finally {
      visionStatus.busy = false;
    }
  }
  function dispose() {
    disposed = true;
    apiKey.value = '';
    clearKey.value = false;
  }
  return {
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
    dispose,
  };
}
