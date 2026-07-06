import { reactive } from 'vue'
import { aiChatStream } from '@/api/aiChat'
import { flushSseEvents, readSseEvents } from '@/utils/sse'
import { AI_FALLBACK_MESSAGE, isTechnicalErrorText, sanitizeAiContent, sanitizeAiErrorMessage } from '@/utils/aiErrorFallback'
import {
  createAgentTimelineStep,
  createInitialAgentProgress,
  createProgressSummary,
  isAgentTimelineEvent,
} from '@/utils/agentTimeline'

const MAX_SESSIONS = 20
const MODES = ['chat', 'maintenance']

const states = reactive({})
const controllers = {}

function normalizeMode(mode) {
  return mode === 'chat' ? 'chat' : 'maintenance'
}

function createSessionId() {
  return `${Date.now()}${Math.floor(Math.random() * 1000).toString().padStart(3, '0')}`
}

function numericSessionId(sessionId) {
  const match = String(sessionId || '').match(/^\d+/)
  return match ? match[0] : createSessionId()
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function createWelcomeMessage(content) {
  return {
    id: `${Date.now()}-welcome`,
    role: 'assistant',
    content,
    images: [],
    evidenceImages: [],
    diagnosisItems: [],
    diagnosticFollowUp: null,
    timestamp: nowTime(),
    status: 'done',
    agentSteps: [],
    agentProgress: { text: '', running: false },
  }
}

function inferSessionMode(session) {
  if (MODES.includes(session?.mode)) return session.mode
  const id = String(session?.id || '')
  if (id.endsWith('-chat')) return 'chat'
  if (id.endsWith('-maintenance')) return 'maintenance'
  const messages = Array.isArray(session?.messages) ? session.messages : []
  if (messages.some((message) => message?.mode === 'chat')) return 'chat'
  if (messages.some((message) => message?.mode === 'maintenance')) return 'maintenance'
  if (messages.some((message) => (message?.agentSteps || []).length || (message?.evidenceImages || []).length)) {
    return 'maintenance'
  }
  return 'maintenance'
}

function normalizeSession(session) {
  const mode = inferSessionMode(session)
  const id = String(session?.id || createSessionId())
  const normalized = { ...session, id, mode }
  if (!/^\d+$/.test(id) && !normalized.backendSessionId) {
    normalized.backendSessionId = numericSessionId(id)
  }
  return normalized
}

function createSession(welcomeMessage, mode = 'maintenance') {
  const sessionMode = normalizeMode(mode)
  return {
    id: createSessionId(),
    mode: sessionMode,
    title: '新对话',
    updatedAt: Date.now(),
    messages: [createWelcomeMessage(welcomeMessage)],
  }
}

function safeParse(value, fallback) {
  try {
    return JSON.parse(value)
  } catch {
    return fallback
  }
}

function ensure(storageKey, welcomeMessage) {
  if (!states[storageKey]) {
    states[storageKey] = {
      storageKey,
      welcomeMessage,
      sessions: [],
      currentSessionIds: { chat: '', maintenance: '' },
      currentSessionId: '',
      streaming: false,
      loading: false,
      loaded: false,
    }
  }
  if (!states[storageKey].currentSessionIds) {
    states[storageKey].currentSessionIds = { chat: '', maintenance: states[storageKey].currentSessionId || '' }
  }
  if (welcomeMessage && !states[storageKey].welcomeMessage) {
    states[storageKey].welcomeMessage = welcomeMessage
  }
  return states[storageKey]
}

function syncLegacyCurrentSessionId(state, mode) {
  state.currentSessionId = state.currentSessionIds[normalizeMode(mode)] || ''
}

function currentSession(state, mode = 'maintenance') {
  const normalizedMode = normalizeMode(mode)
  const sessionId = state.currentSessionIds[normalizedMode]
  return state.sessions.find((session) => session.id === sessionId && inferSessionMode(session) === normalizedMode)
}

function ensureModeSession(state, mode = 'maintenance') {
  const normalizedMode = normalizeMode(mode)
  let session = currentSession(state, normalizedMode)
  if (!session) {
    session = state.sessions.find((item) => inferSessionMode(item) === normalizedMode)
  }
  if (!session) {
    session = createSession(state.welcomeMessage, normalizedMode)
    state.sessions = [session, ...state.sessions]
  }
  state.currentSessionIds[normalizedMode] = session.id
  syncLegacyCurrentSessionId(state, normalizedMode)
  return session
}

function persist(state) {
  const data = state.sessions.slice(0, MAX_SESSIONS)
  state.sessions = data
  localStorage.setItem(state.storageKey, JSON.stringify(data))
}

function touchSession(state, session) {
  session.mode = inferSessionMode(session)
  session.updatedAt = Date.now()
  const firstUser = session.messages.find((message) => message.role === 'user')
  session.title = firstUser?.content?.slice(0, 32) || (firstUser?.images?.length ? '图片对话' : '新对话')
  state.sessions = [session, ...state.sessions.filter((item) => item.id !== session.id)]
  state.currentSessionIds[session.mode] = session.id
  syncLegacyCurrentSessionId(state, session.mode)
  persist(state)
}

async function normalizeImages(files) {
  const images = await Promise.all(
    (files || [])
      .filter((file) => file.type === 'image' && file.status !== 'error')
      .map(async (file) => {
        try {
          if (file.uploadPromise && file.status === 'uploading') {
            await file.uploadPromise
          }
          return file.url
        } catch {
          return ''
        }
      }),
  )

  return images
    .filter((url) => typeof url === 'string' && url && !url.startsWith('blob:') && !url.startsWith('data:'))
}

export const aiChatStore = {
  get(storageKey, welcomeMessage) {
    return ensure(storageKey, welcomeMessage)
  },

  load(storageKey, welcomeMessage, mode = 'maintenance') {
    const state = ensure(storageKey, welcomeMessage)
    if (state.loaded) {
      ensureModeSession(state, mode)
      persist(state)
      return state
    }

    state.loading = true
    const stored = safeParse(localStorage.getItem(storageKey) || '[]', [])
    state.sessions = Array.isArray(stored) && stored.length
      ? stored.map((session) => normalizeSession(session))
      : []
    state.currentSessionIds = { chat: '', maintenance: '' }
    state.sessions.forEach((session) => {
      const sessionMode = inferSessionMode(session)
      if (!state.currentSessionIds[sessionMode]) state.currentSessionIds[sessionMode] = session.id
    })
    state.loaded = true
    state.loading = false
    ensureModeSession(state, mode)
    persist(state)
    return state
  },

  newSession(storageKey, mode = 'maintenance') {
    const state = ensure(storageKey)
    const sessionMode = normalizeMode(mode)
    const session = createSession(state.welcomeMessage, sessionMode)
    state.sessions = [session, ...state.sessions]
    state.currentSessionIds[sessionMode] = session.id
    syncLegacyCurrentSessionId(state, sessionMode)
    persist(state)
  },

  selectSession(storageKey, sessionId, mode = 'maintenance') {
    const state = ensure(storageKey)
    const sessionMode = normalizeMode(mode)
    if (state.sessions.some((session) => session.id === sessionId && inferSessionMode(session) === sessionMode)) {
      state.currentSessionIds[sessionMode] = sessionId
      syncLegacyCurrentSessionId(state, sessionMode)
    }
  },

  deleteSession(storageKey, sessionId, mode = 'maintenance') {
    const state = ensure(storageKey)
    const sessionMode = normalizeMode(mode)
    state.sessions = state.sessions.filter((session) => session.id !== sessionId)
    if (state.currentSessionIds[sessionMode] === sessionId) {
      const nextSession = state.sessions.find((session) => inferSessionMode(session) === sessionMode)
      state.currentSessionIds[sessionMode] = nextSession?.id || ''
    }
    ensureModeSession(state, sessionMode)
    persist(state)
  },

  clearCurrent(storageKey, mode = 'maintenance') {
    const state = ensure(storageKey)
    const session = ensureModeSession(state, mode)
    if (!session) return
    session.messages = [createWelcomeMessage(state.welcomeMessage)]
    touchSession(state, session)
  },

  currentSession(storageKey, mode = 'maintenance') {
    const state = ensure(storageKey)
    const session = ensureModeSession(state, mode)
    return session
  },

  async send(storageKey, { text, files = [], thinking = false, mode = 'maintenance', context = undefined }) {
    const state = ensure(storageKey)
    const sessionMode = normalizeMode(mode)
    const session = ensureModeSession(state, sessionMode)
    const trimmedText = (text || '').trim()
    if (!session || state.streaming || (!trimmedText && !files.length)) return
    const content = trimmedText

    const controller = new AbortController()
    controllers[storageKey] = controller
    state.streaming = true
    let fullContent = ''
    let typeTimer = null
    let assistant = null

    const startTypewriter = () => {
      if (typeTimer || !assistant) return
      typeTimer = setInterval(() => {
        if (assistant.content.length < fullContent.length) {
          assistant.content = fullContent.slice(0, assistant.content.length + 2)
        }
        if (assistant.content.length >= fullContent.length && typeTimer) {
          clearInterval(typeTimer)
          typeTimer = null
        }
      }, 24)
    }

    const waitForTypewriter = () => new Promise((resolve) => {
      const check = setInterval(() => {
        if (assistant.content.length >= fullContent.length) {
          clearInterval(check)
          resolve()
        }
      }, 24)
    })

    try {
      const requestImages = await normalizeImages(files)
      if (!trimmedText && !requestImages.length) {
        throw new Error('图片上传失败，无法发送给 AI')
      }

      session.messages.push({
        id: `${Date.now()}-user`,
        role: 'user',
        content,
        images: requestImages,
        evidenceImages: [],
        diagnosticFollowUp: null,
        mode: sessionMode,
        timestamp: nowTime(),
        status: 'done',
        agentSteps: [],
        agentProgress: { text: '', running: false },
      })

      assistant = reactive({
        id: `${Date.now()}-assistant`,
        role: 'assistant',
        content: '',
        images: [],
        evidenceImages: [],
        diagnosisItems: [],
        diagnosticFollowUp: null,
        mode: sessionMode,
        timestamp: nowTime(),
        status: 'streaming',
        agentSteps: [],
        agentProgress: createInitialAgentProgress(),
        latencyMs: 0,
      })
      session.messages.push(assistant)
      touchSession(state, session)

      const response = await aiChatStream({
        sessionId: numericSessionId(session.backendSessionId || session.id),
        message: content,
        images: requestImages,
        thinking,
        context,
      }, controller.signal)

      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      if (!response.body) throw new Error('响应体为空')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamCompleted = false
      const handleEvent = (event) => {
        const data = event?.data || {}

        if (isAgentTimelineEvent(event?.event)) {
          assistant.agentSteps.push(createAgentTimelineStep(event, assistant.agentSteps.length))
          assistant.agentProgress = createProgressSummary(assistant)
          if (event.event !== 'error') {
            return
          }
        }

        if (event.event === 'tool_result') {
          const list = assistant.agentSteps
          for (let i = list.length - 1; i >= 0; i--) {
            const step = list[i]
            if (
              step.event === 'tool' &&
              !step.result &&
              (!data.tool || step.rawData?.tool === data.tool)
            ) {
              step.result = {
                text: data.text || '',
                items: Array.isArray(data.items) ? data.items : [],
              }
              break
            }
          }
          return
        }

        if (event.event === 'token') {
          if (assistant.status === 'error') return
          const token = data.content || ''
          if (isTechnicalErrorText(token) || isTechnicalErrorText(fullContent + token)) {
            fullContent = AI_FALLBACK_MESSAGE
            assistant.status = 'error'
            assistant.agentProgress = createProgressSummary(assistant)
            startTypewriter()
            return
          }
          fullContent += token
          startTypewriter()
          return
        }

        if (event.event === 'done') {
          assistant.evidenceImages = Array.isArray(data.evidenceImages) ? data.evidenceImages : []
          assistant.diagnosisItems = Array.isArray(data.diagnosisItems) ? data.diagnosisItems : []
          assistant.diagnosticFollowUp = data.diagnosticFollowUp || data.metadata?.diagnostic_follow_up || null
          assistant.latencyMs = data.latency_ms || data.latencyMs || 0
          assistant.agentProgress = createProgressSummary({ ...assistant, status: 'done' }, data)
          streamCompleted = true
          return
        }

        if (event.event === 'session_id') {
          if (data.session_id) {
            session.backendSessionId = data.session_id
          }
          return
        }

        if (event.event === 'error') {
          const message = sanitizeAiErrorMessage(data.message)
          fullContent = sanitizeAiContent(fullContent)
          fullContent += fullContent ? `\n\n${message}` : message
          assistant.status = 'error'
          startTypewriter()
          assistant.agentProgress = createProgressSummary(assistant)
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = readSseEvents(buffer, handleEvent)
        if (streamCompleted) break
      }
      if (streamCompleted) {
        try { await reader.cancel() } catch {}
      }
      flushSseEvents(buffer, handleEvent)

      if (!fullContent.trim() && !assistant.evidenceImages.length) fullContent = '(空响应)'
      fullContent = sanitizeAiContent(fullContent)
      startTypewriter()
      await waitForTypewriter()
      assistant.content = fullContent
      if (assistant.status !== 'error') assistant.status = 'done'
      assistant.agentProgress = createProgressSummary(assistant)
    } catch (error) {
      if (!assistant) return

      if (error.name === 'AbortError') {
        if (typeTimer) {
          clearInterval(typeTimer)
          typeTimer = null
        }
        assistant.content = sanitizeAiContent(fullContent || assistant.content)
        assistant.status = 'stopped'
        if (!assistant.content.trim()) assistant.content = '已停止生成。'
      } else {
        if (typeTimer) {
          clearInterval(typeTimer)
          typeTimer = null
        }
        assistant.status = 'error'
        assistant.content = fullContent || assistant.content
        assistant.content = assistant.content
          ? `${assistant.content}\n\n抱歉，发生了错误，请稍后再试。`
          : '抱歉，发生了错误，请稍后再试。'
      }
    } finally {
      if (typeTimer) clearInterval(typeTimer)
      state.streaming = false
      delete controllers[storageKey]
      touchSession(state, session)
    }
  },

  stop(storageKey) {
    const controller = controllers[storageKey]
    if (controller) controller.abort()
  },
}
