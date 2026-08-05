import { request } from './request'

const BASE = '/weixiu/admin/task-evidence-candidates'

export function getTaskEvidenceCandidateList(params = {}) {
  return request({ url: BASE, method: 'GET', params, throwOnError: true })
}

export function getTaskEvidenceCandidate(id) {
  return request({ url: `${BASE}/${id}`, method: 'GET', throwOnError: true })
}

export function updateTaskEvidenceCandidate(id, data) {
  return request({ url: `${BASE}/${String(id)}`, method: 'PUT', data, throwOnError: true })
}

export function retryTaskEvidenceCandidate(id) {
  return request({ url: `${BASE}/${id}/retry`, method: 'POST', throwOnError: true })
}

export function reviewTaskEvidenceCandidate(id, { status, comment, rowVersion }) {
  const params = { status, rowVersion }
  if (comment) params.comment = comment
  return request({ url: `${BASE}/${id}/review`, method: 'POST', params, throwOnError: true })
}

export function promoteTaskEvidenceCandidate(id) {
  return request({ url: `${BASE}/${id}/promote`, method: 'POST', throwOnError: true })
}
