<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { gsap } from 'gsap'
import {
  Monitor,
  Bell,
  CircleCheck,
  Search,
  Tickets,
  ChatDotRound,
  Share,
  Reading,
  UploadFilled,
  Promotion,
  ArrowRight,
} from '@element-plus/icons-vue'
import { getUserOverview } from '@/api/stat'

const router = useRouter()

const userName = computed(() => {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}').name || '工程师'
  } catch {
    return '工程师'
  }
})

/* —— 顶部指标（数据来自 /weixiu/stat/user-overview，初始为 0，加载后填充） —— */
const stats = reactive([
  { key: 'dev', label: '设备总数', value: 0, display: 0, decimals: 0, suffix: '', icon: Monitor, tone: 'accent', spark: 'M0,18 L8,14 L16,16 L24,9 L32,12 L40,6 L48,10 L56,4 L64,8 L72,3', to: '/user/tasks' },
  { key: 'todo', label: '我的待办', value: 0, display: 0, decimals: 0, suffix: '', icon: Bell, tone: 'info', spark: 'M0,16 L8,10 L16,17 L24,8 L32,15 L40,7 L48,14 L56,6 L64,13 L72,9', to: '/user/tasks' },
  { key: 'rate', label: '任务完成率', value: 0, display: 0, decimals: 1, suffix: '%', icon: CircleCheck, tone: 'success', spark: 'M0,14 L8,12 L16,13 L24,9 L32,11 L40,7 L48,9 L56,5 L64,7 L72,4', to: '/user/tasks' },
])

/* —— 中央检修台周围的功能节点 —— */
const nodes = [
  { label: '智能检索', sub: 'RETRIEVAL', icon: Search, to: '/user/search', side: 'l', slot: 0 },
  { label: '检修任务', sub: 'TASKS', icon: Tickets, to: '/user/tasks', side: 'l', slot: 1 },
  { label: 'AI 对话', sub: 'ASSISTANT', icon: ChatDotRound, to: '/user/ai-chat', side: 'l', slot: 2 },
  { label: '知识图谱', sub: 'GRAPH', icon: Share, to: '/user/graph', side: 'r', slot: 0 },
  { label: '知识问答', sub: 'QUIZ', icon: Reading, to: '/user/quiz', side: 'r', slot: 1 },
  { label: '经验上传', sub: 'CASES', icon: UploadFilled, to: '/user/case-upload', side: 'r', slot: 2 },
]

/* —— 检修任务流转（按后端真实状态计数，count 初始 0，加载后填充） —— */
const taskFlow = reactive([
  { status: 'CREATED', label: '已创建', count: 0, tone: 'accent' },
  { status: 'GENERATING', label: '生成中', count: 0, tone: 'info' },
  { status: 'GENERATED', label: '待执行', count: 0, tone: 'warning' },
  { status: 'EXECUTING', label: '执行中', count: 0, tone: 'gold' },
  { status: 'CLOSED', label: '已完成', count: 0, sub: '已闭环', tone: 'success' },
])

/* —— 知识问答示例 —— */
const quizSamples = [
  '电机振动大是什么原因？',
  '如何处理轴承过热？',
  '推荐相关检修案例',
]

/* —— AI 检修表单 —— */
const target = ref('电机 - MTR-2301')
const problem = ref('')
const range = ref('近 7 天运行数据')

function go(to) {
  router.push(to)
}
function startDiagnose() {
  router.push({ path: '/user/search', query: problem.value.trim() ? { q: problem.value.trim() } : {} })
}
const assistantInput = ref('')
// 点击示例问题：仅填充到输入框，不直接发送
function fillAssistant(q) {
  assistantInput.value = q
}
// 点击发送：携带问题跳转 AI 对话页并真正发送
function askAssistant() {
  const text = assistantInput.value.trim()
  if (!text) return
  router.push({ path: '/user/ai-chat', query: { q: text } })
}

/* ============ 3D 全息检修台 ============ */
const rootRef = ref(null)
const platformRef = ref(null)
const coreRef = ref(null)

const motion = { spin: 0, tilt: 60, vel: 0.18, dragging: false }
const AUTO = 0.18
let reduce = false
let tickerFn = null
let ctx = null

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

