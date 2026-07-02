<script setup>
import { computed, nextTick, onActivated, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatDotRound,
  ChatLineRound,
  Delete,
  FolderOpened,
  Microphone,
  Mute,
  Plus,
  Refresh,
  Tools,
} from '@element-plus/icons-vue'
import AIBottomInput from '@/components/AIBottomInput.vue'
import AgentActivityPanel from '@/components/ai/AgentActivityPanel.vue'
import ChatMessage from '@/components/ai/ChatMessage.vue'
import SessionSidebar from '@/components/ai/SessionSidebar.vue'
import { aiChatStore } from '@/stores/aiChatStore'
import { useSpeech } from '@/composables/useSpeech'

// 自动朗读开关（AI 回复生成完自动念），状态共享自全局语音单例
const { state: speechState, setAutoRead } = useSpeech()

const AGENT_PANEL_EVENTS = new Set([
  'tool',
  'retrieval_start',
  'retrieval_route',
  'retrieval_quality',
  'retrieval_supplement',
  'retrieval_expand',
  'retrieval_done',
  'verification',
])

const props = defineProps({
  storageKey: {
    type: String,
    default: 'ai-sessions',
  },
  welcomeMessage: {
    type: String,
    default: '您好！我是 AI 助手，可以帮助您进行知识库检索、案例分析和检修任务处理等操作。有什么可以帮助您的吗？',
  },
})

const route = useRoute()
const router = useRouter()
const state = aiChatStore.get(props.storageKey, props.welcomeMessage)
const modeStorageKey = `${props.storageKey}:mode`
const showHistory = ref(false)
const bodyRef = ref(null)
const userScrolledUp = ref(false)
const storedMode = localStorage.getItem(modeStorageKey)
const currentMode = ref(storedMode === 'chat' ? 'chat' : 'maintenance')
const selectedAgentMessage = ref(null)
const autoAgentMessageId = ref('')
const dismissedAgentMessageId = ref('')
const agentFocus = ref({ toolId: '', nonce: 0 })

const userInitial = computed(() => {
  const fallback = route.path.startsWith('/admin') ? 'A' : 'U'
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || '{}')
    return userInfo.name ? userInfo.name[0] : fallback
  } catch {
    return fallback
  }
})

const currentSession = computed(() =>
  aiChatStore.currentSession(props.storageKey, currentMode.value),
)
const modeSessions = computed(() =>
  state.sessions.filter((session) => (session.mode || 'maintenance') === currentMode.value),
)
const messages = computed(() => currentSession.value?.messages || [])

const isStreaming = computed(() => currentSession.value?.streaming || false)
const latestAssistantMessage = computed(() =>
  [...messages.value].reverse().find((message) => message.role === 'assistant') || null,
)

const showStopBtn = computed(() => latestAssistantMessage.value?.status === 'streaming')

function hasAgentPanelData(message) {
  return message?.mode !== 'chat'
    && (message?.agentSteps || []).some((step) => AGENT_PANEL_EVENTS.has(step.event))
}

const currentAgentMessage = computed(() => {
  const message = latestAssistantMessage.value
  return hasAgentPanelData(message) ? message : null
})

const panelMessage = computed(() =>
  selectedAgentMessage.value
  || (currentAgentMessage.value?.id === autoAgentMessageId.value ? currentAgentMessage.value : null),
)

const showAgentPanel = computed(() =>
  currentMode.value === 'maintenance'
  && !!panelMessage.value
  && dismissedAgentMessageId.value !== panelMessage.value.id,
)

function resetAgentPanel() {
  selectedAgentMessage.value = null
  autoAgentMessageId.value = ''
  dismissedAgentMessageId.value = ''
}

function openAgentPanel(message, toolId = '') {
  if (
    currentMode.value !== 'maintenance'
    || message?.mode === 'chat'
    || message?.status === 'streaming'
  ) return
  selectedAgentMessage.value = message
  dismissedAgentMessageId.value = ''
  agentFocus.value = { toolId: toolId || '', nonce: agentFocus.value.nonce + 1 }
}

