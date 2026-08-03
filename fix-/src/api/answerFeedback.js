import { request } from './request'

const PREFIX = '/weixiu/answer-feedback'

export function submitAnswerFeedback(data) {
  return request({ url: PREFIX, method: 'POST', data, throwOnError: true })
}

export function getAnswerFeedbackPage(params = {}) {
  return request({ url: `${PREFIX}/page`, method: 'GET', params, throwOnError: true })
}

export function getAnswerFeedbackDetail(id) {
  return request({ url: `${PREFIX}/${id}`, method: 'GET', throwOnError: true })
}

export function convertAnswerFeedback(id, data) {
  return request({ url: `${PREFIX}/${id}/convert`, method: 'POST', data, throwOnError: true })
}

export function dismissAnswerFeedback(id, comment = '') {
  return request({ url: `${PREFIX}/${id}/dismiss`, method: 'POST', data: { comment }, throwOnError: true })
}
