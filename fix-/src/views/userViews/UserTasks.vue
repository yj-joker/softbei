<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { gsap } from 'gsap'
import {
  ArrowRight,
  CircleCheck,
  Delete,
  Plus,
  Refresh,
  Search,
  Tickets,
  Timer,
} from '@element-plus/icons-vue'
import { deleteTask, getTaskDetail, listTasks } from '@/api/maintenanceTask'
import { notifyStore } from '@/stores/notifyStore'
import { taskStatus, TASK_STATUS, urgency } from '@/constants/taskStatus'
import TaskCreateDialog from '@/components/task/TaskCreateDialog.vue'

const router = useRouter()
const pageRef = ref(null)
const loading = ref(false)
const tasks = ref([])
const total = ref(0)
const showCreate = ref(false)
const query = reactive({ page: 1, size: 12, status: '', deviceName: '' })

let taskMotionContext = null

const statusTabs = computed(() => [
  { value: '', label: '全部任务' },
  ...Object.entries(TASK_STATUS).map(([value, meta]) => ({ value, label: meta.label })),
])

const pageStats = computed(() => {
  const rows = tasks.value
  return {
    waiting: rows.filter((t) => ['CREATED', 'GENERATING', 'GENERATED'].includes(t.status)).length,
    executing: rows.filter((t) => t.status === 'EXECUTING').length,
    completed: rows.filter((t) => t.status === 'CLOSED').length,
  }
})

const overviewMetrics = computed(() => [
  { label: '检索结果', value: total.value, note: '条任务', type: 'total' },
  { label: '当前页待处理', value: pageStats.value.waiting, note: '生成 / 待执行', type: 'waiting' },
  { label: '当前页执行中', value: pageStats.value.executing, note: '现场作业', type: 'executing' },
  { label: '当前页已完成', value: pageStats.value.completed, note: '闭环任务', type: 'completed' },
])

const resultRange = computed(() => {
  if (!total.value || !tasks.value.length) return '0'
  const start = (query.page - 1) * query.size + 1
  const end = start + tasks.value.length - 1
  return `${start}-${end}`
})

const DONE_STEP_STATUSES = new Set(['AI_PASSED', 'COMPLETED', 'SKIPPED'])

function flowStage(task) {
  const total = Number(task.stepCount || 0)
  const completed = Number(task.completedStepCount || 0)

  if (task.status === 'CLOSED') {
    return {
      percent: 100,
      label: total ? `已完成 ${total} / ${total} 步` : '任务已闭环',
    }
  }

  if (task.status === 'EXECUTING') {
    const percent = total ? Math.round((completed / total) * 100) : 0
    return {
      percent: Math.min(100, Math.max(0, percent)),
      label: total ? `已完成 ${completed} / ${total} 步` : '等待步骤同步',
    }
  }

  const labels = {
    CREATED: '等待生成规程',
    GENERATING: 'AI 生成步骤',
    GENERATED: total ? `等待执行 · 0 / ${total} 步` : '等待开始执行',
    GENERATE_FAILED: '生成异常待重试',
  }
  return { percent: 0, label: labels[task.status] || '等待状态同步' }
}

function formatDate(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '等待时间信息'
}

