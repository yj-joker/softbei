<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Aim,
  Check,
  Close,
  Edit,
  Plus,
  Refresh,
  Search,
  SwitchButton,
  View,
  WarningFilled,
} from '@element-plus/icons-vue'
import {
  approveDomainRule,
  createDomainRule,
  disableDomainRule,
  getDomainRuleDetail,
  getDomainRulePage,
  rejectDomainRule,
  retrySyncDomainRule,
  submitDomainRule,
  updateDomainRule,
} from '@/api/domainRule'

const STATUS = {
  draft: { label: '草稿', type: 'info', color: 'var(--plaza-text-muted)', bar: 'var(--plaza-text-muted)' },
  pending: { label: '待审核', type: 'warning', color: '#b7791f', bar: '#f59e0b' },
  active: { label: '已发布', type: 'success', color: 'var(--plaza-accent)', bar: 'var(--plaza-accent)' },
  disabled: { label: '已禁用', type: 'info', color: '#64748b', bar: '#94a3b8' },
  rejected: { label: '已驳回', type: 'danger', color: '#dc2626', bar: '#ef4444' },
}

const SYNC_STATUS = {
  not_synced: { label: '未同步', type: 'info' },
  syncing: { label: '同步中', type: 'warning' },
  synced: { label: '已同步', type: 'success' },
  failed: { label: '同步失败', type: 'danger' },
}

const statusTabs = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'pending', label: '待审核' },
  { key: 'active', label: '已发布' },
  { key: 'rejected', label: '已驳回' },
  { key: 'disabled', label: '已禁用' },
]

const loading = ref(false)
const rules = ref([])
const stats = reactive({ draft: 0, pending: 0, active: 0, rejected: 0, disabled: 0, failed: 0 })
const pagination = reactive({ page: 1, size: 10, total: 0 })

const filters = reactive({
  status: '',
  deviceType: '',
  keyword: '',
})

const formRef = ref(null)
const dialog = reactive({
  show: false,
  mode: 'create',
  title: '新建诊断规则',
  saving: false,
})

const form = reactive({
  id: null,
  title: '',
  deviceType: '',
  symptomText: '',
  symptomKeys: [],
  conditionText: '',
  conclusion: '',
  question: '',
  options: [],
  evidenceRefsText: '[]',
  reviewComment: '',
})

const tagInput = ref('')
const optionInput = ref('')
const advancedPanels = ref([])

const detailDrawer = reactive({
  show: false,
  loading: false,
  data: null,
})

const formRules = {
  title: [{ required: true, message: '请填写规则标题', trigger: 'blur' }],
  deviceType: [{ required: true, message: '请填写设备类型', trigger: 'blur' }],
  symptomText: [{ required: true, message: '请填写故障现象', trigger: 'blur' }],
  conclusion: [{ required: true, message: '请填写专家判断', trigger: 'blur' }],
}

const autoConditionText = computed(() => {
  const device = form.deviceType.trim() || '当前设备'
  const symptoms = normalizeSymptomKeys()
  const symptomLabel = symptoms.length ? symptoms.join('、') : '录入故障现象'
  return `当设备类型为${device}，且故障现象包含${symptomLabel}时，命中该专家规则。`
})

const activeStatusInfo = computed(() => STATUS[filters.status] || { label: '全部规则' })

function responseList(data) {
  return data?.records || data?.list || data?.rows || []
}

function responseTotal(data) {
  return Number(data?.total ?? data?.totalCount ?? 0)
}

async function loadList(page = 1) {
  loading.value = true
  pagination.page = page
  try {
    const params = {
      page,
      size: pagination.size,
      status: filters.status || undefined,
      deviceType: filters.deviceType || undefined,
      keyword: filters.keyword || undefined,
    }
    const res = await getDomainRulePage(params)
    rules.value = responseList(res.data)
    pagination.total = responseTotal(res.data)
    await refreshStats()
  } catch (error) {
    ElMessage.error(`加载诊断规则失败：${error.message || '请稍后重试'}`)
  } finally {
    loading.value = false
  }
}

