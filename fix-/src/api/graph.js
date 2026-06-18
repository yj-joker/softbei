import { request } from './request'

// ============ 浏览 / 展开（双端共用，只读） ============

/** 搜索设备（空 keyword 返回全部，上限 50），作为图谱入口 */
export function searchDevices(keyword = '', limit = 30) {
  return request({ url: '/weixiu/device/search', method: 'GET', params: { keyword, limit } })
}

/** 设备概览（含部件数 / 故障数） */

// 注：后端这三个分页查询是「0 基页码」，且 page/size 不可为 null（否则 NPE），
// 故统一默认 page=0 / size=50；管理端按需传显式分页对象 { page, size }。

/** 设备 → 部件（OWNS），分页 */
export function getDeviceComponents(deviceId, { page = 0, size = 50, componentName } = {}) {
  return request({ url: '/weixiu/device/components', method: 'POST', data: { deviceId, componentName, page, size } })
}

/** 部件 → 故障（CAUSES），分页 */
export function getComponentFaults(componentId, { page = 0, size = 50, faultName } = {}) {
  return request({ url: '/weixiu/component/faults', method: 'POST', data: { componentId, faultName, page, size } })
}

/** 故障 → 解决方案（HAS_SOLUTION），分页 */
export function getFaultSolutions(faultId, { page = 0, size = 50, solutionTitle } = {}) {
  return request({ url: '/weixiu/fault/solutions', method: 'POST', data: { faultId, solutionTitle, page, size } })
}

/** 故障 → 相关案例（RECORDED），分页 */
export function getFaultCases(faultId, { page = 0, size = 50 } = {}) {
  return request({ url: '/weixiu/fault/cases', method: 'POST', data: { faultId, page, size } })
}

/** 诊断路径搜索：按故障/部件描述召回完整链路子图 */
export function searchDiagnosisPaths(payload) {
  return request({
    url: '/weixiu/path/search',
    method: 'POST',
    data: { page: 0, size: 12, minScore: 0.5, ...payload },
  })
}

/** 单实体详情 */

// ============ 图谱 CRUD（仅 admin 管理页调用） ============
// 约定：save 返回 Result<实体>（含生成的 id）；update 用 PUT；删除用 DELETE /{id}

/** 设备 */
export const deviceApi = {
  save:   (data) => request({ url: '/weixiu/device/save',   method: 'POST',   data, throwOnError: true }),
  update: (data) => request({ url: '/weixiu/device/update', method: 'PUT',    data, throwOnError: true }),
  remove: (id)   => request({ url: `/weixiu/device/${id}`,  method: 'DELETE', throwOnError: true }),
}

/** 部件 */
export const componentApi = {
  save:   (data) => request({ url: '/weixiu/component/save',   method: 'POST',   data, throwOnError: true }),
  update: (data) => request({ url: '/weixiu/component/update', method: 'PUT',    data, throwOnError: true }),
  remove: (id)   => request({ url: `/weixiu/component/${id}`,  method: 'DELETE', throwOnError: true }),
}

/** 故障 */
export const faultApi = {
  save:   (data) => request({ url: '/weixiu/fault/save',   method: 'POST',   data, throwOnError: true }),
  update: (data) => request({ url: '/weixiu/fault/update', method: 'PUT',    data, throwOnError: true }),
  remove: (id)   => request({ url: `/weixiu/fault/${id}`,  method: 'DELETE', throwOnError: true }),
}

/** 解决方案 */
export const solutionApi = {
  save:   (data) => request({ url: '/weixiu/solution/save',   method: 'POST',   data, throwOnError: true }),
  update: (data) => request({ url: '/weixiu/solution/update', method: 'PUT',    data, throwOnError: true }),
  remove: (id)   => request({ url: `/weixiu/solution/${id}`,  method: 'DELETE', throwOnError: true }),
}

/**
 * 建立关系。relationType 取值：
 * DEVICE_OWNS_COMPONENT / COMPONENT_CAUSES_FAULT / FAULT_HAS_SOLUTION / DEVICE_HAS_FAULT / CASE_RECORD_RECORDED_FAULT
 */
export function createRelation(sourceId, targetId, relationType) {
  return request({ url: '/weixiu/relation/creat', method: 'POST', data: { sourceId, targetId, relationType }, throwOnError: true })
}
