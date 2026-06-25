<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowDown,
  ArrowUp,
  ChatDotRound,
  Close,
  Connection,
  Picture,
  Position,
  Promotion,
} from '@element-plus/icons-vue'
import { uploadImage } from '@/api/user'
import { taskAssistantStore } from '@/stores/taskAssistantStore'
import { extractUploadedImageUrl } from '@/utils/upload'

const props = defineProps({
  taskId: { required: true },
  steps: { type: Array, default: () => [] },
  activeStepId: { default: null },
})

const s = computed(() => taskAssistantStore.get(props.taskId))
const input = ref('')
const pendingImages = ref([])
const uploading = ref(false)
const pendingSend = ref(false)
const bodyRef = ref(null)
const inputRef = ref(null)
const openTimelines = ref({})

const sortedSteps = computed(() =>
  props.steps.slice().sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0)),
)
const focusedStep = computed(() => sortedSteps.value.find((x) => x.id === s.value.focusedStepId) || null)
const focusedTitle = computed(() =>
  focusedStep.value ? `第 ${focusedStep.value.sortOrder} 步 · ${focusedStep.value.title}` : '整个任务',
)
const quickPrompts = computed(() => {
  if (!focusedStep.value) {
    return ['概括这条任务的关键风险', '建议我先检查哪些信息']
  }
  return [`解释“${focusedStep.value.title}”的操作重点`, '这一步最容易出现哪些错误？']
})

// 聚焦步骤默认跟随「正在执行」的步骤；用户手动切换后停止自动跟随
function syncFocus(now, old) {
  const st = s.value
  if (st.focusedStepId == null || st.focusedStepId === old) st.focusedStepId = now
}
watch(() => props.activeStepId, (n, o) => syncFocus(n, o))
onMounted(() => {
  if (s.value.focusedStepId == null) s.value.focusedStepId = props.activeStepId
  taskAssistantStore.loadHistory(props.taskId)
  scrollDown()
})

watch(() => s.value.messages.length, scrollDown)
watch(
  () => (s.value.messages[s.value.messages.length - 1] || {}).content,
  scrollDown,
)

