<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { gsap } from 'gsap'
import { User, Lock, ArrowRight, Check } from '@element-plus/icons-vue'
import { login } from '@/api/user'

const router = useRouter()
const route = useRoute()

// 入场动效：左侧品牌信息逐项上浮 + 右侧卡片弹入
onMounted(() => {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  const tl = gsap.timeline({ defaults: { ease: 'power3.out' } })
  tl.from('.brand-logo', { y: -16, opacity: 0, duration: 0.5 })
    .from('.brand-title', { y: 24, opacity: 0, duration: 0.6 }, '-=0.25')
    .from('.brand-desc', { y: 18, opacity: 0, duration: 0.5 }, '-=0.35')
    .from('.feature-item', { x: -18, opacity: 0, duration: 0.45, stagger: 0.1 }, '-=0.3')
    .from('.login-card', { y: 30, opacity: 0, scale: 0.97, duration: 0.6 }, '-=0.7')
  gsap.to('.circle-1', { x: 30, y: 20, duration: 9, yoyo: true, repeat: -1, ease: 'sine.inOut' })
  gsap.to('.circle-2', { x: -24, y: -18, duration: 11, yoyo: true, repeat: -1, ease: 'sine.inOut' })
})

const formData = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const formRef = ref(null)
const loading = ref(false)
const loginSuccess = ref(false)
const errorMessage = ref('')

const handleSubmit = async () => {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    errorMessage.value = ''

    try {
      const res = await login(formData.username, formData.password)
      console.log('登录响应:', res)
      if (res.code === '200') {
        loginSuccess.value = true
        // 保存用户信息（后端直接返回UserVO，没有嵌套user对象和token）
        const userData = res.data
        localStorage.setItem('userInfo', JSON.stringify(userData))
        loading.value = false
        setTimeout(() => {
          // 优先跳回会话失效前的来源页（仅允许站内路径，避免开放重定向）；
          // 若来源页与当前角色权限不符，路由守卫会再兜底纠正。
          const redirect = route.query.redirect
          if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
            router.replace(redirect)
          } else if (Number(userData.type) === 1) {
            router.push('/admin')
          } else {
            router.push('/user')
          }
        }, 800)
      } else {
        throw new Error(res.message || res.msg || '登录失败')
      }
    } catch (error) {
      console.error('登录失败:', error)
      loading.value = false
      errorMessage.value = error.message || '用户名或密码错误'
    }
  })
}

const goToLanding = () => {
  router.push('/')
}

const goToForgotPassword = () => {
  router.push('/forgot-password')
}
</script>

<template>
  <div class="login-page">
    <!-- Background decoration -->
    <div class="bg-decoration">
      <div class="bg-grid"></div>
      <div class="bg-circle circle-1"></div>
      <div class="bg-circle circle-2"></div>
    </div>

    <div class="login-container">
      <!-- Left side - branding -->
      <div class="login-branding">
        <div class="brand-content">
          <div class="brand-logo">
            <div class="brand-logo-icon">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/>
              </svg>
            </div>
            <span class="logo-text">智维</span>
          </div>
          <h1 class="brand-title">让智能检修<br />成为你的伙伴</h1>
          <p class="brand-desc">设备检修知识检索系统，基于多模态大模型技术，实现智能化设备检修知识检索、标准化作业指引与知识沉淀。</p>
          <div class="brand-features">
            <div class="feature-item">
              <span class="feature-dot"></span>
              <span>多模态智能检索</span>
            </div>
            <div class="feature-item">
              <span class="feature-dot"></span>
              <span>标准化作业指引</span>
            </div>
            <div class="feature-item">
              <span class="feature-dot"></span>
              <span>知识实时沉淀</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Right side - login form -->
      <div class="login-form-wrapper">
        <div class="login-card">
          <div class="card-header">
            <h2 class="card-title">欢迎回来</h2>
            <p class="card-subtitle">登录到设备检修知识检索系统</p>
          </div>

          <el-form
            ref="formRef"
            :model="formData"
            :rules="rules"
            class="login-form"
          >
            <el-form-item prop="username">
              <label class="input-label">用户名</label>
              <el-input
                v-model="formData.username"
                placeholder="请输入用户名"
                size="large"
                clearable
                class="login-input"
              >
                <template #prefix>
                  <el-icon class="input-icon"><User /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <el-form-item prop="password">
              <label class="input-label">密码</label>
              <el-input
                v-model="formData.password"
                type="password"
                placeholder="请输入密码"
                size="large"
                show-password
                clearable
                class="login-input"
                @keyup.enter="handleSubmit"
              >
                <template #prefix>
                  <el-icon class="input-icon"><Lock /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <div class="forgot-row">
              <span class="forgot-link" @click="goToForgotPassword">忘记密码？</span>
            </div>

            <div v-if="errorMessage" class="error-message">
              <span>{{ errorMessage }}</span>
            </div>

            <div class="form-actions">
              <el-button
                size="large"
                type="primary"
                :loading="loading && !loginSuccess"
                :class="{ 'is-success': loginSuccess }"
                class="btn-login"
                @click="handleSubmit"
              >
                <template v-if="loginSuccess">
                  <el-icon class="btn-success-icon"><Check /></el-icon>
                  <span>登录成功</span>
                </template>
                <template v-else>
                  登录
                  <el-icon class="btn-arrow"><ArrowRight /></el-icon>
                </template>
              </el-button>
            </div>
          </el-form>

          <div class="card-footer">
            <span class="back-home" @click="goToLanding">
              <el-icon class="back-icon"><ArrowRight /></el-icon>
              返回首页
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--plaza-bg);
  position: relative;
  overflow: hidden;
}

