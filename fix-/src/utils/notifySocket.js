// 全局 STOMP over SockJS 客户端（单例）。
// 后端：端点 /ws（SockJS），按用户推送到 /user/queue/notifications，握手用 session cookie 鉴权。
// 经 vite 代理 /api -> localhost:8080，故连接 /api/ws（同源、自动带 cookie）。
import { Client } from '@stomp/stompjs'
import SockJS from 'sockjs-client'

let client = null

export function connectNotify({ onMessage, onConnect, onDisconnect, onAuthFail }) {
  if (client && client.active) return client
  let connectedOnce = false   // 是否曾经握手成功
  let failCount = 0           // 连续「未连上就被关闭」次数（多半是未登录/会话失效）
  client = new Client({
    webSocketFactory: () => new SockJS('/api/ws'),
    reconnectDelay: 4000,          // 断线 4s 后自动重连
    heartbeatIncoming: 10000,
    heartbeatOutgoing: 10000,
    onConnect: () => {
      connectedOnce = true
      failCount = 0
      client.subscribe('/user/queue/notifications', (frame) => {
        try { onMessage && onMessage(JSON.parse(frame.body)) } catch (e) { /* ignore */ }
      })
      onConnect && onConnect()
    },
    onWebSocketClose: () => {
      onDisconnect && onDisconnect()
      // 从未成功连接过就被关闭 → 握手被后端拒绝（未登录/会话过期）。
      // 不要无限重连刷日志：连续两次失败后停掉，并回调通知上层去跳登录。
      if (!connectedOnce) {
        failCount += 1
        if (failCount >= 2) {
          disconnectNotify()
          onAuthFail && onAuthFail()
        }
      }
    },
    onStompError: () => { onDisconnect && onDisconnect() },
  })
  client.activate()
  return client
}

export function disconnectNotify() {
  if (client) { client.deactivate(); client = null }
}
