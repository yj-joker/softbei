<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { submitAnswerFeedback } from '@/api/answerFeedback'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  message: { type: Object, default: null },
  sessionId: { type: [String, Number], default: '' },
})

const emit = defineEmits(['update:modelValue', 'submitted'])
const submitting = ref(false)
const form = reactive({ reasonCode: 'incorrect', comment: '' })

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

watch(() => props.modelValue, (open) => {
  if (!open) return
  form.reasonCode = 'incorrect'
  form.comment = ''
})

function answerScope() {
  const metadata = props.message?.responseMetadata || {}
  const scope = metadata.scope_decision || metadata.domain_rule_match?.scope_binding || {}
  return {
    deviceType: scope.device_type || scope.detected_device_type || '',
    documentId: scope.document_id || scope.requested_document_id || '',
  }
}

async function submit() {
  if (!props.sessionId || !props.message?.persistedMessageId || !props.message?.content?.trim()) {
    ElMessage.error('当前回答无法关联到有效会话')
    return
  }
  submitting.value = true
  try {
    const scope = answerScope()
    const response = await submitAnswerFeedback({
      sessionId: props.sessionId,
      assistantMessageId: props.message.persistedMessageId,
      assistantAnswer: props.message.content,
      reasonCode: form.reasonCode,
      comment: form.comment.trim(),
      ...scope,
    })
    emit('submitted', response.data)
    visible.value = false
    ElMessage.success('已提交，管理员审核后可沉淀为规则')
  } catch (error) {
    ElMessage.error(error?.message || '提交失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="答案纠错" width="min(460px, calc(100vw - 28px))" append-to-body>
    <el-form label-position="top">
      <el-form-item label="问题类型">
        <el-radio-group v-model="form.reasonCode">
          <el-radio-button value="incorrect">内容错误</el-radio-button>
          <el-radio-button value="incomplete">信息缺失</el-radio-button>
          <el-radio-button value="source_error">引用错误</el-radio-button>
          <el-radio-button value="order_error">步骤有误</el-radio-button>
          <el-radio-button value="other">其他</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="补充说明">
        <el-input
          v-model="form.comment"
          type="textarea"
          :rows="4"
          maxlength="500"
          show-word-limit
          placeholder="指出错误位置或缺失信息"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:deep(.el-radio-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:deep(.el-radio-button__inner) {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  box-shadow: none;
}
</style>
