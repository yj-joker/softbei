<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, DocumentAdd, Share, CloseBold, Right } from '@element-plus/icons-vue'
import { getTaskList, promoteToProcedure, promoteToGraph, skipPromotion } from '@/api/task'
import DistillationReviewPanel from '@/components/DistillationReviewPanel.vue'
import CaseReviewPanel from '@/components/case/CaseReviewPanel.vue'
import ExpirationReviewSection from '@/components/ExpirationReviewSection.vue'

/* ========== Tab ========== */
const activeTab = ref('list')
const focusTaskId = ref(null)

function goToDistill(taskId) {
  focusTaskId.value = taskId
  activeTab.value = 'review'
}

/* ========== 筛选条件 ========== */
const filters = reactive({
  status: '',
  deviceName: '',
  promotedProcedure: '',
  promotedGraph: '',
})

/* ========== 表格 / 分页 ========== */
const loading = ref(false)
const tableData = ref([])
const pagination = reactive({ page: 1, size: 15, total: 0 })
const busyIds = ref(new Set())

/* ========== 展开行 ========== */
const expandedRows = ref(new Set())

/* ========== 状态枚举 ========== */
const STATUS_MAP = {
  CREATED:         { label: '已创建',   color: 'var(--plaza-accent)', bg: 'var(--plaza-accent-soft)' },
  GENERATING:      { label: '生成中',   color: '#df9226', bg: '#fdf2e0' },
  GENERATED:       { label: '已生成',   color: '#5e8c3e', bg: '#f1f5e6' },
  GENERATE_FAILED: { label: '生成失败', color: '#c5402c', bg: '#fbeae4' },
  EXECUTING:       { label: '执行中',   color: '#a8605f', bg: '#f5ece8' },
  CLOSED:          { label: '已关闭',   color: 'var(--plaza-text-muted)', bg: 'var(--plaza-panel-bg)' },
}
const PROMO_MAP = {
  PENDING:  { label: '待沉淀', color: '#df9226', bg: '#fdf2e0' },
  PROMOTED: { label: '已沉淀', color: '#5e8c3e', bg: '#f1f5e6' },
  SKIPPED:  { label: '已跳过', color: 'var(--plaza-text-muted)', bg: 'var(--plaza-panel-bg)' },
}
const URGENCY_MAP = {
  0: { label: '低',   color: '#5e8c3e', bg: '#f1f5e6' },
  1: { label: '普通', color: 'var(--plaza-accent)', bg: 'var(--plaza-accent-soft)' },
  2: { label: '紧急', color: '#c5402c', bg: '#fbeae4' },
}
const LEVEL_MAP = { ROUTINE: '日常保养', MINOR: '小修', MAJOR: '大修' }
const STATUS_OPTIONS = Object.keys(STATUS_MAP).map((k) => ({ value: k, label: STATUS_MAP[k].label }))
const PROMO_OPTIONS = [
  { value: '',       label: '全部' },
  { value: 'PENDING',  label: '待沉淀' },
  { value: 'PROMOTED', label: '已沉淀' },
  { value: 'SKIPPED',  label: '已跳过' },
]

