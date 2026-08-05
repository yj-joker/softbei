<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { confirmTaskResolution } from '@/api/maintenanceTask'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  taskId: { required: true },
  task: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'submitted'])
const submitting = ref(false)
const errorText = ref('')
const form = reactive({ resolutionStatus: '', finalFaultCause: '', effectiveMeasure: '', completionSummary: '' })
const options = [
  { value: 'RESOLVED', label: '已解决', description: '故障已排除，设备恢复正常。' },
  { value: 'PARTIALLY_RESOLVED', label: '部分解决', description: '已完成部分处理，仍需后续跟进。' },
  { value: 'UNRESOLVED', label: '未解决', description: '本次检修未排除故障。' },
]

function reset() {
  Object.assign(form, {
    resolutionStatus: props.task?.resolutionStatus || '',
    finalFaultCause: props.task?.finalFaultCause || '',
    effectiveMeasure: props.task?.effectiveMeasure || '',
    completionSummary: props.task?.completionSummary || '',
  })
  errorText.value = ''
}
watch(() => props.modelValue, (open) => { if (open) reset() })
function close() { if (!submitting.value) emit('update:modelValue', false) }
async function submit() {
  if (!form.resolutionStatus) { errorText.value = '请选择本次检修结果'; return }
  submitting.value = true
  errorText.value = ''
  try {
    await confirmTaskResolution(props.taskId, {
      resolutionStatus: form.resolutionStatus,
      finalFaultCause: form.finalFaultCause.trim() || null,
      effectiveMeasure: form.effectiveMeasure.trim() || null,
      completionSummary: form.completionSummary.trim() || null,
    })
    ElMessage.success('任务结果已保存')
    emit('submitted')
    emit('update:modelValue', false)
  } catch (err) {
    errorText.value = err?.message || '保存失败，请稍后重试'
  } finally { submitting.value = false }
}
</script>

<template>
  <el-dialog :model-value="modelValue" title="确认检修结果" width="560px" align-center append-to-body :close-on-click-modal="false" @update:model-value="emit('update:modelValue', $event)">
    <p class="dialog-lead">步骤记录已保留，请确认本次任务的最终处理结果。</p>
    <el-form label-position="top" @submit.prevent>
      <el-form-item label="检修结果" required>
        <el-radio-group v-model="form.resolutionStatus" class="resolution-options" aria-label="检修结果">
          <el-radio v-for="item in options" :key="item.value" :value="item.value" border class="resolution-option">
            <span class="option-copy"><b>{{ item.label }}</b><small>{{ item.description }}</small></span>
          </el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="最终故障原因"><el-input v-model="form.finalFaultCause" maxlength="500" show-word-limit placeholder="可选，记录确认后的故障原因" /></el-form-item>
      <el-form-item label="有效处理措施"><el-input v-model="form.effectiveMeasure" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="可选，记录实际有效的处理措施" /></el-form-item>
      <el-form-item label="完成摘要"><el-input v-model="form.completionSummary" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="可选，补充本次任务的现场结论" /></el-form-item>
    </el-form>
    <p v-if="errorText" class="form-error" role="alert">{{ errorText }}</p>
    <template #footer>
      <el-button :disabled="submitting" @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" :disabled="submitting" @click="submit">保存任务结果</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dialog-lead { margin: 0 0 16px; color: var(--plaza-text-muted); font-size: 13px; line-height: 1.6; }
.resolution-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; width: 100%; }
.resolution-option { height: auto; min-height: 72px; margin: 0 !important; padding: 10px !important; align-items: flex-start; }
.option-copy { display: flex; flex-direction: column; gap: 5px; white-space: normal; }
.option-copy b { color: var(--plaza-heading); font-size: 13px; }
.option-copy small { color: var(--plaza-text-muted); font-size: 11px; line-height: 1.4; }
.form-error { margin: 0; color: var(--plaza-danger, #c5402c); font-size: 13px; }
@media (max-width: 560px) { .resolution-options { grid-template-columns: 1fr; } }
</style>
