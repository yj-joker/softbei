<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { gsap } from 'gsap'
import {
  ArrowLeft,
  Check,
  Clock,
  Close,
  DataAnalysis,
  Document,
  Headset,
  Refresh,
  Tickets,
  Warning,
} from '@element-plus/icons-vue'
import { getTaskDetail, startTask, retryGenerate } from '@/api/maintenanceTask'
import { draftFromTask, getMyCases } from '@/api/caseRecord'
import { notifyStore } from '@/stores/notifyStore'
import { taskAssistantStore } from '@/stores/taskAssistantStore'
import { taskStatus, urgency, levelLabel, stepActionable } from '@/constants/taskStatus'
import { useStepReadAlong } from '@/composables/useStepReadAlong'
import TaskStepCard from '@/components/task/TaskStepCard.vue'
import TaskAssistantPanel from '@/components/task/TaskAssistantPanel.vue'
import TaskVoiceModePanel from '@/components/task/TaskVoiceModePanel.vue'
import CaseSubmitDialog from '@/components/case/CaseSubmitDialog.vue'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id
const pageRef = ref(null)

const task = ref(null)
const loading = ref(false)
const acting = ref(false)
const panelRef = ref(null)
const voiceMode = ref(false)
const caseDialog = ref(false)
const caseDraft = ref(null)
const myCasesDrawer = ref(false)
const myCasesLoading = ref(false)
const myCases = ref([])

let motionContext = null

const DONE_SET = ['AI_PASSED', 'COMPLETED', 'SKIPPED']
const steps = computed(() => (task.value?.steps || []).slice().sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0)))
// 当前应执行的步骤 = 第一个「待执行 / 未通过」的步骤
const activeStepId = computed(() => {
  const s = steps.value.find((x) => stepActionable(x.status))
  return s ? s.id : null
})
const activeStep = computed(() => steps.value.find((x) => x.id === activeStepId.value) || null)
const completedSteps = computed(() => steps.value.filter((s) => DONE_SET.includes(s.status)))
const verifyingSteps = computed(() => steps.value.filter((s) => s.status === 'SUBMITTED'))
const estimatedMinutes = computed(() => steps.value.reduce((t, s) => t + Number(s.estimatedMinutes || 0), 0))
const progressPct = computed(() => (steps.value.length ? Math.round((completedSteps.value.length / steps.value.length) * 100) : 0))
const st = computed(() => taskStatus(task.value?.status))
const urgencyMeta = computed(() => urgency(task.value?.urgencyLevel))
const showWork = computed(() => ['EXECUTING', 'CLOSED'].includes(task.value?.status))

// —— 分步推进看板：节点状态 ——
const flowNodes = computed(() =>
  steps.value.map((s) => ({
    id: s.id,
    no: s.sortOrder,
    title: s.title,
    state: DONE_SET.includes(s.status)
      ? 'done'
      : s.status === 'SUBMITTED'
        ? 'verifying'
        : s.id === activeStepId.value
          ? 'active'
          : 'pending',
  })),
)

function formatDate(value) {
  if (!value) return '时间待同步'
  return String(value).replace('T', ' ').slice(0, 16)
}