/* Background Decoration */
.bg-decoration {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--plaza-accent-soft) 1px, transparent 1px),
    linear-gradient(90deg, var(--plaza-accent-soft) 1px, transparent 1px);
  background-size: 48px 48px;
  -webkit-mask-image: radial-gradient(circle at 50% 40%, #000 30%, transparent 78%);
  mask-image: radial-gradient(circle at 50% 40%, #000 30%, transparent 78%);
}

.bg-circle {
  position: absolute;
  border-radius: 50%;
  filter: blur(8px);
  background: radial-gradient(circle at 30% 30%, var(--plaza-accent-soft-strong) 0%, var(--plaza-atmosphere-b) 45%, transparent 70%);
}

.circle-1 {
  width: clamp(250px, 50vw, 500px);
  height: clamp(250px, 50vw, 500px);
  top: -15vw;
  right: -10vw;
}

.circle-2 {
  width: clamp(200px, 40vw, 400px);
  height: clamp(200px, 40vw, 400px);
  bottom: -10vw;
  left: -10vw;
}

/* Main Container */
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: clamp(30px, 8vw, 80px);
  max-width: 1100px;
  width: 100%;
  padding: 5vw 4vw;
  position: relative;
  z-index: 1;
}

/* Branding Side */
.login-branding {
  flex: 1;
  max-width: 440px;
}

.brand-content {
  padding: 2vw 0;
}

.brand-logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 700;
  font-size: clamp(14px, 2vw, 20px);
  letter-spacing: 0.02em;
  margin-bottom: clamp(20px, 4vw, 32px);
  cursor: pointer;
  transition: opacity 0.2s ease;
}

.brand-logo:hover {
  opacity: 0.8;
}

.brand-logo-icon {
  position: relative;
  width: clamp(28px, 5vw, 36px);
  height: clamp(28px, 5vw, 36px);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--plaza-accent);
  border-radius: 50%;
  flex-shrink: 0;
}

.brand-logo-icon::before {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  border: 3px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  animation: pulse-ring 2s ease-in-out infinite;
}

.brand-logo-icon svg {
  color: #fff;
  width: 40%;
  height: 40%;
  filter: drop-shadow(0 0 4px var(--plaza-accent-soft-strong));
}

@keyframes pulse-ring {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.3);
    opacity: 0;
  }
}

.logo-icon {
  background: var(--plaza-accent);
  color: #fff;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 16px;
}

.logo-text {
  color: var(--plaza-text);
}

.brand-title {
  font-size: clamp(1.5rem, 4vw, 2.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  line-height: 1.3;
  margin: 0 0 clamp(12px, 2vw, 20px);
  letter-spacing: -0.02em;
}

.brand-desc {
  font-size: clamp(0.85rem, 1.8vw, 1rem);
  color: var(--plaza-text-muted);
  line-height: 1.7;
  margin: 0 0 clamp(20px, 3vw, 32px);
}

.brand-features {
  display: flex;
  flex-direction: column;
  gap: clamp(8px, 1.5vw, 12px);
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text);
}

.feature-dot {
  width: 8px;
  height: 8px;
  background: var(--plaza-accent);
  border-radius: 50%;
  flex-shrink: 0;
}

/* Form Side */
.login-form-wrapper {
  flex: 1;
  max-width: 420px;
}

