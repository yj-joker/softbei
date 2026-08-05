<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowUp, CircleCheck, CircleClose, Refresh, Search, WarningFilled } from '@element-plus/icons-vue'
import TaskEvidenceGraphEditor from '@/components/task-evidence/TaskEvidenceGraphEditor.vue'
import TaskEvidenceSupportPanel from '@/components/task-evidence/TaskEvidenceSupportPanel.vue'
import { normalizeCandidateGraph } from '@/utils/taskEvidenceCandidateGraph'
import {
  getTaskEvidenceCandidate,
  getTaskEvidenceCandidateList,
  promoteTaskEvidenceCandidate,
  retryTaskEvidenceCandidate,
  reviewTaskEvidenceCandidate,
  updateTaskEvidenceCandidate,
} from '@/api/taskEvidenceCandidate'

const router = useRouter()
const filters = reactive({ extractionStatus: '', reviewStatus: '', taskNumber: '', deviceName: '', resolutionStatus: '' })
const loading = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const rows = ref([])
const selected = ref(null)
const expandedCandidateId = ref(null)
const draft = ref(normalizeCandidateGraph())
const baseline = ref(normalizeCandidateGraph())
const dirty = ref(false)
const editComment = ref('')
const reviewComment = ref('')
const page = reactive({ page: 1, size: 15, total: 0 })
const busyIds = ref(new Set())
let pollTimer = null
let listRequestSequence = 0
let foregroundLoadToken = 0
let operationToken = 0

const extractionStatusLabel = { PENDING: '候选整理中', READY: '待审核', FAILED: '整理失败' }
const reviewStatusLabel = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回' }
const statusType = { PENDING: 'warning', READY: 'success', FAILED: 'danger', APPROVED: 'success', REJECTED: 'info' }
const reviewOptions = [{ value: 'PENDING', label: '待审核' }, { value: 'APPROVED', label: '已通过' }, { value: 'REJECTED', label: '已驳回' }]
const extractionOptions = [{ value: 'PENDING', label: '整理中' }, { value: 'READY', label: '已就绪' }, { value: 'FAILED', label: '失败' }]
const resolutionOptions = [{ value: 'RESOLVED', label: '已解决' }, { value: 'PARTIALLY_RESOLVED', label: '部分解决' }, { value: 'UNRESOLVED', label: '未解决' }]

const selectedReviewPending = computed(() => selected.value?.extractionStatus === 'READY' && selected.value?.reviewStatus === 'PENDING')
const currentBusy = computed(() => selected.value ? busyIds.value.has(candidateId(selected.value)) : false)
const anyOperationBusy = computed(() => saving.value || busyIds.value.size > 0)
const selectedCanReview = computed(() => selectedReviewPending.value && !dirty.value && !anyOperationBusy.value)
const selectedCanRetry = computed(() => selected.value?.extractionStatus === 'FAILED' && !anyOperationBusy.value)
const selectedCanPromote = computed(() => selected.value?.reviewStatus === 'APPROVED' && selected.value?.promotedGraph !== 'PROMOTED' && !anyOperationBusy.value)
const selectedCanViewGraph = computed(() => selected.value?.promotedGraph === 'PROMOTED')
const editorReadOnly = computed(() => selected.value?.promotedGraph === 'PROMOTED' || anyOperationBusy.value)

