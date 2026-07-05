<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Check, CloseBold } from '@element-plus/icons-vue'
import { getReviewList, approveReview, rejectReview } from '@/api/expiration'

/* ========== 触发来源标签 ========== */
const TRIGGER_MAP = {
  task_promotion: { label: '任务沉淀', color: '#5e8c3e', bg: '#f1f5e6' },
  manual_upgrade: { label: '手册更新', color: '#4c7db8', bg: '#eaf1f9' },
}

/* ========== 判定结果标签 ========== */
const VERDICT_MAP = {
  REPLACE:     { label: '替代',  color: '#c5402c', bg: '#fbeae4' },
  SUPPLEMENT:  { label: '补充',  color: '#a8605f', bg: '#f5ece8' },
  UNRELATED:   { label: '无关',  color: 'var(--plaza-text-muted)', bg: 'var(--plaza-panel-bg)' },
}

/* ========== 置信度档位 ========== */
function confidenceTag(conf) {
  const n = Number(conf)
  if (n >= 0.8) return { label: '高', color: '#5e8c3e', bg: '#f1f5e6' }
  if (n >= 0.5) return { label: '中', color: '#df9226', bg: '#fdf2e0' }
  return { label: '低', color: '#c5402c', bg: '#fbeae4' }
}

/* ========== 状态 ========== */
const loading = ref(false)
const reviews = ref([])
const busyIds = ref(new Set())
const statusFilter = ref('PENDING')

const stats = reactive({ total: 0, pending: 0, approved: 0, rejected: 0 })

/* ========== 加载 ========== */
async function loadReviews() {
  loading.value = true
  try {
    const res = await getReviewList({ page: 1, size: 50, status: statusFilter.value })
    if (res.code === '200' || res.code === 200) {
      reviews.value = res.data?.records || res.data?.list || []
      const all = await getReviewList({ page: 1, size: 1 })
      if (all.code === '200' || all.code === 200) {
        stats.total = all.data?.total || 0
      }
      // 分别计数
      const pRes = await getReviewList({ page: 1, size: 1, status: 'PENDING' })
      if (pRes.code === '200' || pRes.code === 200) stats.pending = pRes.data?.total || 0
    } else {
      reviews.value = []
      stats.total = stats.pending = 0
    }
  } catch (e) {
    reviews.value = []
    stats.total = stats.pending = 0
  } finally {
    loading.value = false
  }
}

