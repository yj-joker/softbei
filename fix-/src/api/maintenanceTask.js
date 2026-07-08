import { request } from './request'

// 检修任务接口（基址 /weixiu/task）。普通用户调用时后端按 userType 自动只返回本人任务。
// 注意：/promote、/promote-to-graph、/skip-promotion 是 @RequireAdmin，用户端不调用。
const BASE = '/weixiu/task'

/** 创建检修任务（触发异步生成步骤，返回 MaintenanceTaskVO） */
export function createTask(data) {
  return request({ url: BASE, method: 'POST', data, throwOnError: true })
}

/** 重新生成步骤（GENERATE_FAILED → GENERATING） */
export function retryGenerate(taskId) {
  return request({ url: `${BASE}/${taskId}/retry`, method: 'POST', throwOnError: true })
}

/** 开始执行（GENERATED → EXECUTING） */
export function startTask(taskId) {
  return request({ url: `${BASE}/${taskId}/start`, method: 'POST', throwOnError: true })
}

/** 提交/执行某步骤（触发异步 AI 验证），data: { images, note, checkpointConfirmed } */
export function executeStep(taskId, stepId, data) {
  return request({ url: `${BASE}/${taskId}/steps/${stepId}/execute`, method: 'POST', data, throwOnError: true })
}

/** 强制完成步骤（AI 未通过但工人确认无误） */
export function forceCompleteStep(taskId, stepId, reason = '') {
  return request({ url: `${BASE}/${taskId}/steps/${stepId}/force-complete`, method: 'POST', data: { reason }, throwOnError: true })
}

/** 任务详情（含步骤列表） */
export function getTaskDetail(taskId) {
  return request({ url: `${BASE}/${taskId}`, method: 'GET' })
}

/** 任务列表（分页，1 基页码） */
export function listTasks({ page = 1, size = 12, status, deviceName } = {}) {
  return request({ url: BASE, method: 'GET', params: { page, size, status, deviceName } })
}

/** 步骤列表 */
/**
 * 步骤 AI 答疑（SSE 流式）。后端原样转发 Python 事件流，
 * 故返回的是原始事件 JSON（{event,data}），调用方用 getReader 解析。
 * 经 vite 代理 /api → 8080，自动带 session cookie。
 * @returns {Promise<Response>}
 */
/**
 * 检修步骤助手（任务级一条会话，SSE 流式）。
 * 后端自动注入：当前聚焦步骤+证据、全步总览+进度、工人偏好、近 N 轮历史。
 * @returns {Promise<Response>}
 */
export function taskChatStream(taskId, { message, images = [], focusedStepId = null } = {}, signal) {
  return fetch(`/api/weixiu/task/${taskId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({ message, images, focusedStepId }),
  })
}

/** 任务对话历史（进面板时渲染，时间正序） */
export function getTaskChatHistory(taskId) {
  return request({ url: `${BASE}/${taskId}/chat/history`, method: 'GET' })
}

export function startTaskVoiceSession(taskId, { focusedStepId = null } = {}) {
  return request({
    url: `${BASE}/${taskId}/voice/start`,
    method: 'POST',
    data: { focusedStepId },
    throwOnError: true,
  })
}

export function sendTaskVoiceTurn(taskId, {
  transcript,
  focusedStepId = null,
  confirmed = false,
  override = false,
  images = [],
  note = '',
  checkpointConfirmed = false,
} = {}) {
  return request({
    url: `${BASE}/${taskId}/voice/turn`,
    method: 'POST',
    data: {
      transcript,
      focusedStepId,
      confirmed,
      override,
      images,
      note,
      checkpointConfirmed,
    },
    throwOnError: true,
    timeout: 180000,
  })
}

export function endTaskVoiceSession(taskId) {
  return request({
    url: `${BASE}/${taskId}/voice/end`,
    method: 'POST',
    throwOnError: true,
  })
}
