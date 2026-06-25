<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { gsap } from 'gsap'
import {
  Calendar,
  Clock,
  EditPen,
  Key,
  Lock,
  Message,
  Phone,
  User,
} from '@element-plus/icons-vue'
import { getUserById, updateUser } from '@/api/user'
import { useAccountSecurity } from '@/composables/useAccountSecurity'

const loading = ref(false)
const submitting = ref(false)
const isEditing = ref(false)
const formRef = ref(null)
const userInfo = ref({})

const form = reactive({ phone: '' })

const rules = {
  phone: [
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的手机号', trigger: 'blur' },
  ],
}

// 账号安全：邮箱验证码绑定（mode=1）+ 邮箱验证码改密码（mode=2），逻辑/倒计时封装在 composable
const {
  emailForm, emailCountdown, emailSending, emailBinding, sendBindCode, bindEmail,
  pwdForm, pwdCountdown, pwdSending, pwdSubmitting, hasEmail, sendPwdCode, changePassword,
} = useAccountSecurity(() => userInfo.value.email, () => fetchUserInfo())

const userInitial = computed(() => userInfo.value.name?.[0] || 'A')
const isActive = computed(() => Number(userInfo.value.status) === 1)
const normalizedForm = computed(() => ({ phone: form.phone?.trim() || '' }))
const isDirty = computed(() => normalizedForm.value.phone !== (userInfo.value.phone || ''))

const identityReadouts = computed(() => [
  { label: '联系方式', value: userInfo.value.phone || '未绑定手机号', icon: Phone },
  { label: '协作邮箱', value: userInfo.value.email || '未绑定邮箱', icon: Message },
  { label: '入职日期', value: formatDate(userInfo.value.hireDate, false), icon: Calendar },
  { label: '最近登录', value: formatDate(userInfo.value.lastLoginTime), icon: Clock },
])

function getCurrentUser() {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}')
  } catch {
    return {}
  }
}

function syncLocalUser(nextUser) {
  const current = getCurrentUser()
  localStorage.setItem('userInfo', JSON.stringify({ ...current, ...nextUser }))
}

function syncForm() {
  form.phone = userInfo.value.phone || ''
  formRef.value?.clearValidate()
}

function getTypeText(type) {
  const map = { 0: '普通用户', 1: '管理员' }
  return map[Number(type)] ?? '未知'
}

function getGenderText(gender) {
  const map = { 0: '男', 1: '女' }
  return map[Number(gender)] ?? '-'
}

function getStatusText(status) {
  const code = Number(status)
  if (Number.isNaN(code)) return '状态未知'
  return code === 1 ? '账号已激活' : '账号未激活'
}

function formatDate(value, withTime = true) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  })
}

async function fetchUserInfo() {
  const current = getCurrentUser()
  if (!current.id) {
    ElMessage.warning('未找到当前用户信息，请重新登录')
    return
  }

  userInfo.value = { ...current, ...userInfo.value }
  syncForm()
  loading.value = true
  try {
    const res = await getUserById(current.id)
    if (res.code === '200' || res.code === 200) {
      userInfo.value = res.data || {}
      syncForm()
      syncLocalUser(userInfo.value)
    } else {
      ElMessage.error(res.message || '获取个人信息失败')
    }
  } catch {
    ElMessage.error('获取个人信息失败')
  } finally {
    loading.value = false
  }
}

function startEditing() {
  syncForm()
  isEditing.value = true
}

