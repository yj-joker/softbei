<script setup>
import { computed } from 'vue'

const props = defineProps({
  candidate: { type: Object, default: () => ({}) },
  activeEvidenceRefs: { type: Array, default: () => [] },
})

function parse(value) {
  if (typeof value !== 'string') return value || {}
  try { return JSON.parse(value) } catch (_) { return {} }
}
function list(value) {
  if (Array.isArray(value)) return value
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => typeof item === 'object' ? { key, ...item } : { key, value: item })
  return []
}
function evidenceText(item) {
  return item?.excerpt || item?.text || item?.content || item?.value || item?.stepId || '-'
}
function fieldLabel(item) {
  const field = String(item?.ref || item?.key || '').split(':').at(-1)
  return {
    deviceName: '设备', faultDescription: '故障描述', finalFaultCause: '最终原因', effectiveMeasure: '有效措施', completionSummary: '完成总结', resolutionStatus: '维修结果', resolvedAt: '解决时间', startedAt: '开始时间', completedAt: '完成时间', title: '步骤名称', content: '操作要求', safetyNote: '安全提示', status: '步骤状态', note: '工人备注', checkpointItems: '检查项', checkpointConfirmed: '检查确认', aiPass: 'AI 验证', aiConfidence: 'AI 置信度', aiReason: 'AI 判断依据', images: '现场图片', reportImages: '报告图片',
  }[field] || field || '证据'
}
function displayValue(item) {
  const field = String(item?.ref || item?.key || '').split(':').at(-1)
  const value = evidenceText(item)
  if (field === 'status') return value === 'COMPLETED' ? '已完成' : value
  if (field === 'resolutionStatus') return { RESOLVED: '已解决', PARTIALLY_RESOLVED: '部分解决', UNRESOLVED: '未解决' }[value] || value
  if (['checkpointConfirmed', 'aiPass'].includes(field)) return String(value).toLowerCase() === 'true' ? '是' : '否'
  return value
}
function isWide(item) {
  return ['content', 'safetyNote', 'note', 'aiReason', 'completionSummary', 'effectiveMeasure'].includes(
    String(item?.ref || item?.key || '').split(':').at(-1),
  )
}
function isActive(item) {
  const refs = props.activeEvidenceRefs || []
  return refs.includes(item?.ref) || refs.includes(item?.stepId)
}
const grouped = computed(() => {
  const source = list(props.candidate?.evidenceJson ? parse(props.candidate.evidenceJson) : props.candidate?.evidence)
  const taskSummary = []
  const steps = new Map()
  source.forEach((item) => {
    const ref = String(item?.ref || '')
    if (ref.startsWith('step:') || item?.stepId) {
      const stepId = String(item?.stepId || ref.split(':')[1] || 'unknown')
      const group = steps.get(stepId) || { stepId, title: '', items: [] }
      const entry = { ...item, fieldLabel: fieldLabel(item), displayValue: displayValue(item) }
      if (ref.endsWith(':title')) group.title = entry.displayValue
      else group.items.push(entry)
      steps.set(stepId, group)
    } else taskSummary.push({ ...item, fieldLabel: fieldLabel(item), displayValue: displayValue(item) })
  })
  return { taskSummary, steps: [...steps.values()] }
})
</script>

<template>
  <section class="tesp-root" aria-label="任务支持证据">
    <header class="tesp-head"><div><p>支持证据</p><h3>任务摘要与检修步骤</h3></div></header>
    <el-empty v-if="!grouped.taskSummary.length && !grouped.steps.length" description="暂无可展示的任务证据" :image-size="64" />
    <template v-else>
      <section v-if="grouped.taskSummary.length" class="tesp-summary">
        <h4>任务摘要</h4>
        <div class="tesp-grid">
          <div v-for="(item, index) in grouped.taskSummary" :key="index" class="tesp-field" :class="{ 'is-active': isActive(item), 'is-wide': isWide(item) }"><span>{{ item.fieldLabel }}</span><strong>{{ item.displayValue }}</strong></div>
        </div>
      </section>
      <el-collapse class="tesp-steps">
        <el-collapse-item v-for="(step, index) in grouped.steps" :key="step.stepId" :name="step.stepId">
          <template #title><span class="tesp-step-number">{{ index + 1 }}</span><span class="tesp-step-title">{{ step.title || `步骤 ${index + 1}` }}</span></template>
          <div class="tesp-grid">
            <div v-for="(item, itemIndex) in step.items" :key="itemIndex" class="tesp-field" :class="{ 'is-active': isActive(item), 'is-wide': isWide(item) }"><span>{{ item.fieldLabel }}</span><strong>{{ item.displayValue }}</strong><a v-if="item.imageUrl || item.imageURL || item.url" :href="item.imageUrl || item.imageURL || item.url" target="_blank" rel="noopener">查看图片</a></div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </section>
</template>

<style scoped>
.tesp-root { color:var(--plaza-text); }.tesp-head { margin-bottom:12px; }.tesp-head p { margin:0 0 4px; color:var(--plaza-accent); font-size:12px; font-weight:700; }.tesp-head h3, .tesp-summary h4 { margin:0; color:var(--plaza-heading); }.tesp-summary { margin-bottom:14px; padding:14px; border:1px solid var(--plaza-border); border-radius:7px; background:var(--plaza-bg-card); }.tesp-summary h4 { margin-bottom:10px; font-size:15px; }.tesp-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:0; }.tesp-field { display:grid; grid-template-columns:104px minmax(0, 1fr); gap:10px; align-items:start; min-width:0; padding:10px 12px; border-bottom:1px solid var(--plaza-border); }.tesp-field span { min-width:104px; color:var(--plaza-text-muted); font-size:12px; }.tesp-field strong { min-width:0; color:var(--plaza-text); font-size:13px; font-weight:500; line-height:1.55; overflow-wrap:anywhere; }.tesp-field a { grid-column:2; font-size:12px; }.tesp-field.is-active { background:var(--el-color-primary-light-9); }.tesp-steps { border-top:1px solid var(--plaza-border); }.tesp-step-number { display:flex; align-items:center; justify-content:center; flex:0 0 26px; width:26px; height:26px; margin-right:10px; border-radius:50%; color:#fff; background:var(--plaza-accent); font-size:12px; font-weight:700; line-height:1; vertical-align:middle; }.tesp-step-title { min-width:0; overflow-wrap:anywhere; }
@media (min-width: 721px) {
  .tesp-field.is-wide { grid-column:1 / -1; grid-template-columns:1fr; gap:5px; }
  .tesp-field.is-wide span { min-width:0; }
}
@media (max-width: 720px) { .tesp-grid { grid-template-columns:1fr; }.tesp-field { grid-template-columns:minmax(0, 1fr); gap:4px; }.tesp-field span { min-width:0; }.tesp-field a { grid-column:1; }.tesp-step-title { line-height:1.35; } }
</style>
