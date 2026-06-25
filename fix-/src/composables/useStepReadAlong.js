import { ref, computed } from 'vue'
import { useSpeech } from '@/composables/useSpeech'

/**
 * 检修「跟读模式」状态机。
 *
 * 读完一步停下，工人点「下一步」再读下一步，跟着维修节奏走。每步念「标题 + 内容 + 安全提示」。
 * 底层复用全局单例 {@link useSpeech}，所以跟读与聊天朗读互斥（开跟读会停掉聊天朗读，反之亦然）。
 *
 * @param {import('vue').Ref<Array>} stepsRef 步骤数组 ref（已按 sortOrder 排序）
 * @param {{ onStep?: Function, onFinish?: Function }} [options]
 *        onStep(step, index)：每读一步时回调（用于滚动到该步 + 高亮）
 *        onFinish()：全部读完时回调（提示「跟读完成」）
 */
export function useStepReadAlong(stepsRef, options = {}) {
  const { speak, stop } = useSpeech()

  const active = ref(false)       // 是否处于跟读中
  const index = ref(-1)           // 当前在读的步骤下标
  const waitingNext = ref(false)  // 当前步已读完、等工人点「下一步」

  const ID_PREFIX = 'stepread-'

  // 拼接每步要念的文本：第 N 步，标题。内容。安全提示：xxx。（safetyNote 为空则跳过安全提示句）
  function buildText(step) {
    const parts = [`第 ${step.sortOrder} 步，${step.title || ''}。`]
    if (step.content) parts.push(`${step.content}。`)
    if (step.safetyNote) parts.push(`安全提示：${step.safetyNote}。`)
    return parts.join('')
  }

  function readAt(i) {
    const steps = stepsRef.value || []
    if (i < 0 || i >= steps.length) return
    index.value = i
    waitingNext.value = false
    const step = steps[i]
    if (typeof options.onStep === 'function') options.onStep(step, i)
    speak(ID_PREFIX + i, buildText(step), {
      onEnded: () => {
        // 仅响应「仍在跟读且就是这一步」自然播完的情形（避免被切走的旧音频误触发）
        if (!active.value || index.value !== i) return
        if (i >= steps.length - 1) {
          finish()
        } else {
          waitingNext.value = true // 停下，等工人点「下一步」
        }
      },
    })
  }

  // fromIndex：从第几步开始跟读（默认 0 = 从头）。供「从此步跟读」按下标进入。
  function start(fromIndex = 0) {
    const steps = stepsRef.value || []
    if (!steps.length) return
    const i = Math.min(Math.max(Number(fromIndex) || 0, 0), steps.length - 1)
    active.value = true
    readAt(i)
  }

  function next() {
    if (!active.value) return
    readAt(index.value + 1)
  }

  // 全部读完：复位 + 提示
  function finish() {
    active.value = false
    waitingNext.value = false
    index.value = -1
    stop()
    if (typeof options.onFinish === 'function') options.onFinish()
  }

  // 中途退出：复位，不提示
  function exit() {
    active.value = false
    waitingNext.value = false
    index.value = -1
    stop()
  }

  const currentStepId = computed(() => {
    const steps = stepsRef.value || []
    return active.value && index.value >= 0 && index.value < steps.length ? steps[index.value].id : null
  })
  const isLast = computed(() => index.value >= (stepsRef.value || []).length - 1)

  return { active, index, waitingNext, currentStepId, isLast, start, next, finish, exit }
}
