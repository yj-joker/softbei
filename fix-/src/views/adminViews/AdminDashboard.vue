<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { gsap } from 'gsap'
import {
  User,
  Warning,
  CircleCheck,
  List,
  Collection,
  ChatDotRound,
  Share,
  Setting,
  Files,
  Promotion,
  ArrowRight,
} from '@element-plus/icons-vue'
import { getAdminOverview } from '@/api/stat'

const router = useRouter()

const adminName = computed(() => {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}').name || '管理员'
  } catch {
    return '管理员'
  }
})

/* —— 顶部指标（数据来自 /weixiu/stat/admin-overview，初始为 0，加载后填充） —— */
const stats = reactive([
  { key: 'users', label: '用户总数', value: 0, display: 0, decimals: 0, suffix: '', icon: User, tone: 'accent', spark: 'M0,18 L8,14 L16,16 L24,9 L32,12 L40,6 L48,10 L56,4 L64,8 L72,3', to: '/admin/system?tab=users' },
  { key: 'review', label: '待审核案例', value: 0, display: 0, decimals: 0, suffix: '', icon: Warning, tone: 'info', spark: 'M0,16 L8,10 L16,17 L24,8 L32,15 L40,7 L48,14 L56,6 L64,13 L72,9', to: '/admin/tasks' },
  { key: 'devices', label: '设备总数', value: 0, display: 0, decimals: 0, suffix: '', icon: CircleCheck, tone: 'success', spark: 'M0,14 L8,12 L16,13 L24,9 L32,11 L40,7 L48,9 L56,5 L64,7 L72,4', to: '/admin/knowledge-center?tab=graph' },
])

/* —— 中央控制台周围的管理节点 —— */
const nodes = [
  { label: '任务管理', sub: 'TASKS', icon: List, to: '/admin/tasks', side: 'l', slot: 0 },
  { label: '知识中心', sub: 'KNOWLEDGE', icon: Collection, to: '/admin/knowledge-center', side: 'l', slot: 1 },
  { label: 'AI 助手', sub: 'ASSISTANT', icon: ChatDotRound, to: '/admin/ai-chat', side: 'l', slot: 2 },
  { label: '知识图谱', sub: 'GRAPH', icon: Share, to: '/admin/knowledge-center?tab=graph', side: 'r', slot: 0 },
  { label: '系统管理', sub: 'SYSTEM', icon: Setting, to: '/admin/system', side: 'r', slot: 1 },
  { label: '案例审核', sub: 'REVIEW', icon: Files, to: '/admin/tasks', side: 'r', slot: 2 },
]

/* —— 任务流转（按后端真实状态计数，count 初始 0，加载后填充） —— */
const taskFlow = reactive([
  { status: 'CREATED', label: '已创建', count: 0, tone: 'accent' },
  { status: 'GENERATING', label: '生成中', count: 0, tone: 'info' },
  { status: 'GENERATED', label: '待执行', count: 0, tone: 'warning' },
  { status: 'EXECUTING', label: '执行中', count: 0, tone: 'gold' },
  { status: 'CLOSED', label: '已完成', count: 0, sub: '已闭环', tone: 'success' },
])

/* —— 最近动态 —— 后端暂无操作流水接口，置空并在模板显示空状态，不展示编造数据 */
const recentActivities = reactive([])

/* —— 管理助手示例 —— */
const assistantChips = [
  '本月新增多少知识条目？',
  '帮我整理待审核案例',
  '统计高频检修分类',
]

/* —— 检修任务状态分布（饼图，clicks 复用为“任务数”，加载后填充） —— */
const taskStatusOrder = [
  { status: 'CREATED', name: '已创建' },
  { status: 'GENERATING', name: '生成中' },
  { status: 'GENERATED', name: '待执行' },
  { status: 'EXECUTING', name: '执行中' },
  { status: 'CLOSED', name: '已完成' },
]
const categoryClicksRaw = reactive(taskStatusOrder.map((s) => ({ name: s.name, clicks: 0 })))
const chartColors = ['#c4602f', '#df9226', '#5e8c3e', '#a8605f', '#c5402c', '#e0982f']

const pieData = computed(() => {
  const items = categoryClicksRaw.map((item, i) => ({ name: item.name, clicks: item.clicks, color: chartColors[i % chartColors.length] }))
  const total = items.reduce((sum, item) => sum + item.clicks, 0)
  // 无任务时给一个占位灰环，避免除零导致 NaN 路径
  if (total === 0) {
    return items.map((item, i) => ({ ...item, angle: i === 0 ? 360 : 0, startAngle: 0, percent: '0.0', color: 'var(--plaza-border)' }))
  }
  let currentAngle = 0
  return items.map((item) => {
    const angle = (item.clicks / total) * 360
    const startAngle = currentAngle
    currentAngle += angle
    return { ...item, angle, startAngle, percent: ((item.clicks / total) * 100).toFixed(1) }
  })
})

