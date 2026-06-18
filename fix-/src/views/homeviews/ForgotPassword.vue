<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, Check, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { sendEmail, verifyEmail } from '@/api/user'
import { ElMessage } from 'element-plus'

const router = useRouter()

const step = ref(1) // 1: 用户名+邮箱, 2: 验证码+新密码, 3: 完成
const loading = ref(false)

const formData = reactive({
  username: '',
  email: '',
  code: '',
  password: '',
  confirmPassword: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码长度至少6位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    { validator: (rule, value, callback) => {
      if (value !== formData.password) {
        callback(new Error('两次输入的密码不一致'))
      } else {
        callback()
      }
    }, trigger: 'blur' },
  ],
}

const formRef = ref(null)
const countdown = ref(0)
let countdownTimer = null

const sendCode = async () => {
  if (!formData.email.trim()) return
  if (countdown.value > 0) return
  try {
    await sendEmail(formData.email, 2) // mode=2 = 找回密码
    ElMessage.success('验证码已发送至您的邮箱')
  } catch (e) {
    ElMessage.error(e.message || '发送失败，请稍后重试')
    return
  }
  countdown.value = 60
  countdownTimer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

const handleNext = async () => {
  // 步骤1只验证用户名和邮箱
  const usernameValid = formData.username.trim().length > 0
  const emailValid = formData.email.trim().length > 0 && formData.email.includes('@')
  if (!usernameValid || !emailValid) {
    ElMessage.warning('请填写用户名和邮箱')
    return
  }
  loading.value = true
  try {
    await sendEmail(formData.email, 0) // mode=0 = 重置密码
    ElMessage.success('验证码已发送至您的邮箱')
  } catch (e) {
    ElMessage.error(e.message || '发送失败，请稍后重试')
    loading.value = false
    return
  }
  loading.value = false
  step.value = 2
  if (countdownTimer) clearInterval(countdownTimer)
  countdown.value = 60
  countdownTimer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

const handleSubmit = async () => {
  if (!formRef.value) return
  // 只验证步骤2相关字段
  await formRef.value.validateField(['code', 'password', 'confirmPassword'], async (isValid) => {
    if (!isValid) return
    loading.value = true
    try {
      await verifyEmail(formData.code, 2, formData.password)
      step.value = 3
    } catch (e) {
      ElMessage.error(e.message || '验证失败，验证码错误或已过期')
      loading.value = false
    }
  })
}

const goToLogin = () => {
  router.push('/login')
}

const goBack = () => {
  if (step.value > 1) {
    step.value--
  } else {
    goToLogin()
  }
}
</script>

<template>
  <div class="forgot-page">
    <!-- Background decoration -->
    <div class="bg-decoration">
      <div class="bg-grid"></div>
      <div class="bg-circle circle-1"></div>
      <div class="bg-circle circle-2"></div>
    </div>

    <div class="forgot-container">
      <div class="forgot-card">
        <!-- Back button -->
        <div class="back-btn" @click="goBack">
          <el-icon class="back-icon"><ArrowLeft /></el-icon>
          <span>返回</span>
        </div>

        <!-- Step indicator -->
        <div class="step-indicator">
          <div class="step-dot" :class="{ active: step >= 1, completed: step > 1 }">
            <el-icon v-if="step > 1"><Check /></el-icon>
            <span v-else>1</span>
          </div>
          <div class="step-line" :class="{ active: step > 1 }"></div>
          <div class="step-dot" :class="{ active: step >= 2, completed: step > 2 }">
            <el-icon v-if="step > 2"><Check /></el-icon>
            <span v-else>2</span>
          </div>
          <div class="step-line" :class="{ active: step > 2 }"></div>
          <div class="step-dot" :class="{ active: step >= 3 }">
            <span>3</span>
          </div>
        </div>

        <!-- Step 1: 输入用户名+邮箱 -->
        <div v-if="step === 1" class="step-content">
          <div class="step-header">
            <h2 class="step-title">找回密码</h2>
            <p class="step-desc">请输入您的用户名和绑定邮箱</p>
          </div>

          <el-form
            ref="formRef"
            :model="formData"
            :rules="rules"
            class="forgot-form"
          >
            <el-form-item prop="username">
              <label class="input-label">用户名</label>
              <el-input
                v-model="formData.username"
                placeholder="请输入用户名"
                size="large"
                clearable
                class="forgot-input"
              >
              </el-input>
            </el-form-item>

            <el-form-item prop="email">
              <label class="input-label">邮箱</label>
              <el-input
                v-model="formData.email"
                placeholder="请输入绑定邮箱"
                size="large"
                clearable
                class="forgot-input"
              >
              </el-input>
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              :loading="loading"
              class="btn-next"
              @click="handleNext"
            >
              下一步
              <el-icon class="btn-icon"><ArrowRight /></el-icon>
            </el-button>
          </el-form>
        </div>

        <!-- Step 2: 验证码+新密码 -->
        <div v-if="step === 2" class="step-content">
          <div class="step-header">
            <h2 class="step-title">验证并设置新密码</h2>
            <p class="step-desc">验证码已发送至 {{ formData.email }}</p>
          </div>

          <el-form
            ref="formRef"
            :model="formData"
            :rules="rules"
            class="forgot-form"
            @submit.prevent="handleSubmit"
          >
            <el-form-item prop="code">
              <label class="input-label">验证码</label>
              <div class="code-input-wrapper">
                <el-input
                  v-model="formData.code"
                  placeholder="请输入验证码"
                  size="large"
                  maxlength="6"
                  class="code-input"
                >
                </el-input>
                <div
                  class="code-countdown"
                  :class="{ disabled: countdown > 0 }"
                  @click="countdown > 0 ? null : sendCode()"
                >
                  <span v-if="countdown > 0">{{ countdown }}s</span>
                  <span v-else>重新发送</span>
                </div>
              </div>
            </el-form-item>

            <el-form-item prop="password">
              <label class="input-label">新密码</label>
              <el-input
                v-model="formData.password"
                type="password"
                placeholder="请输入新密码（至少6位）"
                size="large"
                show-password
                class="forgot-input"
              >
              </el-input>
            </el-form-item>

            <el-form-item prop="confirmPassword">
              <label class="input-label">确认新密码</label>
              <el-input
                v-model="formData.confirmPassword"
                type="password"
                placeholder="请再次输入新密码"
                size="large"
                show-password
                class="forgot-input"
              >
              </el-input>
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              :loading="loading"
              class="btn-next"
              @click="handleSubmit"
            >
              确认重置
            </el-button>
          </el-form>
        </div>

        <!-- Step 3: 完成 -->
        <div v-if="step === 3" class="step-content">
          <div class="success-container">
            <div class="success-icon">
              <el-icon><Check /></el-icon>
            </div>
            <h2 class="success-title">密码重置成功</h2>
            <p class="success-desc">您的密码已成功修改，请使用新密码登录</p>
            <el-button
              type="primary"
              size="large"
              class="btn-login"
              @click="goToLogin"
            >
              返回登录
              <el-icon class="btn-icon"><ArrowRight /></el-icon>
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.forgot-page {
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
    linear-gradient(rgba(249, 115, 22, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(249, 115, 22, 0.03) 1px, transparent 1px);
  background-size: 48px 48px;
}

.bg-circle {
  position: absolute;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(249, 115, 22, 0.06) 0%, transparent 60%);
}

.circle-1 {
  width: clamp(200px, 40vw, 400px);
  height: clamp(200px, 40vw, 400px);
  top: -10vw;
  right: -8vw;
}

.circle-2 {
  width: clamp(150px, 30vw, 300px);
  height: clamp(150px, 30vw, 300px);
  bottom: -5vw;
  left: -5vw;
}

/* Main Container */
.forgot-container {
  display: flex;
  align-items: center;
  justify-content: center;
  max-width: 440px;
  width: 100%;
  padding: 5vw 4vw;
  position: relative;
  z-index: 1;
}

.forgot-card {
  background: var(--plaza-bg-card);
  border-radius: clamp(12px, 2vw, 20px);
  border: 1px solid var(--plaza-border);
  box-shadow: 0 4px 24px rgba(51, 65, 85, 0.04);
  padding: clamp(24px, 4vw, 40px);
  width: 100%;
}

/* Back Button */
.back-btn {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  cursor: pointer;
  margin-bottom: clamp(16px, 3vw, 24px);
  transition: color 0.2s ease;
}

.back-btn:hover {
  color: var(--plaza-accent);
}

.back-icon {
  font-size: clamp(14px, 1.8vw, 16px);
}

/* Step Indicator */
.step-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  margin-bottom: clamp(24px, 4vw, 40px);
}

.step-dot {
  width: clamp(28px, 4vw, 32px);
  height: clamp(28px, 4vw, 32px);
  border-radius: 50%;
  background: var(--el-fill-color-light);
  border: 2px solid var(--plaza-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: clamp(12px, 1.5vw, 14px);
  font-weight: 600;
  color: var(--plaza-text-muted);
  transition: all 0.3s ease;
}

.step-dot.active {
  background: var(--plaza-accent);
  border-color: var(--plaza-accent);
  color: #fff;
}

.step-dot.completed {
  background: var(--app-success);
  border-color: var(--app-success);
  color: #fff;
}

.step-line {
  width: clamp(40px, 8vw, 60px);
  height: 2px;
  background: var(--plaza-border);
  transition: background 0.3s ease;
}

.step-line.active {
  background: var(--plaza-accent);
}

/* Step Content */
.step-content {
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.step-header {
  text-align: center;
  margin-bottom: clamp(20px, 3vw, 32px);
}

.step-title {
  font-size: clamp(1.2rem, 2.5vw, 1.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  margin: 0 0 0.5rem;
}

.step-desc {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  margin: 0;
}

/* Form */
.forgot-form {
  margin: 0;
}

.forgot-form :deep(.el-form-item) {
  margin-bottom: clamp(14px, 2vw, 20px);
}

.input-label {
  display: block;
  font-size: clamp(12px, 1.5vw, 14px);
  font-weight: 500;
  color: var(--plaza-text);
  margin-bottom: 0.5rem;
}

.label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.code-input-wrapper {
  display: flex;
  align-items: stretch;
  background: var(--plaza-bg-input);
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  overflow: hidden;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  height: clamp(40px, 6vw, 48px);
}

.code-input-wrapper:hover {
  border-color: var(--plaza-accent);
}

.code-input-wrapper:focus-within {
  border-color: var(--plaza-accent) !important;
  box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.1) !important;
}

.code-input {
  flex: 1;
}

.code-input :deep(.el-input__wrapper) {
  border: none !important;
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  padding-left: 16px;
  height: 100%;
}

.code-input :deep(.el-input__inner) {
  height: 100% !important;
  line-height: 100%;
}

.code-input :deep(.el-input__wrapper:hover) {
  border: none !important;
  box-shadow: none !important;
}

.code-input :deep(.el-input.is-focus .el-input__wrapper) {
  border: none !important;
  box-shadow: none !important;
}

.code-countdown {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 clamp(10px, 2vw, 16px);
  min-width: clamp(70px, 12vw, 90px);
  border-left: 1px solid var(--plaza-border);
  background: var(--plaza-bg-card);
  color: var(--plaza-accent);
  font-size: clamp(11px, 1.4vw, 13px);
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
  user-select: none;
  white-space: nowrap;
}

.code-countdown:hover:not(.disabled) {
  background: var(--plaza-accent-soft);
}

.code-countdown.disabled {
  color: var(--plaza-text-muted);
  cursor: not-allowed;
}

.resend-btn {
  font-size: 13px;
  color: var(--plaza-accent);
  cursor: pointer;
  transition: color 0.2s ease;
}

.resend-btn:hover {
  color: var(--plaza-accent-hover);
}

.resend-btn.disabled {
  color: var(--plaza-text-muted);
  cursor: not-allowed;
}

.forgot-input :deep(.el-input__wrapper) {
  border-radius: 10px;
  padding-left: 16px;
  background: var(--plaza-bg-input) !important;
  border: 1px solid var(--plaza-border);
  box-shadow: none !important;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.forgot-input :deep(.el-input__wrapper:hover) {
  border-color: var(--plaza-accent);
}

.forgot-input :deep(.el-input.is-focus .el-input__wrapper) {
  border-color: var(--plaza-accent) !important;
  box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.1) !important;
}

.forgot-input :deep(.el-input__inner) {
  color: var(--plaza-text) !important;
  font-size: clamp(13px, 1.5vw, 15px);
}

.forgot-input :deep(.el-input__inner::placeholder) {
  color: var(--plaza-text-muted) !important;
}

.btn-next {
  width: 100%;
  height: clamp(40px, 6vw, 50px);
  border-radius: 10px;
  font-weight: 600;
  font-size: clamp(13px, 1.5vw, 15px);
  background: var(--plaza-accent) !important;
  border: none !important;
  color: #fff !important;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  margin-top: 0.8rem;
  transition: background 0.2s ease, transform 0.1s ease;
}

.btn-next:hover {
  background: var(--plaza-accent-hover) !important;
  transform: translateY(-1px);
}

.btn-icon {
  font-size: clamp(14px, 1.8vw, 16px);
}

/* Success State */
.success-container {
  text-align: center;
  padding: 20px 0;
}

.success-icon {
  width: clamp(50px, 8vw, 64px);
  height: clamp(50px, 8vw, 64px);
  border-radius: 50%;
  background: var(--app-success);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto clamp(16px, 3vw, 24px);
}

.success-icon .el-icon {
  font-size: clamp(24px, 4vw, 32px);
  color: #fff;
}

.success-title {
  font-size: clamp(1.2rem, 2.5vw, 1.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  margin: 0 0 0.8rem;
}

.success-desc {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  margin: 0 0 clamp(20px, 3vw, 32px);
}

.btn-login {
  width: 100%;
  height: clamp(40px, 6vw, 50px);
  border-radius: 10px;
  font-weight: 600;
  font-size: clamp(13px, 1.5vw, 15px);
  background: var(--plaza-accent) !important;
  border: none !important;
  color: #fff !important;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
}

.btn-login:hover {
  background: var(--plaza-accent-hover) !important;
}

/* Responsive */
@media (max-width: 480px) {
  .forgot-card {
    padding: 24px 16px;
  }

  .step-title {
    font-size: 1.3rem;
  }

  .step-line {
    width: 40px;
  }
}
</style>