import { request } from './request'

/**
 * 分页查询过期判定待审列表
 * @param {object} params - { page, size, status }
 * @param {number} [params.page=1]
 * @param {number} [params.size=10]
 * @param {string} [params.status] - PENDING / APPROVED / REJECTED
 */
export function getReviewList(params = {}) {
  return request({ url: '/weixiu/admin/expiration/reviews', method: 'GET', params })
}

/**
 * 管理员确认过期：标记旧知识节点为 deprecated
 * @param {number|string} id - 待审记录 ID
 */
export function approveReview(id) {
  return request({ url: `/weixiu/admin/expiration/reviews/${id}/approve`, method: 'POST', throwOnError: true })
}

/**
 * 管理员驳回过期判定：旧知识保持 active
 * @param {number|string} id - 待审记录 ID
 */
export function rejectReview(id) {
  return request({ url: `/weixiu/admin/expiration/reviews/${id}/reject`, method: 'POST', throwOnError: true })
}
