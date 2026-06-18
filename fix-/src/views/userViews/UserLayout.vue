<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import RunningTasksTray from '@/components/RunningTasksTray.vue'
import { notifyStore } from '@/stores/notifyStore'
import { enterShell, enterMain } from '@/utils/motion'
import {
  House,
  Search,
  ChatDotRound,
  Share,
  Tickets,
  User,
  SwitchButton,
  Reading,
  UploadFilled,
} from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()

// 登录后建立 WebSocket 通知连接 + 恢复后台任务对账
onMounted(() => notifyStore.init())

// 外壳 / 内容入场动效（GSAP）
const railRef = ref(null)
const topbarRef = ref(null)
const contentRef = ref(null)
onMounted(() => {
  enterShell(railRef.value)
  enterMain(topbarRef.value, contentRef.value)
})

const isCollapsed = ref(true)
function toggleRail() {
  isCollapsed.value = !isCollapsed.value
}

const clock = ref('')
let clockTimer = null
function tick() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  clock.value = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
onMounted(() => { tick(); clockTimer = setInterval(tick, 1000) })
onUnmounted(() => clockTimer && clearInterval(clockTimer))

const userAvatar = computed(() => {
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || '{}')
    return userInfo.name ? userInfo.name[0] : 'U'
  } catch {
    return 'U'
  }
})

const userName = computed(() => {
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || '{}')
    return userInfo.name || '用户'
  } catch {
    return '用户'
  }
})

const userType = computed(() => {
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || '{}')
    // 后端返回 type: 1 表示管理员, type: 0 表示普通用户
    return userInfo.type == 1 ? '管理员' : '普通用户'
  } catch {
    return '普通用户'
  }
})

const menuItems = [
  { path: '/user/dashboard', name: '首页概览', icon: House },
  { path: '/user/search', name: '智能检索', icon: Search },
  { path: '/user/graph', name: '知识图谱', icon: Share },
  { path: '/user/tasks', name: '检修任务', icon: Tickets },
  { path: '/user/ai-chat', name: 'AI 对话', icon: ChatDotRound },
  { path: '/user/quiz', name: '知识问答', icon: Reading },
  { path: '/user/case-upload', name: '经验上传', icon: UploadFilled },
  { path: '/user/profile', name: '个人中心', icon: User },
]

function isActive(path) {
  return route.path === path || route.path.startsWith(path + '/')
}

const currentSection = computed(
  () => menuItems.find((m) => isActive(m.path))?.name || '用户中心'
)

function goToHome() {
  router.push('/user/dashboard')
}

function goToLogin() {
  notifyStore.stop() // 断开 WS，避免登出后无限重连握手失败
  localStorage.removeItem('userInfo')
  router.push('/login')
}

</script>

<template>
  <div class="shell">
    <!-- ===== Sidebar：工程控制台 ===== -->
    <aside
      ref="railRef"
      class="rail"
      :class="{ collapsed: isCollapsed }"
    >
      <div class="brand" @click="goToHome">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2-2 2.5-2.5z" />
          </svg>
        </div>
        <div class="brand-meta">
          <span class="brand-name">检修系统</span>
          <span class="brand-sub">MAINTENANCE&nbsp;OS</span>
        </div>
      </div>

      <div class="rail-tag">
        <span class="rail-tag-dot" />
        <span class="rail-tag-text">操作台 · WORKER</span>
      </div>

      <nav class="nav">
        <router-link
          v-for="(item, i) in menuItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
        >
          <span class="nav-bar" />
          <span class="nav-idx">{{ String(i + 1).padStart(2, '0') }}</span>
          <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
          <span class="nav-text">{{ item.name }}</span>
        </router-link>
      </nav>

      <div class="rail-foot">
        <div class="foot-user">
          <div class="foot-avatar">{{ userAvatar }}</div>
          <div class="foot-meta">
            <span class="foot-name">{{ userName }}</span>
            <span class="foot-role">{{ userType }}</span>
          </div>
        </div>
        <button class="foot-logout" title="退出登录" @click="goToLogin">
          <el-icon><SwitchButton /></el-icon>
          <span class="foot-logout-text">退出</span>
        </button>
      </div>
    </aside>

    <!-- ===== Main ===== -->
    <div class="main" :class="{ expanded: !isCollapsed }">
      <header ref="topbarRef" class="topbar">
        <div class="loc">
          <button
            class="rail-toggle"
            type="button"
            :aria-label="isCollapsed ? '展开侧边栏' : '收起侧边栏'"
            :title="isCollapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="toggleRail"
          >
            <span :class="{ open: !isCollapsed }"><i /><i /><i /></span>
          </button>
          <span class="loc-root">用户中心</span>
          <span class="loc-sep">/</span>
          <span class="loc-cur">{{ currentSection }}</span>
        </div>
        <div class="top-right">
          <div class="status-pill">
            <span class="status-dot" />
            <span class="status-text">{{ userName }}</span>
            <span class="status-clock">{{ clock }}</span>
          </div>
        </div>
      </header>

      <main ref="contentRef" class="content">
        <!-- 缓存 AI 对话页：切到别的页面时流式输出不中断 -->
        <router-view v-slot="{ Component }">
          <keep-alive include="UserAIChat">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>

      <RunningTasksTray />
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--plaza-bg);
}

