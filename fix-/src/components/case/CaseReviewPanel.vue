<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveCase, getPendingCases, rejectCase } from '@/api/caseRecord'
import { searchDevices } from '@/api/graph'

const loading = ref(false)
const approving = ref(false)
const cases = ref([])
const selected = ref(null)
const devices = ref([])
const deviceLoading = ref(false)
const pagination = reactive({ page: 1, size: 10, total: 0 })

const form = reactive({
  id: null,
  title: '',
  summary: '',
  diagnosis: '',
  resolution: '',
  result: '',
  experienceSummary: '',
  tagsText: '',
  deviceId: null,
  faultName: '',
  downtime: null,
  cost: null,
  imageUrls: [],
})

const hasSelected = computed(() => !!selected.value)

function rows(res) {
  return res?.data?.records || res?.data?.list || res?.data || []
}

function total(res, fallback) {
  return res?.data?.total ?? fallback
}

function normalizeTags(tags) {
  if (Array.isArray(tags)) return tags.filter(Boolean).join('，')
  return tags || ''
}

function parseTags(text) {
  return String(text || '')
    .split(/[，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function fillForm(item) {
  selected.value = item
  form.id = item.id
  form.title = item.title || item.caseTitle || ''
  form.summary = item.summary || ''
  form.diagnosis = item.diagnosis || item.diagnosisProcess || ''
  form.resolution = item.resolution || item.solution || ''
  form.result = item.result || item.repairResult || ''
  form.experienceSummary = item.experienceSummary || item.experience || ''
  form.tagsText = normalizeTags(item.tags)
  form.deviceId = item.deviceId ?? null
  form.faultName = item.faultName || item.faultDescription || ''
  form.downtime = item.downtime ?? item.downTime ?? null
  form.cost = item.cost ?? null
  form.imageUrls = item.imageUrls || item.images || []
}

function buildDto() {
  return {
    title: form.title,
    summary: form.summary,
    diagnosis: form.diagnosis,
    resolution: form.resolution,
    result: form.result,
    experienceSummary: form.experienceSummary,
    // 后端 tags 是 String（逗号分隔），清洗分隔符后拼回字符串；发数组会触发 Jackson 反序列化报错
    tags: parseTags(form.tagsText).join(','),
    deviceId: form.deviceId || undefined,
    faultName: form.faultName,
    downtime: form.downtime,
    cost: form.cost,
    imageUrls: form.imageUrls,
  }
}

async function loadCases(page = pagination.page) {
  loading.value = true
  pagination.page = page
  try {
    const res = await getPendingCases(pagination.page, pagination.size)
    const list = rows(res)
    cases.value = Array.isArray(list) ? list : []
    pagination.total = total(res, cases.value.length)
    if (cases.value.length) fillForm(cases.value[0])
    else selected.value = null
  } catch (error) {
    ElMessage.error('加载待审案例失败：' + (error.message || ''))
  } finally {
    loading.value = false
  }
}

async function loadDevices(keyword = '') {
  deviceLoading.value = true
  try {
    const res = await searchDevices(keyword, 50)
    const list = rows(res)
    devices.value = Array.isArray(list) ? list : []
  } catch (error) {
    ElMessage.warning('设备列表加载失败：' + (error.message || ''))
  } finally {
    deviceLoading.value = false
  }
}

async function handleApprove() {
  if (!form.id) return
  approving.value = true
  try {
    await approveCase(form.id, buildDto())
    ElMessage.success('案例已通过审核')
    await loadCases()
  } catch (error) {
    ElMessage.error(error.message || '通过失败，请稍后重试')
  } finally {
    approving.value = false
  }
}

async function handleReject() {
  if (!form.id) return
  try {
    const { value } = await ElMessageBox.prompt('请输入驳回意见，工人会在「我的案例」中看到。', '驳回案例', {
      inputType: 'textarea',
      inputPlaceholder: '例如：案例经验描述过于笼统，请补充现场排查依据。',
      confirmButtonText: '确认驳回',
      cancelButtonText: '取消',
      inputValidator: (value) => !!String(value || '').trim() || '请填写驳回意见',
    })
    await rejectCase(form.id, value)
    ElMessage.success('已驳回案例')
    await loadCases()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || '驳回失败')
  }
}

watch(hasSelected, (value) => {
  if (value) loadDevices()
})

loadCases()
</script>

<template>
  <section class="case-review">
    <header class="review-head">
      <div>
        <h2>案例审核</h2>
        <p>审核工人从已完成任务中沉淀的经验案例，可先修正 AI 草稿再通过。</p>
      </div>
      <el-button :loading="loading" @click="loadCases()">刷新</el-button>
    </header>

    <div class="review-layout">
      <aside class="case-list" v-loading="loading">
        <button
          v-for="item in cases"
          :key="item.id"
          type="button"
          class="case-list-item"
          :class="{ active: selected?.id === item.id }"
          @click="fillForm(item)"
        >
          <strong>{{ item.title || item.caseTitle || '未命名案例' }}</strong>
          <span>{{ item.faultName || item.deviceName || '待补充故障信息' }}</span>
        </button>
        <div v-if="!cases.length && !loading" class="empty-list">暂无待审核案例</div>
        <el-pagination
          v-if="pagination.total > pagination.size"
          v-model:current-page="pagination.page"
          small
          layout="prev, pager, next"
          :page-size="pagination.size"
          :total="pagination.total"
          @current-change="loadCases"
        />
      </aside>

      <main class="review-form">
        <div v-if="!selected" class="empty-detail">请选择左侧案例进行审核</div>
        <template v-else>
          <el-form label-width="96px">
            <el-form-item label="关联设备">
              <el-select
                v-model="form.deviceId"
                filterable
                remote
                clearable
                :remote-method="loadDevices"
                :loading="deviceLoading"
                placeholder="选择设备"
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
            <el-form-item label="故障名称"><el-input v-model="form.faultName" /></el-form-item>
            <el-form-item label="案例标题"><el-input v-model="form.title" /></el-form-item>
            <el-form-item label="案例摘要"><el-input v-model="form.summary" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="诊断过程"><el-input v-model="form.diagnosis" type="textarea" :rows="4" /></el-form-item>
            <el-form-item label="处理方案"><el-input v-model="form.resolution" type="textarea" :rows="4" /></el-form-item>
            <el-form-item label="处理结果"><el-input v-model="form.result" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="经验总结"><el-input v-model="form.experienceSummary" type="textarea" :rows="4" /></el-form-item>
            <div class="review-grid">
              <el-form-item label="标签"><el-input v-model="form.tagsText" /></el-form-item>
              <el-form-item label="停机时长"><el-input-number v-model="form.downtime" :min="0" :precision="1" /></el-form-item>
              <el-form-item label="维修成本"><el-input-number v-model="form.cost" :min="0" :precision="2" /></el-form-item>
            </div>
            <el-form-item v-if="form.imageUrls.length" label="图片">
              <div class="review-images">
                <img v-for="(url, index) in form.imageUrls" :key="index" :src="url" alt="案例图片" />
              </div>
            </el-form-item>
          </el-form>

          <div class="review-actions">
            <el-button type="danger" plain @click="handleReject">驳回</el-button>
            <el-button type="primary" :loading="approving" @click="handleApprove">修正后通过</el-button>
          </div>
        </template>
      </main>
    </div>
  </section>
</template>

<style scoped>
.case-review {
  min-height: 520px;
}

.review-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.review-head h2 {
  color: var(--plaza-heading);
  font-size: 20px;
  font-weight: 800;
}

.review-head p {
  margin-top: 6px;
  color: var(--plaza-text-muted);
  font-size: 13px;
}

.review-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 16px;
  align-items: start;
}

.case-list,
.review-form {
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: #fff;
  box-shadow: var(--plaza-shadow-organic);
}

.case-list {
  min-height: 360px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.case-list-item {
  width: 100%;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--plaza-bg);
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.case-list-item:hover,
.case-list-item.active {
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.case-list-item strong,
.case-list-item span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-list-item strong {
  color: var(--plaza-heading);
  font-weight: 700;
}

.case-list-item span {
  margin-top: 4px;
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.empty-list,
.empty-detail {
  color: var(--plaza-text-muted);
  text-align: center;
  padding: 80px 0;
}

.review-form {
  padding: 18px 18px 14px;
}

.review-grid {
  display: grid;
  grid-template-columns: 1fr 190px 190px;
  gap: 8px;
}

.review-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.review-images img {
  width: 112px;
  height: 86px;
  object-fit: cover;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
}

.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--plaza-border);
}

@media (max-width: 980px) {
  .review-layout {
    grid-template-columns: 1fr;
  }

  .review-grid {
    grid-template-columns: 1fr;
  }
}
</style>
