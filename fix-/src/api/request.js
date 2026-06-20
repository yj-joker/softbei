import JSONbigFactory from 'json-bigint'
import { ElMessage } from 'element-plus'

const baseURL = '/api'

// 在词法层面解析 JSON，避免「大整数」(如雪花 ID) 超过 Number.MAX_SAFE_INTEGER 时丢精度。
// 相比此前用正则给 16+ 位数字串加引号的做法，这里只对真正的「数字 token」生效，
// 绝不会误改字符串值内部的数字（例如手册正文里的序列号、预签名 URL 里的时间戳），
// 从而修复章节搜索等接口返回长数字内容时 JSON.parse 报错的问题。
const JSONbig = JSONbigFactory()

// 默认超时：普通请求 20s；文件上传（FormData）放宽到 120s。可由 options.timeout 覆盖。
const DEFAULT_TIMEOUT = 20000
const UPLOAD_TIMEOUT = 120000

// 将 json-bigint 解析出的 BigNumber 归一化：
//   - 超出安全整数范围的整数 → 字符串（保留精度，交给前端按字符串使用）
//   - 安全整数 / 浮点数        → 普通 JS number（行为与原生 JSON.parse 一致）
function bigNumberReviver(key, value) {
  if (value && typeof value === 'object' && value.constructor && value.constructor.name === 'BigNumber') {
    if (value.isInteger()) {
      const n = Number(value.toString())
      return Number.isSafeInteger(n) ? n : value.toString()
    }
    return Number(value.toString())
  }
  return value
}

// 网络层硬失败 → 用户可读文案。区分「超时 / 断网 / 其它」三类，
// 这些是调用方无法靠业务码预期的失败，统一在此兜底提示，避免「点了没反应」。
function classifyNetworkError(error) {
  if (error && error.name === 'AbortError') return '请求超时，请稍后重试'
  if (error instanceof TypeError) return '网络异常，请检查网络连接'
  return '服务暂时不可用，请稍后重试'
}

// Toast 去重：并发请求同时失败时，1 秒内相同文案只弹一次，避免刷屏。
let lastTipText = ''
let lastTipAt = 0
function tip(text, type = 'error') {
  const now = Date.now()
  if (text === lastTipText && now - lastTipAt < 1000) return
  lastTipText = text
  lastTipAt = now
  ElMessage({ message: text, type })
}

/**
 * 统一请求封装
 * @param {object}  options
 * @param {string}  options.url
 * @param {string}  [options.method='GET']
 * @param {object}  [options.data]
 * @param {object}  [options.params]
 * @param {object}  [options.headers]
 * @param {boolean} [options.throwOnError=false] 业务码非 200 时抛出（既有行为，保留）
 * @param {boolean} [options.autoTip=false]      业务码非 200 时自动弹提示（默认关闭，保证零回归）
 * @param {boolean} [options.silent=false]       关闭本次请求的网络层错误提示（后台轮询/对账用）
 * @param {number}  [options.timeout]            毫秒；默认普通 20s / 上传 120s
 */
export async function request(options) {
  const { url, method = 'GET', data, params, headers = {}, throwOnError = false, autoTip = false, silent = false, timeout } = options

  let fullUrl = baseURL + url
  if (params) {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        searchParams.append(key, value)
      }
    })
    const queryString = searchParams.toString()
    if (queryString) {
      fullUrl += '?' + queryString
    }
  }

  const isFormData = data instanceof FormData
  const config = {
    method,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  }

  if (data && method !== 'GET') {
    if (isFormData) {
      config.body = data
      delete config.headers['Content-Type']
    } else {
      config.body = JSON.stringify(data)
    }
  }

  // 超时控制：到时 abort；网络层失败统一友好提示后再抛出，保持「失败即中断后续逻辑」的既有契约。
  const controller = new AbortController()
  config.signal = controller.signal
  const timer = setTimeout(() => controller.abort(), timeout ?? (isFormData ? UPLOAD_TIMEOUT : DEFAULT_TIMEOUT))

  let response
  try {
    response = await fetch(fullUrl, config)
  } catch (error) {
    if (!silent) tip(classifyNetworkError(error))
    throw error
  } finally {
    clearTimeout(timer)
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    let json
    try {
      json = JSONbig.parse(await response.text(), bigNumberReviver)
    } catch (error) {
      if (!silent) tip('服务返回异常，请稍后重试')
      throw error
    }
    // 会话失效（后端 SessionInterceptor 返回 401）：提示 + 清登录态并带 redirect 跳登录页，
    // 登录后由 Login.vue 跳回原页。登录页自身的请求不处理，避免回环。
    if (json && String(json.code) === '401' && location.pathname !== '/login') {
      try { localStorage.removeItem('userInfo') } catch (e) {}
      if (!silent) tip('登录已过期，请重新登录', 'warning')
      const redirect = encodeURIComponent(location.pathname + location.search)
      location.href = `/login?redirect=${redirect}`
    }
    // 业务码非 200：默认交调用方处理（零回归）；仅当显式 autoTip 时才在此统一提示。
    if (autoTip && String(json.code) !== '200' && String(json.code) !== '401') {
      tip(json.message || json.msg || '操作失败')
    }
    if (throwOnError && json.code !== '200') {
      throw new Error(json.message || json.msg || '请求失败')
    }
    return json
  }

  if (!response.ok) {
    if (!silent) tip('服务暂时不可用，请稍后重试')
    throw new Error(`HTTP ${response.status}`)
  }

  return response
}

export default request