function closeAgentPanel() {
  if (panelMessage.value) dismissedAgentMessageId.value = panelMessage.value.id
  selectedAgentMessage.value = null
}

const maintenancePrompts = [
  { title: '故障诊断', text: '循环水泵运行时出现异常振动，应该优先排查哪些部位？' },
  { title: '案例检索', text: '根据故障现象帮我检索相似维修案例。' },
  { title: '步骤整理', text: '请把当前问题整理成现场检修步骤和注意事项。' },
  { title: '原因验证', text: '某部件反复过热，可能原因和验证方法是什么？' },
]
const chatPrompts = [
  { title: '日常交流', text: '你好，简单介绍一下你能提供哪些帮助。' },
  { title: '内容整理', text: '帮我把一段复杂描述整理得更清晰。' },
  { title: '思路讨论', text: '我有一个想法，想和你一起讨论并完善。' },
  { title: '快速问答', text: '请用简洁的方式回答我的问题。' },
]
const quickPrompts = computed(() => (currentMode.value === 'chat' ? chatPrompts : maintenancePrompts))
const modeSubtitle = computed(() =>
  currentMode.value === 'chat' ? '轻量交流与通用问答' : '设备检修知识分析与作业建议',
)
const emptyDescription = computed(() =>
  currentMode.value === 'chat'
    ? 'Chat 模式用于轻量交流与通用问答，回答更直接简洁。'
    : '描述设备、故障现象、现场图片或已执行步骤，我会结合知识库、案例与知识图谱，把建议拆成可执行的排查路径。',
)

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = bodyRef.value
    if (!el) return
    if (force || !userScrolledUp.value) {
      el.scrollTop = el.scrollHeight
      userScrolledUp.value = false
    }
  })
}

function handleScroll() {
  const el = bodyRef.value
  if (!el) return
  userScrolledUp.value = el.scrollHeight - el.scrollTop - el.clientHeight > 80
}

function handleNewSession() {
  aiChatStore.newSession(props.storageKey, currentMode.value)
  resetAgentPanel()
  scrollToBottom(true)
}

function handleSelectSession(sessionId) {
  aiChatStore.selectSession(props.storageKey, sessionId, currentMode.value)
  resetAgentPanel()
  if (window.innerWidth <= 1180) showHistory.value = false
  scrollToBottom(true)
}

function handleDeleteSession(sessionId) {
  aiChatStore.deleteSession(props.storageKey, sessionId, currentMode.value)
  resetAgentPanel()
  scrollToBottom(true)
}

function handleClear() {
  aiChatStore.clearCurrent(props.storageKey, currentMode.value)
  resetAgentPanel()
  scrollToBottom(true)
}

function handleSend(payload) {
  resetAgentPanel()
  aiChatStore.send(props.storageKey, { ...payload, mode: currentMode.value })
  scrollToBottom(true)
}

function handleStop() {
  aiChatStore.stop(props.storageKey, currentSession.value?.id)
}

function sendQuickPrompt(prompt) {
  handleSend({ text: prompt, files: [], thinking: currentMode.value === 'maintenance' })
}

function changeMode(mode) {
  if (isStreaming.value || currentMode.value === mode) return
  currentMode.value = mode
  localStorage.setItem(modeStorageKey, mode)
  aiChatStore.currentSession(props.storageKey, mode)
  resetAgentPanel()
  scrollToBottom(true)
}

watch(
  () => messages.value.map((message) => `${message.id}:${message.content}:${message.status}:${(message.evidenceImages || []).length}:${(message.agentSteps || []).length}:${message.agentProgress?.text || ''}`).join('|'),
  () => scrollToBottom(),
)

// 切换到新的助手消息时重置面板
watch(
  () => latestAssistantMessage.value?.id,
  (next, previous) => {
    if (next !== previous) resetAgentPanel()
  },
)

