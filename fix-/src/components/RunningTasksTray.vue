<script setup>
import { computed, reactive, ref, onBeforeUnmount } from 'vue'
import { gsap } from 'gsap'
import { notifyStore } from '@/stores/notifyStore'

// 进行中 + 刚失败（失败项标红暂留 6 秒后自动移除）
const jobs = computed(() => Object.values(notifyStore.state.jobs).filter(j => j.status === 'running' || j.status === 'failed' || j.status === 'done'))
const runningCount = computed(() => jobs.value.filter(j => j.status === 'running').length)

// 每秒自增的响应式时钟，驱动「已运行 X 秒」重新计算
const now = ref(Date.now())
const ticker = setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(ticker))

const reduce = typeof window !== 'undefined'
  && window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

function elapsed(ts) {
  const s = Math.max(0, Math.floor((now.value - ts) / 1000))
  if (s < 60) return s + ' 秒'
  return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒'
}

// 后端进度是离散档位（解析20/构建35/向量化60…），直接显示会「停在固定数字上跳变」。
// 这里对知识导入任务做涓流动画：档位之间持续小步前进，真实档位到达时对齐（只增不减），
// 让进度条始终在动、不卡死。本质是展示层的时间估算，关键节点仍由后端真实档位校正。
const displayMap = reactive({})

function displayPercent(job) {
  const v = displayMap[job.key]
  return Math.round(v == null ? (job.percent || 0) : v)
}

function tickProgress() {
  const list = jobs.value
  for (const job of list) {
    if (job.kind !== 'knowledge') continue
    let cur = displayMap[job.key]
    if (job.status === 'done') {
      // 完成：从当前值快速冲到 100%
      if (cur == null) cur = 92
      displayMap[job.key] = Math.min(100, cur + Math.max(1.5, (100 - cur) * 0.25))
      continue
    }
    if (job.status !== 'running') continue
    const target = typeof job.percent === 'number' ? job.percent : 0
    if (cur == null) cur = Math.max(target, 3)
    if (cur < target) {
      cur = target                                   // 真实档位到达：直接对齐，不倒退
    } else {
      const ceiling = Math.min(target + 14, 97)      // 档位间隙：朝下一档涓流，封顶留给真实档位
      if (cur < ceiling) cur = Math.min(ceiling, cur + Math.max(0.15, (ceiling - cur) * 0.04))
    }
    displayMap[job.key] = cur
  }
  for (const key of Object.keys(displayMap)) {
    if (!list.some(j => j.key === key)) delete displayMap[key]   // 任务结束后清理，防泄漏
  }
}

const progressTicker = setInterval(tickProgress, 220)
onBeforeUnmount(() => clearInterval(progressTicker))

/* ---------- GSAP：仅用于入场/离场这类「高光时刻」，连续动效交给 CSS ---------- */
function trayEnter(el, done) {
  if (reduce) return done()
  gsap.fromTo(el,
    { autoAlpha: 0, y: 26, scale: 0.95, transformOrigin: 'right bottom' },
    { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.6)', onComplete: done })
}
function trayLeave(el, done) {
  if (reduce) return done()
  gsap.to(el, { autoAlpha: 0, y: 18, scale: 0.96, duration: 0.3, ease: 'power2.in', onComplete: done })
}
function itemEnter(el, done) {
  if (reduce) return done()
  gsap.fromTo(el,
    { autoAlpha: 0, x: 22, height: 0, marginTop: 0 },
    { autoAlpha: 1, x: 0, height: 'auto', marginTop: 0, duration: 0.42, ease: 'power3.out', onComplete: done })
}
function itemLeave(el, done) {
  if (reduce) return done()
  gsap.to(el, { autoAlpha: 0, x: 26, height: 0, marginTop: 0, paddingTop: 0, paddingBottom: 0, duration: 0.3, ease: 'power2.in', onComplete: done })
}
</script>

<template>
  <transition :css="false" @enter="trayEnter" @leave="trayLeave">
    <section v-if="jobs.length" class="tasks-tray" role="status" aria-live="polite">
      <span class="tray-accent" />

      <header class="tray-head">
        <span class="tray-pulse" :class="{ 'is-idle': !runningCount }"><i /></span>
        <div class="tray-headtext">
          <span class="tray-title">{{ runningCount ? '后台任务进行中' : '后台任务' }}</span>
          <span class="tray-sub">完成后将自动提示，可安心切换页面</span>
        </div>
        <span v-if="runningCount" class="tray-count">{{ runningCount }}</span>
      </header>

      <transition-group tag="ul" class="tray-list" :css="false" @enter="itemEnter" @leave="itemLeave">
        <li v-for="job in jobs" :key="job.key" class="tray-item" :class="{ 'is-failed': job.status === 'failed', 'is-done': job.status === 'done' }">
          <span v-if="job.status === 'failed'" class="ti-fail" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </span>
          <span v-else-if="job.status === 'done'" class="ti-done" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 13l4 4L19 7" />
            </svg>
          </span>
          <span v-else class="ti-ring" aria-hidden="true" />
          <div class="ti-main">
            <div class="ti-title">{{ job.title }}</div>
            <div class="ti-meta">
              <template v-if="job.status === 'failed'">
                <span class="ti-failtext">生成失败，请稍后重试</span>
              </template>
              <template v-else-if="job.kind === 'knowledge'">
                <span class="ti-stage">{{ job.stage || '处理中' }}</span>
                <span class="ti-progress"><i :style="{ width: displayPercent(job) + '%' }" /></span>
                <span class="ti-pct">{{ displayPercent(job) }}%</span>
              </template>
              <template v-else>
                <span class="ti-time">已运行 {{ elapsed(job.startedAt) }}</span>
                <span class="ti-bar"><i /></span>
              </template>
            </div>
          </div>
          <button
            class="ti-x"
            title="从列表移除（不影响后台执行）"
            aria-label="移除"
            @click="notifyStore.dismiss(job.key)"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </li>
      </transition-group>
    </section>
  </transition>
</template>

<style scoped>
.tasks-tray {
  position: fixed;
  right: 20px;
  bottom: 20px;
  width: 320px;
  background:
    radial-gradient(120% 80% at 100% 0%, rgba(255, 166, 43, 0.08), transparent 55%),
    linear-gradient(180deg, var(--plaza-bg-card), var(--plaza-bg-card));
  border: 1px solid var(--plaza-border);
  border-radius: 16px;
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.7) inset,
    0 18px 44px -12px rgba(150, 90, 50, 0.32),
    0 4px 12px -6px rgba(120, 80, 50, 0.18);
  z-index: 3000;
  overflow: hidden;
  font-family: 'Public Sans', 'Inter', -apple-system, 'Microsoft YaHei', sans-serif;
}

