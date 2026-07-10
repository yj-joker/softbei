import { ref } from 'vue'

/**
 * 实时语音识别（DashScope Paraformer，经 Java WS 桥接）。
 * 链路：麦克风 → AudioWorklet(16k PCM) → WS /weixiu/ai/asr-stream → 后端 → 文本回传。
 *
 * 用法：
 *   const { recording, partial, error, start, stop } = useAsrStream()
 *   await start({ onFinal: (text) => { inputValue.value += text } })
 *   stop()
 *
 * - partial：当前未定稿句子（实时刷新，用于「边说边显示」）
 * - onFinal：每当一句话定稿时回调一次（追加到输入框）
 */
export function useAsrStream() {
  const recording = ref(false)
  const partial = ref('')
  const error = ref('')

  let ws = null
  let audioCtx = null
  let workletNode = null
  let sinkNode = null
  let sourceNode = null
  let mediaStream = null
  let onFinal = null
  let cleanupTimer = null  // 跟踪 stop() 里的延迟清理定时器，防止 stop→start 竞争

  async function start(handlers = {}) {
    // 若上次 stop() 的1.2s清理定时器还未触发，先取消它并立即清理旧连接，
    // 否则定时器会在新连接建立后才触发，把新 ws/audioCtx 一并销毁。
    if (cleanupTimer !== null) {
      clearTimeout(cleanupTimer)
      cleanupTimer = null
      cleanup()
    }
    onFinal = handlers.onFinal || null
    error.value = ''
    partial.value = ''

    // 1) 麦克风
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
    } catch (e) {
      error.value = '无法访问麦克风：' + (e.message || '请检查麦克风权限')
      cleanup()
      throw e
    }

    // 2) WebSocket（与登录同源，cookie 自动带上，后端握手校验登录态）
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    try {
      ws = new WebSocket(`${proto}://${location.host}/weixiu/ai/asr-stream`)
      ws.binaryType = 'arraybuffer'
      await new Promise((resolve, reject) => {
        ws.onopen = resolve
        ws.onerror = () => reject(new Error('语音服务连接失败（可能未登录或服务不可用）'))
      })
    } catch (e) {
      error.value = e.message
      cleanup()
      throw e
    }

    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'partial') {
        partial.value = msg.text || ''
      } else if (msg.type === 'final') {
        if (msg.text && onFinal) onFinal(msg.text)
        partial.value = ''
      } else if (msg.type === 'error') {
        error.value = msg.message || '语音识别错误'
      }
    }

    // 3) 音频管线：source → worklet（采 PCM）→ 静音 gain → destination（保证图被驱动）
    audioCtx = new (window.AudioContext || window.webkitAudioContext)()
    await audioCtx.audioWorklet.addModule('/asr-worklet.js')
    sourceNode = audioCtx.createMediaStreamSource(mediaStream)
    workletNode = new AudioWorkletNode(audioCtx, 'pcm-worklet', {
      processorOptions: { targetRate: 16000 },
    })
    workletNode.port.onmessage = (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data) // Int16 PCM ArrayBuffer
    }
    sinkNode = audioCtx.createGain()
    sinkNode.gain.value = 0 // 静音，不回放
    sourceNode.connect(workletNode)
    workletNode.connect(sinkNode)
    sinkNode.connect(audioCtx.destination)

    recording.value = true
  }

  function stop() {
    recording.value = false
    // 通知后端「说完了」，触发最终句 + onComplete；留点时间收尾再断开
    try {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }))
    } catch (e) { /* ignore */ }
    cleanupTimer = setTimeout(() => {
      cleanupTimer = null
      cleanup()
    }, 1200)
  }

  function cleanup() {
    // 若有待触发的延迟清理定时器也一并取消
    if (cleanupTimer !== null) {
      clearTimeout(cleanupTimer)
      cleanupTimer = null
    }
    try { sourceNode && sourceNode.disconnect() } catch (e) {}
    try { workletNode && workletNode.disconnect() } catch (e) {}
    try { sinkNode && sinkNode.disconnect() } catch (e) {}
    try { audioCtx && audioCtx.state !== 'closed' && audioCtx.close() } catch (e) {}
    try { mediaStream && mediaStream.getTracks().forEach((t) => t.stop()) } catch (e) {}
    try { ws && ws.close() } catch (e) {}
    sourceNode = workletNode = sinkNode = audioCtx = mediaStream = ws = null
    recording.value = false
  }

  return { recording, partial, error, start, stop, cleanup }
}
