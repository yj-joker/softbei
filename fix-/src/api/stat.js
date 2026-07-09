import { request } from './request'

// 首页概览统计接口（基址 /weixiu/stat）。
// 指标均为后端实时 count，替代首页原有的写死示例数据；请求自动携带 session cookie。
const BASE = '/weixiu/stat'

/**
 * 用户端首页概览统计。
 * 返回 { deviceTotal, myOpenTasks, myClosedTasks, myTaskTotal, completionRate, taskFlow }
 * taskFlow: { CREATED, GENERATING, GENERATED, EXECUTING, CLOSED }
 */
export function getUserOverview() {
  return request({ url: `${BASE}/user-overview`, method: 'GET' })
}

/**
 * 管理端首页概览统计（后端 @RequireAdmin）。
 * 返回 { userTotal, taskTotal, pendingCaseTotal, deviceTotal, taskStatusDist }
 */
export function getAdminOverview() {
  return request({ url: `${BASE}/admin-overview`, method: 'GET' })
}

/**
 * 最近操作动态（管理端首页，后端 @RequireAdmin）。
 * 返回 [{ user, action, status, time }]，time 为 ISO 时间字符串。
 */
export function getRecentActivities(limit = 10) {
  return request({ url: `${BASE}/recent-activities`, method: 'GET', params: { limit } })
}
