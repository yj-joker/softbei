<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CircleCheck,
  Close,
  Headset,
  Microphone,
  Mute,
  Position,
  SwitchButton,
  VideoPause,
  Warning,
} from '@element-plus/icons-vue'
import { endTaskVoiceSession, sendTaskVoiceTurn, startTaskVoiceSession } from '@/api/maintenanceTask'
import { useAsrStream } from '@/composables/useAsrStream'
import { useSpeech } from '@/composables/useSpeech'

const props = defineProps({
  taskId: { required: true },
  steps: { type: Array, default: () => [] },
  activeStepId: { default: null },
})

const emit = defineEmits(['updated', 'exit', 'focus-step'])

const SUBMIT_DELAY_MS = 2000

const asr = useAsrStream()
const speech = useSpeech()

const focusedStepId = ref(null)
const sessionActive = ref(false)
const sessionBusy = ref(false)
const turnBusy = ref(false)
const sessionError = ref('')
const pendingTranscript = ref('')
const manualText = ref('')
const turns = ref([])
const autoSpeak = ref(true)
const forceOverride = ref(false)
const historyRef = ref(null)

let submitTimer = null
let destroyed = false

const sortedSteps = computed(() =>
  props.steps.slice().sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0)),
)
const focusedStep = computed(() =>
  sortedSteps.value.find((item) => String(item.id) === String(focusedStepId.value)) || null,
)
const focusedTitle = computed(() =>
  focusedStep.value ? `第 ${focusedStep.value.sortOrder} 步 · ${focusedStep.value.title}` : '整个任务',
)
const listening = computed(() => asr.recording.value)
const partialText = computed(() => asr.partial.value)
const voiceError = computed(() => sessionError.value || asr.error.value)
const waitingSubmit = computed(() => Boolean(submitTimer && pendingTranscript.value.trim()))
const latestAssistantTurn = computed(() => turns.value.find((turn) => turn.role === 'assistant') || null)
const latestUserTurn = computed(() => turns.value.find((turn) => turn.role === 'user') || null)
const visibleTurns = computed(() => turns.value.filter((turn) => turn.role !== 'system').slice(0, 4))
const showOverrideAction = computed(() =>
  Boolean(latestAssistantTurn.value?.needsConfirmation || latestAssistantTurn.value?.override),
)
const statusText = computed(() => {
  if (sessionBusy.value) return '连接中'
  if (turnBusy.value) return '处理中'
  if (listening.value && waitingSubmit.value) return '2 秒后提交'
  if (listening.value) return '监听中'
  if (sessionActive.value) return '待命'
  return '未连接'
})
const panelClass = computed(() => ({
  recording: listening.value,
  busy: sessionBusy.value || turnBusy.value,
  error: Boolean(voiceError.value),
}))

watch(
  () => props.activeStepId,
  (now, old) => {
    if (focusedStepId.value == null || String(focusedStepId.value) === String(old)) {
      focusedStepId.value = now
    }
  },
)

onMounted(async () => {
  focusedStepId.value = props.activeStepId || sortedSteps.value[0]?.id || null
  await openSession()
  await startListening()
})

onUnmounted(() => {
  destroyed = true
  cleanupVoiceMode()
})

function stepTitle(stepId) {
  const step = sortedSteps.value.find((item) => String(item.id) === String(stepId))
  return step ? `第 ${step.sortOrder} 步 · ${step.title}` : '整个任务'
}

function pushTurn(turn) {
  turns.value.unshift({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    ...turn,
  })
  turns.value = turns.value.slice(0, 20)
  nextTick(() => {
    const el = historyRef.value
    if (el) el.scrollTop = 0
  })
}

async function openSession() {
  if (sessionActive.value || sessionBusy.value) return
  sessionBusy.value = true
  sessionError.value = ''
  try {
    const res = await startTaskVoiceSession(props.taskId, { focusedStepId: focusedStepId.value })
    const data = res?.data || {}
    if (data.currentStepId) setFocusedStep(data.currentStepId)
    sessionActive.value = true
    pushTurn({ role: 'system', text: '语音检修已开启', meta: stepTitle(focusedStepId.value) })
  } catch (err) {
    sessionError.value = err.message || '语音检修开启失败'
    ElMessage.error('语音检修开启失败：' + (err.message || ''))
  } finally {
    sessionBusy.value = false
  }
}

async function closeSession() {
  clearSubmitTimer()
  pendingTranscript.value = ''
  try { asr.cleanup() } catch (e) {}
  speech.stop()
  if (!sessionActive.value) return
  sessionBusy.value = true
  try {
    await endTaskVoiceSession(props.taskId)
  } catch (err) {
    if (!destroyed) ElMessage.warning('语音会话结束失败：' + (err.message || ''))
  } finally {
    sessionActive.value = false
    sessionBusy.value = false
  }
}