function list(value) {
  if (value == null) return []
  if (Array.isArray(value)) return value
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => typeof item === 'object' ? { key, ...item } : { key, value: item })
  return [value]
}
function parse(value) {
  if (typeof value !== 'string') return value || {}
  try { return JSON.parse(value) } catch { return {} }
}
function text(value) {
  if (value == null || value === '') return '-'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
function warningList(candidate) { return list(parse(candidate?.warnings)) }
function extractionError(candidate) { return candidate?.extractionError }
function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
function warningSeverity(item) { return String(item?.severity || item?.level || 'INFO').toUpperCase() }
function warningType(severity) { return severity === 'ERROR' || severity === 'HIGH' ? 'danger' : severity === 'WARN' || severity === 'MEDIUM' ? 'warning' : 'info' }
function candidateId(candidate) { return candidate?.id == null ? '' : String(candidate.id) }
function initializeDraft(candidate) {
  const normalized = normalizeCandidateGraph(parse(candidate?.candidateJson))
  draft.value = normalizeCandidateGraph(normalized)
  baseline.value = normalizeCandidateGraph(normalized)
  dirty.value = false
  editComment.value = candidate?.editComment || ''
}
function markDirty() { if (!editorReadOnly.value) dirty.value = true }
function discardDraft() {
  draft.value = normalizeCandidateGraph(baseline.value)
  dirty.value = false
  editComment.value = selected.value?.editComment || ''
}
function applyCandidateVo(vo) {
  const id = candidateId(vo)
  selected.value = { ...(selected.value || {}), ...vo, id }
  rows.value = rows.value.map((row) => candidateId(row) === id ? { ...row, ...vo, id } : row)
  initializeDraft(vo)
}
async function saveDraft() {
  if (saving.value || busyIds.value.size || !selected.value || editorReadOnly.value || !dirty.value) return
  const id = candidateId(selected.value)
  const token = ++operationToken
  const rowVersion = selected.value.rowVersion
  const candidateJson = normalizeCandidateGraph(draft.value)
  saving.value = true
  try {
    const res = await updateTaskEvidenceCandidate(id, {
      rowVersion,
      candidateJson,
      editComment: editComment.value,
    })
    const vo = res.data
    if (!vo) throw new Error('服务端未返回更新后的候选')
    if (candidateId(vo) !== id) throw new Error('保存响应候选不匹配')
    if (token === operationToken && expandedCandidateId.value === id && candidateId(selected.value) === id) {
      applyCandidateVo(vo)
      ElMessage.success('候选草稿已保存')
    }
  } catch (error) {
    if (token === operationToken && expandedCandidateId.value === id && candidateId(selected.value) === id) {
      ElMessage.error(`保存失败：${error.message || '请求异常'}，本地修改已保留`)
    }
  } finally {
    if (token === operationToken) saving.value = false
    syncPolling()
  }
}
async function guardDirtyDraft(action = '离开当前候选') {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(`当前候选有未保存修改，${action}将放弃这些修改。是否继续？`, '未保存修改', {
      type: 'warning', confirmButtonText: '放弃并继续', cancelButtonText: '留在此处',
    })
    discardDraft()
    return true
  } catch { return false }
}
function clearDetail() {
  operationToken += 1
  expandedCandidateId.value = null
  selected.value = null
  detailLoading.value = false
  reviewComment.value = ''
  editComment.value = ''
  dirty.value = false
}
async function closeDetail() {
  if (anyOperationBusy.value) return false
  if (!await guardDirtyDraft('收起详情')) return false
  clearDetail()
  return true
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}
function syncPolling() {
  stopPolling()
  if (!foregroundLoadToken && !anyOperationBusy.value && rows.value.some((row) => row.extractionStatus === 'PENDING')) {
    pollTimer = setTimeout(() => load(page.page, true), 2500)
  }
}
async function load(pageNumber = 1, polling = false, internal = false) {
  if (polling && (foregroundLoadToken || anyOperationBusy.value)) return
  if (!polling && !internal && anyOperationBusy.value) return
  if (!polling) stopPolling()
  const sequence = ++listRequestSequence
  const foregroundToken = !polling ? ++foregroundLoadToken : 0
  if (!polling) loading.value = true
  page.page = pageNumber
  try {
    const params = { page: pageNumber, size: page.size }
    Object.entries(filters).forEach(([key, value]) => { if (value) params[key] = value })
    const res = await getTaskEvidenceCandidateList(params)
    if (sequence !== listRequestSequence) return
    rows.value = (res.data?.records || res.data?.list || []).map((row) => ({ ...row, id: candidateId(row) }))
    page.total = res.data?.total || 0
  } catch (error) {
    if (sequence !== listRequestSequence) return
    rows.value = []
    page.total = 0
    stopPolling()
    ElMessage.error(`加载候选失败：${error.message || '请求异常'}`)
  } finally {
    if (foregroundToken === foregroundLoadToken) {
      loading.value = false
      foregroundLoadToken = 0
    }
    if (sequence === listRequestSequence) syncPolling()
  }
}
async function reset() {
  if (anyOperationBusy.value) return
  if (!await guardDirtyDraft('重置筛选')) return
  Object.keys(filters).forEach((key) => { filters[key] = '' })
  clearDetail()
  await load(1)
}
async function openDetail(row) {
  const id = candidateId(row)
  if (expandedCandidateId.value === id) {
    await closeDetail()
    return
  }
  if (anyOperationBusy.value) return
  if (!await guardDirtyDraft('切换候选')) return
  const token = ++operationToken
  expandedCandidateId.value = id
  selected.value = { ...row, id }
  initializeDraft(selected.value)
  detailLoading.value = true
  reviewComment.value = ''
  try {
    const res = await getTaskEvidenceCandidate(id)
    if (candidateId(res.data) !== id) throw new Error('详情响应候选不匹配')
    if (token === operationToken && expandedCandidateId.value === id) {
      selected.value = { ...res.data, id: candidateId(res.data) }
      initializeDraft(selected.value)
    }
  } catch (error) {
    if (token === operationToken && expandedCandidateId.value === id) {
      ElMessage.error(`加载详情失败：${error.message || '请求异常'}`)
      clearDetail()
    }
  } finally {
    if (token === operationToken && expandedCandidateId.value === id) detailLoading.value = false
  }
}
async function reopenDetail(candidate) {
  const id = candidateId(candidate)
  const token = ++operationToken
  try {
    const res = await getTaskEvidenceCandidate(id)
    if (candidateId(res.data) !== id) throw new Error('详情响应候选不匹配')
    if (token === operationToken && expandedCandidateId.value === id) {
      selected.value = { ...res.data, id: candidateId(res.data) }
      initializeDraft(selected.value)
    }
  } catch (error) {
    if (token === operationToken && expandedCandidateId.value === id) {
      ElMessage.error(`刷新详情失败：${error.message || '请求异常'}`)
      clearDetail()
    }
  }
}
function markBusy(id, busy) {
  const key = String(id)
  const next = new Set(busyIds.value)
  busy ? next.add(key) : next.delete(key)
  busyIds.value = next
}
async function retry(row) {
  const id = candidateId(row)
  if (saving.value || busyIds.value.size || dirty.value) return
  stopPolling()
  markBusy(id, true)
  try {
    await retryTaskEvidenceCandidate(id)
    ElMessage.success('已提交重新整理')
    clearDetail()
    await load(page.page, false, true)
  } catch (error) { ElMessage.error(`重试失败：${error.message || '请求异常'}`) }
  finally {
    markBusy(id, false)
    syncPolling()
  }
}
async function review(row, status) {
  const id = candidateId(row)
  if (saving.value || busyIds.value.size) return
  if (dirty.value) {
    ElMessage.warning('当前图谱有未保存修改，请先保存草稿再审核')
    return
  }
  stopPolling()
  markBusy(id, true)
  try {
    let comment = reviewComment.value
    if (status === 'REJECTED' && !comment) {
      try { comment = await ElMessageBox.prompt('请输入驳回原因（可选）', '驳回候选', { inputPlaceholder: '说明需要补充或修正的证据' }).then((result) => result.value) }
      catch { return }
    }
    await reviewTaskEvidenceCandidate(id, { status, comment, rowVersion: row.rowVersion })
    ElMessage.success(status === 'APPROVED' ? '候选已通过，可继续沉淀到知识图谱' : '候选已驳回')
    await load(page.page, false, true)
    if (status === 'APPROVED') await reopenDetail(row)
    else clearDetail()
  } catch (error) { ElMessage.error(`审核失败：${error.message || '候选可能已被其他管理员处理'}`) }
  finally {
    markBusy(id, false)
    syncPolling()
  }
}
async function promote(candidate) {
  const id = candidateId(candidate)
  if (saving.value || busyIds.value.size || dirty.value) return
  stopPolling()
  markBusy(id, true)
  try {
    try {
      await ElMessageBox.confirm('确认将已审核候选沉淀到知识图谱？系统只会写入已确认实体和已验证方案。', '沉淀确认', { type: 'warning', confirmButtonText: '确认沉淀' })
    } catch { return }
    await promoteTaskEvidenceCandidate(id)
    ElMessage.success('已沉淀到知识图谱')
    await load(page.page, false, true)
    await reopenDetail(candidate)
  } catch (error) { ElMessage.error(`沉淀失败：${error.message || '请求异常'}`) }
  finally {
    markBusy(id, false)
    syncPolling()
  }
}
function viewTaskGraph(candidate) {
  if (anyOperationBusy.value) return
  clearDetail()
  router.push({ path: '/admin/knowledge-center', query: { tab: 'graph', taskId: String(candidate.taskId) } })
}