async function confirmDiscard(message = '当前修改尚未保存，确定放弃吗？') {
  if (!isDirty.value) return true
  try {
    await ElMessageBox.confirm(message, '放弃修改', {
      confirmButtonText: '放弃修改',
      cancelButtonText: '继续编辑',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

async function cancelEditing() {
  if (!(await confirmDiscard())) return
  syncForm()
  isEditing.value = false
}

async function handleUpdate() {
  if (!isDirty.value || submitting.value) return

  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const current = getCurrentUser()
    const res = await updateUser({
      id: current.id,
      name: userInfo.value.name,
      number: userInfo.value.number,
      phone: normalizedForm.value.phone || null,
      email: userInfo.value.email || null, // 邮箱不在此表单改，原样回传避免被清空（邮箱走验证码绑定）
    })
    if (res.code === '200' || res.code === 200) {
      userInfo.value = {
        ...userInfo.value,
        phone: normalizedForm.value.phone,
      }
      syncLocalUser(userInfo.value)
      isEditing.value = false
      ElMessage.success('个人资料已更新')
      await fetchUserInfo()
    } else {
      ElMessage.error(res.message || '保存失败')
    }
  } catch {
    ElMessage.error('保存失败')
  } finally {
    submitting.value = false
  }
}

function handleBeforeUnload(event) {
  if (!isEditing.value || !isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(async () => {
  if (!isEditing.value || !isDirty.value) return true
  return confirmDiscard('离开个人中心将丢失未保存的修改，确定离开吗？')
})

onMounted(() => {
  fetchUserInfo()
  window.addEventListener('beforeunload', handleBeforeUnload)

  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  nextTick(() => {
    gsap.timeline({ defaults: { ease: 'power3.out' } })
      .from('.profile-head', { y: -16, opacity: 0, duration: 0.5 })
      .from('.identity-console', { y: 22, opacity: 0, duration: 0.55 }, '-=0.25')
      .from('.avatar', { scale: 0.5, rotate: -10, opacity: 0, duration: 0.55, ease: 'back.out(1.7)' }, '-=0.35')
      .from('.readout', { y: 14, opacity: 0, duration: 0.4, stagger: 0.07 }, '-=0.2')
      .from('.profile-grid > * > .ui-reveal, .profile-grid > .ui-reveal', { y: 24, opacity: 0, duration: 0.5, stagger: 0.12 }, '-=0.2')
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
})
</script>

<template>
  <section class="admin-profile-page" v-loading="loading">
    <!-- 页头 -->
    <header class="profile-head">
      <div class="head-copy">
        <span class="eyebrow">ADMIN PROFILE · ACCESS CONTROL</span>
        <h1>个人中心</h1>
        <p>管理员身份、协作联系方式与账号安全设置。</p>
      </div>
      <div class="head-status">
        <span class="head-code">ADMIN IDENTITY</span>
        <span class="head-chip" :class="{ offline: !isActive }">
          <i />{{ isActive ? '账号服务正常' : '账号状态需确认' }}
        </span>
      </div>
    </header>

    <!-- 身份控制台（深色） -->
    <section class="identity-console">
      <div class="console-grid" aria-hidden="true" />
      <div class="identity-main">
        <div class="avatar-shell">
          <span class="avatar">{{ userInitial }}</span>
          <i :class="{ offline: !isActive }" />
        </div>
        <div class="identity-copy">
          <span class="identity-eyebrow">PLATFORM ADMIN · 管理员身份</span>
          <h2>{{ userInfo.name || '未设置姓名' }}</h2>
          <div class="identity-meta">
            <span>{{ getTypeText(userInfo.type) }}</span>
            <i />
            <span>{{ userInfo.number || '暂无工号' }}</span>
          </div>
        </div>
      </div>

      <div class="identity-status">
        <span class="status-label">ACCOUNT STATUS</span>
        <strong :class="{ offline: !isActive }">
          <i />{{ getStatusText(userInfo.status) }}
        </strong>
        <small>身份信息由系统账号服务统一维护</small>
      </div>

      <div class="identity-readouts">
        <div v-for="item in identityReadouts" :key="item.label" class="readout">
          <span class="readout-icon"><el-icon><component :is="item.icon" /></el-icon></span>
          <span class="readout-copy">
            <small>{{ item.label }}</small>
            <b>{{ item.value }}</b>
          </span>
        </div>
      </div>
    </section>

    <div class="profile-grid">
      <!-- 基础资料 -->
      <section class="profile-panel ui-reveal">
        <header class="panel-head">
          <div>
            <span>PROFILE RECORD</span>
            <h3>基础资料</h3>
            <p>{{ isEditing ? '修改用于平台协作的联系方式。' : '查看当前管理员账号资料与身份归属。' }}</p>
          </div>
          <button v-if="!isEditing" type="button" class="secondary-button edit-trigger" @click="startEditing">
            <el-icon><EditPen /></el-icon>
            编辑个人资料
          </button>
          <span v-else class="editing-chip"><i /> 编辑中</span>
        </header>

        <div class="profile-content">
          <div class="immutable-grid" aria-label="只读身份资料">
            <div class="profile-field">
              <span class="field-icon"><el-icon><User /></el-icon></span>
              <span>
                <small>姓名</small>
                <b>{{ userInfo.name || '-' }}</b>
              </span>
              <em>系统资料</em>
            </div>
            <div class="profile-field">
              <span class="field-index">ID</span>
              <span>
                <small>工号</small>
                <b>{{ userInfo.number || '-' }}</b>
              </span>
              <em>不可修改</em>
            </div>
            <div class="profile-field">
              <span class="field-index">SX</span>
              <span>
                <small>性别</small>
                <b>{{ getGenderText(userInfo.gender) }}</b>
              </span>
            </div>
            <div class="profile-field">
              <span class="field-index">RL</span>
              <span>
                <small>账号角色</small>
                <b>{{ getTypeText(userInfo.type) }}</b>
              </span>
            </div>
          </div>

          <div class="section-divider">
            <span>CONTACT CHANNELS</span>
            <i />
          </div>

          <div v-if="!isEditing" class="contact-view">
            <div class="contact-row">
              <span class="contact-icon"><el-icon><Phone /></el-icon></span>
              <span class="contact-copy">
                <small>手机号</small>
                <b>{{ userInfo.phone || '暂未绑定手机号' }}</b>
              </span>
              <span class="contact-state" :class="{ missing: !userInfo.phone }">
                {{ userInfo.phone ? '已配置' : '待完善' }}
              </span>
            </div>
            <div class="contact-row">
              <span class="contact-icon"><el-icon><Message /></el-icon></span>
              <span class="contact-copy">
                <small>邮箱</small>
                <b>{{ userInfo.email || '暂未绑定邮箱' }}</b>
              </span>
              <span class="contact-state" :class="{ missing: !userInfo.email }">
                {{ userInfo.email ? '已配置' : '待完善' }}
              </span>
            </div>
          </div>

          <el-form
            v-else
            ref="formRef"
            :model="form"
            :rules="rules"
            label-position="top"
            class="contact-form"
          >
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="form.phone" type="tel" autocomplete="tel" placeholder="请输入手机号" clearable>
                <template #prefix><el-icon><Phone /></el-icon></template>
              </el-input>
            </el-form-item>
            <p class="form-tip">邮箱需通过验证码绑定，请在右侧「绑定邮箱」面板操作。</p>

            <div class="form-actions">
              <span class="save-hint">
                {{ isDirty ? '检测到未保存修改' : '修改手机号或邮箱后可保存' }}
              </span>
              <div>
                <button type="button" class="secondary-button" @click="cancelEditing">取消</button>
                <button
                  type="button"
                  class="primary-button"
                  :disabled="!isDirty || submitting"
                  @click="handleUpdate"
                >
                  {{ submitting ? '正在保存' : '保存修改' }}
                </button>
              </div>
            </div>
          </el-form>
        </div>
      </section>

      <!-- 账号与安全 / 修改密码 -->
      <aside class="account-column">
        <section class="account-panel ui-reveal">
          <header class="panel-head compact">
            <div>
              <span>ACCOUNT SECURITY</span>
              <h3>账号与安全</h3>
              <p>检查登录状态和账号访问方式。</p>
            </div>
          </header>

          <div class="security-list">
            <div class="security-row">
              <span class="security-icon success"><el-icon><Lock /></el-icon></span>
              <span>
                <b>登录凭证</b>
                <small>账号验证状态正常</small>
              </span>
              <i class="security-dot" />
            </div>
            <div class="security-row">
              <span class="security-icon"><el-icon><Clock /></el-icon></span>
              <span>
                <b>最近登录</b>
                <small>{{ formatDate(userInfo.lastLoginTime) }}</small>
              </span>
            </div>
          </div>
        </section>

        <section class="account-panel ui-reveal">
          <header class="panel-head compact">
            <div>
              <span>EMAIL BINDING</span>
              <h3>{{ userInfo.email ? '换绑邮箱' : '绑定邮箱' }}</h3>
              <p>{{ userInfo.email ? ('当前已绑定：' + userInfo.email) : '绑定邮箱后才能通过邮箱验证码修改密码。' }}</p>
            </div>
            <span class="pwd-mark"><el-icon><Message /></el-icon></span>
          </header>

          <div class="pwd-form">
            <el-form label-position="top">
              <el-form-item label="邮箱">
                <el-input v-model="emailForm.email" type="email" placeholder="请输入要绑定的邮箱" clearable>
                  <template #prefix><el-icon><Message /></el-icon></template>
                </el-input>
              </el-form-item>
              <el-form-item label="验证码">
                <div class="code-row">
                  <el-input v-model="emailForm.code" placeholder="请输入邮箱验证码" maxlength="6" />
                  <button
                    type="button"
                    class="secondary-button code-btn"
                    :disabled="emailCountdown > 0 || emailSending"
                    @click="sendBindCode"
                  >
                    {{ emailSending ? '发送中…' : emailCountdown > 0 ? emailCountdown + 's' : '发送验证码' }}
                  </button>
                </div>
              </el-form-item>
              <button
                type="button"
                class="primary-button pwd-submit"
                :disabled="emailBinding"
                @click="bindEmail"
              >
                {{ emailBinding ? '正在绑定' : (userInfo.email ? '确认换绑' : '确认绑定') }}
              </button>
            </el-form>
          </div>
        </section>

        <section class="account-panel pwd-panel ui-reveal">
          <header class="panel-head compact">
            <div>
              <span>PASSWORD RESET</span>
              <h3>修改密码</h3>
              <p>通过已绑定邮箱的验证码更新登录密码。</p>
            </div>
            <span class="pwd-mark"><el-icon><Key /></el-icon></span>
          </header>

          <p v-if="!hasEmail" class="pwd-need-email">请先绑定邮箱后再修改密码。</p>
          <div v-else class="pwd-form">
            <el-form label-position="top">
              <el-form-item label="新密码">
                <el-input v-model="pwdForm.newPassword" type="password" placeholder="请输入新密码（至少6位）" show-password />
              </el-form-item>
              <el-form-item label="确认密码">
                <el-input v-model="pwdForm.confirmPassword" type="password" placeholder="请再次输入新密码" show-password />
              </el-form-item>
              <el-form-item label="邮箱验证码">
                <div class="code-row">
                  <el-input v-model="pwdForm.code" placeholder="请输入邮箱验证码" maxlength="6" />
                  <button
                    type="button"
                    class="secondary-button code-btn"
                    :disabled="pwdCountdown > 0 || pwdSending"
                    @click="sendPwdCode"
                  >
                    {{ pwdSending ? '发送中…' : pwdCountdown > 0 ? pwdCountdown + 's' : '发送验证码' }}
                  </button>
                </div>
              </el-form-item>
              <button
                type="button"
                class="primary-button pwd-submit"
                :disabled="pwdSubmitting"
                @click="changePassword"
              >
                {{ pwdSubmitting ? '正在提交' : '修改密码' }}
              </button>
            </el-form>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.admin-profile-page {
  max-width: 1180px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* —— 页头 —— */
.profile-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
}
.head-copy .eyebrow {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--plaza-accent);
}
.head-copy h1 {
  margin: 6px 0 0;
  font-family: var(--font-display);
  font-size: 1.9rem;
  font-weight: 800;
  color: var(--plaza-heading);
}
.head-copy p {
  margin: 6px 0 0;
  color: var(--plaza-text-muted);
  font-size: 13px;
}
.head-status {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.head-code {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.13em;
  color: var(--plaza-text-muted);
}
.head-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 999px;
  background: var(--plaza-success-soft);
  color: var(--plaza-success);
  font-size: 12px;
  font-weight: 700;
}
.head-chip i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--plaza-success);
  box-shadow: 0 0 0 4px rgba(94, 140, 62, 0.14);
}
.head-chip.offline {
  background: var(--plaza-danger-soft);
  color: var(--plaza-danger);
}
.head-chip.offline i {
  background: var(--plaza-danger);
  box-shadow: 0 0 0 4px rgba(197, 64, 44, 0.14);
}

/* —— 身份控制台（深色） —— */
.identity-console {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  padding: 28px;
  overflow: hidden;
  border-radius: 15px;
  color: #f4ece0;
  border: 1px solid var(--plaza-accent-soft-strong);
  background: var(--plaza-console-grad);
  box-shadow: 0 26px 60px -24px rgba(20, 14, 8, 0.5);
}
.console-grid {
  position: absolute;
  inset: 0;
  opacity: 0.6;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.028) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.028) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: linear-gradient(90deg, #000, transparent 78%);
}
.identity-main,
.identity-status,
.identity-readouts {
  position: relative;
  z-index: 1;
}
.identity-main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 18px;
}
.avatar-shell {
  position: relative;
  flex: 0 0 auto;
}
.avatar {
  display: grid;
  width: 76px;
  height: 76px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 18px;
  color: #fff;
  background: var(--plaza-accent-grad);
  box-shadow: 0 14px 30px var(--plaza-accent-soft-strong);
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 800;
}
.avatar-shell > i {
  position: absolute;
  right: -3px;
  bottom: -3px;
  width: 15px;
  height: 15px;
  border: 3px solid #1b140d;
  border-radius: 50%;
  background: #4fae74;
}
.avatar-shell > i.offline { background: #e15a5a; }
.identity-copy { min-width: 0; }
.identity-eyebrow {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.13em;
  color: var(--plaza-console-accent);
}
.identity-copy h2 {
  margin: 6px 0 0;
  color: #fff;
  font-family: var(--font-display);
  font-size: clamp(26px, 3vw, 36px);
  font-weight: 800;
  letter-spacing: -0.03em;
}
.identity-meta {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: 7px;
  color: #b3a692;
  font-size: 11px;
}
.identity-meta i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #7a6b58;
}
.identity-status {
  display: flex;
  min-width: 190px;
  flex-direction: column;
  align-items: flex-end;
  justify-content: center;
}
.status-label {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.13em;
  color: #8a7c68;
}
.identity-status strong {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  color: #ece2d4;
  font-size: 12px;
  font-weight: 700;
}
.identity-status strong > i,
.security-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4fae74;
  box-shadow: 0 0 0 4px rgba(79, 174, 116, 0.14);
}
.identity-status strong.offline > i {
  background: #e15a5a;
  box-shadow: 0 0 0 4px rgba(225, 90, 90, 0.14);
}
.identity-status small {
  margin-top: 6px;
  color: #8a7c68;
  font-size: 9px;
}
.identity-readouts {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-top: 2px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 11px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.07);
}
.readout {
  display: grid;
  min-width: 0;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 13px 15px;
  background: rgba(0, 0, 0, 0.30);
}
.readout-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid var(--plaza-accent-soft-strong);
  border-radius: 9px;
  color: var(--plaza-console-accent);
  background: var(--plaza-accent-soft);
}
.readout-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.readout-copy small {
  color: #8a7c68;
  font-size: 8px;
}
.readout-copy b {
  margin-top: 3px;
  overflow: hidden;
  color: #d9cebd;
  font-size: 10px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* —— 内容栅格 —— */
.profile-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.62fr);
  align-items: stretch;
  gap: 16px;
}
.profile-panel,
.account-panel {
  padding: 22px;
  border: 1px solid var(--plaza-border);
  border-radius: 14px;
  background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--plaza-border);
}
.panel-head > div > span {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.13em;
  color: var(--plaza-accent);
}
.panel-head h3 {
  margin: 5px 0 0;
  color: var(--plaza-heading);
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 800;
}
.panel-head p {
  margin: 5px 0 0;
  color: var(--plaza-text-muted);
  font-size: 11px;
}

