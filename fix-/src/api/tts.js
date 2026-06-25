// 语音合成（TTS）请求：把文字发到 Java /weixiu/ai/tts，拿回 mp3 音频 blob。
// 走原生 fetch（而非统一 request 封装），因为返回是二进制音频流、且需支持 AbortSignal 取消。
// 与统一封装保持一致的两点：同源 /api 前缀、credentials: 'include'（带 session cookie 鉴权）。

const baseURL = '/api'

/**
 * 合成语音。
 * @param {string} text 要朗读的文字
 * @param {AbortSignal} [signal] 取消信号（切换朗读对象时中止上一请求）
 * @param {string} [voice] 音色，缺省用后端默认 longxiaochun
 * @returns {Promise<Blob>} audio/mpeg 音频 blob
 */
export async function synthesizeSpeech(text, signal, voice) {
  const res = await fetch(baseURL + '/weixiu/ai/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    signal,
    body: JSON.stringify(voice ? { text, voice } : { text }),
  })
  if (!res.ok) {
    throw new Error('TTS HTTP ' + res.status)
  }
  return await res.blob()
}