// 点流程节点 → 平滑滚到对应步骤卡
function scrollToStep(id) {
  const el = document.getElementById('step-' + id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

// 跟读模式：逐步念「标题+内容+安全提示」，读完一步停下等点「下一步」；当前步滚动到可视区 + 高亮
const readAlong = useStepReadAlong(steps, {
  onStep: (step) => scrollToStep(step.id),
  onFinish: () => ElMessage.success('跟读完成'),
})

// 「从此步跟读」开关：再次点正在跟读的那一步 = 停止；否则从该步开始（在别处跟读时点别的步=切换过去）
function startReadAlongFrom(step) {
  if (voiceMode.value) return
  if (readAlong.active.value && readAlong.currentStepId.value === step.id) {
    readAlong.exit()
    return
  }
  const i = steps.value.findIndex((x) => x.id === step.id)
  if (i >= 0) readAlong.start(i)
}

function enterVoiceMode() {
  readAlong.exit()
  voiceMode.value = true
  if (activeStepId.value) taskAssistantStore.setFocus(taskId, activeStepId.value)
}

function exitVoiceMode() {
  voiceMode.value = false
}

let verifyPollTimer = null
async function load() {
  loading.value = true
  try {
    const res = await getTaskDetail(taskId)
    task.value = res?.data || null
  } catch (err) {
    ElMessage.error('加载失败：' + (err.message || ''))
  } finally {
    loading.value = false
  }
  scheduleVerifyPoll()
}

// 兜底：有步骤处于 SUBMITTED(AI验证中) 时每 8 秒自刷，确保即使 STEP_VERIFIED 推送丢失，
// 页面也能反映验证完成（不再永远停在「验证中」）。无在验证步骤时自动停止。
function scheduleVerifyPoll() {
  if (verifyPollTimer) { clearTimeout(verifyPollTimer); verifyPollTimer = null }
  const verifying = (task.value?.steps || []).some(s => s.status === 'SUBMITTED')
  if (verifying) verifyPollTimer = setTimeout(load, 8000)
}

async function onStart() {
  acting.value = true
  try { await startTask(taskId); ElMessage.success('已开始执行'); await load() }
  catch (err) { ElMessage.error('操作失败：' + (err.message || '')) }
  finally { acting.value = false }
}

async function onRetry() {
  acting.value = true
  try {
    await retryGenerate(taskId)
    notifyStore.trackJob({ key: 'task:' + taskId, kind: 'task', refId: taskId, title: '重新生成检修步骤' })
    ElMessage.success('已重新触发生成')
    await load()
  } catch (err) { ElMessage.error('操作失败：' + (err.message || '')) }
  finally { acting.value = false }
}

// 点步骤卡「答疑」→ 助手聚焦到该步并聚焦输入框（同一条对话，不再弹抽屉）
function onChat(step) {
  taskAssistantStore.setFocus(taskId, step.id)
  panelRef.value?.focusInput?.()
}

async function onVoiceUpdated(data) {
  if (!data) return
  if (Array.isArray(data.steps) && task.value) {
    task.value = { ...task.value, steps: data.steps }
    scheduleVerifyPoll()
  } else {
    await load()
  }
  if (data.currentStepId) {
    taskAssistantStore.setFocus(taskId, data.currentStepId)
    await nextTick()
    scrollToStep(data.currentStepId)
  }
}

async function onVoiceFocusStep(stepId) {
  if (!stepId) return
  taskAssistantStore.setFocus(taskId, stepId)
  await nextTick()
  scrollToStep(stepId)
}

async function openCaseDialog() {
  acting.value = true
  try {
    const res = await draftFromTask(taskId)
    caseDraft.value = res?.data || null
    caseDialog.value = true
  } catch (err) {
    ElMessage.error(err.message || '案例草稿生成失败')
  } finally {
    acting.value = false
  }
}

async function openMyCases() {
  myCasesDrawer.value = true
  await loadMyCases()
}

async function loadMyCases() {
  myCasesLoading.value = true
  try {
    const res = await getMyCases(1, 30)
    const data = res?.data?.records || res?.data?.list || res?.data || []
    myCases.value = Array.isArray(data) ? data : []
  } catch (err) {
    ElMessage.error('加载我的案例失败：' + (err.message || ''))
  } finally {
    myCasesLoading.value = false
  }
}

function caseStatusText(status) {
  const map = {
    pending: '待审核',
    PENDING: '待审核',
    approved: '已通过',
    APPROVED: '已通过',
    rejected: '已驳回',
    REJECTED: '已驳回',
  }
  return map[status] || status || '未知'
}

async function setupMotion() {
  await nextTick()
  if (!pageRef.value) return
  motionContext?.revert()
  motionContext = gsap.context(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    gsap
      .timeline({ defaults: { ease: 'power3.out' } })
      .from('.console-utility', { autoAlpha: 0, y: -8, duration: 0.32 })
      .from('.task-command', { autoAlpha: 0, y: 18, duration: 0.52 }, '-=0.12')
      .from('.command-readout', { autoAlpha: 0, x: 18, duration: 0.42 }, '-=0.3')
      .from('.flow-board', { autoAlpha: 0, y: 12, duration: 0.4 }, '-=0.2')
      .from('.workflow-heading', { autoAlpha: 0, y: 12, duration: 0.36 }, '-=0.16')
      .from('.step-card-shell', { autoAlpha: 0, y: 14, stagger: 0.055, duration: 0.4 }, '-=0.16')
      .from('.assistant-column', { autoAlpha: 0, x: 18, duration: 0.46 }, '-=0.38')
  }, pageRef.value)
}

// 收到 WS 通知（步骤验证 / 生成完成）后刷新
watch(() => notifyStore.state.notifications.length, () => load())
// 进入执行态后步骤区出现，补一次入场动画
watch(showWork, (v) => { if (v) setupMotion() })

onMounted(async () => {
  await load()
  await setupMotion()
})

onUnmounted(() => {
  motionContext?.revert()
  readAlong.exit() // 离开页面时停掉正在播放的跟读语音
  if (verifyPollTimer) clearTimeout(verifyPollTimer)
})
</script>

<template>
  <div ref="pageRef" class="task-console" v-loading="loading">
    <div class="console-utility">
      <button type="button" class="back-button" @click="router.push('/user/tasks')">
        <el-icon><ArrowLeft /></el-icon>
        返回任务列表
      </button>
      <span class="console-code">TASK EXECUTION · FIELD WORKFLOW</span>
    </div>

    <template v-if="task">
      <!-- 任务指挥头（深色） -->
      <section class="task-command">
        <div class="command-grid" />
        <div class="command-copy">
          <div class="command-badges">
            <span class="task-number">{{ task.taskNumber || ('#' + task.id) }}</span>
            <span class="status-badge" :style="{ color: st.color, background: st.bg }">
              <i v-if="st.spin" class="status-spinner" />{{ st.label }}
            </span>
            <span class="urgency-badge" :style="{ color: urgencyMeta.color, background: urgencyMeta.bg }">
              {{ urgencyMeta.label }}优先级
            </span>
          </div>

          <span class="command-eyebrow">ACTIVE MAINTENANCE OBJECT</span>
          <h1>{{ task.deviceName || '未指定设备' }}</h1>
          <p>{{ task.faultDescription || '等待补充故障描述与现场现象。' }}</p>

          <div class="command-meta">
            <span><el-icon><Tickets /></el-icon> 检修等级 {{ levelLabel(task.maintenanceLevel) }}</span>
            <span v-if="task.procedureName"><el-icon><Document /></el-icon> {{ task.procedureName }}</span>
            <span><el-icon><Clock /></el-icon> {{ formatDate(task.createdAt) }}</span>
          </div>

          <div v-if="(task.reportImages || []).length" class="report-images">
            <img v-for="(u, i) in task.reportImages" :key="i" :src="u" :alt="`故障上报图片 ${i + 1}`" />
          </div>
        </div>

        <div class="command-readout">
          <div
            class="progress-dial"
            :style="{ '--task-progress': progressPct * 3.6 + 'deg' }"
            role="img"
            :aria-label="`任务完成进度 ${progressPct}%`"
          >
            <span><b>{{ progressPct }}</b>%</span>
          </div>
          <div class="progress-copy">
            <span>OVERALL PROGRESS</span>
            <strong>{{ completedSteps.length }} / {{ steps.length || task.stepCount || 0 }}</strong>
            <small>步骤已完成</small>
          </div>

          <div class="readout-grid">
            <span><el-icon><DataAnalysis /></el-icon><b>{{ activeStep?.sortOrder || '—' }}</b><small>当前步骤</small></span>
            <span><el-icon><Clock /></el-icon><b>{{ estimatedMinutes ? `${estimatedMinutes} MIN` : '—' }}</b><small>预计总耗时</small></span>
            <span><el-icon><Check /></el-icon><b>{{ verifyingSteps.length }}</b><small>AI 验证中</small></span>
          </div>

          <div class="command-actions">
            <button v-if="task.status === 'GENERATED'" type="button" class="command-primary" :disabled="acting" @click="onStart">
              开始执行
            </button>
            <button v-else-if="task.status === 'GENERATE_FAILED'" type="button" class="command-primary" :disabled="acting" @click="onRetry">
              <el-icon><Refresh /></el-icon> 重新生成
            </button>
          </div>
        </div>
      </section>

      <!-- 状态条 -->
      <section
        v-if="['GENERATING', 'GENERATE_FAILED', 'GENERATED', 'CLOSED'].includes(task.status)"
        class="state-strip"
        :class="{ error: task.status === 'GENERATE_FAILED', success: task.status === 'CLOSED' }"
      >
        <span class="state-icon">
          <el-icon v-if="task.status === 'GENERATE_FAILED'"><Warning /></el-icon>
          <el-icon v-else-if="task.status === 'CLOSED'"><Check /></el-icon>
          <i v-else-if="task.status === 'GENERATING'" class="state-spinner" />
          <el-icon v-else><Tickets /></el-icon>
        </span>
        <span>
          <b v-if="task.status === 'GENERATING'">AI 正在生成检修步骤</b>
          <b v-else-if="task.status === 'GENERATE_FAILED'">检修步骤生成失败</b>
          <b v-else-if="task.status === 'GENERATED'">作业步骤已经准备就绪</b>
          <b v-else>该检修任务已全部完成</b>
          <small v-if="task.status === 'GENERATING'">生成完成后会自动同步任务状态并通知你。</small>
          <small v-else-if="task.status === 'GENERATE_FAILED'">请重新触发生成，或检查任务描述是否完整。</small>
          <small v-else-if="task.status === 'GENERATED'">共 {{ steps.length || task.stepCount || 0 }} 个步骤，开始后将进入现场执行流程。</small>
          <small v-else>可以将本次处理过程沉淀为案例，供后续检修复用。</small>
        </span>
      </section>

      <!-- 执行中 / 已完成 -->
      <section v-if="showWork" class="workflow">
        <!-- 分步推进看板（保留） -->
        <section class="flow-board">
          <header class="flow-head">
            <div class="flow-title">
              <span class="flow-kicker">检修流程 · WORKFLOW</span>
              <h3>分步推进看板</h3>
            </div>
          </header>
          <div class="flow-rail">
            <button
              v-for="(n, i) in flowNodes"
              :key="n.id"
              class="flow-node"
              :class="n.state"
              :style="{ '--i': i }"
              @click="scrollToStep(n.id)"
              :title="n.title"
            >
              <span class="fn-dot">
                <svg v-if="n.state === 'done'" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                <span v-else>{{ n.no }}</span>
              </span>
              <span class="fn-label">{{ n.title }}</span>
            </button>
          </div>
        </section>

        <div class="workflow-heading">
          <div>
            <span>MAINTENANCE SEQUENCE</span>
            <h2>检修步骤</h2>
          </div>
          <div class="workflow-summary">
            <span><i class="done" /> 已完成 {{ completedSteps.length }}</span>
            <span><i class="active" /> 当前 {{ activeStep?.sortOrder || '—' }}</span>
            <span><i /> 总计 {{ steps.length }}</span>
          </div>
        </div>

        <!-- 语音检修模式控制条：从步骤区进入，进入后隐藏右侧检修助手。 -->
        <div v-if="steps.length" class="readalong-bar" :class="{ on: voiceMode }">
          <button v-if="!voiceMode" type="button" class="ra-start" @click="enterVoiceMode">
            <el-icon><Headset /></el-icon> 语音检修
          </button>
          <template v-else>
            <span class="ra-status">
              <i class="ra-dot" />
              语音检修中 · {{ activeStep ? `第 ${activeStep.sortOrder} 步` : '全部步骤' }}
            </span>
            <div class="ra-actions">
              <button type="button" class="ra-exit" @click="exitVoiceMode">
                <el-icon><Close /></el-icon> 退出语音检修
              </button>
            </div>
          </template>
        </div>

        <div class="work-grid" :class="{ 'voice-mode-grid': voiceMode }">
          <div class="steps-column">
            <TaskVoiceModePanel
              v-if="voiceMode"
              class="voice-inline-panel"
              :task-id="taskId"
              :steps="steps"
              :active-step-id="activeStepId"
              @updated="onVoiceUpdated"
              @focus-step="onVoiceFocusStep"
              @exit="exitVoiceMode"
            />
            <div class="step-list-shell">
              <div class="timeline-line" aria-hidden="true" />
              <TaskStepCard
                v-for="s in steps"
                :key="s.id"
                :id="'step-' + s.id"
                :step="s"
                :task-id="taskId"
                :executing="task.status === 'EXECUTING'"
                :active="s.id === activeStepId"
                :reading="s.id === readAlong.currentStepId.value"
                @submitted="load"
                @chat="onChat"
                @read-along="startReadAlongFrom"
              />
            </div>
          </div>
          <aside v-if="!voiceMode" class="assistant-column">
            <TaskAssistantPanel
              ref="panelRef"
              :task-id="taskId"
              :steps="steps"
              :active-step-id="activeStepId"
            />
          </aside>
        </div>
      </section>
    </template>

    <section v-else-if="!loading" class="missing-task">
      <el-icon><Warning /></el-icon>
      <h2>未读取到任务信息</h2>
      <p>任务可能已被删除，或当前账号无权访问。</p>
      <button type="button" class="command-primary" @click="router.push('/user/tasks')">返回任务列表</button>
    </section>

    <CaseSubmitDialog
      v-model:visible="caseDialog"
      :draft="caseDraft"
      @submitted="loadMyCases"
    />

    <el-drawer v-model="myCasesDrawer" title="我的案例" size="460px">
      <div v-loading="myCasesLoading" class="my-case-list">
        <div v-if="!myCases.length && !myCasesLoading" class="my-case-empty">暂无案例提交记录</div>
        <article v-for="item in myCases" :key="item.id" class="my-case-card">
          <div class="my-case-head">
            <strong>{{ item.title || item.caseTitle || '未命名案例' }}</strong>
            <span :class="['case-status', String(item.status || '').toLowerCase()]">
              {{ caseStatusText(item.status) }}
            </span>
          </div>
          <p>{{ item.summary || item.experienceSummary || '暂无摘要' }}</p>
          <div v-if="item.reviewComment" class="review-comment">
            审核意见：{{ item.reviewComment }}
          </div>
        </article>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.task-console {
  --c-accent-light: var(--plaza-accent);
  max-width: 1320px;
  margin: 0 auto;
  min-height: 100%;
}

.console-utility,
.command-badges,
.command-meta,
.command-actions,
.workflow-heading,
.workflow-summary {
  display: flex;
  align-items: center;
}

.console-utility { justify-content: space-between; gap: 16px; margin-bottom: 12px; }

.back-button {
  display: inline-flex; min-height: 40px; align-items: center; gap: 7px;
  padding: 0 13px; border: 1px solid var(--plaza-border); border-radius: 9px;
  color: var(--plaza-text-muted); background: var(--plaza-bg-card);
  font-size: 12px; font-weight: 700; cursor: pointer;
  transition: color .18s ease, border-color .18s ease, transform .18s ease;
}
.back-button:hover { color: var(--plaza-accent); border-color: var(--plaza-accent); transform: translateX(-2px); }

.console-code,
.command-eyebrow,
.progress-copy > span,
.workflow-heading > div:first-child > span {
  font-family: var(--font-mono); font-weight: 800; letter-spacing: 0.13em;
}
.console-code { color: var(--plaza-text-muted); font-size: 9px; }

/* ===== 深色任务指挥头 ===== */
.task-command {
  position: relative; display: grid; min-height: 280px;
  grid-template-columns: minmax(0, 1fr) 390px; gap: 32px;
  padding: 28px 30px; overflow: hidden; border-radius: var(--plaza-radius-lg);
  border: 1px solid var(--plaza-accent-soft-strong);
  background:
    radial-gradient(120% 120% at 86% -12%, var(--signal-line), transparent 54%),
    linear-gradient(160deg, var(--plaza-heading) 0%, var(--plaza-heading) 74%);
  box-shadow: 0 26px 60px -22px rgba(0, 0, 0, 0.5);
}
.task-command::after {
  position: absolute; top: -150px; right: -85px; width: 390px; height: 390px;
  border: 1px solid var(--plaza-accent-soft-strong); border-radius: 50%;
  box-shadow: 0 0 0 48px var(--plaza-accent-soft), 0 0 0 96px var(--plaza-accent-soft);
  content: '';
}
.command-grid {
  position: absolute; inset: 0; opacity: 0.4; pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: linear-gradient(90deg, #000, transparent 88%);
}
.command-copy, .command-readout { position: relative; z-index: 1; }
.command-copy { min-width: 0; align-self: center; }
.command-badges { flex-wrap: wrap; gap: 7px; margin-bottom: 22px; }
.task-number, .status-badge, .urgency-badge {
  display: inline-flex; min-height: 25px; align-items: center; gap: 6px;
  padding: 0 9px; border-radius: 999px; font-family: var(--font-mono);
  font-size: 9.5px; font-weight: 800;
}
.task-number { color: var(--plaza-border); border: 1px solid rgba(255, 255, 255, 0.12); background: rgba(255, 255, 255, 0.06); }
.command-eyebrow { color: var(--c-accent-light); font-size: 9px; }
.command-copy h1 {
  margin: 8px 0 8px; color: #fff; font-family: var(--font-display);
  font-size: clamp(28px, 3vw, 42px); font-weight: 800; letter-spacing: -0.03em; line-height: 1.08;
}
.command-copy > p { max-width: 720px; margin: 0; color: var(--plaza-border-strong); font-size: 13px; line-height: 1.75; }
.command-meta { flex-wrap: wrap; gap: 9px 16px; margin-top: 21px; }
.command-meta span { display: inline-flex; align-items: center; gap: 6px; color: var(--plaza-text-muted); font-size: 11px; }
.command-meta .el-icon { color: var(--c-accent-light); }
.report-images { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
.report-images img { width: 58px; height: 58px; object-fit: cover; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 8px; }

.command-readout {
  display: grid; grid-template-columns: 92px minmax(0, 1fr); align-content: center;
  gap: 14px 16px; padding: 20px; border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 13px; background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(8px);
}
.progress-dial {
  position: relative; display: grid; width: 88px; height: 88px; place-items: center; border-radius: 50%;
  background: conic-gradient(var(--c-accent-light) var(--task-progress, 0deg), rgba(255, 255, 255, 0.08) 0);
}
.progress-dial::before { position: absolute; width: 72px; height: 72px; border-radius: 50%; background: var(--plaza-heading); content: ''; }
.progress-dial span { position: relative; z-index: 1; color: var(--plaza-border-strong); font-family: var(--font-mono); font-size: 10px; }
.progress-dial b { color: #fff; font-size: 21px; }
.progress-copy { display: flex; min-width: 0; flex-direction: column; justify-content: center; }
.progress-copy > span { color: var(--plaza-text-muted); font-size: 8px; }
.progress-copy strong { margin-top: 3px; color: #fff; font-family: var(--font-display); font-size: 26px; font-weight: 800; }
.progress-copy small { color: var(--plaza-text-muted); font-size: 10px; }
.readout-grid { display: grid; grid-column: 1 / -1; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.readout-grid > span {
  display: grid; min-width: 0; grid-template-columns: 20px minmax(0, 1fr); grid-template-rows: auto auto;
  align-items: center; column-gap: 6px; padding: 9px; border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 9px; background: rgba(255, 255, 255, 0.025);
}
.readout-grid .el-icon { grid-row: 1 / 3; color: var(--c-accent-light); font-size: 16px; }
.readout-grid b { overflow: hidden; color: var(--plaza-panel-bg); font-size: 12px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.readout-grid small { color: var(--plaza-text-muted); font-size: 8px; }
.command-actions { grid-column: 1 / -1; gap: 8px; }
.command-primary, .command-secondary {
  display: inline-flex; min-height: 42px; align-items: center; justify-content: center; gap: 7px;
  padding: 0 18px; border-radius: 9px; font-size: 12px; font-weight: 800; cursor: pointer;
}
.command-primary { border: 1px solid transparent; color: #fff; background: linear-gradient(145deg, var(--plaza-accent), var(--plaza-accent)); box-shadow: 0 8px 20px var(--plaza-accent); }
.command-primary:hover { filter: brightness(1.05); }
.command-secondary { border: 1px solid rgba(255, 255, 255, 0.14); color: var(--plaza-border); background: rgba(255, 255, 255, 0.05); }
.command-primary:disabled { opacity: 0.55; cursor: not-allowed; }

/* ===== 状态条 ===== */
.state-strip {
  display: grid; grid-template-columns: 42px minmax(0, 1fr); align-items: center; gap: 11px;
  margin-top: 14px; padding: 13px 15px; border: 1px solid var(--plaza-border-strong);
  border-radius: 12px; background: var(--plaza-bg-card);
}
.state-strip.error { border-color: #f1c3b6; background: var(--plaza-danger-soft); }
.state-strip.success { border-color: #cbe0ad; background: var(--plaza-success-soft); }
.state-icon { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 10px; color: var(--plaza-warning); background: var(--plaza-warning-soft); font-size: 18px; }
.state-strip.error .state-icon { color: #c33b3b; background: #ffe4e4; }
.state-strip.success .state-icon { color: #4e7a32; background: var(--plaza-success-soft); }
.state-strip > span:last-child { display: flex; min-width: 0; flex-direction: column; }
.state-strip b { color: var(--plaza-heading); font-size: 12.5px; }
.state-strip small { margin-top: 3px; color: var(--plaza-text-muted); font-size: 11px; }
.status-spinner, .state-spinner { display: inline-block; border-radius: 50%; border: 2px solid currentColor; border-top-color: transparent; animation: console-spin 0.8s linear infinite; }
.status-spinner { width: 8px; height: 8px; }
.state-spinner { width: 18px; height: 18px; }
@keyframes console-spin { to { transform: rotate(360deg); } }

/* ===== 检修步骤区 ===== */
.workflow { margin-top: 16px; }
.workflow-heading { justify-content: space-between; gap: 20px; margin-bottom: 11px; }
.workflow-heading > div:first-child > span { color: var(--plaza-text-muted); font-size: 8px; }
.workflow-heading h2 { margin: 4px 0 0; color: var(--plaza-heading); font-family: var(--font-display); font-size: 21px; font-weight: 800; }
.workflow-summary { gap: 15px; color: var(--plaza-text-muted); font-size: 10px; }
.workflow-summary span { display: inline-flex; align-items: center; gap: 6px; }
.workflow-summary i { width: 7px; height: 7px; border-radius: 50%; background: var(--plaza-border-strong); }
.workflow-summary i.done { background: #5e8c3e; }
.workflow-summary i.active { background: var(--plaza-accent); box-shadow: 0 0 0 4px var(--plaza-accent-soft); }

/* ===== 跟读模式控制条 ===== */
.readalong-bar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 11px; }
.readalong-bar.on { padding: 9px 13px; border: 1px solid var(--plaza-accent-soft-strong); border-radius: 11px; background: var(--plaza-accent-soft); }
.ra-start {
  display: inline-flex; min-height: 38px; align-items: center; gap: 7px; padding: 0 15px;
  border: 1px solid var(--plaza-accent-soft-strong); border-radius: 9px;
  color: var(--plaza-accent); background: var(--plaza-bg-card);
  font-size: 12px; font-weight: 800; cursor: pointer;
  transition: background .18s ease, border-color .18s ease;
}
.ra-start:hover { background: var(--plaza-accent-soft); border-color: var(--plaza-accent); }
.ra-status { display: inline-flex; align-items: center; gap: 8px; color: var(--plaza-accent); font-size: 12px; font-weight: 700; }
.ra-status em { color: var(--plaza-text-muted); font-style: normal; font-size: 11px; font-weight: 600; }
.ra-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--plaza-accent); box-shadow: 0 0 0 4px var(--plaza-accent-soft); animation: ra-pulse 1.4s ease-in-out infinite; }
@keyframes ra-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
.ra-actions { display: inline-flex; align-items: center; gap: 8px; }
.ra-exit { display: inline-flex; min-height: 36px; align-items: center; gap: 5px; padding: 0 13px; border-radius: 8px; font-size: 11px; font-weight: 800; cursor: pointer; }
.ra-exit { border: 1px solid var(--plaza-border-strong); color: var(--plaza-text-muted); background: var(--plaza-bg-card); }
.ra-exit:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); }
@media (prefers-reduced-motion: reduce) { .ra-dot { animation: none; } }

.work-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(350px, 0.38fr); align-items: start; gap: 14px; }
.work-grid.voice-mode-grid { grid-template-columns: minmax(0, 1fr); }
.steps-column { display: flex; min-width: 0; flex-direction: column; gap: 10px; }
.step-list-shell { position: relative; display: flex; min-width: 0; flex-direction: column; gap: 10px; }
.timeline-line { position: absolute; top: 28px; bottom: 28px; left: 27px; width: 1px; background: linear-gradient(var(--plaza-accent), var(--plaza-border) 45%, var(--plaza-border)); }
.assistant-column { position: sticky; top: 8px; height: calc(100vh - 104px); min-height: 610px; max-height: 880px; }

/* ===== 分步推进看板（保留，配色用 fix） ===== */
.flow-board { margin-bottom: 16px; background: var(--plaza-bg-card); border: 1px solid var(--plaza-border); border-radius: var(--plaza-radius-lg); padding: 18px 20px 22px; box-shadow: var(--plaza-shadow-organic); }
.flow-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
.flow-kicker { font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; letter-spacing: 1.4px; color: var(--plaza-accent); }
.flow-title h3 { font-family: var(--font-display); font-size: 19px; font-weight: 700; color: var(--plaza-heading); margin: 4px 0 0; }
.flow-rail { display: flex; gap: 6px; overflow-x: auto; margin-top: 16px; padding: 4px 2px 8px; }
.flow-node { flex: 0 0 auto; display: flex; flex-direction: column; align-items: center; gap: 7px; width: 92px; padding: 4px; background: none; border: none; cursor: pointer;
  animation: nodeIn .5s cubic-bezier(.22,1,.36,1) backwards; animation-delay: calc(var(--i) * 0.05s); }
@keyframes nodeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.fn-dot { position: relative; width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font-family: var(--font-mono); font-size: 13px; font-weight: 700;
  background: var(--plaza-bg-input); color: var(--plaza-text-muted); border: 2px solid var(--plaza-border-strong); transition: all .25s ease; }
.fn-dot::after { content: ''; position: absolute; left: 100%; top: 50%; width: 6px; height: 2px; background: var(--plaza-border-strong); transform: translateY(-50%); }
.flow-node:last-child .fn-dot::after { display: none; }
.fn-label { font-size: 11px; line-height: 1.3; color: var(--plaza-text-muted); max-width: 88px; max-height: 28px; overflow: hidden; text-align: center; transition: color .25s ease; }
.flow-node.done .fn-dot { background: #5e8c3e; border-color: #5e8c3e; color: #fff; }
.flow-node.done .fn-dot::after { background: #5e8c3e; }
.flow-node.active .fn-dot { background: var(--plaza-accent-grad); border-color: transparent; color: #fff; box-shadow: 0 0 0 4px var(--plaza-accent-soft); animation: nodePulse 1.8s ease-in-out infinite; }
.flow-node.active .fn-label { color: var(--plaza-accent); font-weight: 700; }
.flow-node.verifying .fn-dot { background: var(--plaza-warning-soft); border-color: var(--plaza-warning); color: var(--plaza-warning); }
@keyframes nodePulse { 0%,100% { box-shadow: 0 0 0 4px var(--plaza-accent-soft); } 50% { box-shadow: 0 0 0 7px transparent; } }
.flow-node:hover .fn-label { color: var(--plaza-text); }

/* ===== 缺省 / 我的案例 ===== */
.missing-task { display: grid; min-height: 340px; place-items: center; align-content: center; gap: 8px; padding: 40px; text-align: center; }
.missing-task > .el-icon { color: var(--plaza-accent); font-size: 32px; }
.missing-task h2, .missing-task p { margin: 0; }
.missing-task h2 { color: var(--plaza-heading); }
.missing-task p { color: var(--plaza-text-muted); }

.my-case-list { min-height: 180px; display: flex; flex-direction: column; gap: 10px; }
.my-case-empty { color: var(--plaza-text-muted); text-align: center; padding: 44px 0; }
.my-case-card { border: 1px solid var(--plaza-border); border-radius: 10px; padding: 12px; background: var(--plaza-bg-card); }
.my-case-head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.my-case-head strong { color: var(--plaza-heading); font-weight: 700; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.my-case-card p { margin: 8px 0 0; color: var(--plaza-text-muted); line-height: 1.6; font-size: 13px; }
.case-status { flex-shrink: 0; font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 999px; color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.case-status.approved { color: #5e8c3e; background: var(--plaza-success-soft); }
.case-status.rejected { color: var(--plaza-danger); background: var(--plaza-danger-soft); }
.review-comment { margin-top: 8px; padding: 8px; border-radius: 8px; background: var(--plaza-warning-soft); color: #b45309; font-size: 12px; line-height: 1.5; }

@media (max-width: 1280px) {
  .task-command { grid-template-columns: minmax(0, 1fr) 350px; }
  .work-grid { grid-template-columns: minmax(0, 1fr) 360px; }
  .work-grid.voice-mode-grid { grid-template-columns: 1fr; }
}
@media (max-width: 1080px) {
  .task-command { grid-template-columns: 1fr; }
  .command-readout { grid-template-columns: 82px minmax(0, 1fr); }
  .work-grid { grid-template-columns: 1fr; }
  .work-grid.voice-mode-grid { grid-template-columns: 1fr; }
  .assistant-column { position: static; width: 100%; height: 620px; min-height: 620px; }
}
@media (max-width: 680px) {
  .console-code { display: none; }
  .task-command { gap: 20px; padding: 22px 18px; }
  .command-copy h1 { font-size: 28px; }
  .command-readout { grid-template-columns: 72px minmax(0, 1fr); padding: 14px; }
  .readout-grid { grid-template-columns: 1fr; }
  .workflow-heading { align-items: flex-start; flex-direction: column; }
  .assistant-column { height: 560px; min-height: 560px; }
}
@media (prefers-reduced-motion: reduce) {
  .status-spinner, .state-spinner { animation: none; }
  .back-button { transition: none; }
}
</style>