/* 顶部流动强调条 */
.tray-accent {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--plaza-accent), #ffa62b, var(--plaza-accent), transparent);
  background-size: 220% 100%;
  animation: accent-flow 2.4s linear infinite;
}
@keyframes accent-flow { to { background-position: -220% 0; } }

/* ── 头部 ── */
.tray-head {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 13px 15px 12px;
  border-bottom: 1px solid var(--plaza-border);
}
.tray-pulse {
  position: relative;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
.tray-pulse i {
  position: absolute;
  inset: 4px;
  border-radius: 50%;
  background: var(--plaza-accent);
}
.tray-pulse::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--plaza-accent);
  animation: pulse-ring 1.8s ease-out infinite;
}
@keyframes pulse-ring {
  0% { transform: scale(0.6); opacity: 0.8; }
  100% { transform: scale(1.8); opacity: 0; }
}
.tray-headtext { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tray-title {
  font-family: var(--font-display, 'Public Sans');
  font-size: 13.5px;
  font-weight: 800;
  letter-spacing: 0.2px;
  color: var(--plaza-heading);
}
.tray-sub { font-size: 10.5px; color: var(--plaza-text-muted); }
.tray-count {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  border: 1px solid var(--plaza-accent-soft-strong);
  border-radius: 20px;
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 列表 ── */
.tray-list { list-style: none; margin: 0; padding: 6px; max-height: 300px; overflow-y: auto; }
.tray-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 9px;
  border-radius: 10px;
  transition: background 0.16s ease;
}
.tray-item:hover { background: var(--plaza-bg-card); }
.tray-item.is-failed { background: rgba(197, 64, 44, 0.06); }
.tray-item.is-failed:hover { background: rgba(197, 64, 44, 0.1); }

/* 失败标记 */
.ti-fail {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #c5402c;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.ti-failtext {
  font-size: 11.5px;
  font-weight: 600;
  color: #c5402c;
}

/* 完成态：暖橄榄绿 + back-out 弹性对勾，呼应托盘入场动效 */
.ti-done {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--plaza-success);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: done-pop 0.42s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}
@keyframes done-pop {
  0% { transform: scale(0.2); opacity: 0; }
  100% { transform: scale(1); opacity: 1; }
}
.tray-item.is-done { background: rgba(94, 140, 62, 0.07); }
.is-done .ti-progress i { background: var(--plaza-success); }
.is-done .ti-pct { color: var(--plaza-success); }
.is-done .ti-stage { color: var(--plaza-success); }

/* 头部脉冲在无进行中任务（仅剩失败项）时转为静止灰点 */
.tray-pulse.is-idle::before { animation: none; opacity: 0; }
.tray-pulse.is-idle i { background: var(--plaza-border-strong); }

/* 环形 indeterminate（conic 渐变 + 旋转，比 border spinner 更精致） */
.ti-ring {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border-radius: 50%;
  background: conic-gradient(from 0deg, var(--plaza-accent), #ffa62b 120deg, transparent 300deg);
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
          mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px));
  animation: spin 0.85s linear infinite;
}

.ti-main { flex: 1; min-width: 0; }
.ti-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--plaza-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ti-meta { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.ti-time {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--plaza-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}
/* 微光进度条：表达「在跑」 */
.ti-bar {
  position: relative;
  flex: 1;
  height: 3px;
  border-radius: 3px;
  background: var(--plaza-panel-bg);
  overflow: hidden;
}
.ti-bar i {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  width: 40%;
  border-radius: 3px;
  background: linear-gradient(90deg, transparent, var(--plaza-accent), transparent);
  animation: bar-slide 1.5s ease-in-out infinite;
}
@keyframes bar-slide {
  0% { transform: translateX(-110%); }
  100% { transform: translateX(280%); }
}

/* 确定进度（知识导入阶段百分比） */
.ti-stage {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--plaza-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}
.ti-progress {
  position: relative;
  flex: 1;
  height: 3px;
  border-radius: 3px;
  background: var(--plaza-panel-bg);
  overflow: hidden;
}
.ti-progress i {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: 3px;
  background: var(--plaza-accent);
  transition: width 0.4s ease;
}
.ti-pct {
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 700;
  color: var(--plaza-accent);
  flex-shrink: 0;
}

.ti-x {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: var(--plaza-border-strong);
  border-radius: 7px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s ease, color 0.15s ease, transform 0.18s ease;
}
.ti-x:hover {
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
  transform: rotate(90deg);
}

@keyframes spin { to { transform: rotate(360deg); } }

/* 尊重「减少动态效果」偏好 */
@media (prefers-reduced-motion: reduce) {
  .tray-accent, .tray-pulse::before, .ti-ring, .ti-bar i { animation: none; }
  .ti-x:hover { transform: none; }
}
</style>
