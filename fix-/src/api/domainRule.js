import { request } from './request'

const PREFIX = '/weixiu/domain-rule'

export function getDomainRulePage(params = {}) {
  return request({ url: `${PREFIX}/page`, method: 'GET', params, throwOnError: true })
}

export function getDomainRuleDetail(id) {
  return request({ url: `${PREFIX}/${id}`, method: 'GET', throwOnError: true })
}

export function createDomainRule(data) {
  return request({ url: `${PREFIX}/save`, method: 'POST', data, throwOnError: true })
}

export function updateDomainRule(id, data) {
  return request({ url: `${PREFIX}/${id}`, method: 'PUT', data, throwOnError: true })
}

export function submitDomainRule(id) {
  return request({ url: `${PREFIX}/${id}/submit`, method: 'POST', throwOnError: true })
}

export function approveDomainRule(id, data = undefined) {
  return request({ url: `${PREFIX}/${id}/approve`, method: 'POST', data, throwOnError: true })
}

export function rejectDomainRule(id, comment) {
  return request({ url: `${PREFIX}/${id}/reject`, method: 'POST', data: { comment }, throwOnError: true })
}

export function disableDomainRule(id) {
  return request({ url: `${PREFIX}/${id}/disable`, method: 'POST', throwOnError: true })
}

export function retrySyncDomainRule(id) {
  return request({ url: `${PREFIX}/${id}/retry-sync`, method: 'POST', throwOnError: true })
}
