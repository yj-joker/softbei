import { request } from './request'

/**
 * 新增解决方案
 * @param {object} solutionDTO
 */
export function saveSolution(solutionDTO) {
  return request({
    url: '/weixiu/solution/save',
    method: 'POST',
    data: solutionDTO
  })
}

/**
 * 更新解决方案
 * @param {object} solutionDTO
 */
export function updateSolution(solutionDTO) {
  return request({
    url: '/weixiu/solution/update',
    method: 'PUT',
    data: solutionDTO
  })
}

/**
 * 删除解决方案
 * @param {string} id
 */
export function deleteSolution(id) {
  return request({
    url: `/weixiu/solution/${id}`,
    method: 'DELETE'
  })
}