function polarToCartesian(cx, cy, r, angle) {
  const rad = (angle - 90) * Math.PI / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}
function describeArc(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, endAngle)
  const end = polarToCartesian(cx, cy, r, startAngle)
  const largeArcFlag = endAngle - startAngle <= 180 ? 0 : 1
  return ['M', cx, cy, 'L', start.x, start.y, 'A', r, r, 0, largeArcFlag, 0, end.x, end.y, 'Z'].join(' ')
}
const piePaths = computed(() =>
  pieData.value.map((item) => ({
    path: describeArc(110, 110, 100, item.startAngle, item.startAngle + item.angle),
    color: item.color,
    percent: item.percent,
  })),
)
const hoveredSlice = ref(null)

/* —— 管理控制台检索表单 —— */
const target = ref('知识条目')
const keyword = ref('')
const range = ref('全部知识库')

function go(to) {
  router.push(to)
}
function startSearch() {
  router.push({ path: '/admin/knowledge-center', query: { tab: 'knowledge', ...(keyword.value.trim() ? { q: keyword.value.trim() } : {}) } })
}
const assistantInput = ref('')
function fillAssistant(q) {
  assistantInput.value = q
}
function askAssistant() {
  const text = assistantInput.value.trim()
  if (!text) return
  router.push({ path: '/admin/ai-chat', query: { q: text } })
}

/* ============ 3D 全息控制台 ============ */
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
    const res = await getAdminOverview()
    if (!res || String(res.code) !== '200' || !res.data) return
    const d = res.data
    const map = { users: Number(d.userTotal) || 0, review: Number(d.pendingCaseTotal) || 0, devices: Number(d.deviceTotal) || 0 }
    stats.forEach((s) => {
      s.value = map[s.key] ?? 0
      gsap.to(s, { display: s.value, duration: 1.2, ease: 'power2.out' })
    })
    const dist = d.taskStatusDist || {}
    taskFlow.forEach((t) => { t.count = Number(dist[t.status]) || 0 })
    // 饼图：按状态顺序填充任务数
    taskStatusOrder.forEach((s, i) => { categoryClicksRaw[i].clicks = Number(dist[s.status]) || 0 })
  } catch (e) {
    // 失败保持 0 占位，不展示编造数据
  }
}