let lastX = 0, downY = 0, baseTilt = 60
function onPointerDown(e) {
  motion.dragging = true
  lastX = e.clientX
  downY = e.clientY
  baseTilt = motion.tilt
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
}
function onPointerMove(e) {
  const dx = e.clientX - lastX
  lastX = e.clientX
  const step = dx * 0.45
  motion.spin += step
  motion.vel = step
  motion.tilt = clamp(baseTilt - (e.clientY - downY) * 0.22, 42, 74)
}
function onPointerUp() {
  motion.dragging = false
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
}

/* —— 拉取真实概览统计 —— */
async function loadOverview() {
  try {
    const res = await getUserOverview()
    if (!res || String(res.code) !== '200' || !res.data) return
    const d = res.data
    const map = { dev: Number(d.deviceTotal) || 0, todo: Number(d.myOpenTasks) || 0, rate: Number(d.completionRate) || 0 }
    stats.forEach((s) => {
      s.value = map[s.key] ?? 0
      // 数字滚动到真实值；若入场动画已把 display 滚到 0，这里再滚到真实值
      gsap.to(s, { display: s.value, duration: 1.2, ease: 'power2.out' })
    })
    const flow = d.taskFlow || {}
    taskFlow.forEach((t) => {
      t.count = Number(flow[t.status]) || 0
    })
  } catch (e) {
    // 网络/业务失败时保持 0 占位，不展示编造数据（request.js 已统一提示）
  }
}

onMounted(() => {
  reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (reduce) { motion.vel = 0 }

  loadOverview()

  ctx = gsap.context(() => {
    // 入场
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } })
    tl.from('.hero-kicker', { y: -12, autoAlpha: 0, duration: 0.4 })
      .from('.dash-stat', { x: -18, autoAlpha: 0, duration: 0.5, stagger: 0.1 }, '-=0.2')
      .from('.hero', { y: 26, autoAlpha: 0, duration: 0.6 }, '-=0.3')
      .from('.holo', { scale: 0.7, autoAlpha: 0, duration: 0.7, ease: 'back.out(1.5)' }, '-=0.4')
      .from('.diag-node', { autoAlpha: 0, scale: 0.8, duration: 0.45, stagger: 0.08 }, '-=0.4')
      .from('.col-right > .card', { x: 26, autoAlpha: 0, duration: 0.55, stagger: 0.12 }, '-=0.5')
      .from('.flow-col', { y: 20, autoAlpha: 0, duration: 0.45, stagger: 0.08 }, '-=0.4')

    // 指标数字滚动
    stats.forEach((s, i) => {
      gsap.to(s, { display: s.value, duration: 1.4, delay: 0.35 + i * 0.12, ease: 'power2.out' })
    })

    // 连接线数据流
    gsap.to('.flow-line', { strokeDashoffset: -200, duration: 6, repeat: -1, ease: 'none' })

    // 检修台漂浮
    if (!reduce) {
      gsap.to(coreRef.value, { y: -10, duration: 3, yoyo: true, repeat: -1, ease: 'sine.inOut' })
    }

    // 平台旋转（每帧驱动，支持拖拽）
    tickerFn = () => {
      if (!motion.dragging) {
        motion.vel += (AUTO - motion.vel) * 0.04
        motion.spin += motion.vel
      }
      if (platformRef.value) gsap.set(platformRef.value, { rotationX: motion.tilt, rotationZ: motion.spin })
      if (coreRef.value) gsap.set(coreRef.value, { rotationY: clamp((motion.vel - AUTO) * 9, -18, 18) })
    }
    gsap.ticker.add(tickerFn)
  }, rootRef.value)
})

onBeforeUnmount(() => {
  if (tickerFn) gsap.ticker.remove(tickerFn)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  ctx && ctx.revert()
})
</script>