// 回答完成（streaming → done）且含有 Agent 检索事件时，自动展开侧栏面板
watch(
  () => [
    latestAssistantMessage.value?.id,
    latestAssistantMessage.value?.status,
    (latestAssistantMessage.value?.agentSteps || [])
      .filter((step) => AGENT_PANEL_EVENTS.has(step.event))
      .length,
  ],
  ([messageId, status, eventCount], previous = []) => {
    const previousStatus = previous[1]
    if (
      messageId
      && status === 'done'
      && previousStatus === 'streaming'
      && Number(eventCount) > 0
      && latestAssistantMessage.value?.mode !== 'chat'
      && dismissedAgentMessageId.value !== messageId
    ) {
      autoAgentMessageId.value = messageId
      dismissedAgentMessageId.value = ''
    }
  },
)

// 从工作台「AI 助手」携带 ?q= 跳转过来时，自动发送该问题
let autoSendLock = false
function maybeAutoSendFromQuery() {
  if (autoSendLock) return
  const q = String(route.query.q || '').trim()
  if (!q) return
  autoSendLock = true
  handleSend({ text: q, files: [], thinking: currentMode.value === 'maintenance' })
  router.replace({ query: {} }).finally(() => { autoSendLock = false })
}

onMounted(() => {
  aiChatStore.load(props.storageKey, props.welcomeMessage, currentMode.value)
  showHistory.value = window.innerWidth > 1180
  scrollToBottom(true)
  maybeAutoSendFromQuery()
})

// keep-alive 复用实例时，再次携带 ?q= 进入也能触发发送
onActivated(() => {
  maybeAutoSendFromQuery()
})
</script>

<template>
  <section
    class="ai-chat-page"
    :class="{ 'history-visible': showHistory, 'agent-visible': showAgentPanel }"
  >
    <SessionSidebar
      :open="showHistory"
      :sessions="modeSessions"
      :current-session-id="currentSession?.id || ''"
      :mode="currentMode"
      @new="handleNewSession"
      @select="handleSelectSession"
      @delete="handleDeleteSession"
    />

    <div class="chat-workspace">
      <div class="chat-main">
        <header class="chat-header">
        <div class="title-block">
          <div class="title-mark">
            <el-icon><ChatDotRound /></el-icon>
          </div>
          <div class="title-copy">
            <span>FIELD INTELLIGENCE</span>
            <h1>AI 智能助手</h1>
            <p>{{ modeSubtitle }}</p>
          </div>
        </div>

        <div class="header-controls">
          <div class="mode-switch" aria-label="AI 对话模式">
            <button
              type="button"
              :class="{ active: currentMode === 'chat' }"
              :disabled="isStreaming"
              @click="changeMode('chat')"
            >
              <el-icon><ChatLineRound /></el-icon>
              <span>Chat</span>
            </button>
            <button
              type="button"
              :class="{ active: currentMode === 'maintenance' }"
              :disabled="isStreaming"
              @click="changeMode('maintenance')"
            >
              <el-icon><Tools /></el-icon>
              <span>检修</span>
            </button>
          </div>

          <div class="header-actions">
            <button
              type="button"
              :title="speechState.autoRead ? '自动朗读：开（点击关闭）' : '自动朗读：关（点击开启）'"
              :class="{ active: speechState.autoRead }"
              @click="setAutoRead(!speechState.autoRead)"
            >
              <el-icon><Microphone v-if="speechState.autoRead" /><Mute v-else /></el-icon>
            </button>
            <button type="button" title="会话记录" :class="{ active: showHistory }" @click="showHistory = !showHistory">
              <el-icon><FolderOpened /></el-icon>
            </button>
            <button type="button" title="新对话" @click="handleNewSession">
              <el-icon><Plus /></el-icon>
            </button>
            <button type="button" title="清空当前对话" @click="handleClear">
              <el-icon><Delete /></el-icon>
            </button>
          </div>
        </div>
      </header>

      <main ref="bodyRef" class="messages-container" @scroll="handleScroll">
        <div class="message-stream">
          <section v-if="messages.length <= 1" class="empty-state">
            <div class="empty-heading">
              <div class="empty-mark">
                <el-icon><ChatDotRound /></el-icon>
              </div>
              <div>
                <span>READY FOR FIELD SUPPORT</span>
                <h2>今天要解决什么检修问题？</h2>
              </div>
            </div>
            <p>{{ emptyDescription }}</p>
            <div class="prompt-grid">
              <button
                v-for="prompt in quickPrompts"
                :key="prompt.text"
                type="button"
                :disabled="isStreaming"
                @click="sendQuickPrompt(prompt.text)"
              >
                <span>{{ prompt.title }}</span>
                <b>{{ prompt.text }}</b>
              </button>
            </div>
          </section>

          <ChatMessage
            v-for="message in messages"
            :key="message.id"
            :message="message"
            :user-initial="userInitial"
            :agent-enabled="currentMode === 'maintenance'"
            @open-agent="openAgentPanel"
          />
        </div>
      </main>

      <transition name="fade">
        <button v-if="userScrolledUp" type="button" class="latest-btn" @click="scrollToBottom(true)">
          <el-icon><Refresh /></el-icon>
          <span>回到最新</span>
        </button>
      </transition>

        <AIBottomInput
          :generating="isStreaming"
          @send="handleSend"
          @stop="handleStop"
        />
      </div>

      <Transition name="agent-panel">
        <AgentActivityPanel
          v-if="showAgentPanel"
          :message="panelMessage"
          :focus="agentFocus"
          @close="closeAgentPanel"
        />
      </Transition>
    </div>
  </section>