async function refreshStats() {
  try {
    const [draftRes, pendingRes, activeRes, rejectedRes, disabledRes, allRes] = await Promise.all([
      getDomainRulePage({ page: 1, size: 1, status: 'draft' }),
      getDomainRulePage({ page: 1, size: 1, status: 'pending' }),
      getDomainRulePage({ page: 1, size: 1, status: 'active' }),
      getDomainRulePage({ page: 1, size: 1, status: 'rejected' }),
      getDomainRulePage({ page: 1, size: 1, status: 'disabled' }),
      getDomainRulePage({ page: 1, size: 1000 }),
    ])
    stats.draft = responseTotal(draftRes.data)
    stats.pending = responseTotal(pendingRes.data)
    stats.active = responseTotal(activeRes.data)
    stats.rejected = responseTotal(rejectedRes.data)
    stats.disabled = responseTotal(disabledRes.data)
    stats.failed = responseList(allRes.data).filter((item) => item.syncStatus === 'failed').length
  } catch {
    // 统计失败不影响列表主流程。
  }
}

function switchStatus(status) {
  filters.status = status
  loadList(1)
}

function resetForm() {
  form.id = null
  form.title = ''
  form.deviceType = ''
  form.symptomText = ''
  form.symptomKeys = []
  form.conditionText = ''
  form.conclusion = ''
  form.question = ''
  form.options = []
  form.evidenceRefsText = '[]'
  form.reviewComment = ''
  tagInput.value = ''
  optionInput.value = ''
  advancedPanels.value = []
}

function fillForm(rule) {
  form.id = rule.id
  form.title = rule.title || ''
  form.deviceType = rule.deviceType || ''
  form.symptomKeys = [...(rule.symptomKeys || [])]
  form.symptomText = form.symptomKeys.join('、')
  form.conditionText = rule.conditionText || ''
  form.conclusion = rule.conclusion || ''
  form.question = rule.question || ''
  form.options = [...(rule.options || [])]
  form.evidenceRefsText = formatEvidenceRefs(rule.evidenceRefs || [])
  form.reviewComment = rule.reviewComment || ''
}

function openCreate() {
  resetForm()
  dialog.mode = 'create'
  dialog.title = '新建专家经验规则'
  dialog.show = true
}

async function openEdit(rule) {
  resetForm()
  dialog.mode = 'edit'
  dialog.title = '编辑专家经验规则'
  dialog.show = true
  try {
    const res = await getDomainRuleDetail(rule.id)
    fillForm(res.data || rule)
  } catch {
    fillForm(rule)
  }
}

function addTag() {
  const value = tagInput.value.trim()
  if (!value) return
  if (!form.symptomKeys.includes(value)) form.symptomKeys.push(value)
  tagInput.value = ''
}

function addOption() {
  const value = optionInput.value.trim()
  if (!value) return
  if (!form.options.includes(value)) form.options.push(value)
  optionInput.value = ''
}

function removeTag(index) {
  form.symptomKeys.splice(index, 1)
}

function removeOption(index) {
  form.options.splice(index, 1)
}

function uniqueText(values) {
  const result = []
  const seen = new Set()
  values.forEach((item) => {
    const text = String(item || '').trim()
    if (!text || seen.has(text)) return
    result.push(text)
    seen.add(text)
  })
  return result
}

function splitInputText(value) {
  return uniqueText(String(value || '').split(/[\n\r,，、;；]+/))
}

function normalizeSymptomKeys() {
  return uniqueText([
    ...splitInputText(form.symptomText),
    ...form.symptomKeys,
  ])
}

function formatEvidenceRefs(refs) {
  if (!Array.isArray(refs) || refs.length === 0) return ''
  return refs.map((ref) => {
    if (ref?.text) return ref.text
    return [ref?.source, ref?.section, ref?.page ? `第${ref.page}页` : '']
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .join(' ｜ ')
  }).filter(Boolean).join('\n')
}

function parseEvidenceRefs(value) {
  const text = String(value || '').trim()
  if (!text) return []
  if (text.startsWith('[')) {
    const parsed = JSON.parse(text)
    if (!Array.isArray(parsed)) {
      throw new Error('evidence refs must be an array')
    }
    return parsed
  }
  return text.split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/[|｜]/).map((part) => part.trim()).filter(Boolean)
      if (parts.length >= 2) {
        const pageMatch = parts[2]?.match(/\d+/)
        return {
          source: parts[0],
          section: parts[1],
          ...(pageMatch ? { page: Number(pageMatch[0]) } : {}),
        }
      }
      return { text: line }
    })
}