<template>
  <div ref="rootRef" class="user-dashboard">
    <span class="hero-kicker">检修智脑 · MAINTENANCE&nbsp;CONSOLE · 欢迎回来，{{ userName }}</span>

    <div class="dash">
      <!-- ================= 左列 ================= -->
      <div class="col-left">
        <!-- 顶部指标 -->
        <div class="stats">
          <button
            v-for="s in stats"
            :key="s.key"
            class="dash-stat"
            :class="s.tone"
            @click="go(s.to)"
          >
            <div class="stat-top">
              <span class="stat-ico"><el-icon><component :is="s.icon" /></el-icon></span>
              <span class="stat-val">
                {{ s.decimals ? s.display.toFixed(1) : Math.round(s.display) }}<i>{{ s.suffix }}</i>
              </span>
            </div>
            <span class="stat-label">{{ s.label }}</span>
            <svg class="stat-spark" viewBox="0 0 72 22" preserveAspectRatio="none">
              <path :d="s.spark" />
            </svg>
          </button>
        </div>

        <!-- AI 检修 主面板 -->
        <section class="hero">
          <header class="hero-head">
            <h2 class="hero-title"><span class="bar" />智能检索</h2>
            <span class="hero-tag">INTELLIGENT&nbsp;RETRIEVAL</span>
          </header>

          <div class="hero-body">
            <!-- 表单 -->
            <div class="form">
              <label class="f-label">诊断目标</label>
              <div class="f-select">
                <input v-model="target" />
                <el-icon><ArrowRight /></el-icon>
              </div>

              <label class="f-label">问题描述</label>
              <div class="f-area">
                <textarea v-model="problem" maxlength="300" placeholder="请输入设备异常现象或粘贴告警信息..." />
                <span class="f-count">{{ problem.length }}/300</span>
              </div>

              <label class="f-label">数据范围</label>
              <div class="f-select">
                <input v-model="range" />
                <el-icon><ArrowRight /></el-icon>
              </div>

              <button class="f-go" @click="startDiagnose">
                <span class="tri">▶</span> 开始智能检索
              </button>
            </div>

            <!-- 3D 全息检修台 + 功能节点 -->
            <div class="diagram">
              <svg class="links" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path class="flow-line" d="M19,20 L50,50" />
                <path class="flow-line" d="M17,50 L50,50" />
                <path class="flow-line" d="M19,80 L50,50" />
                <path class="flow-line" d="M81,20 L50,50" />
                <path class="flow-line" d="M83,50 L50,50" />
                <path class="flow-line" d="M81,80 L50,50" />
              </svg>

              <div class="holo">
                <div ref="platformRef" class="holo-platform">
                  <span class="ring r1" />
                  <span class="ring r2" />
                  <span class="ring r3" />
                  <span class="hub" />
                </div>
                <div ref="coreRef" class="holo-core" @pointerdown.prevent="onPointerDown">
                  <svg viewBox="0 0 160 160" class="bench">
                    <defs>
                      <linearGradient id="bg1" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0" style="stop-color: var(--plaza-accent)" />
                        <stop offset="1" style="stop-color: var(--plaza-accent-hover)" />
                      </linearGradient>
                    </defs>
                    <!-- 显示器机身 -->
                    <path class="bn-fill" d="M40,34 L120,34 L120,92 L40,92 Z" />
                    <path class="bn-line" d="M40,34 L120,34 L120,92 L40,92 Z" />
                    <!-- 屏幕 -->
                    <path class="bn-line" d="M47,41 L113,41 L113,85 L47,85 Z" />
                    <polyline class="bn-wave" points="53,73 62,73 67,57 74,79 80,65 87,75 94,51 100,69 107,69" />
                    <!-- 支架与底座 -->
                    <path class="bn-line" d="M80,92 L80,104" />
                    <path class="bn-fill" d="M62,112 L98,112 L93,104 L67,104 Z" />
                    <path class="bn-line" d="M62,112 L98,112 L93,104 L67,104 Z" />
                    <!-- 键盘 -->
                    <path class="bn-fill" d="M46,120 L114,120 L122,134 L38,134 Z" />
                    <path class="bn-line" d="M46,120 L114,120 L122,134 L38,134 Z" />
                    <path class="bn-line" d="M51,124 L61,124 M65,124 L75,124 M79,124 L89,124 M93,124 L103,124 M107,124 L113,124 M44,129 L116,129" />
                    <!-- 电源指示灯 -->
                    <circle class="bn-fill2" cx="80" cy="88" r="2.2" />
                  </svg>
                  <span class="core-glow" />
                </div>
                <span class="holo-hint">拖拽可旋转</span>
              </div>

              <router-link
                v-for="n in nodes"
                :key="n.label"
                :to="n.to"
                class="diag-node"
                :class="[n.side === 'l' ? 'left' : 'right', 's' + n.slot]"
              >
                <span class="dn-ico"><el-icon><component :is="n.icon" /></el-icon></span>
                <span class="dn-text">
                  <span class="dn-label">{{ n.label }}</span>
                  <span class="dn-sub">{{ n.sub }}</span>
                </span>
              </router-link>
            </div>
          </div>
        </section>

        <!-- 检修任务流转 -->
        <section class="card flow-card">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />检修任务</h3>
            <router-link to="/user/tasks" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <div class="flow">
            <div class="flow-track"><span class="flow-fill" /></div>
            <div class="flow-cols">
              <router-link
                v-for="(t, i) in taskFlow"
                :key="t.status"
                to="/user/tasks"
                class="flow-col"
              >
                <span class="flow-dot" :class="t.tone"><i /></span>
                <div class="flow-stat" :class="t.tone">
                  <span class="fc-head">
                    <span class="fc-label">{{ t.label }}</span>
                    <span class="fc-count">{{ t.count }}</span>
                  </span>
                  <span class="fc-sub">{{ t.sub || '当前数量' }}</span>
                </div>
              </router-link>
            </div>
          </div>
        </section>
      </div>

      <!-- ================= 右列 ================= -->
      <div class="col-right">
        <!-- AI 助手 -->
        <section class="card assistant">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />AI 助手</h3>
          </header>
          <p class="asst-hello">您好，我是 AI 助手。可询问设备故障分析、检修建议、知识查询等问题。</p>
          <div class="asst-chips">
            <button @click="fillAssistant('电机振动大是什么原因？')">电机振动大是什么原因？</button>
            <button @click="fillAssistant('如何处理轴承过热？')">如何处理轴承过热？</button>
            <button @click="fillAssistant('推荐相关检修案例')">推荐相关检修案例</button>
          </div>
          <div class="asst-input">
            <input
              v-model="assistantInput"
              placeholder="请输入您的问题..."
              @keyup.enter="askAssistant()"
            />
            <button class="asst-send" @click="askAssistant()"><el-icon><Promotion /></el-icon></button>
          </div>
        </section>

        <!-- 知识问答（替换设备健康） -->
        <section class="card quiz">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />知识问答</h3>
            <router-link to="/user/quiz" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <p class="quiz-intro">挑战检修知识题库，巩固设备原理与排障要点。</p>
          <div class="quiz-samples">
            <button v-for="q in quizSamples" :key="q" class="quiz-chip" @click="go('/user/quiz')">
              {{ q }}
            </button>
          </div>
          <button class="quiz-go" @click="go('/user/quiz')">
            开始知识问答 <el-icon><ArrowRight /></el-icon>
          </button>
        </section>

        <!-- 知识图谱 -->
        <section class="card">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />知识图谱</h3>
            <router-link to="/user/graph" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <button class="mini-graph" @click="go('/user/graph')">
            <svg viewBox="0 0 260 150">
              <line x1="130" y1="75" x2="60" y2="38" />
              <line x1="130" y1="75" x2="48" y2="92" />
              <line x1="130" y1="75" x2="86" y2="120" />
              <line x1="130" y1="75" x2="200" y2="40" />
              <line x1="130" y1="75" x2="214" y2="100" />
              <line x1="130" y1="75" x2="150" y2="128" />
              <circle class="g-core" cx="130" cy="75" r="22" />
              <text class="g-core-t" x="130" y="79" text-anchor="middle">电机</text>
              <g class="g-node"><circle cx="60" cy="38" r="9" /><text x="60" y="24">轴承异常</text></g>
              <g class="g-node"><circle cx="48" cy="92" r="9" /><text x="48" y="110">润滑不足</text></g>
              <g class="g-node"><circle cx="86" cy="120" r="9" /><text x="86" y="138">转子不平衡</text></g>
              <g class="g-node"><circle cx="200" cy="40" r="9" /><text x="200" y="26">振动过大</text></g>
              <g class="g-node"><circle cx="214" cy="100" r="9" /><text x="214" y="118">温度故障</text></g>
              <g class="g-node"><circle cx="150" cy="128" r="9" /><text x="150" y="146">温度过高</text></g>
            </svg>
          </button>
        </section>

      </div>
    </div>
  </div>
