// AudioWorklet 处理器：把麦克风音频降采样到 16kHz 并转成 16bit PCM，
// 通过 port 把 Int16 的 ArrayBuffer（可转移）发回主线程，再经 WebSocket 送给后端。
// 注册名：'pcm-worklet'
class PCMWorklet extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const opts = (options && options.processorOptions) || {}
    this.targetRate = opts.targetRate || 16000
    // sampleRate 是 worklet 全局变量 = AudioContext 采样率（常见 44100/48000）
    this.ratio = sampleRate / this.targetRate
    this._pos = 0 // 跨 process 调用保留的小数读取位置
  }

  process(inputs) {
    const input = inputs[0]
    const channel = input && input[0]
    if (!channel || channel.length === 0) return true

    const out = []
    let i = this._pos
    while (i < channel.length) {
      let s = channel[Math.floor(i)]
      // Float32 [-1,1] → Int16
      s = s < 0 ? s * 0x8000 : s * 0x7fff
      if (s > 32767) s = 32767
      else if (s < -32768) s = -32768
      out.push(s)
      i += this.ratio
    }
    this._pos = i - channel.length

    if (out.length) {
      const pcm = new Int16Array(out)
      this.port.postMessage(pcm.buffer, [pcm.buffer])
    }
    return true
  }
}

registerProcessor('pcm-worklet', PCMWorklet)