onMounted(() => load())
onUnmounted(() => stopPolling())
</script>

<template>
  <section class="tec-root" aria-labelledby="tec-title">
    <header class="tec-head"><h2 id="tec-title">任务证据候选审核</h2><el-button :loading="loading" :disabled="anyOperationBusy" @click="load(page.page)"><el-icon><Refresh /></el-icon>刷新</el-button></header>
    <div class="tec-filters">
      <el-select class="tec-filter-control" v-model="filters.extractionStatus" :disabled="anyOperationBusy" clearable placeholder="整理状态"><el-option v-for="o in extractionOptions" :key="o.value" v-bind="o" /></el-select>
      <el-select class="tec-filter-control" v-model="filters.reviewStatus" :disabled="anyOperationBusy" clearable placeholder="审核状态"><el-option v-for="o in reviewOptions" :key="o.value" v-bind="o" /></el-select>
      <el-input class="tec-filter-control" v-model="filters.taskNumber" :disabled="anyOperationBusy" clearable placeholder="任务编号" @keyup.enter="load(1)" />
      <el-input class="tec-filter-control" v-model="filters.deviceName" :disabled="anyOperationBusy" clearable placeholder="设备名称" @keyup.enter="load(1)" />
      <el-select class="tec-filter-control" v-model="filters.resolutionStatus" :disabled="anyOperationBusy" clearable placeholder="维修结果"><el-option v-for="o in resolutionOptions" :key="o.value" v-bind="o" /></el-select>
      <div class="tec-filter-actions"><el-button type="primary" :disabled="anyOperationBusy" @click="load(1)"><el-icon><Search /></el-icon>搜索</el-button><el-button :disabled="anyOperationBusy" @click="reset">重置</el-button></div>
    </div>
      <el-table v-loading="loading" :data="rows" row-key="id" empty-text="暂无证据候选" :row-class-name="() => anyOperationBusy ? 'tec-row-disabled' : ''" @row-click="openDetail">
      <el-table-column prop="taskNumber" label="任务编号" min-width="130" /><el-table-column prop="deviceName" label="设备" min-width="130" /><el-table-column prop="resolutionStatus" label="维修结果" width="100" />
      <el-table-column label="整理状态" width="100"><template #default="{ row }"><el-tag :type="statusType[row.extractionStatus]">{{ extractionStatusLabel[row.extractionStatus] || row.extractionStatus || '-' }}</el-tag></template></el-table-column>
      <el-table-column label="审核状态" width="100"><template #default="{ row }"><el-tag :type="statusType[row.reviewStatus]">{{ reviewStatusLabel[row.reviewStatus] || row.reviewStatus || '-' }}</el-tag></template></el-table-column>
      <el-table-column prop="evidenceVersion" label="证据版本" width="90" /><el-table-column label="警告" width="70"><template #default="{ row }">{{ warningList(row).length }}</template></el-table-column>
      <el-table-column prop="updatedAt" label="更新时间" min-width="160"><template #default="{ row }">{{ formatDate(row.updatedAt) }}</template></el-table-column>
      <el-table-column label="操作" width="190" fixed="right"><template #default="{ row }"><el-button v-if="row.extractionStatus === 'FAILED'" size="small" type="warning" :disabled="anyOperationBusy || dirty" :loading="busyIds.has(String(row.id))" @click.stop="retry(row)">重新整理</el-button><el-button size="small" :disabled="anyOperationBusy" :type="expandedCandidateId === String(row.id) ? 'primary' : 'default'" @click.stop="openDetail(row)"><el-icon><ArrowUp v-if="expandedCandidateId === String(row.id)" /><ArrowDown v-else /></el-icon>{{ expandedCandidateId === String(row.id) ? '收起图谱' : '查看图谱' }}</el-button></template></el-table-column>
    </el-table>

    <transition name="tec-expand">
      <section v-if="expandedCandidateId && selected" v-loading="detailLoading" class="tec-inline-detail" aria-label="候选关系图谱">
        <div class="tec-detail-head"><div><span class="tec-eyebrow">候选关系图谱</span><h3>{{ selected.taskNumber || `任务 #${selected.taskId || '-'}` }}</h3><div class="tec-meta"><span>设备：{{ selected.deviceName || '-' }}</span><span>证据版本：{{ selected.evidenceVersion ?? '-' }}</span><span>rowVersion：{{ selected.rowVersion ?? '-' }}</span><el-tag v-if="dirty" type="warning" size="small">有未保存修改</el-tag><el-tag v-if="selected.promotedGraph === 'PROMOTED'" type="success" size="small">已入图，只读</el-tag></div></div><el-button text :disabled="anyOperationBusy" @click="closeDetail"><el-icon><ArrowUp /></el-icon>收起</el-button></div>

        <div class="tec-workbench"><TaskEvidenceGraphEditor v-model="draft" :read-only="editorReadOnly" @dirty="markDirty" /><TaskEvidenceSupportPanel :candidate="selected" :active-evidence-refs="[]" /></div>

        <section v-if="warningList(selected).length" class="tec-warnings"><h4>候选警告（{{ warningList(selected).length }}）</h4><el-alert v-for="(warning, index) in warningList(selected)" :key="index" :type="warningType(warningSeverity(warning))" :closable="false"><b>{{ warningSeverity(warning) }}</b> {{ text(warning.message || warning.reason || warning) }}</el-alert></section>
        <div v-if="!editorReadOnly" class="tec-draft-bar"><el-input v-model="editComment" maxlength="500" show-word-limit placeholder="编辑说明（可选）" aria-label="编辑说明" @input="markDirty" /><div><el-button :disabled="!dirty || saving" @click="discardDraft">放弃修改</el-button><el-button type="primary" :loading="saving" :disabled="!dirty" @click="saveDraft">保存草稿</el-button></div></div>
        <el-input v-if="selectedReviewPending" v-model="reviewComment" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="审核备注（可选）" aria-label="审核备注" />
        <el-alert v-if="dirty && selectedReviewPending" type="warning" :closable="false" title="当前图谱有未保存修改，请先保存草稿再审核" />
        <div v-if="selectedCanRetry" class="tec-error"><el-icon><WarningFilled /></el-icon>{{ extractionError(selected) || '本次证据候选整理失败，请修复原因后重试。' }}</div>
        <div v-if="selectedCanRetry || selectedReviewPending || selectedCanPromote || selectedCanViewGraph" class="tec-actions">
          <el-button v-if="selectedCanRetry" type="warning" :disabled="anyOperationBusy || dirty" :loading="busyIds.has(String(selected.id))" @click="retry(selected)">失败后重新整理</el-button>
          <template v-if="selectedReviewPending"><el-button type="success" :disabled="!selectedCanReview" :loading="busyIds.has(String(selected.id))" @click="review(selected, 'APPROVED')"><el-icon><CircleCheck /></el-icon>审核通过</el-button><el-button type="danger" plain :disabled="!selectedCanReview" :loading="busyIds.has(String(selected.id))" @click="review(selected, 'REJECTED')"><el-icon><CircleClose /></el-icon>审核驳回</el-button></template>
          <el-button v-if="selectedCanPromote" type="primary" :loading="busyIds.has(String(selected.id))" @click="promote(selected)">沉淀到知识图谱</el-button><el-button v-if="selectedCanViewGraph" type="success" plain :disabled="anyOperationBusy" @click="viewTaskGraph(selected)">查看本次图谱</el-button>
        </div>
      </section>
    </transition>
    <el-pagination v-if="page.total" class="tec-pager" :disabled="anyOperationBusy" v-model:current-page="page.page" :page-size="page.size" :total="page.total" layout="total, prev, pager, next" @current-change="load" />
  </section>