/* ===================== Sidebar ===================== */
.rail {
  width: 248px;
  flex-shrink: 0;
  position: fixed;
  top: 16px;
  left: 16px;
  bottom: 16px;
  z-index: 101;
  display: flex;
  flex-direction: column;
  background:
    var(--shell-glow),
    linear-gradient(180deg, var(--shell-ink-soft) 0%, var(--shell-ink) 60%, var(--shell-ink-tail) 100%);
  color: var(--shell-ink-text);
  border: 1px solid var(--shell-ink-line);
  border-radius: 22px;
  box-shadow:
    inset 0 0 0 1px var(--signal-line),
    0 22px 55px -16px rgba(120, 80, 50, 0.30),
    0 8px 22px rgba(120, 80, 50, 0.12);
  transition: width 0.34s cubic-bezier(0.22, 1, 0.36, 1);
  overflow: hidden;
}
.rail::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(var(--shell-ink-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--shell-ink-line) 1px, transparent 1px);
  background-size: 22px 22px;
  -webkit-mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.5), transparent 60%);
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.5), transparent 60%);
  opacity: 0.6;
}
.rail.collapsed { width: 76px; }

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 22px 18px 16px;
  cursor: pointer;
  position: relative;
  z-index: 1;
}
.brand-mark {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 11px;
  display: grid;
  place-items: center;
  color: #1b1205;
  background: linear-gradient(150deg, var(--signal), var(--signal-strong));
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.12) inset, 0 6px 18px rgba(255, 138, 0, 0.32);
}
.brand-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  white-space: nowrap;
  overflow: hidden;
}
.brand-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 19px;
  letter-spacing: 0.2px;
  color: var(--shell-ink-strong);
}
.brand-sub {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 1.5px;
  color: var(--signal);
}

.rail-tag {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 18px 10px;
  padding: 7px 11px;
  border: 1px solid var(--shell-ink-line);
  border-radius: 8px;
  background: var(--shell-hover-bg);
  position: relative;
  z-index: 1;
}
.rail-tag-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--signal);
  box-shadow: 0 0 0 3px var(--signal-soft);
}
.rail-tag-text {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--shell-ink-text-dim);
  white-space: nowrap;
}

.nav {
  flex: 1;
  padding: 8px 14px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  position: relative;
  z-index: 1;
  overflow-y: auto;
}
.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 13px;
  height: 46px;
  padding: 0 12px;
  border-radius: 10px;
  color: var(--shell-ink-text);
  text-decoration: none;
  transition: background 0.18s ease, color 0.18s ease;
  white-space: nowrap;
}
.nav-bar {
  position: absolute;
  left: 0;
  top: 50%;
  height: 0;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--signal);
  transform: translateY(-50%);
  transition: height 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}
.nav-idx {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--shell-ink-text-dim);
  width: 18px;
  flex-shrink: 0;
  transition: color 0.18s ease;
}
.nav-icon {
  font-size: 19px;
  flex-shrink: 0;
  width: 24px;
  display: grid;
  place-items: center;
  transition: color 0.18s ease, transform 0.18s ease;
}
.nav-text {
  font-size: 14.5px;
  font-weight: 500;
  letter-spacing: 0.2px;
}
.nav-item:hover {
  background: var(--shell-hover-bg);
  color: var(--shell-ink-strong);
}
.nav-item:hover .nav-icon { color: var(--signal-strong); }
.nav-item.active {
  background: var(--shell-active-bg);
  color: var(--shell-active-text);
  box-shadow: inset 0 0 0 1px var(--signal-line);
}
.nav-item.active .nav-bar { height: 22px; }
.nav-item.active .nav-idx,
.nav-item.active .nav-icon { color: var(--signal-strong); }