onMounted(() => {
  reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (reduce) { motion.vel = 0 }

  loadOverview()

  ctx = gsap.context(() => {
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } })
    tl.from('.hero-kicker', { y: -12, autoAlpha: 0, duration: 0.4 })
      .from('.dash-stat', { x: -18, autoAlpha: 0, duration: 0.5, stagger: 0.1 }, '-=0.2')
      .from('.hero', { y: 26, autoAlpha: 0, duration: 0.6 }, '-=0.3')
      .from('.holo', { scale: 0.7, autoAlpha: 0, duration: 0.7, ease: 'back.out(1.5)' }, '-=0.4')
      .from('.diag-node', { autoAlpha: 0, scale: 0.8, duration: 0.45, stagger: 0.08 }, '-=0.4')
      .from('.col-right > .card', { x: 26, autoAlpha: 0, duration: 0.55, stagger: 0.12 }, '-=0.5')
      .from('.flow-col', { y: 20, autoAlpha: 0, duration: 0.45, stagger: 0.08 }, '-=0.4')

    stats.forEach((s, i) => {
      gsap.to(s, { display: s.value, duration: 1.4, delay: 0.35 + i * 0.12, ease: 'power2.out' })
    })

    gsap.to('.flow-line', { strokeDashoffset: -200, duration: 6, repeat: -1, ease: 'none' })

    if (!reduce) {
      gsap.to(coreRef.value, { y: -10, duration: 3, yoyo: true, repeat: -1, ease: 'sine.inOut' })
    }

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
  <div ref="rootRef" class="admin-dashboard">
    <span class="hero-kicker">管理后台 · ADMIN&nbsp;CONSOLE · 欢迎回来，{{ adminName }}</span>

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

        <!-- 管理控制台 主面板 -->
        <section class="hero">
          <header class="hero-head">
            <h2 class="hero-title"><span class="bar" />管理控制台</h2>
            <span class="hero-tag">ADMIN&nbsp;CONSOLE</span>
          </header>

          <div class="hero-body">
            <!-- 检索表单 -->
            <div class="form">
              <label class="f-label">检索范围</label>
              <div class="f-select">
                <input v-model="target" />
                <el-icon><ArrowRight /></el-icon>
              </div>

              <label class="f-label">关键词</label>
              <div class="f-area">
                <textarea v-model="keyword" maxlength="300" placeholder="输入知识条目、案例或用户关键词..." />
                <span class="f-count">{{ keyword.length }}/300</span>
              </div>

              <label class="f-label">数据来源</label>
              <div class="f-select">
                <input v-model="range" />
                <el-icon><ArrowRight /></el-icon>
              </div>

              <button class="f-go" @click="startSearch">
                <span class="tri">▶</span> 进入知识中心
              </button>
            </div>

            <!-- 3D 全息控制台 + 管理节点 -->
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
                      <linearGradient id="bgAdmin1" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0" style="stop-color: var(--plaza-accent)" />
                        <stop offset="1" style="stop-color: var(--plaza-accent-hover)" />
                      </linearGradient>
                    </defs>
                    <path class="bn-fill" d="M34,104 L80,84 L126,104 L80,124 Z" />
                    <path class="bn-line" d="M34,104 L80,84 L126,104 L80,124 Z" />
                    <path class="bn-line" d="M44,108 L44,134 M116,108 L116,134 M80,124 L80,146" />
                    <path class="bn-line" d="M60,52 L100,52 L104,86 L56,86 Z" />
                    <polyline class="bn-wave" points="62,74 70,68 76,77 84,62 92,72 98,66" />
                    <circle class="bn-line" cx="118" cy="44" r="13" />
                    <circle class="bn-fill2" cx="118" cy="44" r="5" />
                    <path class="bn-line" d="M118,27 L118,33 M118,55 L118,61 M101,44 L107,44 M129,44 L135,44 M106,32 L110,36 M126,52 L130,56 M130,32 L126,36 M110,52 L106,56" />
                    <path class="bn-line" d="M40,40 a10,10 0 1,0 8,16 L62,70 a5,5 0 0,0 7,-7 L55,49 a10,10 0 0,0 -15,-9 Z" />
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

        <!-- 任务流转 -->
        <section class="card flow-card">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />任务流转</h3>
            <router-link to="/admin/tasks" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <div class="flow">
            <div class="flow-track"><span class="flow-fill" /></div>
            <div class="flow-cols">
              <router-link
                v-for="t in taskFlow"
                :key="t.status"
                to="/admin/tasks"
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
          <p class="asst-hello">您好，我是管理助手。可询问数据统计、内容审核建议、知识整理等问题。</p>
          <div class="asst-chips">
            <button v-for="c in assistantChips" :key="c" @click="fillAssistant(c)">{{ c }}</button>
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

        <!-- 最近动态 -->
        <section class="card activity-card">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />最近动态</h3>
            <router-link to="/admin/system?tab=notify" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <div class="activity-list">
            <div v-for="(item, index) in recentActivities" :key="index" class="activity-item">
              <span class="activity-dot" :class="item.status" />
              <div class="activity-info">
                <span class="activity-user">{{ item.user }}</span>
                <span class="activity-action">{{ item.action }}</span>
              </div>
              <span class="activity-time">{{ item.time }}</span>
            </div>
            <p v-if="!recentActivities.length" class="activity-empty">暂无操作动态</p>
          </div>
        </section>

        <!-- 任务状态分布 -->
        <section class="card pie-card">
          <header class="card-head">
            <h3 class="card-title"><span class="bar" />任务状态分布</h3>
            <router-link to="/admin/tasks" class="more">更多 <el-icon><ArrowRight /></el-icon></router-link>
          </header>
          <div class="pie-body">
            <div class="pie-chart">
              <svg viewBox="0 0 220 220" class="pie-svg">
                <path
                  v-for="(slice, i) in piePaths"
                  :key="i"
                  :d="slice.path"
                  :fill="slice.color"
                  class="pie-slice"
                  :class="{ 'pie-slice-hovered': hoveredSlice === i }"
                  @mouseenter="hoveredSlice = i"
                  @mouseleave="hoveredSlice = null"
                />
              </svg>
            </div>
            <div class="pie-legend">
              <div
                v-for="(item, i) in pieData"
                :key="i"
                class="legend-item"
                :class="{ 'legend-hovered': hoveredSlice === i }"
                @mouseenter="hoveredSlice = i"
                @mouseleave="hoveredSlice = null"
              >
                <span class="legend-bar" :style="{ background: item.color }" />
                <span class="legend-name">{{ item.name }}</span>
                <span class="legend-percent">{{ item.percent }}%</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-dashboard { max-width: 1320px; margin: 0 auto; }
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
  align-items: stretch;
}
.col-left, .col-right { display: flex; flex-direction: column; gap: 20px; min-width: 0; }
/* 底部卡片纵向撑满 → 下边框对齐 */
.col-left > :last-child,
.col-right > :last-child { flex: 1 1 auto; }
.flow-card { display: flex; flex-direction: column; }
.flow-card .flow { margin: auto 0; }
.pie-card { display: flex; flex-direction: column; }
.pie-card .pie-body { margin: auto 0; }

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
.stat-ico { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; font-size: 19px; }
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

