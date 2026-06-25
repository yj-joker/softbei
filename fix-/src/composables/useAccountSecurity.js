import { reactive, ref, computed, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { sendEmail, verifyEmail } from '@/api/user'

/**
 * 账号安全：邮箱验证码绑定（mode=1）+ 邮箱验证码改密码（mode=2）。
 *
 * 后端契约（/weixiu/user）：
 *  - sendEmail(email, mode)        发验证码（1=绑定邮箱，2=重置密码；mode=2 要求 email==已绑邮箱）
 *  - verifyEmail(code, mode, val)  校验码并执行（mode=1 时 val=邮箱；mode=2 时 val=新密码）
 * 验证码 1 分钟有效、1 分钟内不可重复发送（后端 Redis 限制），故前端做 60s 倒计时。
 *
 * @param {() => string} boundEmailGetter 取当前已绑邮箱（改密码用）
 * @param {() => (Promise|void)} onChanged 绑定/改动成功后回调（通常重新拉取用户信息）
 */
const isOk = (res) => res && String(res.code) === '200'
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function useAccountSecurity(boundEmailGetter, onChanged) {
  // —— 绑定/换绑邮箱（mode=1）——
  const emailForm = reactive({ email: '', code: '' })
  const emailCountdown = ref(0)
  const emailSending = ref(false)
  const emailBinding = ref(false)
  let emailTimer = null

  async function sendBindCode() {
    const email = emailForm.email.trim()
    if (emailCountdown.value > 0 || emailSending.value) return
    if (!EMAIL_RE.test(email)) { ElMessage.warning('请输入正确的邮箱地址'); return }
    emailSending.value = true
    try {
      const res = await sendEmail(email, 1)
      if (!isOk(res)) { ElMessage.error(res?.message || '验证码发送失败'); return }
      ElMessage.success('验证码已发送至邮箱，1 分钟内有效')
      emailCountdown.value = 60
      emailTimer = setInterval(() => {
        if (--emailCountdown.value <= 0) { clearInterval(emailTimer); emailTimer = null }
      }, 1000)
    } catch (e) { /* 网络层错误已由 request 统一提示 */ }
    finally { emailSending.value = false }
  }

  async function bindEmail() {
    const email = emailForm.email.trim()
    if (!EMAIL_RE.test(email)) { ElMessage.warning('请输入正确的邮箱地址'); return }
    if (!emailForm.code.trim()) { ElMessage.warning('请输入验证码'); return }
    emailBinding.value = true
    try {
      const res = await verifyEmail(emailForm.code.trim(), 1, email)
      if (!isOk(res)) { ElMessage.error(res?.message || '邮箱绑定失败'); return }
      ElMessage.success('邮箱绑定成功')
      emailForm.code = ''
      await onChanged?.()
    } catch (e) { /* ignore */ }
    finally { emailBinding.value = false }
  }

  // —— 改密码（mode=2，需已绑邮箱）——
  const pwdForm = reactive({ newPassword: '', confirmPassword: '', code: '' })
  const pwdCountdown = ref(0)
  const pwdSending = ref(false)
  const pwdSubmitting = ref(false)
  let pwdTimer = null
  const hasEmail = computed(() => !!(boundEmailGetter() || '').toString().trim())

  async function sendPwdCode() {
    if (!hasEmail.value) { ElMessage.warning('请先绑定邮箱后再修改密码'); return }
    if (pwdCountdown.value > 0 || pwdSending.value) return
    pwdSending.value = true
    try {
      const res = await sendEmail(boundEmailGetter().toString().trim(), 2)
      if (!isOk(res)) { ElMessage.error(res?.message || '验证码发送失败'); return }
      ElMessage.success('验证码已发送至已绑定邮箱')
      pwdCountdown.value = 60
      pwdTimer = setInterval(() => {
        if (--pwdCountdown.value <= 0) { clearInterval(pwdTimer); pwdTimer = null }
      }, 1000)
    } catch (e) { /* ignore */ }
    finally { pwdSending.value = false }
  }

  async function changePassword() {
    if (!hasEmail.value) { ElMessage.warning('请先绑定邮箱后再修改密码'); return }
    const { newPassword, confirmPassword, code } = pwdForm
    if (!code.trim()) { ElMessage.warning('请输入验证码'); return }
    if (!newPassword || newPassword.length < 6) { ElMessage.warning('新密码长度不能少于 6 位'); return }
    if (newPassword !== confirmPassword) { ElMessage.warning('两次输入的密码不一致'); return }
    pwdSubmitting.value = true
    try {
      const res = await verifyEmail(code.trim(), 2, newPassword)
      if (!isOk(res)) { ElMessage.error(res?.message || '密码修改失败'); return }
      ElMessage.success('密码修改成功')
      pwdForm.newPassword = ''
      pwdForm.confirmPassword = ''
      pwdForm.code = ''
    } catch (e) { /* ignore */ }
    finally { pwdSubmitting.value = false }
  }

  onUnmounted(() => {
    if (emailTimer) clearInterval(emailTimer)
    if (pwdTimer) clearInterval(pwdTimer)
  })

  return {
    // 邮箱绑定
    emailForm, emailCountdown, emailSending, emailBinding, sendBindCode, bindEmail,
    // 改密码
    pwdForm, pwdCountdown, pwdSending, pwdSubmitting, hasEmail, sendPwdCode, changePassword,
  }
}
