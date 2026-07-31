<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { Check, Close, Refresh, Search, View } from '@element-plus/icons-vue'
import {
  convertAnswerFeedback,
  dismissAnswerFeedback,
  getAnswerFeedbackDetail,
  getAnswerFeedbackPage,
} from '@/api/answerFeedback'

const router = useRouter()

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const filters = reactive({ page: 1, size: 10, status: 'pending', keyword: '', deviceType: '' })
const convertOpen = ref(false)
const converting = ref(false)
const selected = ref(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const form = reactive({
  title: '',
  deviceType: '',
  symptomText: '',
  conditionText: '',
  correctedAnswer: '',
  reviewComment: '',
})

const reasonLabels = {
  incorrect: '内容错误',
  incomplete: '信息缺失',
  source_error: '引用错误',
  order_error: '步骤有误',
  other: '其他',
}
const statusLabels = { pending: '待处理', converted: '已转规则', dismissed: '已忽略' }

async function loadPage() {
  loading.value = true
  try {
    const response = await getAnswerFeedbackPage(filters)
    rows.value = response.data?.records || []
    total.value = Number(response.data?.total || 0)
  } catch (error) {
    ElMessage.error(error?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function search() {
  filters.page = 1
  loadPage()
}

function openConvert(row) {
  selected.value = row
  form.title = `纠错：${String(row.originalQuestion || '').slice(0, 40)}`
  form.deviceType = row.deviceType || ''
  form.symptomText = row.originalQuestion || ''
  form.conditionText = row.originalQuestion || ''
  form.correctedAnswer = ''
  form.reviewComment = row.userComment || ''
  convertOpen.value = true
}

async function openDetail(row) {
  detail.value = row
  detailOpen.value = true
  detailLoading.value = true
  try {
    const response = await getAnswerFeedbackDetail(row.id)
    detail.value = response.data || row
  } catch (error) {
    ElMessage.error(error?.message || '加载反馈详情失败')
  } finally {
    detailLoading.value = false
  }
}

function openRuleDetail(ruleId) {
  if (!ruleId) return
  detailOpen.value = false
  router.push({
    path: '/admin/knowledge-center',
    query: { tab: 'domain-rules', ruleId: String(ruleId) },
  })
}

function symptomKeys() {
  return [...new Set(form.symptomText.split(/[，,、\n]/).map((item) => item.trim()).filter(Boolean))]
}

async function convertToRule() {
  if (!form.title.trim() || !symptomKeys().length || !form.conditionText.trim() || !form.correctedAnswer.trim()) {
    ElMessage.warning('请填写规则标题、症状关键词、命中条件和修订答案')
    return
  }
  converting.value = true
  try {
    await convertAnswerFeedback(selected.value.id, {
      title: form.title.trim(),
      deviceType: form.deviceType.trim(),
      symptomKeys: symptomKeys(),
      conditionText: form.conditionText.trim(),
      correctedAnswer: form.correctedAnswer.trim(),
      reviewComment: form.reviewComment.trim(),
      evidenceRefs: [],
    })
    convertOpen.value = false
    ElMessage.success('规则草稿已创建，请在诊断规则中提交审核')
    loadPage()
  } catch (error) {
    ElMessage.error(error?.message || '转换失败')
  } finally {
    converting.value = false
  }
}

async function dismiss(row) {
  try {
    const { value } = await ElMessageBox.prompt('填写忽略原因', '忽略反馈', {
      confirmButtonText: '确认忽略',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputPlaceholder: '例如：重复反馈、无法复现或不适合沉淀为规则',
    })
    await dismissAnswerFeedback(row.id, value || '')
    ElMessage.success('已忽略')
    loadPage()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.message || '操作失败')
  }
}

onMounted(loadPage)
</script>

<template>
  <section class="feedback-page">
    <header class="feedback-toolbar">
      <div>
        <h3>回答纠错</h3>
        <p>待处理 {{ total }} 条</p>
      </div>
      <div class="filters">
        <el-select v-model="filters.status" aria-label="处理状态" @change="search">
          <el-option label="待处理" value="pending" />
          <el-option label="已转规则" value="converted" />
          <el-option label="已忽略" value="dismissed" />
        </el-select>
        <el-input v-model="filters.keyword" clearable placeholder="搜索问题或回答" @keyup.enter="search">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button :icon="Refresh" :loading="loading" title="刷新" @click="loadPage" />
      </div>
    </header>

    <el-table v-loading="loading" :data="rows" row-key="id" empty-text="暂无反馈">
      <el-table-column label="问题" min-width="240" show-overflow-tooltip prop="originalQuestion" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">{{ reasonLabels[row.reasonCode] || row.reasonCode }}</template>
      </el-table-column>
      <el-table-column label="设备" width="150" show-overflow-tooltip prop="deviceType" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }"><el-tag effect="plain">{{ statusLabels[row.status] || row.status }}</el-tag></template>
      </el-table-column>
      <el-table-column label="提交时间" width="170" prop="createdAt" />
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button link :icon="View" @click="openDetail(row)">查看详情</el-button>
          <el-button v-if="row.status === 'pending'" link type="primary" :icon="Check" @click="openConvert(row)">转为规则草稿</el-button>
          <el-button v-if="row.status === 'pending'" link type="danger" :icon="Close" @click="dismiss(row)">忽略</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="filters.page"
      v-model:page-size="filters.size"
      class="pagination"
      layout="total, prev, pager, next"
      :total="total"
      @current-change="loadPage"
    />

    <el-dialog v-model="convertOpen" title="人工修订并转为规则草稿" width="min(760px, calc(100vw - 28px))" append-to-body>
      <div v-if="selected" class="original-content">
        <dl>
          <dt>用户问题</dt><dd>{{ selected.originalQuestion }}</dd>
          <dt>原回答</dt><dd>{{ selected.originalAnswer }}</dd>
          <dt>反馈说明</dt><dd>{{ selected.userComment || '未补充' }}</dd>
        </dl>
      </div>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="规则标题"><el-input v-model="form.title" maxlength="200" /></el-form-item>
          <el-form-item label="设备类型"><el-input v-model="form.deviceType" /></el-form-item>
        </div>
        <el-form-item label="症状关键词">
          <el-input v-model="form.symptomText" placeholder="使用逗号分隔" />
        </el-form-item>
        <el-form-item label="命中条件"><el-input v-model="form.conditionText" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="人工修订答案"><el-input v-model="form.correctedAnswer" type="textarea" :rows="5" /></el-form-item>
        <el-form-item label="审核备注"><el-input v-model="form.reviewComment" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="convertOpen = false">取消</el-button>
        <el-button type="primary" :loading="converting" @click="convertToRule">创建草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" title="反馈详情" size="560px" append-to-body>
      <div v-if="detail" v-loading="detailLoading" class="feedback-detail">
        <div class="detail-tags">
          <el-tag effect="plain">{{ statusLabels[detail.status] || detail.status }}</el-tag>
          <el-tag type="info" effect="plain">{{ reasonLabels[detail.reasonCode] || detail.reasonCode }}</el-tag>
        </div>
        <dl class="detail-list">
          <dt>原问题</dt>
          <dd>{{ detail.originalQuestion || '-' }}</dd>
          <dt>原回答</dt>
          <dd>{{ detail.originalAnswer || '-' }}</dd>
          <dt>反馈类型</dt>
          <dd>{{ reasonLabels[detail.reasonCode] || detail.reasonCode || '-' }}</dd>
          <dt>反馈说明</dt>
          <dd>{{ detail.userComment || '-' }}</dd>
          <dt>设备范围</dt>
          <dd>{{ detail.deviceType || '-' }}</dd>
          <dt>文档范围</dt>
          <dd>{{ detail.documentId || '-' }}</dd>
          <dt>修订答案</dt>
          <dd>{{ detail.correctedAnswer || '-' }}</dd>
          <dt>处理说明</dt>
          <dd>{{ detail.processComment || '-' }}</dd>
          <dt>处理人</dt>
          <dd>{{ detail.processedById || '-' }}</dd>
          <dt>处理时间</dt>
          <dd>{{ detail.processedAt || '-' }}</dd>
          <dt>关联规则 ID</dt>
          <dd>
            <el-button
              v-if="detail.domainRuleId"
              link
              type="primary"
              @click="openRuleDetail(detail.domainRuleId)"
            >
              {{ detail.domainRuleId }}
            </el-button>
            <span v-else>-</span>
          </dd>
        </dl>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.feedback-page { min-width: 0; }
.feedback-toolbar { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.feedback-toolbar h3 { margin: 0; color: var(--plaza-heading); font-size: 18px; }
.feedback-toolbar p { margin: 4px 0 0; color: var(--plaza-text-muted); font-size: 13px; }
.filters { display: flex; align-items: center; gap: 8px; }
.filters .el-select { width: 120px; }
.filters .el-input { width: 220px; }
.pagination { justify-content: flex-end; margin-top: 16px; }
.original-content { max-height: 260px; margin-bottom: 16px; overflow: auto; border-bottom: 1px solid var(--plaza-border); }
.original-content dl { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px 12px; margin: 0 0 16px; }
.original-content dt { color: var(--plaza-text-muted); font-size: 12px; font-weight: 700; }
.original-content dd { margin: 0; white-space: pre-wrap; word-break: break-word; line-height: 1.55; }
.form-grid { display: grid; grid-template-columns: 1fr 220px; gap: 12px; }
.feedback-detail { min-height: 160px; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
.detail-list { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 12px 14px; margin: 0; }
.detail-list dt { color: var(--plaza-text-muted); font-size: 13px; font-weight: 700; }
.detail-list dd { min-width: 0; margin: 0; color: var(--plaza-text); line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
@media (max-width: 760px) {
  .feedback-toolbar { align-items: stretch; flex-direction: column; }
  .filters { flex-wrap: wrap; }
  .filters .el-input { flex: 1 1 180px; width: auto; }
  .form-grid { grid-template-columns: 1fr; }
  .detail-list { grid-template-columns: 1fr; gap: 4px; }
  .detail-list dd { margin-bottom: 10px; }
}
</style>