</template>

<style scoped>
.user-dashboard { max-width: 1320px; margin: 0 auto; }
.hero-kicker {
  display: block;
  margin-bottom: 16px;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1.6px;
  color: var(--plaza-accent);
}

.dash {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(0, 1fr);
  gap: 20px;
  align-items: stretch;          /* 两列等高：以较高的一列为准 */
}
.col-left, .col-right { display: flex; flex-direction: column; gap: 20px; min-width: 0; }
/* 顶部：三个指标卡与右列首卡同处列顶 → 上边框对齐 */
/* 底部：每列最后一个卡片纵向撑满 → 下边框对齐且不留空白 */
.col-left > :last-child,
.col-right > :last-child { flex: 1 1 auto; }
/* 撑高后内部内容居中/分布，避免卡片内出现大片空白 */
.flow-card { display: flex; flex-direction: column; }
.flow-card .flow { margin: auto 0; }
.quiz { display: flex; flex-direction: column; }
.quiz .quiz-go { margin-top: auto; }

/* —— 通用卡片 —— */
.card {
  position: relative;
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  padding: 20px 22px;
  box-shadow: var(--plaza-shadow-organic);
}
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.card-title, .hero-title {
  display: flex; align-items: center; gap: 9px;
  font-family: var(--font-display);
  font-size: 1.16rem; font-weight: 700; color: var(--plaza-heading);
}
.bar { width: 4px; height: 17px; border-radius: 3px; background: var(--plaza-accent-grad); }
.more {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 12.5px; color: var(--plaza-text-muted); font-family: var(--font-mono);
  transition: color .2s ease;
}
.more:hover { color: var(--plaza-accent); }