function buildPayload() {
  let evidenceRefs = []
  try {
    evidenceRefs = parseEvidenceRefs(form.evidenceRefsText)
  } catch {
    ElMessage.warning('证据引用格式不正确')
    return null
  }

  const symptomKeys = normalizeSymptomKeys()
  if (!symptomKeys.length) {
    ElMessage.warning('请填写故障现象')
    return null
  }

  return {
    title: form.title.trim(),
    deviceType: form.deviceType.trim(),
    symptomKeys,
    conditionText: form.conditionText.trim() || autoConditionText.value,
    conclusion: form.conclusion.trim(),
    question: form.question.trim() || undefined,
    options: form.options.map((item) => item.trim()).filter(Boolean),
    evidenceRefs,
    reviewComment: form.reviewComment.trim() || undefined,
  }
}

async function submitForm() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  const payload = buildPayload()
  if (!payload) return

  dialog.saving = true
  try {
    if (dialog.mode === 'create') {
      await createDomainRule(payload)
      ElMessage.success('诊断规则已创建')
    } else {
      await updateDomainRule(form.id, payload)
      ElMessage.success('诊断规则已更新')
    }
    dialog.show = false
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`保存失败：${error.message || '请稍后重试'}`)
  } finally {
    dialog.saving = false
  }
}

async function handleSubmit(rule) {
  try {
    await ElMessageBox.confirm(`确认提交《${rule.title}》进入专家审核？`, '提交审核', {
      confirmButtonText: '提交',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await submitDomainRule(rule.id)
    ElMessage.success('已提交审核')
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`提交失败：${error.message || '请稍后重试'}`)
  }
}

async function handleApprove(rule) {
  try {
    await ElMessageBox.confirm(`确认发布《${rule.title}》？发布后会同步到 Python 诊断规则库。`, '审批发布', {
      confirmButtonText: '发布',
      cancelButtonText: '取消',
      type: 'success',
    })
  } catch {
    return
  }

  try {
    await approveDomainRule(rule.id, { reviewComment: '前端审批通过' })
    ElMessage.success('规则已审批发布')
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`审批失败：${error.message || '请稍后重试'}`)
  }
}

async function handleReject(rule) {
  let comment = ''
  try {
    const result = await ElMessageBox.prompt('请填写驳回原因，便于规则创建者修正。', '驳回规则', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：命中条件不够明确，需要补充排除条件',
      inputValidator: (value) => !!value?.trim() || '请填写驳回原因',
      type: 'warning',
    })
    comment = result.value.trim()
  } catch {
    return
  }

  try {
    await rejectDomainRule(rule.id, comment)
    ElMessage.success('规则已驳回')
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`驳回失败：${error.message || '请稍后重试'}`)
  }
}

