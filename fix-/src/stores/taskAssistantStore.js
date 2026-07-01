import { reactive } from 'vue'
import { taskChatStream, getTaskChatHistory } from '@/api/maintenanceTask'
import { flushSseEvents, readSseEvents } from '@/utils/sse'
import { AI_FALLBACK_MESSAGE, isTechnicalErrorText, sanitizeAiContent, sanitizeAiErrorMessage } from '@/utils/aiErrorFallback'
import {
  createAgentTimelineStep,
  createInitialAgentProgress,
  createProgressSummary,
  isAgentTimelineEvent,
} from '@/utils/agentTimeline'

const tasks = reactive({})
const controllers = {}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function ensure(taskId) {
  const key = String(taskId)
  if (!tasks[key]) {
    tasks[key] = { messages: [], streaming: false, focusedStepId: null, loaded: false }
  }
  return tasks[key]
}

function normalizeHistoryMessage(message) {
  return {
    id: message.id || `${message.createdAt || Date.now()}-${message.role}`,
    role: message.role,
    content: message.content || '',
    images: message.images || [],
    evidenceImages: message.evidenceImages || [],
    timestamp: message.createdAt ? String(message.createdAt).replace('T', ' ').slice(11, 16) : '',
    status: 'done',
    agentSteps: [],
    agentProgress: { text: '', running: false },
  }
}

function applyStreamEvent(message, event) {
  const data = event?.data || {}

  if (isAgentTimelineEvent(event?.event)) {
    message.agentSteps.push(createAgentTimelineStep(event, message.agentSteps.length))
    message.agentProgress = createProgressSummary(message)
    if (event.event !== 'error') {
      return
    }
  }

  if (event.event === 'token') {
    if (message.status === 'error') return
    const token = data.content || ''
    if (isTechnicalErrorText(token) || isTechnicalErrorText(message.content + token)) {
      message.content = AI_FALLBACK_MESSAGE
      message.status = 'error'
      message.agentProgress = createProgressSummary(message)
      return
    }
    message.content += token
  } else if (event.event === 'done') {
    message.evidenceImages = Array.isArray(data.evidenceImages) ? data.evidenceImages : []
    message.latencyMs = data.latency_ms || data.latencyMs || 0
    message.agentProgress = createProgressSummary({ ...message, status: 'done' }, data)
  } else if (event.event === 'error') {
    const safeMessage = sanitizeAiErrorMessage(data.message)
    const current = sanitizeAiContent(message.content)
    message.content = current ? `${current}\n${safeMessage}` : safeMessage
    message.status = 'error'
    message.agentProgress = createProgressSummary(message)
  }
}

export const taskAssistantStore = {
  get(taskId) {
    return ensure(taskId)
  },

  setFocus(taskId, stepId) {
    ensure(taskId).focusedStepId = stepId
  },

  async loadHistory(taskId) {
    const state = ensure(taskId)
    if (state.loaded || state.streaming) return

    try {
      const res = await getTaskChatHistory(taskId)
      const list = (res && res.data) || []
      state.messages = list.map(normalizeHistoryMessage)
      state.loaded = true
    } catch {
      // History is optional; a failed load should not block the assistant.
    }
  },

  async send(taskId, { message, images = [] }) {
    const state = ensure(taskId)
    const text = (message || '').trim() || (images.length ? '请分析我上传的图片。' : '')
    if (state.streaming || !text) return

    state.messages.push({
      id: `${Date.now()}-user`,
      role: 'user',
      content: text,
      images,
      evidenceImages: [],
      timestamp: nowTime(),
      status: 'done',
      agentSteps: [],
      agentProgress: { text: '', running: false },
    })
    const assistant = reactive({
      id: `${Date.now()}-assistant`,
      role: 'assistant',
      content: '',
      images: [],
      evidenceImages: [],
      timestamp: nowTime(),
      status: 'streaming',
      agentSteps: [],
      agentProgress: createInitialAgentProgress(),
      latencyMs: 0,
    })
    state.messages.push(assistant)
    state.streaming = true

    const controller = new AbortController()
    controllers[taskId] = controller

    try {
      const response = await taskChatStream(
        taskId,
        { message: text, images, focusedStepId: state.focusedStepId },
        controller.signal,
      )
      if (!response.ok) throw new Error('HTTP ' + response.status)
      if (!response.body) throw new Error('Empty response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        let done
        let value
        try {
          ;({ done, value } = await reader.read())
        } catch (error) {
          if (controller.signal.aborted) break
          throw error
        }
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = readSseEvents(buffer, (event) => applyStreamEvent(assistant, event))
      }

      flushSseEvents(buffer, (event) => applyStreamEvent(assistant, event))
      if (!assistant.content && !assistant.evidenceImages.length) assistant.content = '(无回复)'
      assistant.content = sanitizeAiContent(assistant.content)
      if (assistant.status !== 'error') assistant.status = 'done'
      assistant.agentProgress = createProgressSummary(assistant)
    } catch (error) {
      if (error.name === 'AbortError') {
        assistant.status = 'stopped'
        if (!assistant.content.trim()) assistant.content = '已停止生成。'
      } else {
        assistant.status = 'error'
        assistant.content = sanitizeAiContent(assistant.content)
        if (!assistant.content) assistant.content = '抱歉，助手出错了，请稍后再试。'
      }
      assistant.agentProgress = createProgressSummary(assistant)
    } finally {
      state.streaming = false
      delete controllers[taskId]
    }
  },

  stop(taskId) {
    const controller = controllers[taskId]
    if (controller) controller.abort()
  },

  isStreaming(taskId) {
    return ensure(taskId).streaming
  },
}