/* —— 指标卡 —— */
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.dash-stat {
  position: relative; overflow: hidden; cursor: pointer; text-align: left;
  padding: 16px 18px 14px;
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: 14px;
  box-shadow: var(--plaza-shadow-organic);
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
.dash-stat:hover { transform: translateY(-3px); box-shadow: var(--plaza-shadow-organic-hover); border-color: var(--plaza-border-strong); }
.stat-top { display: flex; align-items: center; justify-content: space-between; }
.stat-ico {
  width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; font-size: 19px;
}
.dash-stat.accent .stat-ico { color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.dash-stat.info .stat-ico { color: var(--plaza-info); background: var(--plaza-info-soft); }
.dash-stat.success .stat-ico { color: var(--plaza-success); background: var(--plaza-success-soft); }
.stat-val { font-family: var(--font-display); font-size: 1.9rem; font-weight: 700; color: var(--plaza-heading); line-height: 1; }
.stat-val i { font-size: 1rem; font-style: normal; color: var(--plaza-text-muted); margin-left: 1px; }
.stat-label { display: block; margin-top: 6px; font-size: 13px; color: var(--plaza-text-muted); }
.stat-spark { display: block; width: 100%; height: 22px; margin-top: 8px; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; opacity: .8; }
.dash-stat.accent .stat-spark { stroke: var(--plaza-accent); }
.dash-stat.info .stat-spark { stroke: var(--signal-strong); }
.dash-stat.success .stat-spark { stroke: var(--plaza-success); }

/* —— AI 检修 暖咖控制台 —— */
.hero {
  position: relative; overflow: hidden;
  margin-top: 14px;            /* 与上方三个指标卡拉开距离，不贴在一起 */
  border-radius: var(--plaza-radius-lg);
  padding: 20px 22px 22px;
  color: var(--plaza-panel-bg);
  background:
    radial-gradient(120% 120% at 10% -10%, var(--signal-line), transparent 55%),
    linear-gradient(160deg, var(--plaza-heading) 0%, var(--plaza-heading) 70%);
  border: 1px solid var(--signal-soft);
  box-shadow: 0 26px 60px -22px rgba(0, 0, 0, .55), inset 0 0 0 1px rgba(255,255,255,.02);
}
.hero::before {
  content: ''; position: absolute; inset: 0; pointer-events: none;
  background-image:
    linear-gradient(var(--signal-soft) 1px, transparent 1px),
    linear-gradient(90deg, var(--signal-soft) 1px, transparent 1px);
  background-size: 26px 26px;
  -webkit-mask-image: radial-gradient(circle at 70% 50%, #000, transparent 75%);
  mask-image: radial-gradient(circle at 70% 50%, #000, transparent 75%);
}
.hero-head { position: relative; display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.hero-title { color: #fbeede; }
.hero-tag { font-family: var(--font-mono); font-size: 10px; letter-spacing: 1.6px; color: var(--signal); }
.hero-body { position: relative; display: grid; grid-template-columns: 264px minmax(0, 1fr); gap: 20px; }

/* 表单 */
.form { display: flex; flex-direction: column; }
.f-label { font-size: 12.5px; color: var(--plaza-border-strong); margin: 12px 0 6px; }
.f-label:first-child { margin-top: 0; }
.f-select, .f-area {
  position: relative;
  background: rgba(255,255,255,.045);
  border: 1px solid var(--signal-line);
  border-radius: 10px;
  transition: border-color .2s ease;
}
.f-select:focus-within, .f-area:focus-within { border-color: var(--signal); }
.f-select input, .f-area textarea {
  width: 100%; background: transparent; border: none; outline: none;
  color: #fbeede; font-size: 13.5px; font-family: inherit;
}
.f-select { display: flex; align-items: center; padding: 10px 12px; }
.f-select .el-icon { color: var(--signal); transform: rotate(90deg); }
.f-area { padding: 10px 12px 22px; }
.f-area textarea { resize: none; height: 64px; line-height: 1.5; }
.f-area textarea::placeholder { color: var(--plaza-text-muted); }
.f-count { position: absolute; right: 10px; bottom: 6px; font-size: 11px; font-family: var(--font-mono); color: var(--plaza-text-muted); }
.f-go {
  margin-top: 16px; height: 44px; border: none; cursor: pointer;
  border-radius: 11px; color: var(--plaza-heading); font-weight: 700; font-size: 14.5px; letter-spacing: .5px;
  background: linear-gradient(135deg, var(--plaza-accent), var(--signal-strong));
  box-shadow: 0 10px 24px -8px var(--signal-strong);
  transition: transform .18s ease, box-shadow .18s ease;
}
.f-go:hover { transform: translateY(-2px); box-shadow: 0 16px 30px -8px var(--signal-strong); }
.f-go .tri { font-size: 11px; }

/* 全息检修台图区 */
.diagram { position: relative; min-height: 340px; display: flex; align-items: center; justify-content: center; }
.links { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.flow-line {
  fill: none; stroke: var(--signal); stroke-width: .5; opacity: .5;
  stroke-dasharray: 3 4; stroke-dashoffset: 0;
}

/* 3D 平台 */
.holo {
  position: relative; width: 230px; height: 230px;
  perspective: 950px;
  display: grid; place-items: center;
}
.holo-platform {
  position: absolute; inset: 0; margin: auto;
  width: 200px; height: 200px;
  transform-style: preserve-3d;
  transform-origin: 50% 50%;
}
.ring { position: absolute; inset: 0; margin: auto; border-radius: 50%; }
.ring.r1 { width: 200px; height: 200px; border: 1px dashed var(--signal); }
.ring.r2 { width: 150px; height: 150px; border: 1.5px solid var(--signal); box-shadow: 0 0 18px var(--signal-soft) inset; }
.ring.r3 {
  width: 96px; height: 96px;
  border: 2px solid transparent;
  border-top-color: var(--signal); border-right-color: var(--signal-strong);
  box-shadow: 0 0 22px var(--signal-line);
}
.hub { position: absolute; inset: 0; margin: auto; width: 30px; height: 30px; border-radius: 50%; background: radial-gradient(circle, rgba(246,176,114,.9), var(--signal-line) 70%, transparent); }
.holo-core {
  position: relative; z-index: 2; width: 150px; height: 150px;
  display: grid; place-items: center; cursor: grab; touch-action: none;
}
.holo-core:active { cursor: grabbing; }
.bench { width: 132px; height: 132px; overflow: visible; }
.bn-line { fill: none; stroke: url(#bg1); stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.bn-fill { fill: var(--signal-soft); }
.bn-fill2 { fill: var(--signal); }
.bn-wave { fill: none; stroke: #ffd9a8; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.core-glow {
  position: absolute; width: 150px; height: 150px; border-radius: 50%; z-index: -1;
  background: radial-gradient(circle, var(--signal-line), transparent 65%);
  filter: blur(6px);
}
.holo-hint {
  position: absolute; bottom: -2px; left: 50%; transform: translateX(-50%);
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px; color: var(--plaza-text-muted);
  opacity: .8;
}

/* 功能节点 */
.diag-node {
  position: absolute; z-index: 3;
  display: flex; align-items: center; gap: 9px;
  padding: 8px 12px; border-radius: 11px;
  background: rgba(0, 0, 0, .78);
  border: 1px solid var(--signal-line);
  box-shadow: 0 8px 20px -10px rgba(0,0,0,.6);
  backdrop-filter: blur(4px);
  transition: transform .2s ease, border-color .2s ease, background .2s ease;
}
.diag-node:hover { transform: scale(1.05); border-color: var(--signal); background: rgba(0, 0, 0, .9); }
.diag-node.left { left: 2px; flex-direction: row; }
.diag-node.right { right: 2px; flex-direction: row-reverse; text-align: right; }
.diag-node.s0 { top: 8%; }
.diag-node.s1 { top: 50%; transform: translateY(-50%); }
.diag-node.s1:hover { transform: translateY(-50%) scale(1.05); }
.diag-node.s2 { bottom: 8%; }
.dn-ico { width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0; display: grid; place-items: center; font-size: 16px; color: var(--signal); background: var(--signal-soft); }
.dn-text { display: flex; flex-direction: column; }
.dn-label { font-size: 13px; font-weight: 600; color: #fbeede; }
.dn-sub { font-family: var(--font-mono); font-size: 9px; letter-spacing: 1px; color: var(--plaza-text-muted); }

/* —— 检修任务流转 —— */
.flow { position: relative; padding-top: 6px; }
.flow-track { position: absolute; top: 13px; left: 8%; right: 8%; height: 2px; background: var(--plaza-border); border-radius: 2px; }
.flow-fill { position: absolute; inset: 0; border-radius: 2px; background: linear-gradient(90deg, var(--plaza-accent), var(--signal), var(--plaza-success)); }
.flow-cols { position: relative; display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.flow-col { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.flow-dot { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; background: var(--plaza-bg-card); border: 2px solid var(--plaza-border-strong); }
.flow-dot i { width: 8px; height: 8px; border-radius: 50%; }
.flow-dot.accent { border-color: var(--plaza-accent); } .flow-dot.accent i { background: var(--plaza-accent); }
.flow-dot.info { border-color: var(--signal-strong); } .flow-dot.info i { background: var(--signal-strong); }
.flow-dot.warning { border-color: var(--plaza-warning); } .flow-dot.warning i { background: var(--plaza-warning); }
.flow-dot.gold { border-color: var(--plaza-gold); } .flow-dot.gold i { background: var(--plaza-gold); }
.flow-dot.success { border-color: var(--plaza-success); } .flow-dot.success i { background: var(--plaza-success); }
.flow-stat { width: 100%; text-align: center; padding: 10px 6px; border-radius: 11px; background: var(--plaza-bg); border: 1px solid var(--plaza-border); transition: border-color .2s ease, transform .2s ease; }
.flow-col:hover .flow-stat { transform: translateY(-2px); border-color: var(--plaza-border-strong); }
.fc-head { display: flex; align-items: baseline; justify-content: center; gap: 8px; }
.fc-label { font-size: 13px; color: var(--plaza-text); }
.fc-count { font-family: var(--font-display); font-weight: 700; font-size: 1.25rem; }
.flow-stat.accent .fc-count { color: var(--plaza-accent); }
.flow-stat.info .fc-count { color: var(--signal-strong); }
.flow-stat.warning .fc-count { color: var(--plaza-warning); }
.flow-stat.gold .fc-count { color: var(--plaza-gold); }
.flow-stat.success .fc-count { color: var(--plaza-success); }
.fc-sub { display: block; margin-top: 2px; font-size: 11px; color: var(--plaza-text-muted); }

/* —— 知识问答 —— */
.quiz-intro { font-size: 13px; color: var(--plaza-text-muted); line-height: 1.6; margin-bottom: 14px; }
.quiz-samples { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.quiz-chip {
  text-align: left; cursor: pointer; padding: 11px 14px; border-radius: 10px;
  background: var(--plaza-bg); border: 1px solid var(--plaza-border);
  color: var(--plaza-text); font-size: 13px; transition: all .2s ease;
}
.quiz-chip:hover { border-color: var(--plaza-accent); background: var(--plaza-accent-soft); color: var(--plaza-accent); transform: translateX(3px); }
.quiz-go, .asst-send {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; cursor: pointer;
}
.quiz-go {
  width: 100%; height: 42px; border: none; border-radius: 11px; font-weight: 700; font-size: 14px;
  color: var(--home-btn-text); background: var(--plaza-accent-grad);
  box-shadow: 0 10px 22px -10px var(--plaza-accent); transition: transform .18s ease;
}
.quiz-go:hover { transform: translateY(-2px); }

/* —— 知识图谱 —— */
.mini-graph { width: 100%; cursor: pointer; padding: 6px; border: none; background: transparent; }
.mini-graph svg { width: 100%; height: auto; }
.mini-graph line { stroke: var(--plaza-border-strong); stroke-width: 1.2; stroke-dasharray: 2 3; }
.mini-graph .g-core { fill: var(--plaza-accent-soft-strong); stroke: var(--plaza-accent); stroke-width: 2; }
.mini-graph .g-core-t { fill: var(--plaza-accent); font-size: 13px; font-weight: 700; font-family: var(--font-display); }
.mini-graph .g-node circle { fill: var(--plaza-bg-card); stroke: var(--signal-strong); stroke-width: 2; transition: fill .2s ease; }
.mini-graph .g-node text { fill: var(--plaza-text-muted); font-size: 10px; text-anchor: middle; }
.mini-graph:hover .g-node circle { fill: var(--signal-soft); }

/* —— AI 助手 —— */
.asst-hello { font-size: 13px; color: var(--plaza-text); line-height: 1.6; padding: 12px 14px; border-radius: 10px; background: var(--plaza-bg); margin-bottom: 12px; }
.asst-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.asst-chips button {
  cursor: pointer; padding: 7px 11px; border-radius: 999px; font-size: 12px;
  background: var(--plaza-bg); border: 1px solid var(--plaza-border); color: var(--plaza-text-muted);
  transition: all .2s ease;
}
.asst-chips button:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.asst-input { display: flex; align-items: center; gap: 8px; padding: 6px 6px 6px 14px; border-radius: 12px; background: var(--plaza-bg); border: 1px solid var(--plaza-border); transition: border-color .2s ease; }
.asst-input:focus-within { border-color: var(--plaza-accent); }
.asst-input input { flex: 1; border: none; background: transparent; outline: none; font-size: 13.5px; color: var(--plaza-text); font-family: inherit; }
.asst-send { width: 36px; height: 36px; flex-shrink: 0; border: none; border-radius: 9px; color: var(--home-btn-text); background: var(--plaza-accent-grad); font-size: 16px; }
.asst-send:hover { filter: brightness(1.05); }

/* —— 响应式 —— */
@media (max-width: 1180px) {
  .dash { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .stats { grid-template-columns: 1fr; }
  .hero-body { grid-template-columns: 1fr; }
  .diagram { min-height: 380px; }
  .flow-cols { grid-template-columns: repeat(2, 1fr); }
}
</style>