/* —— 通用按钮 —— */
.primary-button,
.secondary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 40px;
  padding: 0 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease, border-color 0.18s ease;
}
.primary-button {
  border: none;
  color: var(--home-btn-text, #fff);
  background: var(--plaza-accent-grad);
  box-shadow: 0 10px 22px -10px rgba(196, 96, 47, 0.7);
}
.primary-button:hover:not(:disabled) { transform: translateY(-2px); }
.primary-button:disabled { opacity: 0.55; cursor: not-allowed; box-shadow: none; }
.secondary-button {
  border: 1px solid var(--plaza-border);
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
}
.secondary-button:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.edit-trigger { flex: 0 0 auto; min-height: 44px; }

.editing-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  border: 1px solid var(--plaza-accent);
  border-radius: 999px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 10px;
  font-weight: 800;
}
.editing-chip i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--plaza-accent);
}

.profile-content { padding-top: 16px; }
.immutable-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.profile-field {
  display: grid;
  min-width: 0;
  min-height: 72px;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  padding: 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: var(--plaza-bg-input);
}
.profile-field > span:nth-child(2),
.contact-copy,
.security-row > span:nth-child(2) {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.field-icon,
.field-index {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 9px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 16px;
}
.field-index {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.05em;
}
.profile-field small,
.contact-copy small,
.security-row small {
  color: var(--plaza-text-muted);
  font-size: 9px;
}
.profile-field b,
.contact-copy b,
.security-row b {
  margin-top: 4px;
  overflow: hidden;
  color: var(--plaza-text);
  font-size: 12px;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profile-field em {
  align-self: start;
  padding: 3px 6px;
  border-radius: 999px;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg);
  font-size: 8px;
  font-style: normal;
  white-space: nowrap;
}

.section-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 20px 0 12px;
}
.section-divider span {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.13em;
  color: var(--plaza-text-muted);
}
.section-divider i {
  height: 1px;
  flex: 1;
  background: var(--plaza-border);
}