</template>

<style scoped>
.tec-root { padding:18px 24px 24px; color:var(--plaza-text); }.tec-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px; }.tec-head h2 { margin:0; color:var(--plaza-heading); font-size:20px; }.tec-filters { display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap; margin-bottom:12px; }.tec-filter-control { flex:0 1 180px; width:180px; min-width:130px; }.tec-filter-actions,.tec-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }.tec-filter-actions .el-button + .el-button,.tec-actions .el-button + .el-button { margin-left:0; }.tec-pager { justify-content:flex-end; margin-top:16px; }
.tec-inline-detail { margin-top:14px; padding:22px; border:1px solid color-mix(in srgb, var(--plaza-accent) 28%, var(--plaza-border)); border-radius:14px; background:var(--plaza-bg-card); box-shadow:0 12px 32px rgba(39,47,38,.08); }.tec-detail-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding-bottom:18px; border-bottom:1px solid var(--plaza-border); }.tec-detail-head h3 { margin:3px 0 8px; color:var(--plaza-heading); font-size:20px; }.tec-eyebrow { color:var(--plaza-accent); font-size:12px; font-weight:700; letter-spacing:.12em; }.tec-meta { display:flex; flex-wrap:wrap; align-items:center; gap:8px 18px; color:var(--plaza-text-muted); font-size:13px; }
.tec-workbench { display:grid; grid-template-columns:minmax(0,3fr) minmax(320px,2fr); gap:20px; align-items:start; margin-top:20px; }.tec-workbench > * { min-width:0; }.tec-warnings { display:flex; flex-direction:column; gap:8px; margin-top:18px; padding-top:16px; border-top:1px dashed var(--plaza-border); }.tec-warnings h4 { margin:0; color:var(--plaza-heading); }.tec-draft-bar { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:start; gap:12px; margin-top:18px; padding:14px; border:1px solid var(--plaza-border); border-radius:8px; background:color-mix(in srgb,var(--plaza-accent) 5%,var(--plaza-bg-card)); }.tec-draft-bar > div { display:flex; gap:8px; }.tec-actions { justify-content:flex-end; margin-top:18px; padding-top:16px; border-top:1px solid var(--plaza-border); }.tec-error { display:flex; align-items:center; gap:8px; margin-top:14px; padding:10px; border-radius:6px; color:var(--el-color-danger); background:var(--el-color-danger-light-9); }.tec-expand-enter-active,.tec-expand-leave-active { transition:opacity .2s ease,transform .2s ease; transform-origin:top; }.tec-expand-enter-from,.tec-expand-leave-to { opacity:0; transform:translateY(-8px); }
@media (max-width:900px) { .tec-root { padding:12px; }.tec-head,.tec-detail-head { align-items:flex-start; flex-direction:column; }.tec-filters { flex-direction:column; align-items:stretch; }.tec-filter-control { width:100%; flex-basis:100%; }.tec-filter-actions { justify-content:flex-end; }.tec-inline-detail { padding:15px; }.tec-workbench { grid-template-columns:1fr; }.tec-draft-bar { grid-template-columns:1fr; }.tec-draft-bar > div { justify-content:flex-end; } }
</style>