/* ========== 加载 ========== */
async function loadTasks(page = 1) {
  loading.value = true
  pagination.page = page
  try {
    const params = { page, size: pagination.size }
    if (filters.status)            params.status            = filters.status
    if (filters.deviceName)        params.deviceName        = filters.deviceName
    if (filters.promotedProcedure) params.promotedProcedure = filters.promotedProcedure
    if (filters.promotedGraph)     params.promotedGraph     = filters.promotedGraph

    const res = await getTaskList(params)
    if (res.code === '200' || res.code === 200) {
      tableData.value = res.data?.records || res.data?.list || []
      pagination.total = res.data?.total || 0
    } else {
      tableData.value = []
      pagination.total = 0
    }
  } catch (e) {
    ElMessage.error('加载任务失败：' + (e.message || '请求异常'))
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  loadTasks(1)
}
function handleReset() {
  filters.status = ''
  filters.deviceName = ''
  filters.promotedProcedure = ''
  filters.promotedGraph = ''
  loadTasks(1)
}

/* ========== 行展开 ========== */
function toggleExpand(id) {
  const s = new Set(expandedRows.value)
  s.has(id) ? s.delete(id) : s.add(id)
  expandedRows.value = s
}
function isExpanded(id) {
  return expandedRows.value.has(id)
}

/* ========== 沉淀操作 ========== */
function canPromote(row) {
  return row.status === 'CLOSED'
}

async function handlePromoteProcedure(row) {
  addBusy(row.id)
  try {
    const res = await promoteToProcedure(row.id)
    ElMessage.success(`已沉淀为标准规程（ID: ${res.data}）`)
    loadTasks(pagination.page)
  } catch (e) {
    ElMessage.error('沉淀规程失败：' + (e.message || ''))
  } finally {
    removeBusy(row.id)
  }
}

async function handlePromoteGraph(row) {
  addBusy(row.id)
  try {
    await promoteToGraph(row.id, row.graphExtraction || {})
    ElMessage.success('已沉淀到知识图谱')
    loadTasks(pagination.page)
  } catch (e) {
    ElMessage.error('沉淀图谱失败：' + (e.message || ''))
  } finally {
    removeBusy(row.id)
  }
}

async function handleSkip(row, type) {
  const label = type === 'procedure' ? '规程' : type === 'graph' ? '图谱' : '全部'
  try {
    await ElMessageBox.confirm(
      `确认跳过「${row.taskNumber}」的${label}沉淀？此任务不再出现在待审核列表中。`,
      '跳过确认',
      { confirmButtonText: '确认跳过', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }

  addBusy(row.id)
  try {
    await skipPromotion(row.id, type)
    ElMessage.success(`已跳过${label}沉淀`)
    loadTasks(pagination.page)
  } catch (e) {
    ElMessage.error('操作失败：' + (e.message || ''))
  } finally {
    removeBusy(row.id)
  }
}

function addBusy(id) { const s = new Set(busyIds.value); s.add(id); busyIds.value = s }
function removeBusy(id) { const s = new Set(busyIds.value); s.delete(id); busyIds.value = s }
function isBusy(id) { return busyIds.value.has(id) }

/* ========== 格式化 ========== */
function formatDate(s) {
  if (!s) return '-'
  const d = new Date(s)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function truncate(s, n = 28) {
  if (!s) return '-'
  return s.length > n ? s.slice(0, n) + '…' : s
}

function parseGraphExtraction(extraction) {
  if (!extraction) return null
  if (typeof extraction === 'string') {
    try {
      return JSON.parse(extraction)
    } catch {
      return { raw: extraction }
    }
  }
  return extraction
}

function normalizeList(value) {
  if (!value) return []
  return Array.isArray(value) ? value : [value]
}

function itemText(item, keys = []) {
  if (item == null) return ''
  if (typeof item === 'string' || typeof item === 'number') return String(item)

  const title = keys.map((key) => item[key]).find(Boolean)
  const extras = []
  if (item.specification) extras.push(`规格：${item.specification}`)
  if (item.severity) extras.push(`严重度：${item.severity}`)
  if (item.relatedComponent) extras.push(`关联部件：${item.relatedComponent}`)
  if (item.relatedFault) extras.push(`关联故障：${item.relatedFault}`)
  if (item.summary) extras.push(item.summary)
  if (item.description) extras.push(item.description)

  return [title, ...extras].filter(Boolean).join('｜')
}

function graphExtractionSections(extraction) {
  const obj = parseGraphExtraction(extraction)
  if (!obj) return []
  if (obj.raw) return [{ title: '原始线索', items: [obj.raw] }]

  const sections = [
    {
      title: '设备',
      items: [
        ...normalizeList(obj.deviceName),
        ...normalizeList(obj.deviceNames),
      ].filter(Boolean),
    },
    {
      title: '部件',
      items: normalizeList(obj.components).map((item) => itemText(item, ['name', 'componentName'])).filter(Boolean),
    },
    {
      title: '故障',
      items: normalizeList(obj.faults).map((item) => itemText(item, ['name', 'faultName', 'title'])).filter(Boolean),
    },
    {
      title: '方案',
      items: normalizeList(obj.solutions).map((item) => itemText(item, ['title', 'name', 'solutionTitle'])).filter(Boolean),
    },
  ]

  return sections.filter((section) => section.items.length)
}

function needsDistill(row) {
  return canPromote(row) && (row.promotedProcedure === 'PENDING' || row.promotedGraph === 'PENDING')
}

onMounted(() => loadTasks(1))
</script>

<template>
  <div class="at-root">
    <el-tabs v-model="activeTab" class="at-tabs" type="card">
      <!-- ====== Tab 1: 任务列表 ====== -->
      <el-tab-pane label="任务列表" name="list">
        <!-- 头部 -->
        <div class="at-head">
          <div class="at-title-row">
            <span class="at-title-led" />
            <h2 class="at-title-text">任务管理</h2>
            <span class="at-title-sub">TASK&nbsp;MANAGEMENT</span>
          </div>
        </div>

        <!-- 筛选栏 -->
        <div class="at-filters">
          <div class="filter-card">
            <div class="filter-row">
              <div class="filter-item">
                <label>任务状态</label>
                <el-select v-model="filters.status" placeholder="全部状态" clearable size="default">
                  <el-option
                    v-for="o in STATUS_OPTIONS" :key="o.value"
                    :label="o.label" :value="o.value"
                  />
                </el-select>
              </div>
              <div class="filter-item">
                <label>设备名称</label>
                <el-input
                  v-model="filters.deviceName"
                  placeholder="搜索设备名…"
                  clearable
                  @keyup.enter="handleSearch"
                />
              </div>
              <div class="filter-item">
                <label>规程沉淀</label>
                <el-select v-model="filters.promotedProcedure" placeholder="全部" clearable size="default">
                  <el-option v-for="o in PROMO_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </div>
              <div class="filter-item">
                <label>图谱沉淀</label>
                <el-select v-model="filters.promotedGraph" placeholder="全部" clearable size="default">
                  <el-option v-for="o in PROMO_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </div>
              <div class="filter-btns">
                <el-button type="primary" @click="handleSearch">
                  <el-icon><Search /></el-icon>搜索
                </el-button>
                <el-button @click="handleReset">
                  <el-icon><Refresh /></el-icon>重置
                </el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- 数据表格 -->
        <div class="at-table-wrap">
          <el-table
            v-loading="loading"
            :data="tableData"
            row-key="id"
            stripe
            style="width: 100%"
            :header-cell-style="{ background: 'var(--plaza-panel-bg)', color: 'var(--plaza-text)', fontWeight: 600, fontSize: '12px', letterSpacing: '0.4px' }"
            :cell-style="{ fontSize: '13.5px' }"
            @row-click="(row) => toggleExpand(row.id)"
          >
            <el-table-column type="expand" width="40">
              <template #default="{ row }">
                <div class="expand-panel">
                  <div class="exp-grid">
                    <div class="exp-item">
                      <span class="exp-label">任务编号</span>
                      <span class="exp-val mono">{{ row.taskNumber }}</span>
                    </div>
                    <div class="exp-item">
                      <span class="exp-label">维修等级</span>
                      <span class="exp-val">{{ LEVEL_MAP[row.maintenanceLevel] || row.maintenanceLevel || '-' }}</span>
                    </div>
                    <div class="exp-item">
                      <span class="exp-label">生成模式</span>
                      <span class="exp-val mono">{{ row.generateMode || '-' }}</span>
                    </div>
                    <div class="exp-item">
                      <span class="exp-label">步骤总数</span>
                      <span class="exp-val">{{ row.stepCount ?? '-' }}</span>
                    </div>
                    <div class="exp-item">
                      <span class="exp-label">关联规程</span>
                      <span class="exp-val" :style="{ color: row.procedureName ? 'var(--plaza-accent)' : '' }">
                        {{ row.procedureName || '无' }}
                      </span>
                    </div>
                    <div class="exp-item">
                      <span class="exp-label">更新时间</span>
                      <span class="exp-val mono">{{ formatDate(row.updatedAt) }}</span>
                    </div>
                  </div>
                  <div v-if="row.graphExtraction" class="exp-extraction">
                    <span class="exp-label">AI 图谱线索</span>
                    <div class="exp-clue-list">
                      <template v-if="graphExtractionSections(row.graphExtraction).length">
                        <div
                          v-for="section in graphExtractionSections(row.graphExtraction)"
                          :key="section.title"
                          class="exp-clue-section"
                        >
                          <span class="exp-clue-title">{{ section.title }}</span>
                          <div class="exp-clue-tags">
                            <span v-for="(item, index) in section.items" :key="`${section.title}-${index}`" class="exp-clue-tag">
                              {{ item }}
                            </span>
                          </div>
                        </div>
                      </template>
                      <span v-else class="exp-empty">暂无可展示线索</span>
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>

            <el-table-column prop="taskNumber" label="任务编号" width="140">
              <template #default="{ row }">
                <span class="mono-text">{{ row.taskNumber }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="deviceName" label="设备名称" width="108">
              <template #default="{ row }">
                {{ row.deviceName || '-' }}
              </template>
            </el-table-column>

            <el-table-column prop="faultDescription" label="故障描述" min-width="150">
              <template #default="{ row }">
                <span :title="row.faultDescription">{{ truncate(row.faultDescription, 32) }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="urgencyLevel" label="紧急等级" width="82" align="center">
              <template #default="{ row }">
                <span
                  class="tag-sm"
                  :style="{
                    background: (URGENCY_MAP[row.urgencyLevel] || URGENCY_MAP[1]).bg,
                    color: (URGENCY_MAP[row.urgencyLevel] || URGENCY_MAP[1]).color,
                  }"
                >
                  {{ (URGENCY_MAP[row.urgencyLevel] || URGENCY_MAP[1]).label }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="status" label="状态" width="82" align="center">
              <template #default="{ row }">
                <span
                  class="tag-sm"
                  :style="{
                    background: (STATUS_MAP[row.status] || {}).bg,
                    color: (STATUS_MAP[row.status] || {}).color,
                  }"
                >
                  {{ (STATUS_MAP[row.status] || {}).label || row.status }}
                </span>
              </template>
            </el-table-column>

            <el-table-column prop="promotedProcedure" label="规程沉淀" width="82" align="center">
              <template #default="{ row }">
                <span
                  v-if="row.promotedProcedure"
                  class="tag-sm"
                  :style="{
                    background: (PROMO_MAP[row.promotedProcedure] || {}).bg,
                    color: (PROMO_MAP[row.promotedProcedure] || {}).color,
                  }"
                >
                  {{ (PROMO_MAP[row.promotedProcedure] || {}).label || row.promotedProcedure }}
                </span>
                <span v-else class="tag-sm" style="background:var(--plaza-panel-bg);color:var(--plaza-text-muted)">-</span>
              </template>
            </el-table-column>

            <el-table-column prop="promotedGraph" label="图谱沉淀" width="82" align="center">
              <template #default="{ row }">
                <span
                  v-if="row.promotedGraph"
                  class="tag-sm"
                  :style="{
                    background: (PROMO_MAP[row.promotedGraph] || {}).bg,
                    color: (PROMO_MAP[row.promotedGraph] || {}).color,
                  }"
                >
                  {{ (PROMO_MAP[row.promotedGraph] || {}).label || row.promotedGraph }}
                </span>
                <span v-else class="tag-sm" style="background:var(--plaza-panel-bg);color:var(--plaza-text-muted)">-</span>
              </template>
            </el-table-column>

            <el-table-column prop="createdAt" label="创建时间" width="130">
              <template #default="{ row }">
                <span class="mono-text" style="font-size:12px">{{ formatDate(row.createdAt) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="172" fixed="right">
              <template #default="{ row }">
                <div class="action-cell" @click.stop>
                  <!-- 跳转沉淀审核 -->
                  <el-button
                    v-if="needsDistill(row)"
                    size="small"
                    type="warning"
                    plain
                    @click="goToDistill(row.id)"
                  >
                    去沉淀<el-icon style="margin-left:2px"><Right /></el-icon>
                  </el-button>
                  <el-button
                    v-if="canPromote(row) && row.promotedProcedure === 'PENDING'"
                    size="small"
                    type="primary"
                    plain
                    :loading="isBusy(row.id)"
                    @click="handlePromoteProcedure(row)"
                  >
                    沉淀规程
                  </el-button>
                  <el-button
                    v-if="canPromote(row) && row.promotedGraph === 'PENDING'"
                    size="small"
                    style="color:#a8605f;border-color:#e0c4bf;background:#f5ece8"
                    :loading="isBusy(row.id)"
                    @click="handlePromoteGraph(row)"
                  >
                    沉淀图谱
                  </el-button>
                  <el-button
                    v-if="canPromote(row) && (row.promotedProcedure === 'PENDING' || row.promotedGraph === 'PENDING')"
                    size="small"
                    type="danger"
                    plain
                    :loading="isBusy(row.id)"
                    @click="handleSkip(row, 'both')"
                  >
                    跳过
                  </el-button>
                  <span
                    v-if="!canPromote(row) || (row.promotedProcedure !== 'PENDING' && row.promotedGraph !== 'PENDING')"
                    style="color:var(--plaza-text-muted);font-size:12px"
                  >-</span>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 分页 -->
        <div v-if="pagination.total > 0" class="at-pager">
          <el-pagination
            v-model:current-page="pagination.page"
            :page-size="pagination.size"
            :total="pagination.total"
            :page-sizes="[10, 15, 20, 30]"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="loadTasks"
            @size-change="(s) => { pagination.size = s; loadTasks(1) }"
          />
        </div>
      </el-tab-pane>

      <!-- ====== Tab 2: 沉淀审核 ====== -->
      <el-tab-pane name="review">
        <template #label>
          <span class="tab-label-custom">
            沉淀审核
            <span class="tab-badge">审核</span>
          </span>
        </template>
        <DistillationReviewPanel :jump-to-id="focusTaskId" :key="activeTab === 'review' ? 'review' : 'idle'" />
      </el-tab-pane>

      <el-tab-pane name="case-review">
        <template #label>
          <span class="tab-label-custom">
            案例审核
            <span class="tab-badge">案例</span>
          </span>
        </template>
        <CaseReviewPanel />
      </el-tab-pane>

      <el-tab-pane name="expiration-review">
        <template #label>
          <span class="tab-label-custom">
            过期判定
            <span class="tab-badge">过期</span>
          </span>
        </template>
        <ExpirationReviewSection />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
/* ============================================================
   AdminTasks — 任务管理（列表 + 沉淀审核双 Tab）
   延续「矿石白 + 克制蓝 + 信号琥珀」设计语言
   ============================================================ */
.at-root {
  max-width: 1480px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}

/* ── 卡片式 Tab ── */
.at-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.at-tabs :deep(.el-tabs__header) {
  margin: 0 0 12px 0;
  padding: 4px 8px 0;
  background: rgba(255,255,255,0.5);
  border-bottom: 1px solid var(--plaza-border);
  backdrop-filter: blur(4px);
  border-radius: 12px 12px 0 0;
}
.at-tabs :deep(.el-tabs__nav) {
  border: none;
}
.at-tabs :deep(.el-tabs__item) {
  height: 38px;
  line-height: 38px;
  border-radius: 10px 10px 0 0;
  font-weight: 600;
  font-size: 13.5px;
  letter-spacing: 0.3px;
  padding: 0 22px;
  color: var(--plaza-text-muted);
  border: 1px solid transparent;
  background: transparent;
  transition: all 0.18s ease;
}
.at-tabs :deep(.el-tabs__item:hover) {
  color: var(--plaza-text);
  background: var(--plaza-accent-soft);
}
.at-tabs :deep(.el-tabs__item.is-active) {
  color: var(--plaza-accent);
  background: var(--plaza-bg-card);
  border-color: var(--plaza-border);
  border-bottom-color: var(--plaza-bg-card);
}
.at-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
}
.at-tabs :deep(.el-tab-pane) {
  min-height: 0;
}

.tab-label-custom {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.tab-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 20px;
  color: #d97706;
  background: #fff7ed;
  border: 1px solid #fcd9a6;
  letter-spacing: 0.3px;
  line-height: 1.4;
}

/* ── 头部 ── */
.at-head {
  margin-bottom: 18px;
}
.at-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.at-title-led {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--signal, #ffa62b);
  box-shadow: 0 0 0 3px var(--signal-soft, rgba(255,166,43,.14));
  animation: at-pulse 2.4s ease-in-out infinite;
}
@keyframes at-pulse { 50% { opacity: 0.4; } }
.at-title-text {
  font-family: var(--font-display); font-size: 22px; font-weight: 700; color: var(--plaza-heading); margin: 0; letter-spacing: 0.2px;
}
.at-title-sub {
  font-family: var(--font-mono); font-size: 10px; color: var(--plaza-text-muted); letter-spacing: 2px;
}

/* ── 筛选卡片 ── */
.at-filters {
  margin-bottom: 16px;
}
.filter-card {
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  box-shadow: var(--plaza-shadow-organic);
  padding: 16px 20px;
}
.filter-row {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  flex-wrap: wrap;
}
.filter-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 130px;
}
.filter-item label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--plaza-text-muted);
  letter-spacing: 0.3px;
  text-transform: uppercase;
}
.filter-btns {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding-bottom: 1px;
}

/* ── 表格容器 ── */
.at-table-wrap {
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  box-shadow: var(--plaza-shadow-organic);
  overflow: hidden;
  flex: 1;
}
/* 列宽已收进容器内，隐藏因取整残留的横向滚动条，整表固定展示 */
.at-table-wrap :deep(.el-scrollbar__bar.is-horizontal) { display: none; }
.at-table-wrap :deep(.el-table__body-wrapper)::-webkit-scrollbar { height: 0; }

/* 通用标签 */
.tag-sm {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 20px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.2px;
  white-space: nowrap;
}
.mono-text {
  font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--plaza-heading);
  letter-spacing: 0.2px;
}