function cleanupVoiceMode() {
  closeSession()
}

async function exitMode() {
  await closeSession()
  emit('exit')
}

async function reconnectSession() {
  await closeSession()
  await openSession()
  if (!listening.value) await startListening()
}

async function startListening() {
  if (listening.value || sessionBusy.value) return
  await openSession()
  if (!sessionActive.value) return
  try {
    await asr.start({
      onFinal: (text) => queueTranscript(text),
    })
  } catch (err) {
    sessionError.value = asr.error.value || err.message || '语音识别启动失败'
    ElMessage.error(sessionError.value)
  }
}

function stopListening() {
  if (listening.value) asr.stop()
}

async function toggleListening() {
  if (listening.value) {
    stopListening()
    return
  }
  await startListening()
}

function clearSubmitTimer() {
  if (submitTimer) {
    clearTimeout(submitTimer)
    submitTimer = null
  }
}

function scheduleSubmit(delay = SUBMIT_DELAY_MS) {
  clearSubmitTimer()
  if (!pendingTranscript.value.trim()) return
  submitTimer = setTimeout(() => {
    submitTimer = null
    flushTranscript()
  }, delay)
}

function queueTranscript(text) {
  const transcript = String(text || '').trim()
  if (!transcript) return
  pendingTranscript.value = pendingTranscript.value
    ? `${pendingTranscript.value} ${transcript}`
    : transcript
  scheduleSubmit()
}

async function flushTranscript() {
  if (turnBusy.value) {
    scheduleSubmit(600)
    return
  }
  const transcript = pendingTranscript.value.trim()
  if (!transcript) return
  pendingTranscript.value = ''
  await submitTranscript(transcript)
}

async function sendManual() {
  const transcript = manualText.value.trim()
  if (!transcript) return
  manualText.value = ''
  clearSubmitTimer()
  await submitTranscript(transcript)
}

async function confirmOverrideFromUi() {
  forceOverride.value = true
  await submitTranscript('我确认强制完成当前步骤')
}

function setFocusedStep(stepId) {
  focusedStepId.value = stepId || null
  emit('focus-step', focusedStepId.value)
}

async function submitTranscript(transcript) {
  await openSession()
  if (!sessionActive.value) return

  pushTurn({ role: 'user', text: transcript, meta: stepTitle(focusedStepId.value) })
  turnBusy.value = true
  sessionError.value = ''
  try {
    const res = await sendTaskVoiceTurn(props.taskId, {
      transcript,
      focusedStepId: focusedStepId.value,
      confirmed: forceOverride.value,
      override: forceOverride.value,
    })
    const data = res?.data || {}
    if (data.currentStepId) setFocusedStep(data.currentStepId)
    pushTurn({
      role: 'assistant',
      text: data.replyText || '已处理',
      action: data.actionLabel || data.action,
      result: data.executionResult,
      meta: stepTitle(data.currentStepId || data.targetStepId || focusedStepId.value),
      override: data.overrideRecommended,
      needsConfirmation: data.needsConfirmation,
    })
    emit('updated', data)
    if (autoSpeak.value && data.replyText) {
      speech.speak(`task-voice-${props.taskId}-${Date.now()}`, data.replyText)
    }
  } catch (err) {
    sessionError.value = err.message || '语音处理失败'
    pushTurn({
      role: 'assistant',
      text: '语音处理失败，请重试',
      action: '异常',
      result: 'ERROR',
    })
    ElMessage.error('语音处理失败：' + (err.message || ''))
  } finally {
    forceOverride.value = false
    turnBusy.value = false
    if (pendingTranscript.value.trim()) scheduleSubmit(600)
  }
}

function roleText(role) {
  if (role === 'user') return '工人'
  if (role === 'system') return '系统'
  return 'AI'
}
</script>