function scrollDown() {
  nextTick(() => {
    const el = bodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function renderText(t) {
  return String(t || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
}

function choosePrompt(p) {
  input.value = p
  focusInput()
}

async function onPickFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  if (!files.length) return
  uploading.value = true
  try {
    for (const f of files) {
      const res = await uploadImage(f)
      const url = extractUploadedImageUrl(res)
      if (url) pendingImages.value.push(url)
    }
  } catch (err) {
    ElMessage.error('图片上传失败：' + (err.message || ''))
  } finally {
    uploading.value = false
    if (pendingSend.value) {
      pendingSend.value = false
      if (input.value.trim() || pendingImages.value.length) {
        await send()
      } else {
        ElMessage.error('图片上传失败，未发送给 AI')
      }
    }
  }
}
function removePending(i) {
  pendingImages.value.splice(i, 1)
}

async function send() {
  const text = input.value.trim()
  if (s.value.streaming) return
  if (uploading.value) {
    pendingSend.value = true
    ElMessage.info('图片上传中，完成后将自动发送')
    return
  }
  if (!text && !pendingImages.value.length) return
  const images = pendingImages.value.slice()
  input.value = ''
  pendingImages.value = []
  await taskAssistantStore.send(props.taskId, { message: text, images })
}
function stop() {
  taskAssistantStore.stop(props.taskId)
}

function isUser(message) {
  return message?.role === 'user'
}

function agentSteps(message) {
  return Array.isArray(message?.agentSteps) ? message.agentSteps : []
}

function agentProgress(message) {
  return message?.agentProgress || { text: '', running: false }
}

function showAgentProgress(message) {
  return !isUser(message) && (agentSteps(message).length > 0 || message?.status === 'streaming')
}

function agentProgressText(message) {
  return agentProgress(message).text || (message?.status === 'streaming' ? '正在处理...' : '')
}

function timelineKey(message, index) {
  return message?.id || `${message?.role || 'message'}-${index}`
}

function toggleTimeline(message, index) {
  const key = timelineKey(message, index)
  openTimelines.value = { ...openTimelines.value, [key]: !openTimelines.value[key] }
}

function isTimelineOpen(message, index) {
  return Boolean(openTimelines.value[timelineKey(message, index)])
}

// 供父组件（点步骤卡「问AI」）聚焦输入框
function focusInput() {
  nextTick(() => inputRef.value?.focus?.())
}
defineExpose({ focusInput })
</script>

<template>
  <section class="assistant-panel">
    <header class="assistant-head">
      <div class="assistant-brand">
        <span class="assistant-mark"><el-icon><ChatDotRound /></el-icon></span>
        <span class="brand-copy">
          <small>CONTEXT-AWARE COPILOT</small>
          <b>AI 作业助手</b>
        </span>
      </div>
      <span class="assistant-online"><i /> ONLINE</span>
    </header>

    <div class="focus-control">
      <span class="focus-icon"><el-icon><Connection /></el-icon></span>
      <label>
        <span>当前上下文</span>
        <select v-model="s.focusedStepId">
          <option :value="null">整个任务</option>
          <option v-for="st in sortedSteps" :key="st.id" :value="st.id">
            第 {{ st.sortOrder }} 步 · {{ st.title }}
          </option>
        </select>
      </label>
    </div>

    <div ref="bodyRef" class="assistant-body">
      <div v-if="!s.messages.length" class="assistant-empty">
        <span class="empty-orbit">
          <i />
          <el-icon><ChatDotRound /></el-icon>
        </span>
        <span class="empty-kicker">READY FOR FIELD SUPPORT</span>
        <h3>已接入当前作业上下文</h3>
        <p>我会结合任务信息、步骤要求和现场证据回答。当前聚焦：<b>{{ focusedTitle }}</b></p>
        <div class="quick-prompts">
          <button v-for="p in quickPrompts" :key="p" type="button" @click="choosePrompt(p)">
            <el-icon><Promotion /></el-icon>
            {{ p }}
          </button>
        </div>
      </div>

      <article v-for="(m, i) in s.messages" :key="timelineKey(m, i)" class="message" :class="{ user: isUser(m) }">
        <div class="avatar" :class="{ user: isUser(m) }">
          <span v-if="isUser(m)">我</span>
          <el-icon v-else><ChatDotRound /></el-icon>
        </div>

        <div class="message-main">
          <div class="message-meta">
            <span>{{ isUser(m) ? '我' : '检修 AI 助手' }}</span>
            <span v-if="m.timestamp">{{ m.timestamp }}</span>
            <span v-if="m.status === 'streaming'" class="status">生成中</span>
            <span v-if="m.status === 'stopped'" class="status warn">已停止</span>
            <span v-if="m.status === 'error'" class="status danger">异常</span>
          </div>

          <div v-if="(m.images || []).length" class="b-imgs">
            <img v-for="(u, k) in m.images" :key="k" :src="u" alt="" />
          </div>

          <div class="bubble">
            <div v-if="m.content" class="b-text" v-html="renderText(m.content)" />
            <div v-if="m.status === 'streaming' && !m.content" class="thinking">
              <span />
              <span />
              <span />
            </div>
          </div>

          <div v-if="m.role === 'assistant' && (m.evidenceImages || []).length" class="b-evidence">
            <figure v-for="(item, k) in m.evidenceImages" :key="`${item.imageUrl}-${k}`">
              <img v-if="item.imageUrl" :src="item.imageUrl" :alt="item.caption || item.sectionTitle || '证据图片'" />
              <figcaption v-if="item.caption || item.sectionTitle || item.page">
                <span>{{ item.caption || item.sectionTitle }}</span>
                <small v-if="item.page">P{{ item.page }}</small>
              </figcaption>
            </figure>
          </div>

          <div v-if="showAgentProgress(m)" class="agent-progress" :class="{ running: agentProgress(m).running }">
            <button type="button" class="agent-progress-row" @click="toggleTimeline(m, i)">
              <span class="agent-progress-text">{{ agentProgressText(m) }}</span>
              <el-icon class="agent-progress-toggle">
                <ArrowUp v-if="isTimelineOpen(m, i)" />
                <ArrowDown v-else />
              </el-icon>
            </button>

            <div v-if="isTimelineOpen(m, i) && agentSteps(m).length" class="agent-timeline">
              <div
                v-for="step in agentSteps(m)"
                :key="step.id"
                class="agent-step"
                :class="`is-${step.status || 'done'}`"
              >
                <span class="agent-step-dot" />
                <div class="agent-step-body">
                  <div class="agent-step-title">{{ step.title }}</div>
                  <div v-if="step.detail" class="agent-step-detail">{{ step.detail }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>

    <footer class="assistant-input">
      <div v-if="pendingImages.length" class="pending-images">
        <div v-for="(u, i) in pendingImages" :key="i" class="pending-item">
          <img :src="u" :alt="`待发送图片 ${i + 1}`" />
          <button type="button" aria-label="移除图片" @click="removePending(i)">
            <el-icon><Close /></el-icon>
          </button>
        </div>
      </div>

      <div class="input-shell">
        <label class="attach-button" :class="{ busy: uploading }" title="附加现场照片">
          <input type="file" accept="image/*" multiple hidden @change="onPickFiles" />
          <el-icon><Picture /></el-icon>
          <span>{{ uploading ? '上传中' : '照片' }}</span>
        </label>
        <textarea
          ref="inputRef"
          v-model="input"
          rows="2"
          :placeholder="`询问${focusedStep ? '当前步骤' : '整个任务'}…`"
          @keydown.enter.exact.prevent="send"
        />
        <button
          v-if="!s.streaming"
          type="button"
          class="send-button"
          :disabled="!uploading && !input.trim() && !pendingImages.length"
          @click="send"
        >
          <el-icon><Position /></el-icon>
          {{ pendingSend ? '待发送' : '发送' }}
        </button>
        <button v-else type="button" class="send-button stop" @click="stop">停止</button>
      </div>
      <span class="input-hint">Enter 发送 · 可附加现场照片作为判断依据</span>
    </footer>
  </section>
</template>

<style scoped>
.assistant-panel {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--plaza-border);
  border-radius: 13px;
  background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
}

/* ===== 深色头部 ===== */
.assistant-head {
  display: flex;
  min-height: 72px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 14px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.25);
  color: var(--plaza-panel-bg);
  background:
    radial-gradient(circle at 83% 18%, var(--signal-soft), transparent 30%),
    linear-gradient(145deg, var(--plaza-heading), var(--plaza-heading));
}
.assistant-brand { display: flex; min-width: 0; align-items: center; gap: 10px; }
.brand-copy { display: flex; min-width: 0; flex-direction: column; }
.assistant-mark {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  place-items: center;
  border-radius: 10px;
  color: #fff;
  background: linear-gradient(145deg, var(--plaza-accent), var(--plaza-accent));
  box-shadow: 0 7px 18px var(--plaza-accent);
}
.assistant-brand small { color: var(--plaza-accent); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.1em; }
.assistant-brand b { margin-top: 2px; color: #fff; font-family: var(--font-display); font-size: 15px; }
.assistant-online { display: flex; align-items: center; gap: 5px; color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.08em; }
.assistant-online i { width: 6px; height: 6px; border-radius: 50%; background: #5e8c3e; box-shadow: 0 0 0 3px rgba(94, 140, 62, 0.18); }

/* ===== 上下文聚焦 ===== */
.focus-control {
  display: grid;
  min-height: 58px;
  flex: 0 0 auto;
  grid-template-columns: 32px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--plaza-border);
  background: var(--plaza-bg-card);
}
.focus-icon { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 8px; color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.focus-control label { display: flex; min-width: 0; flex-direction: column; }
.focus-control label > span { color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.11em; }
.focus-control select {
  width: 100%;
  margin-top: 3px;
  overflow: hidden;
  border: 0;
  outline: 0;
  color: var(--plaza-text);
  background: transparent;
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  cursor: pointer;
}

/* ===== 消息区 ===== */
.assistant-body {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  padding: 14px 12px;
  overflow-y: auto;
  background:
    radial-gradient(circle at 90% 0%, var(--plaza-accent-soft), transparent 24%),
    var(--plaza-panel-bg);
  scrollbar-color: var(--plaza-border-strong) transparent;
  scrollbar-width: thin;
}

.assistant-empty { display: flex; max-width: 310px; flex-direction: column; align-items: center; margin: auto; padding: 18px 8px; text-align: center; }
.empty-orbit {
  position: relative;
  display: grid;
  width: 64px;
  height: 64px;
  place-items: center;
  margin-bottom: 13px;
  border: 1px solid var(--plaza-border-strong);
  border-radius: 50%;
  color: var(--plaza-accent);
  background: var(--plaza-warning-soft);
  font-size: 23px;
}
.empty-orbit::after { position: absolute; inset: 7px; border: 1px dashed var(--plaza-accent-soft-strong); border-radius: 50%; content: ''; }
.empty-orbit i { position: absolute; top: 5px; right: 10px; width: 6px; height: 6px; border-radius: 50%; background: var(--plaza-accent); box-shadow: 0 0 0 4px var(--plaza-accent-soft); }
.empty-orbit .el-icon { position: relative; z-index: 1; }
.empty-kicker { color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.12em; }
.assistant-empty h3 { margin: 6px 0 6px; color: var(--plaza-heading); font-family: var(--font-display); font-size: 15px; }
.assistant-empty p { margin: 0; color: var(--plaza-text-muted); font-size: 11px; line-height: 1.7; }
.assistant-empty p b { color: var(--plaza-accent); }

.quick-prompts { display: flex; width: 100%; flex-direction: column; gap: 6px; margin-top: 14px; }
.quick-prompts button {
  display: flex;
  min-height: 37px;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  font-size: 10px;
  text-align: left;
  cursor: pointer;
}
.quick-prompts button .el-icon { flex: 0 0 auto; color: var(--plaza-accent); }
.quick-prompts button:hover { color: var(--plaza-accent); border-color: var(--plaza-accent); background: var(--plaza-accent-soft); }

/* ===== 消息气泡（沿用 fix） ===== */
.message { width: 100%; min-width: 0; display: flex; gap: 9px; }
.message.user { flex-direction: row-reverse; }
.avatar { width: 30px; height: 30px; flex: 0 0 30px; border-radius: 8px; display: grid; place-items: center; background: var(--plaza-heading); color: var(--plaza-bg-card); font-size: 12px; font-weight: 700; }
.avatar.user { background: var(--plaza-accent); }
.message-main { min-width: 0; width: min(100%, 620px); display: flex; flex-direction: column; gap: 5px; }
.message.user .message-main { width: auto; max-width: min(84%, 520px); align-items: flex-end; }
.message-meta { display: flex; align-items: center; gap: 7px; color: var(--plaza-text-muted); font-size: 11.5px; }
.status { color: var(--plaza-accent); font-weight: 600; }
.status.warn { color: #df9226; }
.status.danger { color: #c5402c; }
.bubble { width: 100%; min-width: 0; padding: 10px 12px; border: 1px solid var(--plaza-border); border-radius: 8px; background: var(--plaza-bg-card); color: var(--plaza-text); box-shadow: 0 1px 8px rgba(120, 80, 50, 0.06); font-size: 14px; line-height: 1.65; word-break: break-word; overflow-wrap: anywhere; }
.message.user .bubble { width: fit-content; max-width: 100%; border-color: transparent; background: var(--plaza-accent); color: #fff; }
.b-text { display: block; }
.b-imgs { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 2px; }
.b-imgs img { width: 64px; height: 64px; object-fit: cover; border-radius: 6px; }
.b-evidence { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 6px; margin-top: 3px; }
.b-evidence figure { margin: 0; overflow: hidden; border: 1px solid var(--plaza-border); border-radius: 7px; background: #fff; }
.b-evidence img { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover; }
.b-evidence figcaption { display: flex; justify-content: space-between; gap: 5px; padding: 5px 6px; color: var(--plaza-text); font-size: 11px; line-height: 1.35; }
.b-evidence figcaption span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.b-evidence figcaption small { flex-shrink: 0; color: var(--plaza-accent); font-weight: 700; }
.agent-progress { width: 100%; max-width: 100%; color: var(--plaza-text); font-size: 12px; }
.agent-progress-row { position: relative; width: 100%; min-height: 24px; padding: 2px 0; border: 0; background: transparent; color: inherit; display: flex; align-items: center; justify-content: space-between; gap: 10px; text-align: left; cursor: pointer; overflow: hidden; }
.agent-progress.running .agent-progress-row::after { content: ''; position: absolute; inset: 0 auto 0 -42%; width: 42%; pointer-events: none; background: linear-gradient(90deg, transparent, var(--plaza-accent-soft-strong), transparent); animation: agent-sweep 1.5s linear infinite; }
.agent-progress-text { position: relative; z-index: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-progress-toggle { position: relative; z-index: 1; flex: 0 0 auto; color: var(--plaza-text-muted); font-size: 13px; }
.agent-timeline { margin-top: 5px; padding: 2px 0 2px 12px; border-left: 1px solid rgba(179, 166, 146, 0.4); }
.agent-step { position: relative; display: grid; grid-template-columns: 9px minmax(0, 1fr); gap: 7px; padding: 3px 0 7px; }
.agent-step-dot { width: 7px; height: 7px; margin-top: 5px; margin-left: -16px; border-radius: 999px; background: var(--plaza-text-muted); box-shadow: 0 0 0 3px #fff; }
.agent-step.is-running .agent-step-dot { background: var(--plaza-accent); }
.agent-step.is-warn .agent-step-dot { background: #df9226; }
.agent-step.is-error .agent-step-dot { background: #c5402c; }
.agent-step-body { min-width: 0; }
.agent-step-title { color: var(--plaza-text); font-size: 12px; font-weight: 650; line-height: 1.45; }
.agent-step-detail { margin-top: 1px; color: var(--plaza-text-muted); font-size: 11.5px; line-height: 1.45; word-break: break-word; }
.thinking { display: flex; gap: 4px; align-items: center; justify-content: center; min-width: 52px; height: 24px; }
.thinking span { width: 7px; height: 7px; border-radius: 50%; background: var(--plaza-text-muted); animation: pulse 1.1s infinite; }
.thinking span:nth-child(2) { animation-delay: 0.16s; }
.thinking span:nth-child(3) { animation-delay: 0.32s; }
@keyframes agent-sweep { 0% { transform: translateX(0); } 100% { transform: translateX(340%); } }
@keyframes pulse { 0%, 80%, 100% { opacity: 0.35; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }

/* ===== 输入区 ===== */
.assistant-input { flex: 0 0 auto; padding: 10px; border-top: 1px solid var(--plaza-border); background: var(--plaza-bg-card); }
.pending-images { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.pending-item { position: relative; width: 48px; height: 48px; overflow: hidden; border: 1px solid var(--plaza-border-strong); border-radius: 7px; }
.pending-item img { width: 100%; height: 100%; object-fit: cover; }
.pending-item button { position: absolute; top: 2px; right: 2px; display: grid; width: 18px; height: 18px; place-items: center; border: 0; border-radius: 50%; color: #fff; background: rgba(0, 0, 0, 0.72); cursor: pointer; }
.input-shell { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; align-items: stretch; gap: 7px; }
.attach-button {
  display: flex;
  min-height: 50px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--plaza-accent-soft-strong);
  border-radius: 8px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-size: 8px;
  font-weight: 750;
  cursor: pointer;
}
.attach-button .el-icon { font-size: 16px; }
.attach-button:hover { background: var(--plaza-accent-soft-strong); }
.attach-button.busy { opacity: 0.6; pointer-events: none; }
.input-shell textarea {
  width: 100%;
  min-height: 50px;
  padding: 8px 9px;
  resize: none;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  outline: 0;
  color: var(--plaza-text);
  background: var(--plaza-panel-bg);
  font-family: inherit;
  font-size: 11px;
  line-height: 1.55;
}
.input-shell textarea:focus { border-color: var(--plaza-accent); background: #fff; box-shadow: 0 0 0 3px var(--plaza-accent-soft); }
.send-button {
  display: inline-flex;
  min-width: 67px;
  min-height: 50px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: #fff;
  background: var(--plaza-accent-grad);
  box-shadow: 0 6px 16px var(--plaza-accent-soft-strong);
  font-size: 10px;
  font-weight: 800;
  cursor: pointer;
}
.send-button:disabled { opacity: 0.45; cursor: not-allowed; }
.send-button.stop { color: #fff; border-color: #c5402c; background: #c5402c; box-shadow: none; }
.input-hint { display: block; margin-top: 6px; color: var(--plaza-text-muted); font-size: 8px; text-align: right; }

@media (max-width: 680px) {
  .input-shell { grid-template-columns: 44px minmax(0, 1fr); }
  .send-button { grid-column: 1 / -1; min-height: 42px; }
}
@media (prefers-reduced-motion: reduce) {
  .thinking span,
  .agent-progress.running .agent-progress-row::after { animation: none; }
}
</style>