.contact-view {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.contact-row {
  display: grid;
  min-height: 64px;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  padding: 11px 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: var(--plaza-bg-card);
}
.contact-icon,
.security-icon {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 9px;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg-input);
}
.contact-state {
  padding: 4px 8px;
  border-radius: 999px;
  color: var(--plaza-success);
  background: var(--plaza-success-soft);
  font-size: 9px;
  font-weight: 800;
}
.contact-state.missing {
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.contact-form { min-height: 172px; }
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.contact-form :deep(.el-form-item__label),
.pwd-form :deep(.el-form-item__label) {
  color: var(--plaza-text);
  font-size: 11px;
  font-weight: 700;
}
.form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px solid var(--plaza-border);
}
.form-actions > div { display: flex; gap: 8px; }
.form-actions button { min-height: 44px; }
.save-hint { color: var(--plaza-text-muted); font-size: 9px; }

/* —— 账号列 —— */
.account-column {
  display: flex;
  height: 100%;
  flex-direction: column;
  gap: 12px;
}
.panel-head.compact { align-items: flex-start; }
.security-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}
.security-row {
  display: grid;
  width: 100%;
  min-height: 62px;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: var(--plaza-bg-input);
}
.security-icon.success {
  color: var(--plaza-success);
  background: var(--plaza-success-soft);
}
.security-dot { margin-right: 5px; }