<template>
  <section class="voice-mode-panel" :class="panelClass">
    <header class="voice-strip">
      <div class="voice-title">
        <span class="voice-mark"><el-icon><Headset /></el-icon></span>
        <span>
          <small>VOICE MAINTENANCE</small>
          <b>语音检修中</b>
        </span>
        <span class="voice-status"><i />{{ statusText }}</span>
      </div>

      <div class="voice-tools">
        <button type="button" class="voice-tool" :class="{ on: autoSpeak }" @click="autoSpeak = !autoSpeak">
          <el-icon><CircleCheck v-if="autoSpeak" /><Mute v-else /></el-icon>
          自动播报
        </button>
        <button type="button" class="voice-exit" @click="exitMode" title="退出语音检修">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </header>

    <div class="voice-focus-row">
      <label>
        <span>当前步骤</span>
        <select v-model="focusedStepId" @change="setFocusedStep(focusedStepId)">
          <option :value="null">整个任务</option>
          <option v-for="step in sortedSteps" :key="step.id" :value="step.id">
            第 {{ step.sortOrder }} 步 · {{ step.title }}
          </option>
        </select>
      </label>
    </div>

    <div class="voice-workspace">
      <div class="voice-listen-card">
        <div class="voice-orb" :class="{ on: listening }">
          <el-icon><Microphone /></el-icon>
        </div>
        <div class="voice-live-copy">
          <strong>{{ focusedTitle }}</strong>
          <p v-if="voiceError" class="error-text">{{ voiceError }}</p>
          <p v-else-if="partialText">{{ partialText }}</p>
          <p v-else-if="pendingTranscript">{{ pendingTranscript }}</p>
          <p v-else>{{ listening ? '正在监听现场语音' : '麦克风未开启' }}</p>
        </div>
        <div class="voice-inline-actions">
          <button type="button" class="voice-main-btn" :class="{ danger: listening }" :disabled="sessionBusy" @click="toggleListening">
            <el-icon><VideoPause v-if="listening" /><Microphone v-else /></el-icon>
            {{ listening ? '停止听写' : '开始听写' }}
          </button>
          <button v-if="waitingSubmit" type="button" class="voice-mini-btn" :disabled="turnBusy" @click="flushTranscript">
            <el-icon><Position /></el-icon>
            立即发送
          </button>
          <button v-if="voiceError" type="button" class="voice-mini-btn recover" :disabled="sessionBusy" @click="reconnectSession">
            <el-icon><SwitchButton /></el-icon>
            恢复连接
          </button>
        </div>
      </div>

      <article class="voice-feedback">
        <span class="voice-section-label">AI 当前反馈</span>
        <p v-if="latestAssistantTurn">{{ latestAssistantTurn.text }}</p>
        <p v-else class="muted">你可以直接说“完成了”“回到第二步”“这一步怎么判断”。</p>
        <div v-if="latestAssistantTurn?.action || latestAssistantTurn?.result || latestAssistantTurn?.meta" class="voice-meta">
          <em v-if="latestAssistantTurn?.action">{{ latestAssistantTurn.action }}</em>
          <em v-if="latestAssistantTurn?.result">{{ latestAssistantTurn.result }}</em>
          <em v-if="latestAssistantTurn?.meta">{{ latestAssistantTurn.meta }}</em>
        </div>
        <button v-if="showOverrideAction" type="button" class="voice-confirm-btn" :disabled="turnBusy" @click="confirmOverrideFromUi">
          <el-icon><Warning /></el-icon>
          确认强制完成
        </button>
      </article>
    </div>

    <div class="voice-manual">
      <input
        v-model="manualText"
        :disabled="turnBusy"
        placeholder="语音识别不准时，可在这里修正后发送"
        @keydown.enter.prevent="sendManual"
      />
      <button type="button" :disabled="turnBusy || !manualText.trim()" @click="sendManual">
        <el-icon><Position /></el-icon>
      </button>
    </div>

    <div v-if="visibleTurns.length" ref="historyRef" class="voice-history">
      <article v-for="turn in visibleTurns" :key="turn.id" class="voice-turn" :class="turn.role">
        <span>{{ roleText(turn.role) }}</span>
        <p>{{ turn.text }}</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.voice-mode-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 12px;
  padding: 14px;
  border: 1px solid var(--plaza-accent-soft-strong);
  border-radius: 12px;
  background:
    linear-gradient(180deg, rgba(94, 140, 62, 0.1), transparent 46%),
    var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
}

.voice-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.voice-title {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.voice-mark {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border-radius: 10px;
  color: #fff;
  background: var(--plaza-accent-grad);
  font-size: 20px;
}

.voice-title span:nth-child(2) {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.voice-title small {
  color: var(--plaza-text-muted);
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 850;
  letter-spacing: 0.12em;
}

.voice-title b {
  color: var(--plaza-heading);
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 850;
}

.voice-status {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 9px;
  border-radius: 999px;
  color: var(--plaza-text-muted);
  background: var(--plaza-panel-bg);
  font-size: 11px;
  font-weight: 850;
}

.voice-status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--plaza-border-strong);
}

.voice-mode-panel.recording .voice-status i {
  background: #5e8c3e;
  box-shadow: 0 0 0 4px var(--plaza-accent-soft);
}

