import { reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { synthesizeSpeech } from '@/api/tts'

/**
 * 全局单例语音播报器（聊天朗读 + 检修跟读共用）。
 *
 * 全站同一时刻只有一个在播：点 B 朗读会先停掉 A。组件用 id 标识"是不是我这条在播/在加载"，
 * 据此切换「朗读 / 加载中 / 停止」图标。
 *
 * 用法：
 *   const { state, speak, stop, isSpeaking, isLoading } = useSpeech()
 *   speak('msg-12', '要读的文字', { onEnded: () => next() })  // onEnded 供跟读推进
 *   stop()
 *
 * - state.speakingId / loadingId：当前在播 / 在合成（第一句还没出声）的 id（null 表示空闲）
 * - 切换朗读对象时，作废本轮 + 中止仍在合成的请求，避免旧音频迟到乱入
 *
 * 【低延迟：按句边合成边播】
 * 不再「整段合成完才出声」，而是把文本分句，生产者按序逐句合成、消费者顺序播放：
 * 播第 i 句与合成第 i+1 句并行，点击到出声 ≈ 只等第一句的合成时间。
 * 对外 API（speak/stop/isSpeaking/isLoading/onEnded 语义）保持不变：onEnded 在「最后一句也播完」时才触发。
 */

// —— 模块级单例：跨组件共享同一个播放器与状态 ——
const state = reactive({ speakingId: null, loadingId: null })

let audio = null
let objectUrl = null
let playToken = 0                 // 自增令牌：每次 speak/stop 都 +1，作废上一轮所有在途异步
let controllers = []              // 本轮所有在途合成请求的 AbortController（stop 时全部中止）

// 分句最大长度：超过则在逗号/顿号处或硬切，避免单句过长拖慢首声
const MAX_CHUNK_LEN = 80

/**
 * 把文本切成便于「边合成边播」的句子片段。
 * 先按强标点（。！？；!?;\n）断句（保留标点），过长的句子再按逗号/顿号或硬长度兜底切。
 */
function splitSentences(text) {
  const raw = []
  let buf = ''
  for (const ch of text) {
    buf += ch
    if ('。！？；!?;\n'.includes(ch)) {
      const t = buf.trim()
      if (t) raw.push(t)
      buf = ''
    }
  }
  if (buf.trim()) raw.push(buf.trim())

  // 过长片段二次切分（按逗号/顿号，再不行硬切），保证首声尽快出来
  const out = []
  for (const seg of raw) {
    if (seg.length <= MAX_CHUNK_LEN) {
      out.push(seg)
      continue
    }
    let rest = seg
    while (rest.length > MAX_CHUNK_LEN) {
      const window = rest.slice(0, MAX_CHUNK_LEN)
      let cut = Math.max(window.lastIndexOf('，'), window.lastIndexOf('、'), window.lastIndexOf(','))
      if (cut < MAX_CHUNK_LEN * 0.4) cut = MAX_CHUNK_LEN - 1 // 没合适逗号则硬切
      out.push(rest.slice(0, cut + 1).trim())
      rest = rest.slice(cut + 1)
    }
    if (rest.trim()) out.push(rest.trim())
  }
  return out.filter(Boolean)
}

function revokeUrl() {
  if (objectUrl) {
    try { URL.revokeObjectURL(objectUrl) } catch (e) { /* ignore */ }
    objectUrl = null
  }
}

/** 停止当前播放 + 中止本轮所有在途合成，回到空闲态。 */
function stop() {
  playToken++ // 作废本轮：所有在跑的 producer/consumer 看到令牌变化即退出
  for (const c of controllers) {
    try { c.abort() } catch (e) { /* ignore */ }
  }
  controllers = []
  if (audio) {
    try { audio.pause() } catch (e) { /* ignore */ }
    audio.onended = null
    audio.onerror = null
    audio = null
  }
  revokeUrl()
  state.speakingId = null
  state.loadingId = null
}

/**
 * 朗读一段文字（按句流水线：边合成边播）。
 * @param {string|number} id 调用方唯一标识（消息 id / 步骤 index）
 * @param {string} text 要朗读的文字
 * @param {{ onEnded?: Function }} [opts] onEnded：本条最后一句也自然播完时回调（跟读靠它推进）
 */
async function speak(id, text, opts = {}) {
  // 再次点击正在播的同一条 → 当作停止
  if (state.speakingId === id) {
    stop()
    return
  }
  if (!text || !text.trim()) return

  stop()                       // 先停旧的（含作废旧轮 + 中止旧请求）
  const token = playToken      // 本轮令牌（stop 内已 ++，这里取到的是本轮值）
  const chunks = splitSentences(text)
  if (!chunks.length) return

  const blobs = new Array(chunks.length).fill(null)
  let producerFailed = false
  let waitingIndex = -1        // 消费者正等待哪一句（被 producer 填好后唤醒）
  let started = false          // 第一句是否已开始播（用于清 loadingId）

  state.loadingId = id

  // —— 消费者：顺序播放 blobs[i]，没好就挂起等待 ——
  function playIndex(i) {
    if (token !== playToken) return // 已被切走/停止
    if (i >= chunks.length) {        // 全部播完 → 才算本条结束
      finishOk()
      return
    }
    const blob = blobs[i]
    if (blob == null) {
      if (producerFailed) { failMidway(); return }
      waitingIndex = i               // 等 producer 填好这一句再唤醒
      return
    }
    waitingIndex = -1
    revokeUrl()
    objectUrl = URL.createObjectURL(blob)
    audio = new Audio(objectUrl)
    audio.onended = () => {
      if (token !== playToken) return
      revokeUrl()
      playIndex(i + 1)
    }
    audio.onerror = () => {
      if (token !== playToken) return
      failMidway()
    }
    if (!started) {                  // 第一句开播：清加载态、置在播态
      started = true
      state.loadingId = null
      state.speakingId = id
    }
    audio.play().catch(() => {
      // 播放被拒（极少：非用户手势触发）
      if (token === playToken) stop()
    })
  }

  function finishOk() {
    if (token !== playToken) return
    revokeUrl()
    state.speakingId = null
    state.loadingId = null
    if (typeof opts.onEnded === 'function') opts.onEnded()
  }

  function failMidway() {
    if (token !== playToken) return
    stop()
    ElMessage.error('语音服务暂不可用')
  }

  // —— 生产者：按序逐句合成，填好即唤醒等待中的消费者 ——
  ;(async () => {
    for (let i = 0; i < chunks.length; i++) {
      if (token !== playToken) return
      const controller = new AbortController()
      controllers.push(controller)
      try {
        const blob = await synthesizeSpeech(chunks[i], controller.signal)
        if (token !== playToken) return
        blobs[i] = blob
        if (waitingIndex === i) playIndex(i) // 消费者正等这一句 → 唤醒
      } catch (e) {
        if (e && e.name === 'AbortError') return // 主动切换：静默
        if (token !== playToken) return
        producerFailed = true
        if (!started) {                         // 首句就失败、还没出声
          state.loadingId = null
          ElMessage.error('语音服务暂不可用')
        } else if (waitingIndex === i) {        // 播到一半等不到下一句
          failMidway()
        }
        return
      }
    }
  })()

  // 启动消费者：等第一句
  playIndex(0)
}

export function useSpeech() {
  return {
    state,
    speak,
    stop,
    isSpeaking: (id) => state.speakingId === id,
    isLoading: (id) => state.loadingId === id,
  }
}
