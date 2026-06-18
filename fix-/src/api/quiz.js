import { request } from './request'

// 画像出题/练习接口（基址 /weixiu/quiz）。后端按 session 自动取当前工人。
// 题目/会话 ID 为雪花 ID，request.js 已将超长整数解析为字符串，提交答案时按字符串 key 传回即可。
const BASE = '/weixiu/quiz'

/** AI 出题：异步生成，返回 sessionId（前端轮询 getSession）。 */
export function generateQuiz() {
  return request({ url: `${BASE}/generate`, method: 'POST', throwOnError: true })
}

/** 题库练习：弱点优先抽题，同步返回 { sessionId, questions }（不含答案）。 */
export function practiceQuiz(count = 5) {
  return request({ url: `${BASE}/practice`, method: 'POST', data: { count }, throwOnError: true })
}

/** 查会话+题目（轮询/答题页）。答题前不含答案，提交后回填。 */
export function getQuizSession(sessionId) {
  return request({ url: `${BASE}/${sessionId}`, method: 'GET' })
}

/** 提交答案。answers: { [questionId]: "A" | "A,C" | "对" }，返回成绩+逐题解析。 */
export function submitQuiz(sessionId, answers) {
  return request({ url: `${BASE}/${sessionId}/submit`, method: 'POST', data: { answers }, throwOnError: true })
}

/** 勾选题入个人库。questionIds: string[]，返回入库数。 */
export function saveQuizToBank(sessionId, questionIds) {
  return request({ url: `${BASE}/${sessionId}/save-to-bank`, method: 'POST', data: { questionIds }, throwOnError: true })
}

/** 查个人题库。 */
export function listQuizBank() {
  return request({ url: `${BASE}/bank`, method: 'GET' })
}

/** 查掌握度档案。 */
export function listQuizMastery() {
  return request({ url: `${BASE}/mastery`, method: 'GET' })
}
