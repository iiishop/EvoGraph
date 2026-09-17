<script setup lang="ts">
import { ref, watch } from 'vue';
import { command } from '../../api/client';
import type { Attachment } from '../../types';
const props = defineProps<{ asset: Attachment; projectId: string }>();
const url = ref('');
const error = ref('');
watch(
  () => props.asset.id,
  async () => {
    url.value = '';
    error.value = '';
    if (!props.asset.media_type.startsWith('image/')) return;
    try {
      const result = await command<{ data_url: string }>('attachments.read', {
        project_id: props.projectId,
        attachment_id: props.asset.id,
      });
      url.value = result.data_url;
    } catch (e) {
      error.value = String(e);
    }
  },
  { immediate: true },
);
</script>
<template>
  <figure class="asset-preview">
    <img v-if="url" :src="url" :alt="asset.name" />
    <figcaption>{{ asset.name }} · {{ Math.ceil(asset.size / 1024) }} KB</figcaption>
    <pre v-if="asset.excerpt">{{ asset.excerpt }}</pre>
    <p v-if="error">{{ error }}</p>
  </figure>
</template>
