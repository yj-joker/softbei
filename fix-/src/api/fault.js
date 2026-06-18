import { request } from './request'

/**
 * 新增故障
 * @param {object} faultDTO
 */
export function saveFault(faultDTO) {
  return request({
    url: '/weixiu/fault/save',
    method: 'POST',
    data: faultDTO
  })
}

/**
 * 更新故障
 * @param {object} faultDTO
 */
export function updateFault(faultDTO) {
  return request({
    url: '/weixiu/fault/update',
    method: 'PUT',
    data: faultDTO
  })
}

/**
 * 删除故障
 * @param {string} id
 */
export function deleteFault(id) {
  return request({
    url: `/weixiu/fault/${id}`,
    method: 'DELETE'
  })
}