.rail-foot {
  padding: 12px 14px 16px;
  border-top: 1px solid var(--shell-ink-line);
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
  z-index: 1;
}
.foot-user {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}
.foot-avatar {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 14px;
  color: #1b1205;
  background: linear-gradient(150deg, var(--signal), var(--signal-strong));
}
.foot-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  white-space: nowrap;
}
.foot-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--shell-ink-strong);
  overflow: hidden;
  text-overflow: ellipsis;
}
.foot-role {
  font-size: 11px;
  color: var(--shell-ink-text-dim);
}
.foot-logout {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--shell-ink-line);
  border-radius: 8px;
  background: transparent;
  color: var(--shell-ink-text-dim);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.18s ease;
}
.foot-logout:hover {
  color: var(--signal);
  border-color: var(--signal-line);
  background: var(--signal-soft);
}

.rail.collapsed .brand-meta,
.rail.collapsed .rail-tag-text,
.rail.collapsed .nav-idx,
.rail.collapsed .nav-text,
.rail.collapsed .foot-meta,
.rail.collapsed .foot-logout-text {
  opacity: 0;
  width: 0;
  overflow: hidden;
}
.rail.collapsed .brand,
.rail.collapsed .rail-tag,
.rail.collapsed .nav-item {
  justify-content: center;
  gap: 0;
}
.rail.collapsed .rail-foot {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 0 14px;
}
.rail.collapsed .foot-user {
  flex: 0 0 auto;
  justify-content: center;
}
.rail.collapsed .rail-tag { padding: 7px; }
.rail.collapsed .nav-item { padding: 0; }
.rail.collapsed .foot-logout { padding: 0; width: 32px; justify-content: center; }

/* ===================== Main ===================== */
.main {
  flex: 1;
  margin-left: 108px;
  transition: margin-left 0.34s cubic-bezier(0.22, 1, 0.36, 1);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.main.expanded { margin-left: 280px; }

.topbar {
  height: 64px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--plaza-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 26px;
  position: sticky;
  top: 0;
  z-index: 50;
}
.loc { display: flex; align-items: center; gap: 12px; }
.rail-toggle {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border: 1px solid var(--plaza-border);
  border-radius: 9px;
  background: var(--plaza-bg-card);
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease;
}
.rail-toggle:hover { border-color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.rail-toggle > span {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 16px;
}
.rail-toggle i {
  width: 16px;
  height: 2px;
  border-radius: 2px;
  background: var(--plaza-text);
  transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s ease, width 0.2s ease;
}
.rail-toggle:hover i { background: var(--plaza-accent); }
/* 展开态时变成 ✕，提示「再次点击收回」 */
.rail-toggle > span.open i:nth-child(1) { transform: translateY(6px) rotate(45deg); }
.rail-toggle > span.open i:nth-child(2) { opacity: 0; width: 0; }
.rail-toggle > span.open i:nth-child(3) { transform: translateY(-6px) rotate(-45deg); }
.loc-root {
  font-family: var(--font-mono);
  font-size: 19px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--plaza-text-muted);
}
.loc-sep { color: var(--plaza-border-strong); font-size: 14px; }
.loc-cur {
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 700;
  color: var(--plaza-heading);
  letter-spacing: 0.2px;
}
.top-right { display: flex; align-items: center; gap: 16px; }
.status-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 999px;
  background: var(--plaza-bg-card);
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--plaza-success);
  box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.16);
  animation: pulse 2.4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.16); }
  50% { box-shadow: 0 0 0 5px rgba(22, 163, 74, 0.05); }
}
.status-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--plaza-text);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-clock {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--plaza-text-muted);
  padding-left: 8px;
  border-left: 1px solid var(--plaza-border);
}
.content {
  flex: 1;
  padding: 30px 26px;
  overflow-y: auto;
  position: relative;
}

@media (max-width: 900px) {
  .rail { width: 76px; }
  .rail .brand-meta,
  .rail .rail-tag-text,
  .rail .nav-idx,
  .rail .nav-text,
  .rail .foot-meta,
  .rail .foot-logout-text { opacity: 0; width: 0; overflow: hidden; }
  .rail .brand, .rail .rail-tag, .rail .nav-item { justify-content: center; gap: 0; }
  .rail .rail-foot { flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 12px 0 14px; }
  .rail .foot-user { flex: 0 0 auto; justify-content: center; }
  .rail .nav-item { padding: 0; }
  .rail .foot-logout { padding: 0; width: 32px; justify-content: center; }
  .main { margin-left: 108px; }
  .status-pill { display: none; }
}
</style>