/* ========== 操作 ========== */
async function handleApprove(review) {
  const id = review.id
  if (busyIds.value.has(id)) return

  try {
    await ElMessageBox.confirm(
      '确认后将把旧知识节点标记为「已过时」（deprecated），该操作不可逆。确定？',
      '确认过期',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }

  busyIds.value.add(id)
  try {
    const res = await approveReview(id)
    if (res.code === '200' || res.code === 200) {
      ElMessage.success('已标记为过期')
      removeCard(id)
    } else {
      ElMessage.error(res.message || '操作失败')
    }
  } catch { ElMessage.error('操作失败') }
  finally { busyIds.value.delete(id) }
}

async function handleReject(review) {
  const id = review.id
  if (busyIds.value.has(id)) return

  busyIds.value.add(id)
  try {
    const res = await rejectReview(id)
    if (res.code === '200' || res.code === 200) {
      ElMessage.success('已驳回，旧知识保持有效')
      removeCard(id)
    } else {
      ElMessage.error(res.message || '操作失败')
    }
  } catch { ElMessage.error('操作失败') }
  finally { busyIds.value.delete(id) }
}

function removeCard(id) {
  reviews.value = reviews.value.filter((r) => r.id !== id)
  stats.pending = Math.max(0, stats.pending - 1)
  stats.total = Math.max(0, stats.total - 1)
}

/* ========== 初始化 ========== */
onMounted(loadReviews)
</script>

<template>
  <div class="expiration-review-section">
    <!-- Header -->
    <div class="section-header">
      <div class="header-left">
        <h3 class="section-title">过期判定待审</h3>
        <span v-if="stats.pending > 0" class="pending-badge">{{ stats.pending }} 条待处理</span>
      </div>
      <div class="header-right">
        <el-button
          text
          :icon="Refresh"
          :loading="loading"
          :disabled="loading"
          @click="loadReviews"
        >
          刷新
        </el-button>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && reviews.length === 0" class="empty-state">
      <p>{{ statusFilter === 'PENDING' ? '暂无待审核的过期判定' : '暂无记录' }}</p>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="loading-skeleton">
      <span>加载中…</span>
    </div>

    <!-- 列表 -->
    <template v-if="!loading && reviews.length > 0">
      <div
        v-for="review in reviews"
        :key="review.id"
        class="review-card"
      >
        <!-- 头部标签行 -->
        <div class="card-top">
          <span
            class="badge trigger-badge"
            :style="{ color: (TRIGGER_MAP[review.triggerType] || TRIGGER_MAP.task_promotion).color, background: (TRIGGER_MAP[review.triggerType] || TRIGGER_MAP.task_promotion).bg }"
          >
            {{ (TRIGGER_MAP[review.triggerType] || TRIGGER_MAP.task_promotion).label }}
          </span>

          <span
            class="badge verdict-badge"
            :style="{ color: (VERDICT_MAP[review.verdict] || VERDICT_MAP.UNRELATED).color, background: (VERDICT_MAP[review.verdict] || VERDICT_MAP.UNRELATED).bg }"
          >
            判定：{{ (VERDICT_MAP[review.verdict] || VERDICT_MAP.UNRELATED).label }}
          </span>

          <span
            v-if="review.confidence != null"
            class="badge confidence-badge"
            :style="{ color: confidenceTag(review.confidence).color, background: confidenceTag(review.confidence).bg }"
          >
            置信度 {{ (Number(review.confidence) * 100).toFixed(0) }}%
          </span>

          <span class="device-tag" v-if="review.deviceName">
            {{ review.deviceName }}
          </span>
        </div>

        <!-- 新旧对比 -->
        <div class="compare-row">
          <!-- 旧知识 -->
          <div class="compare-box old-box">
            <div class="compare-label">📋 候选旧知识</div>
            <div class="compare-name">{{ review.candidateSolutionTitle || review.candidateFaultName || '—' }}</div>
            <div class="compare-id">Neo4j ID: {{ review.candidateNodeId }}</div>
          </div>

          <!-- 箭头 -->
          <div class="compare-arrow">→</div>

          <!-- 新知识 -->
          <div class="compare-box new-box">
            <div class="compare-label">🆕 新知识</div>
            <div class="compare-name">{{ review.newSolutionTitle || review.newFaultName || '—' }}</div>
            <div class="compare-id" v-if="review.newSolutionSummary">{{ review.newSolutionSummary.slice(0, 120) }}{{ review.newSolutionSummary.length > 120 ? '…' : '' }}</div>
          </div>
        </div>

        <!-- LLM 理由 -->
        <div class="reason-row" v-if="review.llmReason">
          <span class="reason-label">AI 分析：</span>
          <span class="reason-text">{{ review.llmReason }}</span>
        </div>

        <!-- 操作按钮 -->
        <div class="card-actions">
          <el-button
            type="danger"
            plain
            size="small"
            :icon="Check"
            :loading="busyIds.has(review.id)"
            :disabled="busyIds.has(review.id)"
            @click="handleApprove(review)"
          >
            确认过期
          </el-button>
          <el-button
            type="default"
            size="small"
            :icon="CloseBold"
            :loading="busyIds.has(review.id)"
            :disabled="busyIds.has(review.id)"
            @click="handleReject(review)"
          >
            驳回
          </el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.expiration-review-section {
  padding: 0;
}

/* ---- Header ---- */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--plaza-text, #1a1a1a);
}
.pending-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  color: #df9226;
  background: #fdf2e0;
  font-weight: 500;
}

/* ---- Empty / Loading ---- */
.empty-state {
  padding: 32px 16px;
  text-align: center;
  color: var(--plaza-text-muted, #999);
  font-size: 14px;
}
.loading-skeleton {
  padding: 24px 16px;
  text-align: center;
  color: var(--plaza-text-muted, #999);
  font-size: 13px;
}

/* ---- Card ---- */
.review-card {
  border: 1px solid var(--plaza-border, #eee);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 10px;
  background: var(--plaza-bg-card, #fff);
  transition: box-shadow 0.15s;
}
.review-card:hover {
  box-shadow: 0 1px 6px rgba(0,0,0,0.06);
}

/* ---- Top badges ---- */
.card-top {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  font-weight: 500;
  white-space: nowrap;
}
.device-tag {
  font-size: 12px;
  color: var(--plaza-text-muted, #888);
  margin-left: auto;
}

/* ---- Compare row ---- */
.compare-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
  margin-bottom: 8px;
}
.compare-box {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
}
.old-box {
  background: #fafafa;
  border: 1px dashed #ddd;
}
.new-box {
  background: #f0faf4;
  border: 1px solid #c3e6cb;
}
.compare-label {
  font-size: 11px;
  color: var(--plaza-text-muted, #888);
  margin-bottom: 4px;
}
.compare-name {
  font-weight: 600;
  color: var(--plaza-text, #333);
  word-break: break-all;
}
.compare-id {
  font-size: 11px;
  color: var(--plaza-text-muted, #999);
  margin-top: 2px;
  word-break: break-all;
}
.compare-arrow {
  display: flex;
  align-items: center;
  font-size: 18px;
  color: var(--plaza-text-muted, #bbb);
  flex-shrink: 0;
}

/* ---- Reason ---- */
.reason-row {
  font-size: 12px;
  color: var(--plaza-text-muted, #666);
  margin-bottom: 10px;
  padding: 6px 10px;
  background: #fefbf0;
  border-radius: 4px;
  border-left: 3px solid #f0c929;
}
.reason-label {
  font-weight: 600;
  color: #b0881c;
}
.reason-text {
  word-break: break-all;
}

/* ---- Actions ---- */
.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
</style>
