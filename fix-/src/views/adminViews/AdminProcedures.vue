<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Edit, View, Upload, DocumentDelete, Check, Box, WarningFilled, Delete } from '@element-plus/icons-vue'
import {
  getProcedureList, getProcedureDetail, createProcedure, updateProcedure,
  publishProcedure, archiveProcedure, saveSteps,
} from '@/api/procedure'

/* ========== 枚举 ========== */
const STATUS = {
  DRAFT:     { label: '草稿',   color: '#8a7c6c', bg: '#f1eadd', bar: '#b3a692' },
  PUBLISHED: { label: '已发布', color: '#c4602f', bg: '#f8ece2', bar: '#c4602f' },
  ARCHIVED:  { label: '已归档', color: '#5e8c3e', bg: '#f1f5e6', bar: '#5e8c3e' },
}
const LEVEL = { ROUTINE: '日常保养', MINOR: '小修', MAJOR: '大修' }
const DIFFICULTY = { 简单: '简单', 中等: '中等', 复杂: '复杂' }

/* ========== 列表状态 ========== */
const loading = ref(false)
const procedures = ref([])
const pagination = reactive({ page: 1, size: 12, total: 0 })
const stats = reactive({ draft: 0, published: 0, archived: 0 })

/* ========== 筛选 ========== */
const filterStatus = ref('')
const filterDeviceType = ref('')
const filterName = ref('')

const statusTabs = [
  { key: '',  label: '全部' },
  { key: 'DRAFT',     label: '草稿' },
  { key: 'PUBLISHED', label: '已发布' },
  { key: 'ARCHIVED',  label: '已归档' },
]