.voice-mode-panel.busy .voice-status i {
  background: var(--plaza-warning);
  box-shadow: 0 0 0 4px var(--plaza-warning-soft);
}

.voice-mode-panel.error .voice-status i {
  background: #c5402c;
  box-shadow: 0 0 0 4px rgba(197, 64, 44, 0.12);
}

.voice-tools {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.voice-tool,
.voice-exit,
.voice-mini-btn,
.voice-main-btn,
.voice-confirm-btn,
.voice-manual button {
  display: inline-flex;
  min-width: 0;
  min-height: 34px;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  font-size: 12px;
  font-weight: 850;
  cursor: pointer;
}

.voice-exit {
  width: 34px;
  padding: 0;
}

.voice-tool.on,
.voice-main-btn {
  border-color: transparent;
  color: #fff;
  background: var(--plaza-accent-grad);
}

.voice-main-btn.danger,
.voice-confirm-btn {
  border-color: #c5402c;
  color: #fff;
  background: #c5402c;
}

.voice-mini-btn.recover {
  color: #c5402c;
  border-color: rgba(197, 64, 44, 0.28);
  background: var(--plaza-danger-soft);
}

.voice-tool:hover,
.voice-exit:hover,
.voice-mini-btn:hover,
.voice-manual button:hover {
  border-color: var(--plaza-accent);
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.voice-focus-row label {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.voice-focus-row span,
.voice-section-label {
  color: var(--plaza-text-muted);
  font-size: 11px;
  font-weight: 850;
}

.voice-focus-row select {
  width: 100%;
  min-width: 0;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  outline: 0;
  color: var(--plaza-heading);
  background: var(--plaza-bg-card);
  font-size: 13px;
  font-weight: 800;
}

.voice-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(280px, 0.8fr);
  gap: 10px;
}

.voice-listen-card,
.voice-feedback {
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: var(--plaza-panel-bg);
}

.voice-listen-card {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 12px;
}

.voice-orb {
  display: grid;
  width: 54px;
  height: 54px;
  place-items: center;
  border-radius: 50%;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 23px;
}

.voice-orb.on {
  color: #fff;
  background: #5e8c3e;
  box-shadow: 0 0 0 8px rgba(94, 140, 62, 0.12);
  animation: voicePulse 1.45s ease-in-out infinite;
}

@keyframes voicePulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.voice-live-copy {
  min-width: 0;
}

.voice-live-copy strong {
  display: block;
  color: var(--plaza-heading);
  font-size: 14px;
  font-weight: 850;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.voice-live-copy p {
  margin: 5px 0 0;
  color: var(--plaza-text-muted);
  font-size: 12.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.voice-live-copy .error-text {
  color: #c5402c;
}

.voice-inline-actions {
  display: inline-flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.voice-feedback {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  padding: 11px 12px;
}

.voice-feedback p {
  margin: 0;
  color: var(--plaza-text);
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.voice-feedback p.muted {
  color: var(--plaza-text-muted);
}

.voice-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.voice-meta em {
  display: inline-flex;
  max-width: 100%;
  min-height: 21px;
  align-items: center;
  padding: 0 7px;
  border-radius: 999px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 10px;
  font-style: normal;
  font-weight: 800;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.voice-confirm-btn {
  align-self: flex-start;
}

.voice-manual {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 42px;
  gap: 8px;
}

.voice-manual input {
  width: 100%;
  min-width: 0;
  min-height: 38px;
  padding: 0 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  outline: 0;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  font-size: 13px;
}

.voice-manual input:focus {
  border-color: var(--plaza-accent);
  box-shadow: 0 0 0 3px var(--plaza-accent-soft);
}

.voice-history {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  max-height: 150px;
  overflow-y: auto;
}

.voice-turn {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 7px;
  padding: 8px 9px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-card);
}

.voice-turn.user {
  border-color: var(--plaza-accent-soft-strong);
  background: var(--plaza-accent-soft);
}

.voice-turn > span {
  color: var(--plaza-text-muted);
  font-size: 10px;
  font-weight: 850;
}

.voice-turn p {
  min-width: 0;
  margin: 0;
  color: var(--plaza-text);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

@media (max-width: 920px) {
  .voice-strip,
  .voice-workspace {
    grid-template-columns: 1fr;
  }

  .voice-strip {
    align-items: flex-start;
    flex-direction: column;
  }

  .voice-tools {
    width: 100%;
    justify-content: space-between;
  }

  .voice-workspace,
  .voice-listen-card {
    grid-template-columns: 1fr;
  }

  .voice-inline-actions {
    justify-content: flex-start;
  }

  .voice-history {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .voice-focus-row label {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .voice-orb.on {
    animation: none;
  }
}
</style>