/* —— 修改密码 —— */
.pwd-panel { display: flex; flex: 1; flex-direction: column; }
.pwd-mark {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 9px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 18px;
}
.pwd-form { margin-top: 16px; }
.pwd-submit { width: 100%; min-height: 44px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.code-row :deep(.el-input) { flex: 1; }
.code-btn { flex: 0 0 auto; min-height: 40px; padding: 0 12px; white-space: nowrap; }
.code-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.pwd-need-email {
  margin-top: 16px;
  padding: 14px 12px;
  border: 1px dashed var(--plaza-border);
  border-radius: 10px;
  color: var(--plaza-text-muted);
  font-size: 12px;
  text-align: center;
}
.form-tip { margin: 10px 0 0; color: var(--plaza-text-muted); font-size: 10px; }

@media (max-width: 1100px) {
  .identity-readouts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .profile-grid { grid-template-columns: 1fr; }
  .account-column {
    display: grid;
    height: auto;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
  }
}

@media (max-width: 700px) {
  .profile-head { flex-direction: column; align-items: flex-start; }
  .head-status { align-items: flex-start; }
  .identity-console { grid-template-columns: 1fr; padding: 20px; }
  .identity-status { min-width: 0; align-items: flex-start; }
  .identity-readouts,
  .immutable-grid,
  .form-grid,
  .account-column { grid-template-columns: 1fr; }
  .panel-head,
  .form-actions { align-items: flex-start; flex-direction: column; }
  .edit-trigger,
  .form-actions,
  .form-actions > div,
  .form-actions button { width: 100%; }
}

@media (max-width: 430px) {
  .identity-main { align-items: flex-start; flex-direction: column; }
  .identity-readouts { display: flex; flex-direction: column; }
  .profile-field { grid-template-columns: 38px minmax(0, 1fr); }
  .profile-field em { display: none; }
}
</style>