async function handleDisable(rule) {
  try {
    await ElMessageBox.confirm(`确认禁用《${rule.title}》？禁用后不再参与前置诊断命中。`, '禁用规则', {
      confirmButtonText: '禁用',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await disableDomainRule(rule.id)
    ElMessage.success('规则已禁用')
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`禁用失败：${error.message || '请稍后重试'}`)
  }
}

async function handleRetrySync(rule) {
  try {
    await retrySyncDomainRule(rule.id)
    ElMessage.success('已发起同步重试')
    loadList(pagination.page)
  } catch (error) {
    ElMessage.error(`同步重试失败：${error.message || '请稍后重试'}`)
  }
}

async function openDetail(rule) {
  detailDrawer.show = true
  detailDrawer.loading = true
  detailDrawer.data = rule
  try {
    const res = await getDomainRuleDetail(rule.id)
    detailDrawer.data = res.data || rule
  } catch {
    detailDrawer.data = rule
  } finally {
    detailDrawer.loading = false
  }
}

function statusInfo(status) {
  return STATUS[status] || { label: status || '-', type: 'info', color: 'var(--plaza-text-muted)', bar: 'var(--plaza-text-muted)' }
}

function syncInfo(status) {
  return SYNC_STATUS[status] || { label: status || '-', type: 'info' }
}

function canEdit(rule) {
  return ['draft', 'pending', 'rejected'].includes(rule.status)
}

function canSubmit(rule) {
  return ['draft', 'rejected'].includes(rule.status)
}

function canReview(rule) {
  return rule.status === 'pending'
}

function formatDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(() => loadList(1))
</script>

<template>
  <div class="ap-root domain-rules">
    <div class="ap-head">
      <div class="ap-title-row">
        <span class="ap-title-led" />
        <h2 class="ap-title-text">诊断规则</h2>
        <span class="ap-title-sub">HYBRID&nbsp;DIAGNOSTIC&nbsp;RULES</span>
      </div>
      <button class="ap-create-btn" type="button" @click="openCreate">
        <el-icon><Plus /></el-icon>
        新建规则
      </button>
    </div>

    <div class="ap-stats">
      <div class="stat-card" @click="switchStatus('draft')">
        <span class="stat-num">{{ stats.draft }}</span>
        <span class="stat-label">草稿规则</span>
        <span class="stat-bar draft" />
      </div>
      <div class="stat-card" @click="switchStatus('pending')">
        <span class="stat-num">{{ stats.pending }}</span>
        <span class="stat-label">待专家审核</span>
        <span class="stat-bar pending" />
      </div>
      <div class="stat-card" @click="switchStatus('active')">
        <span class="stat-num">{{ stats.active }}</span>
        <span class="stat-label">已发布规则</span>
        <span class="stat-bar active" />
      </div>
      <div class="stat-card" @click="switchStatus('rejected')">
        <span class="stat-num">{{ stats.rejected }}</span>
        <span class="stat-label">已驳回规则</span>
        <span class="stat-bar rejected" />
      </div>
      <div class="stat-card" @click="switchStatus('disabled')">
        <span class="stat-num">{{ stats.disabled }}</span>
        <span class="stat-label">已禁用规则</span>
        <span class="stat-bar disabled" />
      </div>
      <div class="stat-card sync-failed" @click="filters.status = ''; loadList(1)">
        <span class="stat-num">{{ stats.failed }}</span>
        <span class="stat-label">同步失败</span>
        <span class="stat-bar failed" />
      </div>
    </div>

    <div class="ap-filters">
      <div class="status-tabs">
        <button
          v-for="tab in statusTabs"
          :key="tab.key"
          class="st-tab"
          :class="{ active: filters.status === tab.key }"
          type="button"
          @click="switchStatus(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
      <div class="filter-right">
        <el-input
          v-model="filters.keyword"
          clearable
          placeholder="搜索标题、编号、结论"
          style="width: 220px"
          @keyup.enter="loadList(1)"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-input
          v-model="filters.deviceType"
          clearable
          placeholder="设备类型"
          style="width: 150px"
          @keyup.enter="loadList(1)"
        />
        <el-button type="primary" @click="loadList(1)">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
      </div>
    </div>

    <div class="rule-scope">
      <el-icon><Aim /></el-icon>
      <span>当前视图：{{ activeStatusInfo.label }}</span>
      <span class="scope-tip">规则命中用于“事前确定性诊断”，未命中时再升级到 RAG 和 LLM。</span>
    </div>

    <div v-loading="loading" class="ap-grid-wrap">
      <el-empty v-if="!loading && rules.length === 0" description="暂无诊断规则" />

      <div v-else class="rule-grid">
        <article
          v-for="rule in rules"
          :key="rule.id"
          class="rule-card"
        >
          <span class="card-bar" :style="{ background: statusInfo(rule.status).bar }" />
          <div class="rule-card-main">
            <div class="rule-card-top">
              <div class="rule-title-wrap">
                <h3 class="rule-title">{{ rule.title || '未命名规则' }}</h3>
                <span class="rule-code">{{ rule.ruleCode || `#${rule.id}` }}</span>
              </div>
              <div class="rule-tags">
                <el-tag :type="statusInfo(rule.status).type" size="small">{{ statusInfo(rule.status).label }}</el-tag>
                <el-tag :type="syncInfo(rule.syncStatus).type" size="small" effect="plain">{{ syncInfo(rule.syncStatus).label }}</el-tag>
              </div>
            </div>

            <div class="rule-meta">
              <span>{{ rule.deviceType || '未填写设备' }}</span>
              <span>{{ formatDate(rule.updatedAt || rule.createdAt) }}</span>
            </div>

            <div class="symptom-list">
              <span v-for="item in rule.symptomKeys || []" :key="item" class="symptom-chip">{{ item }}</span>
              <span v-if="!rule.symptomKeys?.length" class="symptom-empty">暂无症状词</span>
            </div>

            <p class="rule-conclusion">{{ rule.conclusion || '暂无诊断结论' }}</p>

            <div v-if="rule.syncStatus === 'failed'" class="sync-error">
              <el-icon><WarningFilled /></el-icon>
              <span>{{ rule.syncError || '同步失败，请查看后端日志或重试同步。' }}</span>
            </div>
          </div>

          <div class="rule-actions">
            <button class="action-btn" type="button" title="查看" @click="openDetail(rule)">
              <el-icon><View /></el-icon>
              查看
            </button>
            <button v-if="canEdit(rule)" class="action-btn" type="button" title="编辑" @click="openEdit(rule)">
              <el-icon><Edit /></el-icon>
              编辑
            </button>
            <button v-if="canSubmit(rule)" class="action-btn success" type="button" title="提交审核" @click="handleSubmit(rule)">
              <el-icon><Check /></el-icon>
              提交
            </button>
            <button v-if="canReview(rule)" class="action-btn success" type="button" title="审批发布" @click="handleApprove(rule)">
              <el-icon><Check /></el-icon>
              通过
            </button>
            <button v-if="canReview(rule)" class="action-btn danger" type="button" title="驳回" @click="handleReject(rule)">
              <el-icon><Close /></el-icon>
              驳回
            </button>
            <button v-if="rule.status === 'active'" class="action-btn danger" type="button" title="禁用" @click="handleDisable(rule)">
              <el-icon><SwitchButton /></el-icon>
              禁用
            </button>
            <button v-if="['failed', 'syncing'].includes(rule.syncStatus)" class="action-btn" type="button" title="重试同步" @click="handleRetrySync(rule)">
              <el-icon><Refresh /></el-icon>
              同步
            </button>
          </div>
        </article>
      </div>
    </div>

    <div v-if="pagination.total > pagination.size" class="ap-pager">
      <el-pagination
        v-model:current-page="pagination.page"
        :page-size="pagination.size"
        :total="pagination.total"
        layout="prev, pager, next, total"
        @current-change="loadList"
      />
    </div>

    <el-dialog
      v-model="dialog.show"
      class="rule-form-dialog"
      :title="dialog.title"
      width="760px"
      :close-on-click-modal="false"
      append-to-body
      align-center
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-position="top" @submit.prevent>
        <div class="rule-form-shell">
          <section class="rule-form-section">
            <div class="form-section-title">专家经验录入</div>
            <div class="form-section-subtitle">核心信息</div>

            <el-row :gutter="16">
              <el-col :xs="24" :sm="13">
                <el-form-item label="规则名称" prop="title">
                  <el-input v-model="form.title" maxlength="80" show-word-limit placeholder="例如：发动机冒蓝烟伴随烧机油" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="11">
                <el-form-item label="适用设备" prop="deviceType">
                  <el-input v-model="form.deviceType" placeholder="例如：摩托车发动机" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item label="故障现象" prop="symptomText">
              <el-input
                v-model="form.symptomText"
                type="textarea"
                :rows="2"
                placeholder="例如：冒蓝烟、烧机油，冷却液没有明显减少"
              />
            </el-form-item>

            <el-form-item label="专家判断" prop="conclusion">
              <el-input
                v-model="form.conclusion"
                type="textarea"
                :rows="4"
                placeholder="例如：优先检查活塞环磨损或气门油封老化，并确认蓝烟出现时机"
              />
            </el-form-item>

            <div class="condition-preview">
              <span class="preview-label">自动命中条件</span>
              <span>{{ form.conditionText.trim() || autoConditionText }}</span>
            </div>
          </section>

          <el-collapse v-model="advancedPanels" class="advanced-rule-collapse">
            <el-collapse-item name="advanced">
              <template #title>
                <span class="advanced-title">更多规则设置</span>
              </template>

              <div class="advanced-fields">
                <el-form-item label="精确命中条件">
                  <el-input
                    v-model="form.conditionText"
                    type="textarea"
                    :rows="3"
                    placeholder="不填写则使用上方自动命中条件"
                  />
                </el-form-item>

                <el-form-item label="精确症状词">
                  <div class="tag-editor">
                    <div class="tag-list">
                      <el-tag v-for="(item, index) in form.symptomKeys" :key="item" closable @close="removeTag(index)">
                        {{ item }}
                      </el-tag>
                    </div>
                    <el-input
                      v-model="tagInput"
                      placeholder="输入后回车添加"
                      @keyup.enter="addTag"
                      @blur="addTag"
                    />
                  </div>
                </el-form-item>

                <el-row :gutter="16">
                  <el-col :xs="24" :sm="12">
                    <el-form-item label="区分追问">
                      <el-input v-model="form.question" placeholder="例如：启动时蓝烟更明显，还是加速时更明显？" />
                    </el-form-item>
                  </el-col>
                  <el-col :xs="24" :sm="12">
                    <el-form-item label="追问选项">
                      <div class="tag-editor">
                        <div class="tag-list">
                          <el-tag v-for="(item, index) in form.options" :key="item" type="warning" closable @close="removeOption(index)">
                            {{ item }}
                          </el-tag>
                        </div>
                        <el-input
                          v-model="optionInput"
                          placeholder="输入后回车添加"
                          @keyup.enter="addOption"
                          @blur="addOption"
                        />
                      </div>
                    </el-form-item>
                  </el-col>
                </el-row>

                <el-form-item label="证据引用">
                  <el-input
                    v-model="form.evidenceRefsText"
                    type="textarea"
                    :rows="3"
                    placeholder="每行一条：维修手册 ｜ 发动机故障诊断 ｜ 第20页"
                  />
                </el-form-item>

                <el-form-item label="审核备注">
                  <el-input v-model="form.reviewComment" type="textarea" :rows="2" placeholder="可填写专家修正说明或规则来源" />
                </el-form-item>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </el-form>

      <template #footer>
        <button class="dlg-btn cancel" type="button" @click="dialog.show = false">取消</button>
        <button class="dlg-btn ok" type="button" :disabled="dialog.saving" @click="submitForm">
          {{ dialog.saving ? '保存中...' : '保存规则' }}
        </button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="detailDrawer.show"
      title="诊断规则详情"
      size="520px"
      append-to-body
    >
      <div v-loading="detailDrawer.loading" class="detail-drawer" v-if="detailDrawer.data">
        <div class="detail-head">
          <h3>{{ detailDrawer.data.title }}</h3>
          <div class="detail-tags">
            <el-tag :type="statusInfo(detailDrawer.data.status).type">{{ statusInfo(detailDrawer.data.status).label }}</el-tag>
            <el-tag :type="syncInfo(detailDrawer.data.syncStatus).type" effect="plain">{{ syncInfo(detailDrawer.data.syncStatus).label }}</el-tag>
          </div>
        </div>

        <div class="confidence-strip">
          <div class="confidence-item green">
            <strong>确定</strong>
            <span>规则命中</span>
          </div>
          <div class="confidence-item yellow">
            <strong>参考</strong>
            <span>图谱/RAG</span>
          </div>
          <div class="confidence-item red">
            <strong>推测</strong>
            <span>LLM 补充</span>
          </div>
        </div>

        <dl class="detail-list">
          <dt>规则编号</dt>
          <dd>{{ detailDrawer.data.ruleCode || '-' }}</dd>
          <dt>设备类型</dt>
          <dd>{{ detailDrawer.data.deviceType || '-' }}</dd>
          <dt>症状关键词</dt>
          <dd>
            <span v-for="item in detailDrawer.data.symptomKeys || []" :key="item" class="symptom-chip">{{ item }}</span>
            <span v-if="!detailDrawer.data.symptomKeys?.length">-</span>
          </dd>
          <dt>命中条件</dt>
          <dd>{{ detailDrawer.data.conditionText || '-' }}</dd>
          <dt>诊断结论</dt>
          <dd>{{ detailDrawer.data.conclusion || '-' }}</dd>
          <dt>区分追问</dt>
          <dd>{{ detailDrawer.data.question || '-' }}</dd>
          <dt>追问选项</dt>
          <dd>
            <span v-for="item in detailDrawer.data.options || []" :key="item" class="option-chip">{{ item }}</span>
            <span v-if="!detailDrawer.data.options?.length">-</span>
          </dd>
          <dt>Python 文档 ID</dt>
          <dd>{{ detailDrawer.data.pythonDocId || '-' }}</dd>
          <dt>同步错误</dt>
          <dd>{{ detailDrawer.data.syncError || '-' }}</dd>
          <dt>审核备注</dt>
          <dd>{{ detailDrawer.data.reviewComment || '-' }}</dd>
        </dl>

        <div class="evidence-block">
          <h4>证据引用</h4>
          <pre>{{ JSON.stringify(detailDrawer.data.evidenceRefs || [], null, 2) }}</pre>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.domain-rules {
  max-width: 1280px;
  margin: 0 auto;
}

.ap-head,
.ap-title-row,
.ap-stats,
.ap-filters,
.filter-right,
.rule-card-top,
.rule-meta,
.rule-tags,
.rule-actions,
.tag-list,
.detail-tags,
.confidence-strip {
  display: flex;
}

.ap-head {
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.ap-title-row {
  align-items: center;
  gap: 10px;
}

.ap-title-led {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--signal, #ffa62b);
  box-shadow: 0 0 0 3px var(--signal-soft, rgba(255, 166, 43, 0.14));
}

.ap-title-text {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--plaza-heading);
}

.ap-title-sub {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--plaza-text-muted);
  letter-spacing: 1.8px;
}