/* —— 管理控制台 暖咖面板 —— */
.hero {
  position: relative; overflow: hidden;
  margin-top: 14px;
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

/* 全息控制台图区 */
.diagram { position: relative; min-height: 340px; display: flex; align-items: center; justify-content: center; }
.links { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.flow-line { fill: none; stroke: var(--signal); stroke-width: .5; opacity: .5; stroke-dasharray: 3 4; stroke-dashoffset: 0; }

.holo { position: relative; width: 230px; height: 230px; perspective: 950px; display: grid; place-items: center; }
.holo-platform { position: absolute; inset: 0; margin: auto; width: 200px; height: 200px; transform-style: preserve-3d; transform-origin: 50% 50%; }
.ring { position: absolute; inset: 0; margin: auto; border-radius: 50%; }
.ring.r1 { width: 200px; height: 200px; border: 1px dashed var(--signal); }
.ring.r2 { width: 150px; height: 150px; border: 1.5px solid var(--signal); box-shadow: 0 0 18px var(--signal-soft) inset; }
.ring.r3 { width: 96px; height: 96px; border: 2px solid transparent; border-top-color: var(--signal); border-right-color: var(--signal-strong); box-shadow: 0 0 22px var(--signal-line); }
.hub { position: absolute; inset: 0; margin: auto; width: 30px; height: 30px; border-radius: 50%; background: radial-gradient(circle, rgba(246,176,114,.9), var(--signal-line) 70%, transparent); }
.holo-core { position: relative; z-index: 2; width: 150px; height: 150px; display: grid; place-items: center; cursor: grab; touch-action: none; }
.holo-core:active { cursor: grabbing; }
.bench { width: 132px; height: 132px; overflow: visible; }
.bn-line { fill: none; stroke: url(#bgAdmin1); stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.bn-fill { fill: var(--signal-soft); }
.bn-fill2 { fill: var(--signal); }
.bn-wave { fill: none; stroke: #ffd9a8; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.core-glow { position: absolute; width: 150px; height: 150px; border-radius: 50%; z-index: -1; background: radial-gradient(circle, var(--signal-line), transparent 65%); filter: blur(6px); }
.holo-hint { position: absolute; bottom: -2px; left: 50%; transform: translateX(-50%); font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px; color: var(--plaza-text-muted); opacity: .8; }

/* 管理节点 */
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

/* —— 任务流转 —— */
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
.asst-send { width: 36px; height: 36px; flex-shrink: 0; border: none; border-radius: 9px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; color: var(--home-btn-text); background: var(--plaza-accent-grad); font-size: 16px; }
.asst-send:hover { filter: brightness(1.05); }

/* —— 最近动态 —— */
.activity-list { display: flex; flex-direction: column; gap: 14px; }
.activity-item { display: flex; align-items: center; gap: 12px; }
.activity-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.activity-dot.approved { background: var(--plaza-success); }
.activity-dot.pending { background: var(--plaza-accent); }
.activity-info { flex: 1; display: flex; gap: 6px; font-size: 13.5px; min-width: 0; }
.activity-user { color: var(--plaza-text); font-weight: 600; white-space: nowrap; }
.activity-action { color: var(--plaza-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.activity-time { font-size: 12px; color: var(--plaza-text-muted); flex-shrink: 0; }
.activity-empty { padding: 18px 0; text-align: center; font-size: 13px; color: var(--plaza-text-muted); }

/* —— 检修分类点击量 —— */
.pie-body { display: flex; align-items: center; gap: 18px; }
.pie-chart { flex-shrink: 0; }
.pie-svg { width: 168px; height: 168px; }
.pie-slice { stroke: var(--plaza-bg-card); stroke-width: 2; transition: opacity .2s ease, transform .2s ease; transform-origin: center; cursor: pointer; }
.pie-slice:hover, .pie-slice-hovered { opacity: .85; transform: scale(1.03); }
.pie-legend { flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.legend-item { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 8px; border: 1px solid transparent; cursor: pointer; transition: all .2s ease; }
.legend-item:hover, .legend-hovered { background: var(--plaza-accent-soft); border-color: var(--plaza-accent); }
.legend-bar { width: 4px; height: 18px; border-radius: 2px; flex-shrink: 0; }
.legend-name { flex: 1; font-size: 12.5px; color: var(--plaza-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend-percent { font-size: 12px; font-weight: 700; color: var(--plaza-accent); flex-shrink: 0; }

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
