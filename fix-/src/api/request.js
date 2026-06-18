import JSONbigFactory from 'json-bigint'

const baseURL = '/api'

// 在词法层面解析 JSON，避免「大整数」(如雪花 ID) 超过 Number.MAX_SAFE_INTEGER 时丢精度。
// 相比此前用正则给 16+ 位数字串加引号的做法，这里只对真正的「数字 token」生效，
// 绝不会误改字符串值内部的数字（例如手册正文里的序列号、预签名 URL 里的时间戳），
// 从而修复章节搜索等接口返回长数字内容时 JSON.parse 报错的问题。
const JSONbig = JSONbigFactory()

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

/**
 * 统一请求封装
 * @param {object} options
 */
export async function request(options) {
  const { url, method = 'GET', data, params, headers = {}, throwOnError = false } = options

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

  const config = {
    method,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  }

  if (data && method !== 'GET') {
    if (data instanceof FormData) {
      config.body = data
      delete config.headers['Content-Type']
    } else {
      config.body = JSON.stringify(data)
    }
  }

  const response = await fetch(fullUrl, config)

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    const text = await response.text()
    const json = JSONbig.parse(text, bigNumberReviver)
    // 会话失效（后端 SessionInterceptor 返回 401）：清登录态并跳登录页。
    // 登录页自身的请求不处理，避免回环。
    if (json && String(json.code) === '401' && location.pathname !== '/login') {
      try { localStorage.removeItem('userInfo') } catch (e) {}
      location.href = '/login'
    }
    if (throwOnError && json.code !== '200') {
      throw new Error(json.message || json.msg || '请求失败')
    }
    return json
  }

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }

  return response
}

export default request