async function animateTaskCards() {
  await nextTick()
  if (!pageRef.value || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  taskMotionContext?.revert()
  taskMotionContext = gsap.context(() => {
    gsap.fromTo(
      '.task-card',
      { autoAlpha: 0, y: 14 },
      { autoAlpha: 1, y: 0, duration: 0.4, stagger: 0.045, ease: 'power2.out', clearProps: 'transform,opacity,visibility' },
    )
  }, pageRef.value)
}

async function load() {
  loading.value = true
  let loaded = false
  try {
    const res = await listTasks({
      ...query,
      status: query.status || undefined,
      deviceName: query.deviceName || undefined,
    })
    const d = res?.data || {}
    const records = d.records || []
    const detailResults = await Promise.all(records.map(async (task) => {
      if (task.status !== 'EXECUTING') return task
      try {
        const detail = await getTaskDetail(task.id)
        const steps = detail?.data?.steps || []
        const completedStepCount = steps.filter((step) => DONE_STEP_STATUSES.has(step.status)).length
        return {
          ...task,
          stepCount: Number(task.stepCount || steps.length),
          completedStepCount,
        }
      } catch {
        return task
      }
    }))
    tasks.value = detailResults
    total.value = Number(d.total || 0)
    loaded = true
  } catch (err) {
    ElMessage.error('加载任务失败：' + (err.message || '请稍后重试'))
  } finally {
    loading.value = false
  }
  if (loaded) animateTaskCards()
}

function selectStatus(status) {
  if (query.status === status) return
  query.status = status
  query.page = 1
  load()
}

function resetSearch() {
  query.page = 1
  load()
}

function resetFilters() {
  query.status = ''
  query.deviceName = ''
  resetSearch()
}

function openTask(task) {
  router.push(`/user/tasks/${task.id}`)
}

async function confirmDelete(e, task) {
  e.stopPropagation()
  try {
    await ElMessageBox.confirm(
      `确认删除任务「${task.taskNumber || task.id}」？此操作不可恢复，关联步骤和对话记录也会一并删除。`,
      '删除检修任务',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    await deleteTask(task.id)
    ElMessage.success('任务已删除')
    load()
  } catch (err) {
    if (err === 'cancel') return
    ElMessage.error('删除失败：' + (err.message || '请稍后重试'))
  }
}

function onCreated(taskId) {
  load()
  if (taskId) router.push(`/user/tasks/${taskId}`)
}

watch(() => notifyStore.state.notifications.length, () => load())
onMounted(load)
onUnmounted(() => taskMotionContext?.revert())
</script>

<template>
  <div ref="pageRef" class="tasks-page">
    <!-- 页头 -->
    <header class="page-head">
      <div class="title-wrap">
        <span class="kicker">MAINTENANCE&nbsp;TASKS · PERSONAL&nbsp;QUEUE</span>
        <h1 class="title"><span class="led" />检修任务</h1>
        <p class="subtitle">统一查看个人任务状态、设备对象与执行进度，快速进入现场作业流程。</p>
      </div>
      <button class="new-btn" @click="showCreate = true">
        <el-icon><Plus /></el-icon> 新建任务
      </button>
    </header>

    <!-- 个人调度台（暖咖控制台） -->
    <section class="overview">
      <div class="overview-grid" />
      <div class="overview-copy">
        <span class="overview-eyebrow">PERSONAL TASK DISPATCH</span>
        <h2>个人检修调度台</h2>
        <p>聚合任务阶段、现场执行与闭环状态，优先处理正在生成和执行中的任务。</p>
        <div class="overview-sync">
          <i />
          <span>实时同步已启用</span>
          <b>PAGE {{ String(query.page).padStart(2, '0') }}</b>
        </div>
      </div>
      <div class="overview-metrics">
        <article
          v-for="m in overviewMetrics"
          :key="m.label"
          class="overview-metric"
          :class="'is-' + m.type"
        >
          <span>{{ m.label }}</span>
          <strong>{{ m.value }}</strong>
          <small>{{ m.note }}</small>
        </article>
      </div>
    </section>

    <!-- 筛选 -->
    <section class="filter-card">
      <div class="filter-heading">
        <div class="fh-left">
          <span class="filter-icon"><el-icon><Tickets /></el-icon></span>
          <span class="fh-copy">
            <b>任务队列筛选</b>
            <small>按流程状态或设备名称快速定位</small>
          </span>
        </div>
        <span class="filter-result">显示 {{ resultRange }} / {{ total }}</span>
      </div>

      <div class="status-tabs">
        <button
          v-for="tab in statusTabs"
          :key="tab.value || 'ALL'"
          class="status-tab"
          :class="{ active: query.status === tab.value }"
          @click="selectStatus(tab.value)"
        >
          <i />
          {{ tab.label }}
        </button>
      </div>

      <div class="search-console">
        <el-input
          v-model="query.deviceName"
          clearable
          placeholder="输入设备名称、型号或关键词"
          @keyup.enter="resetSearch"
          @clear="resetSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <button class="search-btn" @click="resetSearch">
          <el-icon><Search /></el-icon> 检索任务
        </button>
        <button class="reset-btn" title="重置筛选" @click="resetFilters">
          <el-icon><Refresh /></el-icon>
        </button>
      </div>
    </section>

    <!-- 队列标题 -->
    <div class="queue-heading">
      <div>
        <span class="kicker">TASK QUEUE · CURRENT RESULT</span>
        <h2>任务队列</h2>
      </div>
      <span class="queue-count"><b>{{ total }}</b> 条符合条件的任务</span>
    </div>

    <!-- 任务卡片网格 -->
    <section v-loading="loading" class="task-grid">
      <button
        v-for="t in tasks"
        :key="t.id"
        class="task-card"
        :class="{ 'is-urgent': Number(t.urgencyLevel) === 2 }"
        @click="openTask(t)"
      >
        <span class="card-accent" />
        <div class="card-top">
          <span class="task-number">{{ t.taskNumber || ('#' + t.id) }}</span>
          <div class="card-top-right">
            <span
              class="task-state"
              :style="{ color: taskStatus(t.status).color, background: taskStatus(t.status).bg }"
            >
              <i v-if="taskStatus(t.status).spin" />
              {{ taskStatus(t.status).label }}
            </span>
            <button class="del-btn" title="删除任务" @click="confirmDelete($event, t)">
              <el-icon><Delete /></el-icon>
            </button>
          </div>
        </div>

        <div class="device-row">
          <span
            class="urgency-tag"
            :style="{ color: urgency(t.urgencyLevel).color, background: urgency(t.urgencyLevel).bg }"
          >
            {{ urgency(t.urgencyLevel).label }}优先级
          </span>
          <h3>{{ t.deviceName || '未指定设备' }}</h3>
        </div>

        <p class="fault-description">{{ t.faultDescription || '暂无故障描述，等待补充现场现象。' }}</p>

        <div class="stage-block">
          <div class="stage-copy">
            <span>流程阶段</span>
            <b>{{ flowStage(t).label }}</b>
          </div>
          <div class="stage-track">
            <i :style="{ width: flowStage(t).percent + '%' }" />
          </div>
        </div>

        <div class="card-meta">
          <span><el-icon><Tickets /></el-icon><b>{{ t.stepCount || 0 }}</b> 个执行步骤</span>
          <time><el-icon><Timer /></el-icon>{{ formatDate(t.createdAt) }}</time>
        </div>

        <div class="card-action">
          <span>进入任务控制台</span>
          <el-icon><ArrowRight /></el-icon>
        </div>
      </button>

      <div v-if="!loading && !tasks.length" class="empty-state">
        <span class="empty-icon"><el-icon><CircleCheck /></el-icon></span>
        <h2>当前队列没有符合条件的任务</h2>
        <p>可以调整状态或设备关键词，也可以创建一条新的检修任务开始作业。</p>
        <div class="empty-actions">
          <button class="ghost-btn" @click="resetFilters">
            <el-icon><Refresh /></el-icon> 清除筛选
          </button>
          <button class="new-btn" @click="showCreate = true">
            <el-icon><Plus /></el-icon> 新建任务
          </button>
        </div>
      </div>
    </section>

    <div v-if="total > query.size" class="pager">
      <el-pagination
        layout="prev, pager, next"
        :total="total"
        :page-size="query.size"
        :current-page="query.page"
        background
        @current-change="(p) => { query.page = p; load() }"
      />
    </div>

    <TaskCreateDialog v-model="showCreate" @created="onCreated" />
  </div>
</template>

<style scoped>
.tasks-page { max-width: 1240px; margin: 0 auto; }

/* —— 页头 —— */
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.kicker { font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; letter-spacing: 1.5px; color: var(--plaza-accent); }
.title { display: flex; align-items: center; gap: 10px; margin-top: 4px; font-family: var(--font-display); font-size: 26px; font-weight: 700; color: var(--plaza-heading); }
.led { width: 9px; height: 9px; border-radius: 50%; background: var(--plaza-accent); box-shadow: 0 0 0 4px var(--plaza-accent-soft); animation: ledPulse 2.2s ease-in-out infinite; }
@keyframes ledPulse { 0%,100% { box-shadow: 0 0 0 4px var(--plaza-accent-soft); } 50% { box-shadow: 0 0 0 6px transparent; } }
.subtitle { margin-top: 7px; max-width: 560px; color: var(--plaza-text-muted); font-size: 13px; line-height: 1.6; }
.new-btn {
  display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
  border: none; border-radius: 10px; padding: 11px 20px; cursor: pointer;
  color: var(--home-btn-text); font-weight: 600; font-size: 14px;
  background: var(--plaza-accent-grad); box-shadow: 0 8px 20px var(--plaza-accent-soft-strong);
  transition: transform .12s ease, filter .2s ease;
}
.new-btn:hover { filter: brightness(1.05); transform: translateY(-2px); }

/* —— 调度台（暖咖控制台）—— */
.overview {
  position: relative; overflow: hidden;
  display: grid; grid-template-columns: minmax(260px, 0.9fr) minmax(0, 1.35fr);
  gap: 28px; align-items: center;
  min-height: 176px; padding: 24px 26px;
  border-radius: var(--plaza-radius-lg);
  color: var(--plaza-panel-bg);
  background:
    radial-gradient(120% 120% at 8% -10%, var(--signal-line), transparent 55%),
    linear-gradient(160deg, var(--plaza-heading) 0%, var(--plaza-heading) 70%);
  border: 1px solid var(--signal-soft);
  box-shadow: 0 26px 60px -22px rgba(0, 0, 0, .5);
}
.overview-grid {
  position: absolute; inset: 0; pointer-events: none;
  background-image:
    linear-gradient(var(--signal-soft) 1px, transparent 1px),
    linear-gradient(90deg, var(--signal-soft) 1px, transparent 1px);
  background-size: 26px 26px;
  -webkit-mask-image: linear-gradient(90deg, #000, transparent 72%);
  mask-image: linear-gradient(90deg, #000, transparent 72%);
}
.overview-copy, .overview-metrics { position: relative; z-index: 1; }
.overview-eyebrow { font-family: var(--font-mono); font-size: 9px; font-weight: 800; letter-spacing: 0.16em; color: var(--signal); }
.overview-copy h2 { margin: 8px 0 6px; font-family: var(--font-display); font-size: 24px; line-height: 1.15; color: #fbeede; }
.overview-copy p { max-width: 430px; color: var(--plaza-text-muted); font-size: 12px; line-height: 1.7; }
.overview-sync { display: flex; align-items: center; gap: 8px; margin-top: 18px; color: var(--plaza-border-strong); font-size: 10px; }
.overview-sync i { width: 7px; height: 7px; border-radius: 50%; background: #7bbf5a; box-shadow: 0 0 0 4px rgba(123,191,90,0.14); }
.overview-sync b { margin-left: auto; font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.12em; color: var(--plaza-text-muted); }
.overview-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.overview-metric {
  position: relative; min-width: 0; overflow: hidden;
  padding: 16px 14px 14px; border-radius: 11px;
  border: 1px solid var(--signal-soft); background: rgba(255,255,255,0.045);
}
.overview-metric::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 2px; background: var(--plaza-text); }
.overview-metric.is-total::before, .overview-metric.is-executing::before { background: var(--signal); }
.overview-metric.is-completed::before { background: #7bbf5a; }
.overview-metric span { display: block; color: var(--plaza-text-muted); font-size: 9px; }
.overview-metric strong { display: block; margin: 7px 0 4px; font-family: var(--font-mono); font-size: 27px; line-height: 1; color: #fbeede; font-variant-numeric: tabular-nums; }
.overview-metric small { display: block; overflow: hidden; color: var(--plaza-text-muted); font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }

/* —— 筛选卡片 —— */
.filter-card {
  margin-top: 14px; padding: 16px 18px;
  border-radius: var(--plaza-radius-lg);
  border: 1px solid var(--plaza-border); background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
  display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr); gap: 14px 20px;
}
.filter-heading { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--plaza-border); }
.fh-left { display: flex; align-items: center; gap: 10px; }
.fh-copy { display: flex; flex-direction: column; }
.filter-icon { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 9px; color: var(--plaza-accent); background: var(--plaza-accent-soft); font-size: 17px; }
.fh-copy b { color: var(--plaza-heading); font-size: 12.5px; }
.fh-copy small { margin-top: 2px; color: var(--plaza-text-muted); font-size: 9px; }
.filter-result { font-family: var(--font-mono); font-size: 9.5px; color: var(--plaza-text-muted); }
.status-tabs { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; min-width: 0; }
.status-tab {
  display: inline-flex; align-items: center; gap: 7px; min-height: 36px; padding: 0 11px;
  border: 1px solid var(--plaza-border); border-radius: 8px; cursor: pointer;
  color: var(--plaza-text-muted); background: var(--plaza-bg); font-size: 11px; font-weight: 700;
  transition: color .18s ease, border-color .18s ease, background .18s ease;
}
.status-tab i { width: 5px; height: 5px; border-radius: 50%; background: var(--plaza-border-strong); }
.status-tab:hover, .status-tab.active { color: var(--plaza-accent); border-color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.status-tab.active i { background: var(--plaza-accent); box-shadow: 0 0 0 3px var(--plaza-accent-soft); }
.search-console { display: grid; grid-template-columns: minmax(160px, 1fr) auto 42px; gap: 8px; align-items: center; }
.search-btn {
  display: inline-flex; align-items: center; gap: 6px; height: 40px; padding: 0 16px;
  border: none; border-radius: 9px; cursor: pointer; white-space: nowrap;
  color: var(--home-btn-text); font-weight: 600; font-size: 13px; background: var(--plaza-accent-grad);
}
.search-btn:hover { filter: brightness(1.05); }
.reset-btn {
  display: grid; width: 42px; height: 40px; place-items: center;
  border: 1px solid var(--plaza-border); border-radius: 9px; cursor: pointer;
  color: var(--plaza-text-muted); background: var(--plaza-bg-card);
  transition: color .18s ease, border-color .18s ease, background .18s ease;
}
.reset-btn:hover { color: var(--plaza-accent); border-color: var(--plaza-accent); background: var(--plaza-accent-soft); }

/* —— 队列标题 —— */
.queue-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin: 22px 2px 10px; }
.queue-heading h2 { margin-top: 5px; font-family: var(--font-display); font-size: 20px; font-weight: 700; color: var(--plaza-heading); }
.queue-count { color: var(--plaza-text-muted); font-size: 11px; }
.queue-count b { font-family: var(--font-mono); font-size: 13px; color: var(--plaza-accent); }

/* —— 任务卡片网格 —— */
.task-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; min-height: 260px; }
.task-card {
  position: relative; min-width: 0; overflow: hidden; text-align: left; cursor: pointer;
  padding: 17px 17px 0;
  border: 1px solid var(--plaza-border); border-radius: var(--plaza-radius-lg);
  background: var(--plaza-bg-card); box-shadow: var(--plaza-shadow-organic);
  transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
}
.card-accent { position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--plaza-accent); opacity: 0.2; transition: opacity .2s ease; }
.task-card.is-urgent .card-accent { background: var(--plaza-danger); opacity: 0.7; }
.task-card:hover { border-color: var(--plaza-accent); box-shadow: var(--plaza-shadow-organic-hover); transform: translateY(-2px); }
.task-card:hover .card-accent { opacity: 1; }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-top-right { display: flex; align-items: center; gap: 8px; }
.task-number { font-family: var(--font-mono); font-size: 10.5px; font-weight: 700; letter-spacing: 0.04em; color: var(--plaza-text-muted); }
.task-state { display: inline-flex; align-items: center; gap: 5px; min-height: 25px; padding: 0 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.task-state i { width: 8px; height: 8px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: task-spin 0.8s linear infinite; }
.del-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; flex-shrink: 0; border-radius: 7px; border: none;
  background: transparent; color: var(--plaza-text-muted); cursor: pointer; font-size: 14px;
  transition: background .15s ease, color .15s ease;
}
.del-btn:hover { background: #fee2e2; color: #dc2626; }
.device-row { display: flex; align-items: center; gap: 8px; margin-top: 15px; }
.urgency-tag { flex-shrink: 0; padding: 3px 7px; border-radius: 6px; font-size: 10px; font-weight: 700; }
.device-row h3 { overflow: hidden; font-family: var(--font-display); font-size: 17px; font-weight: 700; color: var(--plaza-heading); text-overflow: ellipsis; white-space: nowrap; }
.fault-description { display: -webkit-box; min-height: 42px; margin: 8px 0 14px; overflow: hidden; color: var(--plaza-text-muted); font-size: 12.5px; line-height: 1.7; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.stage-block { padding: 11px 12px; border: 1px solid var(--plaza-border); border-radius: 9px; background: var(--plaza-bg); }
.stage-copy { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--plaza-text-muted); font-size: 9px; }
.stage-copy b { color: var(--plaza-text); font-size: 10px; }
.stage-track { height: 4px; margin-top: 8px; overflow: hidden; border-radius: 999px; background: var(--plaza-border); }
.stage-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--plaza-accent), var(--signal)); }
.card-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 13px; color: var(--plaza-text-muted); font-size: 10px; }
.card-meta span, .card-meta time { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
.card-meta span b { font-family: var(--font-mono); font-size: 11px; color: var(--plaza-accent); }
.card-meta time { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-action {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin: 14px -17px 0; padding: 11px 17px;
  border-top: 1px solid var(--plaza-border); background: var(--plaza-accent-soft);
  color: var(--plaza-accent); font-size: 11px; font-weight: 700;
  transition: background .18s ease;
}
.task-card:hover .card-action { background: var(--plaza-accent-soft-strong); }

/* —— 空状态 —— */
.empty-state {
  grid-column: 1 / -1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 310px; padding: 38px; text-align: center;
  border: 1px solid var(--plaza-border); border-radius: var(--plaza-radius-lg);
  background: var(--plaza-bg-card); box-shadow: var(--plaza-shadow-organic);
}
.empty-icon { display: grid; width: 58px; height: 58px; place-items: center; border-radius: 16px; color: var(--plaza-accent); background: var(--plaza-accent-soft); font-size: 27px; }
.empty-state h2 { margin: 15px 0 6px; font-size: 17px; color: var(--plaza-heading); }
.empty-state p { color: var(--plaza-text-muted); font-size: 12px; }
.empty-actions { display: flex; gap: 9px; margin-top: 18px; }
.ghost-btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 10px 18px; cursor: pointer;
  border: 1px solid var(--plaza-border); border-radius: 10px; color: var(--plaza-text); background: var(--plaza-bg-card); font-weight: 600; font-size: 13px;
}
.ghost-btn:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); }

.pager { display: flex; justify-content: center; margin-top: 22px; }

@keyframes task-spin { to { transform: rotate(360deg); } }

@media (max-width: 1100px) {
  .filter-card { grid-template-columns: 1fr; }
  .task-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .overview { grid-template-columns: 1fr; gap: 20px; }
}
@media (max-width: 680px) {
  .page-head { flex-direction: column; align-items: flex-start; }
  .overview-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .search-console { grid-template-columns: 1fr; }
  .search-btn, .reset-btn { width: 100%; }
  .task-grid { grid-template-columns: 1fr; }
}
</style>
