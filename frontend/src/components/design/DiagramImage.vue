<script setup lang="ts">
import { ref, computed, watch } from 'vue';
defineProps<{ src: string; title: string; expanded?: boolean }>();
const zoom = ref(100), naturalWidth = ref(0), naturalHeight = ref(0);
const fitted = ref(true);
watch(zoom, () => { fitted.value=false; });
const dimensions = computed(()=>fitted.value ? {width:'100%',height:'100%',objectFit:'contain' as const} : {width:naturalWidth.value*zoom.value/100+'px',height:naturalHeight.value*zoom.value/100+'px',maxWidth:'none'});
function loaded(event:Event){const img=event.target as HTMLImageElement;naturalWidth.value=img.naturalWidth;naturalHeight.value=img.naturalHeight;}
</script>
<template><div class="diagram-image"><div class="uml-controls"><label>缩放 <input v-model.number="zoom" type="range" min="30" max="250" step="10" /></label><button class="text-button" @click="fitted=true">适应画布</button><button class="text-button" @click="zoom=100; fitted=false">原始尺寸</button></div><div class="uml-canvas" :class="{expanded}"><img :src="src" :alt="title" :style="dimensions" @load="loaded" /></div></div></template>