.ap-create-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 20px;
  color: #fff;
  background: var(--plaza-accent-grad);
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 8px 20px var(--plaza-accent-soft-strong);
  transition: all 0.18s ease;
}

.ap-create-btn:hover {
  filter: brightness(1.05);
  transform: translateY(-2px);
}

.ap-stats {
  gap: 14px;
  margin-bottom: 16px;
}

.stat-card {
  flex: 1;
  position: relative;
  overflow: hidden;
  min-width: 150px;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid var(--plaza-border);
  border-radius: 12px;
  cursor: pointer;
  box-shadow: var(--plaza-shadow-organic);
  transition: all 0.2s ease;
}

.stat-card:hover {
  border-color: var(--plaza-accent);
  transform: translateY(-2px);
}

.stat-num {
  display: block;
  font-family: var(--font-mono);
  font-size: 30px;
  font-weight: 700;
  color: var(--plaza-heading);
  line-height: 1.1;
}

.stat-label {
  display: block;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--plaza-text-muted);
}

.stat-bar {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 100%;
  height: 3px;
}

.stat-bar.draft { background: var(--plaza-text-muted); }
.stat-bar.pending { background: #f59e0b; }
.stat-bar.active { background: var(--plaza-accent); }
.stat-bar.rejected { background: #ef4444; }
.stat-bar.disabled { background: #94a3b8; }
.stat-bar.failed { background: #ef4444; }

.ap-filters {
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.status-tabs {
  display: flex;
  gap: 4px;
  padding: 3px;
  background: var(--plaza-panel-bg);
  border-radius: 10px;
}

.st-tab {
  padding: 6px 14px;
  color: var(--plaza-text-muted);
  background: transparent;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.st-tab.active {
  color: var(--plaza-heading);
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.filter-right {
  align-items: center;
  gap: 10px;
}

.rule-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  padding: 10px 12px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  font-size: 13px;
}

.scope-tip {
  color: var(--plaza-text-muted);
}

.ap-grid-wrap {
  min-height: 320px;
}

.rule-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 16px;
}

.rule-card {
  display: flex;
  min-height: 178px;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--plaza-border);
  border-radius: 12px;
  box-shadow: var(--plaza-shadow-organic);
  transition: all 0.22s ease;
}

.rule-card:hover {
  border-color: var(--plaza-accent);
  transform: translateY(-2px);
}

.card-bar {
  width: 4px;
  flex-shrink: 0;
}

.rule-card-main {
  flex: 1;
  min-width: 0;
  padding: 16px 18px;
}

.rule-card-top {
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.rule-title {
  margin: 0 0 4px;
  color: var(--plaza-heading);
  font-size: 16px;
  line-height: 1.35;
}

.rule-code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--plaza-text-muted);
}

.rule-tags {
  flex-shrink: 0;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.rule-meta {
  gap: 12px;
  margin-top: 10px;
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.symptom-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.symptom-chip,
.option-chip {
  display: inline-flex;
  align-items: center;
  margin: 0 6px 6px 0;
  padding: 2px 8px;
  color: var(--plaza-text);
  background: var(--plaza-panel-bg);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
}

.option-chip {
  color: #92400e;
  background: #fffbeb;
}

.symptom-empty {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.rule-conclusion {
  display: -webkit-box;
  margin: 10px 0 0;
  overflow: hidden;
  color: var(--plaza-text);
  font-size: 13px;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.sync-error {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin-top: 10px;
  padding: 7px 9px;
  color: #991b1b;
  background: #fef2f2;
  border-radius: 8px;
  font-size: 12px;
}

.rule-actions {
  width: 74px;
  flex-direction: column;
  border-left: 1px solid var(--plaza-panel-bg);
}

.action-btn {
  flex: 1;
  min-height: 38px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  color: var(--plaza-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  transition: all 0.15s ease;
}

.action-btn:hover {
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.action-btn.success:hover {
  color: #16a34a;
  background: #f0fdf4;
}

.action-btn.danger:hover {
  color: #dc2626;
  background: #fef2f2;
}

.ap-pager {
  display: flex;
  justify-content: center;
  margin-top: 24px;
}

.rule-form-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.rule-form-section {
  padding-bottom: 4px;
}

.form-section-title {
  color: var(--plaza-heading);
  font-size: 17px;
  font-weight: 700;
  line-height: 1.4;
}

.form-section-subtitle {
  margin: 2px 0 16px;
  color: var(--plaza-text-muted);
  font-size: 12px;
  font-weight: 600;
}

.condition-preview {
  display: grid;
  grid-template-columns: 92px 1fr;
  gap: 10px;
  margin-top: 4px;
  padding: 10px 12px;
  color: var(--plaza-text);
  background: var(--plaza-panel-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  font-size: 12.5px;
  line-height: 1.6;
}

.preview-label {
  color: var(--plaza-text-muted);
  font-weight: 700;
}

.advanced-rule-collapse {
  border-top: 1px solid var(--plaza-border);
  border-bottom: none;
}

.advanced-rule-collapse :deep(.el-collapse-item__header) {
  height: 44px;
  color: var(--plaza-heading);
  background: transparent;
  border-bottom: none;
}

.advanced-rule-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

.advanced-title {
  font-size: 13px;
  font-weight: 700;
}

.advanced-fields {
  padding-top: 4px;
}

.tag-editor {
  width: 100%;
}

.tag-list {
  gap: 6px;
  flex-wrap: wrap;
  min-height: 28px;
  margin-bottom: 8px;
}

.dlg-btn {
  padding: 9px 22px;
  border: 1px solid;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.dlg-btn.cancel {
  margin-right: 8px;
  color: var(--plaza-text-muted);
  background: #fff;
  border-color: var(--plaza-border);
}

.dlg-btn.ok {
  color: #fff;
  background: var(--plaza-accent);
  border-color: var(--plaza-accent);
}

.dlg-btn.ok:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.detail-drawer {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-head h3 {
  margin: 0 0 10px;
  color: var(--plaza-heading);
  font-size: 18px;
}

.detail-tags {
  gap: 8px;
}

.confidence-strip {
  gap: 8px;
}

.confidence-item {
  flex: 1;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid;
}

.confidence-item strong {
  display: block;
  margin-bottom: 2px;
  font-size: 14px;
}

.confidence-item span {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.confidence-item.green {
  background: #f0fdf4;
  border-color: #bbf7d0;
}

.confidence-item.yellow {
  background: #fffbeb;
  border-color: #fde68a;
}

.confidence-item.red {
  background: #fef2f2;
  border-color: #fecaca;
}

.detail-list {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 10px 14px;
  margin: 0;
}

.detail-list dt {
  color: var(--plaza-text-muted);
  font-size: 13px;
}

.detail-list dd {
  margin: 0;
  color: var(--plaza-text);
  font-size: 13px;
  line-height: 1.6;
}

.evidence-block h4 {
  margin: 0 0 8px;
  color: var(--plaza-heading);
  font-size: 14px;
}

.evidence-block pre {
  max-height: 220px;
  margin: 0;
  overflow: auto;
  padding: 12px;
  color: var(--plaza-text);
  background: var(--plaza-panel-bg);
  border-radius: 10px;
  font-size: 12px;
}

@media (max-width: 820px) {
  .ap-head {
    align-items: stretch;
    flex-direction: column;
    gap: 12px;
  }

  .ap-stats {
    flex-wrap: wrap;
  }

  .stat-card {
    flex: 1 1 calc(50% - 8px);
  }

  .filter-right {
    flex-wrap: wrap;
  }

  .rule-grid {
    grid-template-columns: 1fr;
  }

  .rule-card {
    flex-direction: column;
  }

  .rule-actions {
    width: 100%;
    flex-direction: row;
    border-top: 1px solid var(--plaza-panel-bg);
    border-left: none;
  }

  .condition-preview {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
