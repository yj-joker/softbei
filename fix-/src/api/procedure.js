import { request } from './request'

// ============ 规程 CRUD ============

/** 分页查询规程列表 */
export function getProcedureList(params = {}) {
  return request({ url: '/weixiu/procedure', method: 'GET', params })
}

/** 查询规程详情（含步骤列表） */
export function getProcedureDetail(id) {
  return request({ url: `/weixiu/procedure/${id}`, method: 'GET' })
}

/** 创建规程（可同时提交步骤） */
export function createProcedure(data) {
  return request({ url: '/weixiu/procedure', method: 'POST', data, throwOnError: true })
}

/** 编辑规程基本信息（仅 DRAFT） */
export function updateProcedure(id, data) {
  return request({ url: `/weixiu/procedure/${id}`, method: 'PUT', data, throwOnError: true })
}

/** 发布规程（DRAFT → PUBLISHED） */
export function publishProcedure(id) {
  return request({ url: `/weixiu/procedure/${id}/publish`, method: 'POST', throwOnError: true })
}

/** 归档规程（PUBLISHED → ARCHIVED） */
export function archiveProcedure(id) {
  return request({ url: `/weixiu/procedure/${id}/archive`, method: 'POST', throwOnError: true })
}

// ============ 步骤管理 ============

/** 批量保存步骤（全量替换，仅 DRAFT） */
export function saveSteps(procedureId, steps) {
  return request({ url: `/weixiu/procedure/${procedureId}/steps`, method: 'POST', data: steps, throwOnError: true })
}

/** 查询步骤列表 */

/** 删除单个步骤 */
