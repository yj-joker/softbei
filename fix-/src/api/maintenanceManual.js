import request from './request'

/**
 * 上传维修手册（multipart/form-data）
 * @param {FormData} formData - 包含 manual_name, manual_image, file
 */
export const uploadMaintenanceManual = (formData) => {
  return request({
    url: '/weixiu/maintenance-manual/save',
    method: 'POST',
    data: formData
  })
}

/**
 * 获取维修手册列表
 * @param {object} params - { pageNum, pageSize, keyword? }
 */
export const getMaintenanceManualList = (params) => {
  return request({ url: '/weixiu/maintenance-manual/list', method: 'POST', data: params })
}

/**
 * 获取维修手册详情
 * @param {number|string} id - 手册ID
 */
export const getMaintenanceManualDetail = (id) => {
  return request({ url: `/weixiu/maintenance-manual/${id}`, method: 'GET' })
}

/**
 * 删除维修手册
 * @param {number|string} id - 手册ID
 */
export const deleteMaintenanceManual = (id) => {
  return request({ url: `/weixiu/maintenance-manual/${String(id)}`, method: 'DELETE', throwOnError: true })
}

/**
 * 更新维修手册
 * @param {FormData} formData - 包含 id, manual_name, file (可选)
 */
export const updateMaintenanceManual = (formData) => {
  return request({ url: '/weixiu/maintenance-manual/update', method: 'PUT', data: formData })
}

/**
 * 获取推荐手册
 * @param {number} limit - 推荐数量，默认6
 */
/**
 * 获取手册排行榜
 * @param {string} type - day/week/month/total
 * @param {number} limit - 返回数量，默认10
 */
export const getMaintenanceManualRank = (type = 'total', limit = 10) => {
  return request({ url: `/weixiu/maintenance-manual/rank?type=${type}&limit=${limit}`, method: 'GET' })
}

/**
 * 开始阅读手册（创建阅读会话）
 * @param {number} manualId - 手册ID
 */
export const startMaintenanceManualRead = (manualId) => {
  return request({
    url: '/weixiu/maintenance-manual/read/start',
    method: 'POST',
    data: { manualId }
  })
}

/**
 * 上报阅读心跳
 * @param {string} readSessionId - 阅读会话ID
 */
export const maintenanceManualHeartbeat = (readSessionId) => {
  return request({
    url: '/weixiu/maintenance-manual/read/heartbeat',
    method: 'POST',
    data: { readSessionId }
  })
}

/**
 * 章节智能搜索（向量检索）
 * @param {object} params - { query: string, topK?: number, chunkType?: string, manualId?: number }
 */
export const searchChapter = (params) => {
  return request({ url: '/weixiu/maintenance-manual/search', method: 'POST', data: { topK: 20, ...params } })
}