.login-card {
  background: var(--plaza-bg-card);
  border-radius: clamp(12px, 2vw, 20px);
  border: 1px solid var(--plaza-border);
  box-shadow: 0 4px 24px rgba(51, 65, 85, 0.04);
  padding: clamp(24px, 4vw, 40px);
}

.card-header {
  text-align: center;
  margin-bottom: clamp(20px, 3vw, 32px);
}

.card-title {
  font-size: clamp(1.2rem, 2.5vw, 1.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  margin: 0 0 0.5rem;
}

.card-subtitle {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  margin: 0;
}

.login-form {
  margin: 0;
}

.login-form :deep(.el-form-item) {
  margin-bottom: clamp(14px, 2vw, 20px);
}

.input-label {
  display: block;
  font-size: clamp(12px, 1.5vw, 14px);
  font-weight: 500;
  color: var(--plaza-text);
  margin-bottom: 0.5rem;
}

.forgot-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: clamp(16px, 2vw, 24px);
}

.forgot-link {
  font-size: clamp(11px, 1.4vw, 13px);
  color: var(--plaza-accent);
  cursor: pointer;
  transition: color 0.2s ease;
}

.forgot-link:hover {
  color: var(--plaza-accent-hover);
}

/* Input styling */
.login-input :deep(.el-input__wrapper) {
  border-radius: 10px;
  padding-left: 44px;
  background: var(--plaza-bg-input) !important;
  border: 1px solid var(--plaza-border);
  box-shadow: none !important;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.login-input :deep(.el-input__wrapper:hover) {
  border-color: var(--plaza-accent);
}

.login-input :deep(.el-input.is-focus .el-input__wrapper) {
  border-color: var(--plaza-accent) !important;
  box-shadow: 0 0 0 3px var(--plaza-accent-soft) !important;
}

.login-input :deep(.el-input__inner) {
  color: var(--plaza-text) !important;
  font-size: clamp(13px, 1.5vw, 15px);
}

.login-input :deep(.el-input__inner::placeholder) {
  color: var(--plaza-text-muted) !important;
}

.input-icon {
  color: var(--plaza-text-muted);
  font-size: clamp(14px, 1.8vw, 18px);
}

.error-message {
  color: var(--el-color-danger);
  font-size: clamp(11px, 1.4vw, 13px);
  text-align: center;
  margin-bottom: clamp(12px, 2vw, 16px);
}

/* Form Actions */
.form-actions {
  margin-top: 0.5rem;
}

.btn-login {
  width: 100%;
  height: clamp(40px, 6vw, 50px);
  border-radius: 10px;
  font-weight: 600;
  font-size: clamp(13px, 1.5vw, 15px);
  background: var(--plaza-accent-grad) !important;
  border: none !important;
  color: #fff !important;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  box-shadow: 0 8px 22px var(--plaza-accent-soft-strong);
  transition: box-shadow 0.2s ease, transform 0.12s ease, filter 0.2s ease;
}

.btn-login:hover:not(.is-success) {
  filter: brightness(1.05);
  transform: translateY(-2px);
  box-shadow: 0 12px 28px var(--plaza-accent);
}

.btn-arrow {
  font-size: clamp(14px, 1.8vw, 16px);
  transition: transform 0.2s ease;
}

.btn-login:hover .btn-arrow {
  transform: translateX(3px);
}

.btn-login.is-success {
  background: var(--app-success) !important;
  pointer-events: none;
}

.btn-success-icon {
  font-size: clamp(14px, 1.8vw, 18px);
}

.btn-login :deep(.el-icon.is-loading) {
  font-size: clamp(14px, 1.8vw, 18px);
}

/* Card Footer */
.card-footer {
  margin-top: clamp(16px, 2vw, 24px);
  padding-top: clamp(14px, 2vw, 20px);
  border-top: 1px solid var(--plaza-border);
  text-align: center;
}

.back-home {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  cursor: pointer;
  transition: color 0.2s ease;
}

.back-home:hover {
  color: var(--plaza-accent);
}

.back-icon {
  transform: rotate(180deg);
  font-size: clamp(12px, 1.4vw, 14px);
}

/* Responsive */
@media (max-width: 900px) {
  .login-container {
    gap: 40px;
  }

  .login-branding {
    display: none;
  }

  .login-form-wrapper {
    max-width: 420px;
    width: 100%;
  }
}

@media (max-width: 480px) {
  .login-card {
    padding: 24px 16px;
  }

  .card-title {
    font-size: 1.3rem;
  }

  .btn-login {
    width: 100%;
  }
}
</style>
