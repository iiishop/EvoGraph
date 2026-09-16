<script setup lang="ts">
import { ShieldCheck, Clock3 } from 'lucide-vue-next';
import type { Project } from '../../types';
import { formatTime } from '../../lib/presentation';
defineProps<{ project: Project }>();
</script>
<template>
  <section class="evidence-panel">
    <div class="panel-heading">
      <div>
        <h3>当前结论，有迹可循</h3>
        <p>历史运行结果不会被删除。基线变化后，需要重新验证。</p>
      </div>
      <span class="subtle-tag">{{ project.evidence.length }} 条记录</span>
    </div>
    <div v-if="!project.evidence.length" class="empty-state compact">
      <ShieldCheck :size="30" />
      <h3>还没有验证证据</h3>
      <p>领取里程碑并运行你确认的验收命令后，结果将保存在这里。</p>
    </div>
    <details v-for="e in [...project.evidence].reverse()" :key="e.id" class="evidence-item">
      <summary>
        <span class="result-dot" :class="e.result.toLowerCase()"></span
        ><strong>{{ e.milestone_id }}</strong
        ><span>{{ e.result }}</span
        ><span class="subtle-tag" :class="{ amber: project.evidence_validity[e.id] === 'STALE' }">{{
          project.evidence_validity[e.id] === 'CURRENT' ? '当前基线' : '历史 · 已失效'
        }}</span
        ><small><Clock3 :size="12" /> {{ formatTime(e.created_at) }} · {{ e.duration }}s</small>
      </summary>
      <div class="evidence-body">
        <code>{{ JSON.stringify(e.command) }}</code>
        <p class="muted">
          基线 {{ e.baseline_id }} · 覆盖 {{ e.behavior_revision_ids.length }} 个行为版本
        </p>
        <pre>{{ e.output }}</pre>
      </div>
    </details>
  </section>
</template>
