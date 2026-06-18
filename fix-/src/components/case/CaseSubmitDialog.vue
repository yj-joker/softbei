<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { searchDevices } from '@/api/graph'
import { submitCase } from '@/api/caseRecord'

const props = defineProps({
  visible: { type: Boolean, default: false },
  draft: { type: Object, default: null },
})

const emit = defineEmits(['update:visible', 'submitted'])

const submitting = ref(false)
const deviceLoading = ref(false)
const devices = ref([])
const formRef = ref(null)

const form = reactive({
  title: '',
  summary: '',
  diagnosis: '',
  resolution: '',
  result: '',
  experienceSummary: '',
  tagsText: '',
  downtime: null,
  cost: null,
  deviceId: null,
  faultName: '',
})

const dialogVisible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value),
})

const imageUrls = computed(() => props.draft?.imageUrls || props.draft?.images || [])

const rules = {
  title: [{ required: true, message: '请填写案例标题', trigger: 'blur' }],
  summary: [{ required: true, message: '请填写案例摘要', trigger: 'blur' }],
  diagnosis: [{ required: true, message: '请填写诊断过程', trigger: 'blur' }],
  resolution: [{ required: true, message: '请填写处理方案', trigger: 'blur' }],
  experienceSummary: [{ required: true, message: '请填写经验总结', trigger: 'blur' }],
}

function normalizeTags(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join('，')
  return value || ''
}

function parseTags(value) {
  return String(value || '')
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function resetFromDraft() {
  const d = props.draft || {}
  form.title = d.title || d.caseTitle || ''
  form.summary = d.summary || ''
  form.diagnosis = d.diagnosis || d.diagnosisProcess || ''
  form.resolution = d.resolution || d.solution || ''
  form.result = d.result || d.repairResult || ''
  form.experienceSummary = d.experienceSummary || d.experience || ''
  form.tagsText = normalizeTags(d.tags)
  form.downtime = d.downtime ?? d.downTime ?? null
  form.cost = d.cost ?? null
  form.deviceId = d.deviceId ?? null
  form.faultName = d.faultName || d.faultDescription || ''
}

function buildDto() {
  const d = props.draft || {}
  return {
    sourceType: d.sourceType || 'TASK',
    sourceTaskId: d.sourceTaskId || d.taskId || d.maintenanceTaskId,
    deviceId: form.deviceId || undefined,
    faultName: form.faultName,
    imageUrls: imageUrls.value,
    title: form.title,
    summary: form.summary,
    diagnosis: form.diagnosis,
    resolution: form.resolution,
    result: form.result,
    experienceSummary: form.experienceSummary,
    // 后端 tags 是 String（逗号分隔），清洗分隔符后拼回字符串；发数组会触发 Jackson 反序列化报错
    tags: parseTags(form.tagsText).join(','),
    downtime: form.downtime,
    cost: form.cost,
  }
}

async function loadDevices(keyword = '') {
  deviceLoading.value = true
  try {
    const res = await searchDevices(keyword, 50)
    const data = res?.data?.records || res?.data?.list || res?.data || []
    devices.value = Array.isArray(data) ? data : []
  } catch (error) {
    ElMessage.warning('设备列表加载失败：' + (error.message || ''))
  } finally {
    deviceLoading.value = false
  }
}

async function handleSubmit() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  submitting.value = true
  try {
    await submitCase(buildDto())
    ElMessage.success('已提交，等待审核')
    emit('submitted')
    dialogVisible.value = false
  } catch (error) {
    ElMessage.error(error.message || '提交失败，请修改后重试')
  } finally {
    submitting.value = false
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      resetFromDraft()
      loadDevices()
    }
  },
)

watch(
  () => props.draft,
  () => {
    if (props.visible) resetFromDraft()
  },
)
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="沉淀为案例"
    width="760px"
    :close-on-click-modal="false"
    class="case-dialog"
  >
    <div class="case-draft-tip">
      <strong>AI 已根据任务生成案例草稿</strong>
      <span>请补充现场经验、修正表述后提交审核。</span>
    </div>

    <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" class="case-form">
      <el-form-item label="关联设备">
        <el-select
          v-model="form.deviceId"
          filterable
          remote
          clearable
          reserve-keyword
          :remote-method="loadDevices"
          :loading="deviceLoading"
          placeholder="可选择关联设备"
          style="width: 100%"
        >
          <el-option
            v-for="device in devices"
            :key="device.id"
            :label="device.name || device.deviceName || device.fullLabel"
            :value="device.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="故障名称">
        <el-input v-model="form.faultName" placeholder="如：液压泵异响 / 电机过热" />
      </el-form-item>

      <el-form-item label="案例标题" prop="title">
        <el-input v-model="form.title" maxlength="80" show-word-limit />
      </el-form-item>

      <el-form-item label="案例摘要" prop="summary">
        <el-input v-model="form.summary" type="textarea" :rows="3" />
      </el-form-item>

      <el-form-item label="诊断过程" prop="diagnosis">
        <el-input v-model="form.diagnosis" type="textarea" :rows="4" />
      </el-form-item>

      <el-form-item label="处理方案" prop="resolution">
        <el-input v-model="form.resolution" type="textarea" :rows="4" />
      </el-form-item>

      <el-form-item label="处理结果">
        <el-input v-model="form.result" type="textarea" :rows="3" />
      </el-form-item>

      <el-form-item label="经验总结" prop="experienceSummary">
        <el-input v-model="form.experienceSummary" type="textarea" :rows="4" />
      </el-form-item>

      <div class="case-inline">
        <el-form-item label="标签">
          <el-input v-model="form.tagsText" placeholder="多个标签用逗号分隔" />
        </el-form-item>
        <el-form-item label="停机时长">
          <el-input-number v-model="form.downtime" :min="0" :precision="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="维修成本">
          <el-input-number v-model="form.cost" :min="0" :precision="2" controls-position="right" />
        </el-form-item>
      </div>

      <el-form-item v-if="imageUrls.length" label="现场图片">
        <div class="case-images">
          <img v-for="(url, index) in imageUrls" :key="index" :src="url" alt="现场图片" />
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">提交审核</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.case-draft-tip {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--plaza-info-soft);
}

.case-draft-tip strong {
  color: var(--plaza-heading);
  font-weight: 700;
}

.case-draft-tip span {
  color: var(--plaza-text-muted);
  font-size: 13px;
}

.case-form {
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 8px;
}

.case-inline {
  display: grid;
  grid-template-columns: 1fr 190px 190px;
  gap: 10px;
}

.case-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.case-images img {
  width: 112px;
  height: 86px;
  object-fit: cover;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
}

@media (max-width: 760px) {
  .case-inline {
    grid-template-columns: 1fr;
  }
}
</style>