</template>

<style scoped>
.ai-chat-page {
  position: relative;
  display: flex;
  width: 100%;
  height: 100%;
  min-height: 0;
  flex-direction: row;
  overflow: hidden;
  color: var(--plaza-text);
  background: var(--plaza-bg);
}

.chat-workspace {
  position: relative;
  display: flex;
  min-width: 0;
  height: 100%;
  flex: 1 1 auto;
  overflow: hidden;
}

.chat-main {
  position: relative;
  display: flex;
  min-width: 0;
  height: 100%;
  flex: 1 1 auto;
  flex-direction: column;
  overflow: hidden;
}

/* ===== 深色头部 ===== */
.chat-header {
  display: flex;
  min-height: 78px;
  flex: 0 0 78px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 13px 18px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  color: var(--plaza-heading);
  background:
    radial-gradient(circle at 82% 20%, var(--plaza-accent-soft), transparent 26%),
    linear-gradient(145deg, var(--plaza-bg-card), var(--plaza-panel-bg));
}

.title-block { display: flex; min-width: 0; align-items: center; gap: 11px; }
.title-mark {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  place-items: center;
  border-radius: 10px;
  color: #fff;
  background: var(--plaza-accent-grad);
  box-shadow: 0 8px 18px var(--plaza-accent-soft-strong);
  font-size: 19px;
}
.title-copy { display: flex; min-width: 0; flex-direction: column; }
.title-copy span { color: var(--plaza-accent); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.12em; }
.title-copy h1 { margin: 1px 0 0; color: var(--plaza-heading); font-family: var(--font-display); font-size: 18px; font-weight: 800; line-height: 1.2; }
.title-copy p { margin-top: 2px; overflow: hidden; color: var(--plaza-text-muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }

.header-controls { display: flex; align-items: center; gap: 10px; }
.mode-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 3px;
  padding: 3px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 9px;
  background: rgba(0, 0, 0, 0.04);
}
.mode-switch button {
  display: inline-flex;
  min-width: 74px;
  height: 30px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 10px;
  border: 0;
  border-radius: 7px;
  color: var(--plaza-text-muted);
  background: transparent;
  font-size: 9px;
  font-weight: 700;
  cursor: pointer;
}
.mode-switch button:hover { color: var(--plaza-heading); }
.mode-switch button.active { color: #fff; background: var(--plaza-accent-grad); box-shadow: 0 5px 14px var(--plaza-accent-soft-strong); }
.mode-switch button:disabled { cursor: not-allowed; opacity: 0.55; }

.header-actions { display: flex; gap: 7px; }
.header-actions button {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  color: var(--plaza-text-muted);
  background: rgba(0, 0, 0, 0.04);
  cursor: pointer;
}
.header-actions button:hover,
.header-actions button.active { color: var(--plaza-accent); border-color: var(--plaza-accent-soft-strong); background: var(--plaza-accent-soft); }

/* ===== 消息区 ===== */
.messages-container {
  min-height: 0;
  flex: 1;
  padding: 19px 22px 23px;
  overflow-y: auto;
  background:
    radial-gradient(circle at 86% 0%, var(--plaza-accent-soft), transparent 23%),
    var(--plaza-bg);
}
.message-stream {
  display: flex;
  width: min(920px, 100%);
  min-height: 100%;
  flex-direction: column;
  gap: 16px;
  margin: 0 auto;
}

.empty-state {
  width: 100%;
  margin: auto;
  padding: clamp(22px, 3vw, 34px);
  overflow: hidden;
  border: 1px solid var(--plaza-border);
  border-radius: 15px;
  background:
    radial-gradient(circle at 95% 8%, var(--plaza-accent-soft), transparent 24%),
    var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
}
.empty-heading { display: flex; align-items: center; gap: 13px; }
.empty-mark {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border-radius: 12px;
  color: #fff;
  background: var(--plaza-accent-grad);
  box-shadow: 0 8px 20px var(--plaza-accent-soft-strong);
  font-size: 21px;
}
.empty-heading span { color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 8px; font-weight: 800; letter-spacing: 0.11em; }
.empty-heading h2 { margin: 3px 0 0; color: var(--plaza-heading); font-family: var(--font-display); font-size: clamp(20px, 2.1vw, 28px); font-weight: 800; }
.empty-state > p { max-width: 680px; margin: 14px 0 0; color: var(--plaza-text-muted); font-size: 12px; line-height: 1.8; }

.prompt-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 19px; }
.prompt-grid button {
  display: flex;
  min-height: 66px;
  flex-direction: column;
  gap: 5px;
  padding: 11px 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  color: var(--plaza-text);
  background: var(--plaza-bg-input);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}