/* ========== 加载 ========== */
async function loadList(page = 1) {
  loading.value = true
  pagination.page = page
  try {
    const params = { page, size: pagination.size }
    if (filterStatus.value)     params.status     = filterStatus.value
    if (filterDeviceType.value) params.deviceType = filterDeviceType.value
    if (filterName.value)       params.name       = filterName.value

    const res = await getProcedureList(params)
    if (res.code === '200' || res.code === 200) {
      procedures.value = res.data?.records || res.data?.list || []
      pagination.total = res.data?.total || 0
      // 刷新统计（不带分页，取所有状态的计数）
      await refreshStats()
    }
  } catch (e) {
    ElMessage.error('加载规程失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function refreshStats() {
  try {
    const [draftRes, pubRes, archRes] = await Promise.all([
      getProcedureList({ page: 1, size: 1, status: 'DRAFT' }),
      getProcedureList({ page: 1, size: 1, status: 'PUBLISHED' }),
      getProcedureList({ page: 1, size: 1, status: 'ARCHIVED' }),
    ])
    stats.draft     = draftRes.data?.total || 0
    stats.published = pubRes.data?.total || 0
    stats.archived  = archRes.data?.total || 0
  } catch { /* 统计非关键 */ }
}

function switchStatus(key) {
  filterStatus.value = key
  loadList(1)
}

function handleSearch() { loadList(1) }

/* ========== 创建 / 编辑 / 查看 弹窗 ========== */
const dialog = reactive({
  show: false, mode: 'create', title: '', saving: false,
})
const detailDialog = reactive({ show: false })

const form = reactive({
  id: null, name: '', deviceType: '', maintenanceLevel: '', description: '',
})
const formSteps = ref([])
const detail = reactive({ procedure: null, steps: [] })

function emptyStep(order) {
  return {
    tempId: Date.now() + Math.random(),
    stepOrder: order,
    title: '',
    content: '',
    safetyNote: '',
    isCheckpoint: false,
    checkpointItems: [],
    estimatedMinutes: null,
    referenceImages: [],
  }
}

function openCreate() {
  dialog.mode = 'create'
  dialog.title = '新建标准规程'
  form.id = null
  form.name = ''
  form.deviceType = ''
  form.maintenanceLevel = ''
  form.description = ''
  formSteps.value = [emptyStep(1)]
  dialog.show = true
}

function openEdit(proc) {
  dialog.mode = 'edit'
  dialog.title = '编辑规程'
  form.id = proc.id
  form.name = proc.name || ''
  form.deviceType = proc.deviceType || ''
  form.maintenanceLevel = proc.maintenanceLevel || ''
  form.description = proc.description || ''
  dialog.show = true
  // 异步拉详情填充步骤
  loadStepsForEdit(proc.id)
}

async function loadStepsForEdit(id) {
  try {
    const res = await getProcedureDetail(id)
    if (res.code === '200' || res.code === 200) {
      const vo = res.data
      formSteps.value = (vo.steps || []).map((s, i) => ({
        ...s,
        tempId: s.id || (Date.now() + i),
        checkpointItems: s.checkpointItems || [],
        referenceImages: s.referenceImages || [],
      }))
    }
  } catch {
    formSteps.value = []
  }
}

async function openDetail(proc) {
  detailDialog.show = true
  detail.procedure = proc
  detail.steps = []
  try {
    const res = await getProcedureDetail(proc.id)
    if (res.code === '200' || res.code === 200) {
      detail.procedure = res.data
      detail.steps = res.data.steps || []
    }
  } catch { /* 使用列表缓存数据 */ }
}

/* ========== 步骤编辑操作 ========== */
function addStep() {
  const maxOrder = formSteps.value.reduce((m, s) => Math.max(m, s.stepOrder || 0), 0)
  formSteps.value.push(emptyStep(maxOrder + 1))
}
function removeStep(index) {
  if (formSteps.value.length <= 1) {
    ElMessage.warning('至少保留一个步骤')
    return
  }
  formSteps.value.splice(index, 1)
  // 重新编号
  formSteps.value.forEach((s, i) => (s.stepOrder = i + 1))
}
function toggleCheckpoint(step) {
  step.isCheckpoint = !step.isCheckpoint
  if (!step.isCheckpoint) step.checkpointItems = []
}

/* ========== 提交 ========== */
async function submitForm() {
  if (!form.name.trim()) { ElMessage.warning('请填写规程名称'); return }

  // 校验步骤
  for (let i = 0; i < formSteps.value.length; i++) {
    const s = formSteps.value[i]
    if (!s.title.trim()) { ElMessage.warning(`步骤 ${i + 1} 的标题不能为空`); return }
  }

  dialog.saving = true
  try {
    const dto = {
      name: form.name.trim(),
      deviceType: form.deviceType || undefined,
      maintenanceLevel: form.maintenanceLevel || undefined,
      description: form.description || undefined,
      steps: formSteps.value.map((s) => ({
        title: s.title,
        content: s.content || undefined,
        safetyNote: s.safetyNote || undefined,
        isCheckpoint: s.isCheckpoint,
        checkpointItems: s.checkpointItems?.length ? s.checkpointItems : undefined,
        estimatedMinutes: s.estimatedMinutes || undefined,
        referenceImages: s.referenceImages?.length ? s.referenceImages : undefined,
      })),
    }

    if (dialog.mode === 'create') {
      await createProcedure(dto)
      ElMessage.success('规程创建成功')
    } else {
      // 编辑：分两步——先更新基本信息，再保存步骤
      await updateProcedure(form.id, { name: dto.name, deviceType: dto.deviceType, maintenanceLevel: dto.maintenanceLevel, description: dto.description })
      await saveSteps(form.id, dto.steps)
      ElMessage.success('规程更新成功')
    }
    dialog.show = false
    loadList(pagination.page)
  } catch (e) {
    ElMessage.error('保存失败：' + (e.message || ''))
  } finally {
    dialog.saving = false
  }
}

/* ========== 发布 / 归档 ========== */
async function handlePublish(proc) {
  try {
    await ElMessageBox.confirm(
      `确认发布规程「${proc.name}」？发布后不可再编辑步骤。`,
      '发布确认',
      { confirmButtonText: '确认发布', cancelButtonText: '取消', type: 'success' }
    )
  } catch { return }
  try {
    await publishProcedure(proc.id)
    ElMessage.success('规程已发布')
    loadList(pagination.page)
  } catch (e) {
    ElMessage.error('发布失败：' + (e.message || ''))
  }
}

async function handleArchive(proc) {
  try {
    await ElMessageBox.confirm(
      `确认归档规程「${proc.name}」？归档后仅可查看。`,
      '归档确认',
      { confirmButtonText: '确认归档', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  try {
    await archiveProcedure(proc.id)
    ElMessage.success('规程已归档')
    loadList(pagination.page)
  } catch (e) {
    ElMessage.error('归档失败：' + (e.message || ''))
  }
}

/* ========== 格式化 ========== */
function formatDate(s) {
  if (!s) return '-'
  const d = new Date(s)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function sourceLabel(sourceType) {
  const map = { MANUAL_CREATE: '手动创建', AI_GENERATE: 'AI 生成', TASK_PROMOTE: '任务沉淀' }
  return map[sourceType] || sourceType || '手动创建'
}

const route = useRoute()
const router = useRouter()

// 从「沉淀审核」跳转过来时携带 ?edit={id}，自动打开该规程的编辑弹窗（复用现有编辑流程）
async function openEditByIdFromQuery() {
  const editId = route.query.edit
  if (!editId) return
  try {
    const res = await getProcedureDetail(editId)
    if ((res.code === '200' || res.code === 200) && res.data) {
      openEdit(res.data)
    }
  } catch {
    ElMessage.warning('未找到要编辑的规程')
  } finally {
    // 清掉 query，避免刷新/返回时重复弹出
    router.replace({ query: {} })
  }
}

onMounted(async () => {
  await loadList(1)
  openEditByIdFromQuery()
})
</script>

<template>
  <div class="ap-root">
    <!-- ====== 头部 ====== -->
    <div class="ap-head">
      <div class="ap-title-row">
        <span class="ap-title-led" />
        <h2 class="ap-title-text">标准规程</h2>
        <span class="ap-title-sub">STANDARD&nbsp;MAINTENANCE&nbsp;PROCEDURES</span>
      </div>
      <button class="ap-create-btn" @click="openCreate">
        <el-icon><Plus /></el-icon>新建规程
      </button>
    </div>

    <!-- ====== 统计卡片 ====== -->
    <div class="ap-stats">
      <div class="stat-card sc-draft" @click="switchStatus('DRAFT')">
        <span class="stat-num">{{ stats.draft }}</span>
        <span class="stat-label">草稿</span>
        <span class="stat-bar" />
      </div>
      <div class="stat-card sc-pub" @click="switchStatus('PUBLISHED')">
        <span class="stat-num">{{ stats.published }}</span>
        <span class="stat-label">已发布</span>
        <span class="stat-bar" />
      </div>
      <div class="stat-card sc-arch" @click="switchStatus('ARCHIVED')">
        <span class="stat-num">{{ stats.archived }}</span>
        <span class="stat-label">已归档</span>
        <span class="stat-bar" />
      </div>
    </div>

    <!-- ====== 筛选栏 ====== -->
    <div class="ap-filters">
      <!-- 状态 Tab -->
      <div class="status-tabs">
        <button
          v-for="tab in statusTabs" :key="tab.key"
          class="st-tab"
          :class="{ active: filterStatus === tab.key }"
          @click="switchStatus(tab.key)"
        >{{ tab.label }}</button>
      </div>
      <div class="filter-right">
        <el-input
          v-model="filterName"
          placeholder="搜索规程名称…"
          clearable
          size="default"
          style="width: 220px"
          @keyup.enter="handleSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="filterDeviceType" placeholder="设备类型" clearable size="default" style="width: 150px" @change="handleSearch">
          <el-option label="液压泵" value="液压泵" />
          <el-option label="电动机" value="电动机" />
          <el-option label="传送带" value="传送带" />
          <el-option label="压缩机" value="压缩机" />
          <el-option label="变压器" value="变压器" />
        </el-select>
        <el-button type="primary" @click="handleSearch">
          <el-icon><Search /></el-icon>搜索
        </el-button>
      </div>
    </div>

    <!-- ====== 规程卡片网格 ====== -->
    <div v-loading="loading" class="ap-grid-wrap">
      <!-- 空状态 -->
      <div v-if="!loading && procedures.length === 0" class="ap-empty">
        <div class="empty-icon-wrap">
          <svg class="empty-svg" viewBox="0 0 80 80" fill="none">
            <rect x="12" y="10" width="56" height="60" rx="6" stroke="currentColor" stroke-width="1.5" stroke-dasharray="5 3" opacity="0.4"/>
            <line x1="22" y1="26" x2="58" y2="26" stroke="currentColor" stroke-width="1.5" opacity="0.25"/>
            <line x1="22" y1="35" x2="50" y2="35" stroke="currentColor" stroke-width="1.5" opacity="0.2"/>
            <line x1="22" y1="44" x2="54" y2="44" stroke="currentColor" stroke-width="1.5" opacity="0.15"/>
          </svg>
        </div>
        <h3>{{ filterStatus ? `暂无「${STATUS[filterStatus]?.label || filterStatus}」状态的规程` : '暂无规程' }}</h3>
        <p>点击右上角「新建规程」创建第一条标准维护规程</p>
      </div>

      <!-- 卡片网格 -->
      <div v-else class="ap-grid">
        <div
          v-for="proc in procedures" :key="proc.id"
          class="proc-card"
          :class="`card-${proc.status?.toLowerCase()}`"
        >
          <!-- 左侧状态色条 -->
          <div class="card-bar" :style="{ background: (STATUS[proc.status] || STATUS.DRAFT).bar }" />

          <!-- 内容 -->
          <div class="card-body">
            <div class="card-top">
              <span class="card-status-tag" :style="{ background: (STATUS[proc.status] || STATUS.DRAFT).bg, color: (STATUS[proc.status] || STATUS.DRAFT).color }">
                {{ (STATUS[proc.status] || STATUS.DRAFT).label }}
              </span>
              <span class="card-ver">v{{ proc.version || 1 }}</span>
            </div>
            <h4 class="card-name">{{ proc.name }}</h4>
            <div class="card-meta">
              <span v-if="proc.deviceType" class="meta-chip">{{ proc.deviceType }}</span>
              <span v-if="proc.maintenanceLevel" class="meta-chip">{{ LEVEL[proc.maintenanceLevel] || proc.maintenanceLevel }}</span>
              <span class="meta-chip steps">{{ proc.totalSteps ?? (proc.steps?.length ?? 0) }} 步</span>
            </div>
            <div class="card-footer">
              <span class="card-source">{{ sourceLabel(proc.sourceType) }}</span>
              <span class="card-date">{{ formatDate(proc.createdAt) }}</span>
            </div>
          </div>

          <!-- 操作 -->
          <div class="card-actions" @click.stop>
            <template v-if="proc.status === 'DRAFT'">
              <button class="ca-btn ca-edit" title="编辑" @click="openEdit(proc)"><el-icon><Edit /></el-icon>编辑</button>
              <button class="ca-btn ca-pub" title="发布" @click="handlePublish(proc)"><el-icon><Upload /></el-icon>发布</button>
            </template>
            <template v-else-if="proc.status === 'PUBLISHED'">
              <button class="ca-btn ca-view" title="查看" @click="openDetail(proc)"><el-icon><View /></el-icon>查看</button>
              <button class="ca-btn ca-arch" title="归档" @click="handleArchive(proc)"><el-icon><Box /></el-icon>归档</button>
            </template>
            <button v-else class="ca-btn ca-view" title="查看" @click="openDetail(proc)"><el-icon><View /></el-icon>查看</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ====== 分页 ====== -->
    <div v-if="pagination.total > pagination.size" class="ap-pager">
      <el-pagination
        v-model:current-page="pagination.page"
        :page-size="pagination.size"
        :total="pagination.total"
        layout="prev, pager, next, total"
        @current-change="loadList"
      />
    </div>

    <!-- ================================================================
         创建 / 编辑 弹窗（含步骤编辑器）
         ================================================================ -->
    <el-dialog
      v-model="dialog.show"
      :title="dialog.title"
      width="680px"
      :close-on-click-modal="false"
      append-to-body
      align-center
    >
      <!-- 基本信息 -->
      <div class="dlg-section">
        <div class="dlg-section-title">
          <span class="ds-bar" />基本信息
        </div>
        <el-form label-width="90px" @submit.prevent>
          <el-form-item label="规程名称" required>
            <el-input v-model="form.name" placeholder="如：液压泵年度检修标准规程" maxlength="100" show-word-limit />
          </el-form-item>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="设备类型">
                <el-input v-model="form.deviceType" placeholder="如：液压泵" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="检修等级">
                <el-select v-model="form.maintenanceLevel" placeholder="选择等级" style="width:100%">
                  <el-option label="日常保养 (ROUTINE)" value="ROUTINE" />
                  <el-option label="小修 (MINOR)" value="MINOR" />
                  <el-option label="大修 (MAJOR)" value="MAJOR" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="规程说明">
            <el-input v-model="form.description" type="textarea" :rows="2" placeholder="简要说明规程适用范围和目的…" />
          </el-form-item>
        </el-form>
      </div>

      <!-- 步骤编辑器 -->
      <div class="dlg-section">
        <div class="dlg-section-title">
          <span class="ds-bar" style="background:#a8605f" />维护步骤
          <span class="step-count">{{ formSteps.length }} 步</span>
        </div>

        <div class="steps-editor">
          <div
            v-for="(step, i) in formSteps" :key="step.tempId"
            class="se-card"
            :class="{ 'se-checkpoint': step.isCheckpoint }"
          >
            <div class="se-head">
              <span class="se-num">{{ String(i + 1).padStart(2, '0') }}</span>
              <el-input
                v-model="step.title"
                placeholder="步骤标题（必填）"
                size="default"
                class="se-title-input"
              />
              <div class="se-head-actions">
                <el-tooltip content="设为检查点" placement="top">
                  <button
                    class="se-hbtn"
                    :class="{ active: step.isCheckpoint }"
                    @click="toggleCheckpoint(step)"
                    type="button"
                  >
                    <el-icon><Check /></el-icon>
                  </button>
                </el-tooltip>
                <button class="se-hbtn danger" @click="removeStep(i)" type="button">
                  <el-icon><Delete /></el-icon>
                </button>
              </div>
            </div>

            <div class="se-body">
              <el-input
                v-model="step.content"
                type="textarea"
                :rows="2"
                placeholder="操作详细内容…"
              />
              <el-input
                v-model="step.safetyNote"
                placeholder="安全注意事项"
                class="se-safety"
              >
                <template #prefix>
                  <span style="color:#ef4444;font-weight:700;font-size:12px">安全</span>
                </template>
              </el-input>

              <el-row v-if="step.isCheckpoint" :gutter="12" class="se-extra">
                <el-col :span="12">
                  <el-input
                    v-model="step.checkpointItems[0]"
                    placeholder="检查项（逗号分隔多选）"
                    size="small"
                  />
                </el-col>
                <el-col :span="6">
                  <el-input-number
                    v-model="step.estimatedMinutes"
                    :min="0"
                    :max="480"
                    placeholder="耗时(分)"
                    controls-position="right"
                    size="small"
                    style="width:100%"
                  />
                </el-col>
                <el-col :span="6">
                  <el-input
                    v-model="step.referenceImages[0]"
                    placeholder="图片URL"
                    size="small"
                  />
                </el-col>
              </el-row>
              <el-row v-else :gutter="12" class="se-extra">
                <el-col :span="12">
                  <el-input-number
                    v-model="step.estimatedMinutes"
                    :min="0"
                    :max="480"
                    placeholder="预估耗时(分钟)"
                    controls-position="right"
                    size="small"
                    style="width:100%"
                  />
                </el-col>
              </el-row>
            </div>
          </div>

          <button class="add-step-btn" @click="addStep" type="button">
            <el-icon><Plus /></el-icon>添加步骤
          </button>
        </div>
      </div>

      <template #footer>
        <button class="dlg-btn cancel" @click="dialog.show = false" type="button">取消</button>
        <button class="dlg-btn ok" :disabled="dialog.saving" @click="submitForm" type="button">
          {{ dialog.saving ? '保存中…' : (dialog.mode === 'create' ? '创建规程' : '保存修改') }}
        </button>
      </template>
    </el-dialog>

    <!-- ================================================================
         详情查看弹窗（只读）
         ================================================================ -->
    <el-dialog
      v-model="detailDialog.show"
      :title="detail.procedure?.name || '规程详情'"
      width="660px"
      append-to-body
      align-center
    >
      <div v-if="detail.procedure" class="detail-view">
        <div class="dv-header">
          <div class="dv-tags">
            <span class="dv-status" :style="{ background: (STATUS[detail.procedure.status] || {}).bg, color: (STATUS[detail.procedure.status] || {}).color }">
              {{ (STATUS[detail.procedure.status] || {}).label || detail.procedure.status }}
            </span>
            <span class="dv-ver">版本 v{{ detail.procedure.version || 1 }}</span>
            <span v-if="detail.procedure.sourceType" class="dv-source">{{ sourceLabel(detail.procedure.sourceType) }}</span>
          </div>
          <div class="dv-meta-row">
            <span v-if="detail.procedure.deviceType">{{ detail.procedure.deviceType }}</span>
            <span v-if="detail.procedure.maintenanceLevel">{{ LEVEL[detail.procedure.maintenanceLevel] || detail.procedure.maintenanceLevel }}</span>
            <span>{{ detail.steps.length }} 个步骤</span>
          </div>
          <p v-if="detail.procedure.description" class="dv-desc">{{ detail.procedure.description }}</p>
        </div>

        <div class="dv-steps">
          <div class="dv-section-title">
            <span class="ds-bar" style="background:#a8605f" />步骤清单
          </div>
          <div v-for="(step, i) in detail.steps" :key="step.id || i" class="dv-step" :class="{ 'dv-cp': step.isCheckpoint }">
            <div class="dvs-head">
              <span class="dvs-num">{{ String(i + 1).padStart(2, '0') }}</span>
              <span class="dvs-title">{{ step.title }}</span>
              <span v-if="step.isCheckpoint" class="dvs-cp-tag">检查点</span>
              <span v-if="step.estimatedMinutes" class="dvs-time">{{ step.estimatedMinutes }} min</span>
            </div>
            <p v-if="step.content" class="dvs-content">{{ step.content }}</p>
            <div v-if="step.safetyNote" class="dvs-safety">
              <span class="safety-icon">安全</span>{{ step.safetyNote }}
            </div>
            <div v-if="step.checkpointItems?.length" class="dvs-items">
              <span class="dvs-items-label">检查项：</span>
              <span v-for="(item, j) in step.checkpointItems" :key="j" class="dvs-item-tag">{{ item }}</span>
            </div>
          </div>
          <p v-if="!detail.steps.length" class="dv-empty">暂无步骤</p>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ============================================================
   AdminProcedures — 标准规程管理
   「精密工程手册」设计方向——卡片网格 + 状态色条 + 步骤编号
   ============================================================ */
.ap-root {
  max-width: 1240px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── 头部 ── */
.ap-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.ap-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ap-title-led {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--signal, #ffa62b);
  box-shadow: 0 0 0 3px var(--signal-soft, rgba(255,166,43,.14));
  animation: ap-pulse 2.4s ease-in-out infinite;
}
@keyframes ap-pulse { 50% { opacity: 0.4; } }
.ap-title-text {
  font-family: var(--font-display); font-size: 22px; font-weight: 700; color: var(--plaza-heading); margin: 0; letter-spacing: 0.2px;
}
.ap-title-sub {
  font-family: var(--font-mono); font-size: 10px; color: #b3a692; letter-spacing: 1.8px;
}
.ap-create-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 9px 20px;
  background: var(--plaza-accent-grad); color: #fff;
  border: none; border-radius: 10px;
  font-size: 14px; font-weight: 600; cursor: pointer;
  transition: all 0.18s ease; box-shadow: 0 8px 20px rgba(196,96,47,.28);
}
.ap-create-btn:hover { filter: brightness(1.05); transform: translateY(-2px); box-shadow: 0 12px 26px rgba(196,96,47,.36); }

/* ── 统计卡片 ── */
.ap-stats {
  display: flex; gap: 14px; margin-bottom: 16px;
}
.stat-card {
  flex: 1; position: relative; overflow: hidden;
  background: #fff; border: 1px solid var(--plaza-border);
  border-radius: 12px; padding: 14px 18px;
  cursor: pointer; transition: all 0.2s ease; box-shadow: var(--plaza-shadow-organic);
}
.stat-card:hover { transform: translateY(-2px); box-shadow: var(--plaza-shadow-organic-hover); }
.stat-num {
  font-family: var(--font-mono); font-size: 30px; font-weight: 700; color: var(--plaza-heading); line-height: 1.1;
}
.stat-label {
  display: block; font-size: 12.5px; font-weight: 600; color: var(--plaza-text-muted); letter-spacing: 0.3px;
}
.stat-bar {
  position: absolute; left: 0; bottom: 0; width: 100%; height: 3px;
  border-radius: 0 0 0 4px;
}
.sc-draft .stat-bar { background: #b3a692; }
.sc-pub   .stat-bar { background: var(--plaza-accent); }
.sc-arch  .stat-bar { background: #22c55e; }
.sc-draft:hover  { border-color: #b3a692; }
.sc-pub:hover    { border-color: var(--plaza-accent); }
.sc-arch:hover   { border-color: #22c55e; }

/* ── 筛选 ── */
.ap-filters {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  margin-bottom: 20px; flex-wrap: wrap;
}
.status-tabs {
  display: flex; gap: 4px; background: #f1eadd; border-radius: 10px; padding: 3px;
}
.st-tab {
  padding: 6px 16px; border: none; border-radius: 8px;
  font-size: 13px; font-weight: 600; cursor: pointer;
  background: transparent; color: var(--plaza-text-muted);
  transition: all 0.15s ease;
}
.st-tab:hover { color: var(--plaza-text); }
.st-tab.active { background: #fff; color: var(--plaza-heading); box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.filter-right { display: flex; gap: 10px; align-items: center; }

/* ── 网格 ── */
.ap-grid-wrap { min-height: 320px; }
.ap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

/* 空状态 */
.ap-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 0; text-align: center; color: var(--plaza-text-muted); }
.empty-icon-wrap { width: 80px; height: 80px; border-radius: 50%; background: #fff; border: 1.5px dashed var(--plaza-border); display: flex; align-items: center; justify-content: center; margin-bottom: 16px; }
.empty-svg { width: 48px; height: 48px; color: var(--plaza-text-muted); }
.ap-empty h3 { font-size: 1rem; font-weight: 700; color: var(--plaza-heading); margin: 0 0 6px; }
.ap-empty p { font-size: 13px; margin: 0; }

/* ── 规程卡片 ── */
.proc-card {
  display: flex; background: #fff; border: 1px solid var(--plaza-border);
  border-radius: 12px; overflow: hidden; transition: all 0.22s ease;
  box-shadow: var(--plaza-shadow-organic); min-height: 140px;
}
.proc-card:hover { transform: translateY(-3px); box-shadow: var(--plaza-shadow-organic-hover); border-color: var(--plaza-accent); }

.card-bar { width: 4px; flex-shrink: 0; transition: width 0.25s ease; }
.proc-card:hover .card-bar { width: 6px; }

.card-body { flex: 1; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.card-top { display: flex; align-items: center; gap: 8px; }
.card-status-tag { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 20px; }
.card-ver { font-family: var(--font-mono); font-size: 11px; color: #b3a692; font-weight: 600; }
.card-name { font-size: 16px; font-weight: 700; color: var(--plaza-heading); margin: 0; line-height: 1.35; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-meta { display: flex; gap: 6px; flex-wrap: wrap; }
.meta-chip { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 6px; background: #f1eadd; color: #6b5d4c; }
.meta-chip.steps { background: #f5ece8; color: #a8605f; }
.card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: auto; font-size: 11.5px; color: #b3a692; }
.card-source { font-weight: 500; }

.card-actions { display: flex; flex-direction: column; gap: 1px; width: 72px; flex-shrink: 0; border-left: 1px solid #f1eadd; }
.ca-btn {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px; border: none; background: transparent; cursor: pointer;
  font-size: 12px; font-weight: 600; transition: all 0.15s ease;
  color: var(--plaza-text-muted);
}
.ca-btn .el-icon { font-size: 16px; }
.ca-edit:hover { background: #f8ece2; color: var(--plaza-accent); }
.ca-pub:hover  { background: #f0fdf4; color: #16a34a; }
.ca-view:hover { background: #f8ece2; color: var(--plaza-accent); }
.ca-arch:hover { background: #f5ece8; color: #a8605f; }

/* ── 分页 ── */
.ap-pager { display: flex; justify-content: center; margin-top: 24px; }

/* ================================================================
   弹窗共享样式
   ================================================================ */
.dlg-section { margin-bottom: 18px; }
.dlg-section-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 700; color: var(--plaza-heading);
  margin-bottom: 12px; letter-spacing: 0.2px;
}
.ds-bar { width: 3px; height: 14px; border-radius: 2px; background: var(--plaza-accent); display: inline-block; }
.step-count { font-family: var(--font-mono); font-size: 12px; color: #a8605f; font-weight: 600; margin-left: auto; }

/* 步骤编辑器 */
.steps-editor { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; padding-right: 4px; }
.se-card {
  background: #faf4ea; border: 1px solid #ece3d4; border-radius: 10px; padding: 12px;
  transition: border-color 0.2s ease;
}
.se-card.se-checkpoint { border-color: #bbf7d0; background: #f9fefb; }
.se-head { display: flex; align-items: center; gap: 10px; }
.se-num {
  width: 30px; height: 30px; border-radius: 8px; display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); font-size: 14px; font-weight: 700;
  background: #ece3d4; color: #6b5d4c; flex-shrink: 0;
}
.se-checkpoint .se-num { background: #dcfce7; color: #16a34a; }
.se-title-input { flex: 1; }
.se-head-actions { display: flex; gap: 4px; }
.se-hbtn {
  width: 30px; height: 30px; border-radius: 6px; border: 1px solid #ece3d4;
  background: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center;
  color: #b3a692; transition: all 0.15s ease; font-size: 14px;
}
.se-hbtn:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); }
.se-hbtn.active { background: #f0fdf4; border-color: #bbf7d0; color: #16a34a; }
.se-hbtn.danger:hover { border-color: #fca5a5; color: #ef4444; background: #fef2f2; }
.se-body { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.se-safety { margin-top: 2px; }
.se-extra { align-items: center; }

.add-step-btn {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 10px; border: 1.5px dashed #d6c8b2; border-radius: 10px;
  background: transparent; color: var(--plaza-text-muted);
  font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.18s ease;
}
.add-step-btn:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); background: #f8efe3; }

/* 弹窗按钮 */
.dlg-btn {
  padding: 9px 22px; border-radius: 8px; font-weight: 600; font-size: 13px; cursor: pointer; border: 1px solid; transition: all 0.15s ease;
}
.dlg-btn.cancel { background: #fff; color: var(--plaza-text-muted); border-color: var(--plaza-border); margin-right: 8px; }
.dlg-btn.cancel:hover { color: var(--plaza-text); border-color: #c9a878; }
.dlg-btn.ok { background: var(--plaza-accent); color: #fff; border-color: var(--plaza-accent); }
.dlg-btn.ok:hover { background: #a54d22; }
.dlg-btn.ok:disabled { opacity: 0.6; cursor: not-allowed; }

/* ================================================================
   详情查看弹窗
   ================================================================ */
.detail-view { display: flex; flex-direction: column; gap: 16px; }
.dv-header { display: flex; flex-direction: column; gap: 8px; }
.dv-tags { display: flex; align-items: center; gap: 8px; }
.dv-status { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 20px; }
.dv-ver { font-family: var(--font-mono); font-size: 12px; color: #b3a692; font-weight: 600; }
.dv-source { font-size: 11px; color: var(--plaza-text-muted); background: #f1eadd; padding: 2px 8px; border-radius: 6px; }
.dv-meta-row { display: flex; gap: 12px; font-size: 13px; color: var(--plaza-text-muted); }
.dv-desc { font-size: 13.5px; color: var(--plaza-text); line-height: 1.7; margin: 4px 0 0; }
.dv-section-title { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; color: var(--plaza-heading); margin-bottom: 10px; }

.dv-steps { display: flex; flex-direction: column; gap: 8px; }
.dv-step { background: #faf4ea; border: 1px solid #ece3d4; border-radius: 10px; padding: 12px 14px; }
.dv-step.dv-cp { border-color: #bbf7d0; background: #f9fefb; }
.dvs-head { display: flex; align-items: center; gap: 10px; }
.dvs-num {
  width: 28px; height: 28px; border-radius: 7px; display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); font-size: 13px; font-weight: 700;
  background: #ece3d4; color: #6b5d4c; flex-shrink: 0;
}
.dv-cp .dvs-num { background: #dcfce7; color: #16a34a; }
.dvs-title { font-size: 14px; font-weight: 700; color: var(--plaza-heading); }
.dvs-cp-tag { font-size: 10.5px; font-weight: 600; color: #16a34a; background: #dcfce7; padding: 2px 7px; border-radius: 10px; }
.dvs-time { font-family: var(--font-mono); font-size: 11px; color: #b3a692; margin-left: auto; }
.dvs-content { font-size: 13px; color: var(--plaza-text); margin: 8px 0 0; line-height: 1.6; }
.dvs-safety {
  margin-top: 6px; padding: 6px 10px; background: #fef2f2; border-left: 3px solid #ef4444;
  border-radius: 0 6px 6px 0; font-size: 12.5px; color: #991b1b; display: flex; align-items: flex-start; gap: 6px;
}
.safety-icon { flex-shrink: 0; }
.dvs-items { margin-top: 6px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12px; color: var(--plaza-text-muted); }
.dvs-item-tag { background: #f1eadd; padding: 2px 7px; border-radius: 4px; font-size: 11px; }
.dv-empty { text-align: center; color: var(--plaza-text-muted); font-size: 13px; padding: 20px; }

/* ── 响应式 ── */
@media (max-width: 768px) {
  .ap-head { flex-direction: column; gap: 12px; align-items: stretch; }
  .ap-create-btn { align-self: flex-end; }
  .ap-stats { flex-wrap: wrap; }
  .stat-card { flex: 1 1 calc(50% - 8px); min-width: 120px; }
  .ap-filters { flex-direction: column; align-items: stretch; }
  .filter-right { flex-wrap: wrap; }
  .ap-grid { grid-template-columns: 1fr; }
}
</style>