/* ── 展开行 ── */
.expand-panel {
  padding: 14px 20px;
  background: var(--plaza-bg-card);
  border-top: 1px solid var(--plaza-border);
}
.exp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px 24px;
}
.exp-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.exp-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--plaza-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.exp-val {
  font-size: 13.5px;
  color: var(--plaza-text);
  font-weight: 500;
}
.exp-val.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.exp-extraction {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--plaza-border);
}
.exp-clue-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
}
.exp-clue-section {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
}
.exp-clue-title {
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
  font-size: 12px;
  font-weight: 700;
}
.exp-clue-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.exp-clue-tag {
  max-width: 100%;
  padding: 4px 8px;
  border-radius: 7px;
  background: var(--plaza-bg-input);
  border: 1px solid var(--plaza-border);
  color: var(--plaza-text);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}
.exp-empty {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

/* ── 操作列 ── */
.action-cell {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.action-cell .el-button {
  font-size: 12px;
  padding: 3px 8px;
  margin-left: 0;
}

/* ── 分页 ── */
.at-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* ── 响应式 ── */
@media (max-width: 900px) {
  .filter-row { flex-direction: column; align-items: stretch; }
  .filter-item { min-width: unset; }
  .filter-btns { justify-content: flex-end; }
  .exp-grid { grid-template-columns: 1fr 1fr; }
}
</style>