.prompt-grid button:hover { border-color: var(--plaza-accent); background: var(--plaza-accent-soft); transform: translateY(-1px); }
.prompt-grid button:disabled { opacity: 0.55; cursor: not-allowed; transform: none; }
.prompt-grid span { color: var(--plaza-accent); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.08em; }
.prompt-grid b { color: var(--plaza-text); font-size: 11px; font-weight: 650; line-height: 1.55; }

.latest-btn {
  position: absolute;
  bottom: 126px;
  left: 50%;
  z-index: 10;
  display: inline-flex;
  height: 34px;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic-hover);
  cursor: pointer;
  transform: translateX(-50%);
}

.fade-enter-active,
.fade-leave-active { transition: opacity 0.16s ease; }
.fade-enter-from,
.fade-leave-to { opacity: 0; }

.agent-panel-enter-active {
  transition: opacity 0.28s ease, transform 0.38s cubic-bezier(0.22, 1, 0.36, 1);
}
.agent-panel-leave-active {
  transition: opacity 0.18s ease, transform 0.25s ease;
}
.agent-panel-enter-from,
.agent-panel-leave-to {
  opacity: 0;
  transform: translateX(24px);
}

@media (max-width: 1080px) {
  .agent-visible .chat-main { filter: saturate(0.96); }
}
@media (prefers-reduced-motion: reduce) {
  .agent-panel-enter-active,
  .agent-panel-leave-active { transition: none; }
}

@media (max-width: 860px) {
  .messages-container { padding: 14px 12px 18px; }
  .chat-header { padding-inline: 12px; }
  .title-copy p { display: none; }
  .header-controls { gap: 6px; }
  .mode-switch button { min-width: 36px; padding-inline: 8px; }
  .mode-switch button span { display: none; }
  .prompt-grid { grid-template-columns: 1fr; }
}
</style>
