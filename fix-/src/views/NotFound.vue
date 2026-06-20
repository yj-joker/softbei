<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

// 已登录用户引导回工作台，未登录回首页（角色从 localStorage 取，与路由守卫口径一致）
let authed = false
let userType = null
try {
  const raw = localStorage.getItem('userInfo')
  if (raw) {
    authed = true
    userType = Number(JSON.parse(raw).type)
  }
} catch (e) { /* localStorage 不可用时按未登录处理 */ }

const homeLabel = computed(() => (authed ? '回到工作台' : '回到首页'))

function goHome() {
  if (authed) router.push(userType === 1 ? '/admin' : '/user')
  else router.push('/')
}

function goBack() {
  // 有历史则返回，否则回首页，避免退回到空白
  if (window.history.length > 1) router.back()
  else goHome()
}
</script>

<template>
  <div class="nf-page">
    <div class="nf-card">
      <div class="nf-code">404</div>
      <h1 class="nf-title">页面走丢了</h1>
      <p class="nf-desc">你访问的页面不存在，或地址已变更。</p>
      <div class="nf-actions">
        <button class="nf-btn nf-btn-primary" @click="goHome">{{ homeLabel }}</button>
        <button class="nf-btn nf-btn-ghost" @click="goBack">返回上一页</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.nf-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.nf-card {
  text-align: center;
  max-width: 460px;
  padding: 48px 40px;
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  box-shadow: var(--plaza-shadow-organic);
}
.nf-code {
  font-family: var(--font-display);
  font-size: 92px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: 2px;
  background: var(--plaza-accent-grad);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.nf-title {
  margin-top: 18px;
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--plaza-heading);
}
.nf-desc {
  margin-top: 10px;
  font-size: 14px;
  color: var(--plaza-text-muted);
}
.nf-actions {
  margin-top: 28px;
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}
.nf-btn {
  padding: 10px 22px;
  border-radius: var(--plaza-radius);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease, color 0.15s ease;
}
.nf-btn-primary {
  background: var(--plaza-accent);
  color: var(--home-btn-text);
}
.nf-btn-primary:hover {
  background: var(--plaza-accent-hover);
  transform: translateY(-1px);
  box-shadow: var(--plaza-shadow-organic-hover);
}
.nf-btn-ghost {
  background: transparent;
  border-color: var(--plaza-border-strong);
  color: var(--plaza-text);
}
.nf-btn-ghost:hover {
  background: var(--plaza-accent-soft);
  border-color: var(--plaza-accent);
  color: var(--plaza-accent);
}
</style>
