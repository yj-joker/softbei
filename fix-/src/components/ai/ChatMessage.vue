<script setup>
import { computed } from 'vue'
import { ChatDotRound, CopyDocument, DataAnalysis, Loading, Right, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { useSpeech } from '@/composables/useSpeech'

const props = defineProps({
  message: { type: Object, required: true },
  userInitial: { type: String, default: 'U' },
  agentEnabled: { type: Boolean, default: true },
})

const emit = defineEmits(['open-agent'])

// 语音朗读：全局单例播放器，按 message.id 标识当前是否在播/在加载本条
const { speak, isSpeaking, isLoading } = useSpeech()
const speaking = computed(() => isSpeaking(props.message.id))
const loadingSpeech = computed(() => isLoading(props.message.id))

const isUser = computed(() => props.message.role === 'user')
const diagnosisItems = computed(() =>
  Array.isArray(props.message.diagnosisItems) ? props.message.diagnosisItems : [],
)

// 诊断项（结构化）转为可读/可显示的纯文本，与正文合成同一段，确保朗读完整覆盖、各回复样式统一
function diagnosisToText(items) {
  return items
    .map((it, i) => {
      const part = it.fault_part || it.faultPart || '排查项'
      const pr = it.priority ? `（${it.priority}）` : ''
      const cause = it.root_cause || it.rootCause
      const basis = it.knowledge_basis || it.knowledgeBasis
      let s = `${i + 1}. ${part}${pr}`
      if (cause) s += `\n可能原因：${cause}`
      if (basis) s += `\n知识依据：${basis}`
      return s
    })
    .join('\n\n')
}

// 正文：开头文字 + 诊断纯文本。显示/复制/朗读统一用它（单一来源）
const bodyText = computed(() => {
  const parts = []
  if (props.message.content) parts.push(props.message.content)
  if (!isUser.value && diagnosisItems.value.length) parts.push(diagnosisToText(diagnosisItems.value))
  return parts.join('\n\n')
})

function toggleSpeak() {
  speak(props.message.id, bodyText.value)
}

const messageImageUrls = computed(() =>
  (props.message.images || []).filter((image) => typeof image === 'string' && image),
)
const evidenceImages = computed(() =>
  (props.message.evidenceImages || []).filter((item) => item?.imageUrl),
)
const evidenceImageUrls = computed(() =>
  evidenceImages.value.map((item) => item.imageUrl).filter(Boolean),
)
const agentSteps = computed(() =>
  Array.isArray(props.message.agentSteps) ? props.message.agentSteps : [],
)
const meaningfulAgentSteps = computed(() =>
  agentSteps.value.filter((step) => step.event !== 'status'),
)
const agentProgress = computed(() => props.message.agentProgress || { text: '', running: false })
const isStreaming = computed(() => props.message.status === 'streaming')
const agentContext = computed(() =>
  !isUser.value && props.agentEnabled && props.message.mode !== 'chat',
)
// \u8fd0\u884c\u4e2d\uff1a\u663e\u793a\u5b9e\u65f6\u72b6\u6001\u884c\uff1b\u5b8c\u6210\u4e14\u6709\u5de5\u4f5c\u8bb0\u5f55\uff1a\u663e\u793a\u53ef\u70b9\u51fb\u7684\u300c\u67e5\u770b\u5de5\u4f5c\u8fc7\u7a0b\u300d
const showAgentLive = computed(() => agentContext.value && isStreaming.value)
const hasAgentWork = computed(() =>
  agentContext.value && !isStreaming.value && meaningfulAgentSteps.value.length > 0,
)
const toolChips = computed(() => {
  const seen = new Set()
  const chips = []
  for (const step of meaningfulAgentSteps.value) {
    if (step.event !== 'tool') continue
    const key = step.rawData?.tool || step.title
    if (!key || seen.has(key)) continue
    seen.add(key)
    chips.push({ id: step.id, title: step.title })
  }
  return chips
})
const toolCount = computed(() => toolChips.value.length)
const selectedCount = computed(() => {
  const done = [...meaningfulAgentSteps.value]
    .reverse()
    .find((step) => step.event === 'retrieval_done')
  return Number(done?.rawData?.selectedCount || 0)
})
const agentSummary = computed(() => {
  const parts = []
  if (toolCount.value) parts.push(`\u8c03\u7528 ${toolCount.value} \u4e2a\u5de5\u5177`)
  if (selectedCount.value) parts.push(`\u91c7\u7528 ${selectedCount.value} \u6761\u4f9d\u636e`)
  parts.push('\u67e5\u770b\u5de5\u4f5c\u8fc7\u7a0b')
  return parts.join(' \u00b7 ')
})
const agentLiveText = computed(() =>
  agentProgress.value.text || 'Agent \u6b63\u5728\u6267\u884c\u4efb\u52a1...',
)

async function copyMessage() {
  const text = bodyText.value || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const input = document.createElement('textarea')
    input.value = text
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    document.body.removeChild(input)
  }
}
</script>

<template>
  <article class="chat-message" :class="{ user: isUser, assistant: !isUser }">
    <div class="message-avatar" :class="{ user: isUser }">
      <span v-if="isUser">{{ userInitial }}</span>
      <el-icon v-else><ChatDotRound /></el-icon>
    </div>

    <div class="message-main">
      <div class="message-meta">
        <span>{{ isUser ? '我' : '检修 AI 助手' }}</span>
        <span>{{ message.timestamp }}</span>
        <span v-if="message.status === 'streaming'" class="status">生成中</span>
        <span v-if="message.status === 'stopped'" class="status warn">已停止</span>
        <span v-if="message.status === 'error'" class="status danger">异常</span>
      </div>

      <div v-if="isUser && messageImageUrls.length" class="image-list user-image-list">
        <el-image
          v-for="(image, index) in messageImageUrls"
          :key="`${image}-${index}`"
          class="chat-image"
          :src="image"
          :preview-src-list="messageImageUrls"
          :initial-index="index"
          fit="cover"
          preview-teleported
          hide-on-click-modal
          alt="上传图片"
        />
      </div>

      <div v-if="bodyText || !isUser" class="message-bubble">
        <p v-if="bodyText" class="message-text">{{ bodyText }}</p>

        <div v-if="!isUser && evidenceImages.length" class="evidence-list">
          <figure v-for="(item, index) in evidenceImages" :key="`${item.imageUrl}-${index}`" class="evidence-item">
            <el-image
              class="evidence-image"
              :src="item.imageUrl"
              :preview-src-list="evidenceImageUrls"
              :initial-index="index"
              fit="cover"
              preview-teleported
              hide-on-click-modal
              :alt="item.caption || item.sectionTitle || '证据图片'"
            />
            <figcaption v-if="item.caption || item.sectionTitle || item.page">
              <span v-if="item.caption">{{ item.caption }}</span>
              <span v-else-if="item.sectionTitle">{{ item.sectionTitle }}</span>
              <small v-if="item.page">P{{ item.page }}</small>
            </figcaption>
          </figure>
        </div>
        <div v-if="message.status === 'streaming' && !message.content" class="thinking">
          <span />
          <span />
          <span />
        </div>
      </div>

      <div v-if="showAgentLive" class="agent-live">
        <span class="agent-live-dot" />
        <span class="agent-live-text">{{ agentLiveText }}</span>
      </div>

      <template v-else-if="hasAgentWork">
        <button
          type="button"
          class="agent-summary"
          @click="emit('open-agent', message)"
        >
          <span class="agent-summary-icon"><el-icon><DataAnalysis /></el-icon></span>
          <span>{{ agentSummary }}</span>
          <el-icon class="agent-summary-arrow"><Right /></el-icon>
        </button>

        <div v-if="toolChips.length" class="agent-tool-chips">
          <button
            v-for="chip in toolChips"
            :key="chip.id"
            type="button"
            class="agent-tool-chip"
            @click="emit('open-agent', message, chip.id)"
          >
            <i />
            <span>{{ chip.title }}</span>
            <el-icon class="agent-tool-chip-arrow"><Right /></el-icon>
          </button>
        </div>
      </template>

      <div v-if="!isUser && bodyText" class="message-actions">
        <button type="button" title="复制回复" @click="copyMessage">
          <el-icon><CopyDocument /></el-icon>
          <span>复制</span>
        </button>
        <button
          type="button"
          :title="speaking ? '停止朗读' : '朗读回复'"
          :disabled="loadingSpeech"
          @click="toggleSpeak"
        >
          <el-icon :class="{ 'is-loading': loadingSpeech }">
            <Loading v-if="loadingSpeech" />
            <VideoPause v-else-if="speaking" />
            <VideoPlay v-else />
          </el-icon>
          <span>{{ loadingSpeech ? '合成中' : speaking ? '停止' : '朗读' }}</span>
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.chat-message {
  display: flex;
  gap: 10px;
  max-width: min(820px, 88%);
}

.chat-message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--plaza-heading);
  color: var(--plaza-panel-bg);
  box-shadow: var(--plaza-shadow-organic);
  font-weight: 700;
}

.message-avatar.user {
  background: var(--plaza-accent);
}

.message-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.chat-message.user .message-main {
  align-items: flex-end;
}

.message-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--plaza-text-muted);
  font-size: 11.5px;
}

.status {
  color: var(--plaza-accent);
  font-weight: 600;
}

.status.warn {
  color: var(--plaza-warning);
}

.status.danger {
  color: var(--plaza-danger);
}

.message-bubble {
  padding: 10px 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-card);
  color: var(--plaza-text);
  box-shadow: var(--plaza-shadow-organic);
}

.chat-message.user .message-bubble {
  border-color: transparent;
  background: var(--plaza-accent);
  color: #fff;
}

.message-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.65;
  font-size: 14px;
}

.image-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 7px;
}

.user-image-list {
  justify-content: flex-end;
  margin-bottom: 0;
}

.chat-image {
  width: 224px;
  height: 160px;
  max-width: min(224px, calc(100vw - 120px));
  border-radius: 6px;
  overflow: hidden;
  cursor: zoom-in;
  flex: 0 0 auto;
}

.chat-image :deep(.el-image__inner) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.evidence-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(136px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.evidence-item {
  margin: 0;
  overflow: hidden;
  border: 1px solid rgba(168, 150, 124, 0.24);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
}

.evidence-image {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  cursor: zoom-in;
}

.evidence-image :deep(.el-image__inner) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.evidence-item figcaption {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  padding: 6px 7px;
  color: var(--plaza-text-muted);
  font-size: 11.5px;
  line-height: 1.35;
}

.evidence-item figcaption span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-item figcaption small {
  flex-shrink: 0;
  color: var(--plaza-accent);
  font-weight: 700;
}

.agent-live {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  min-height: 34px;
  padding: 5px 12px;
  border: 1px solid var(--plaza-border);
  border-radius: 9px;
  color: var(--plaza-accent-hover);
  background: var(--plaza-accent-soft);
  font-size: 12px;
}

.agent-live-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--plaza-accent);
  animation: agent-live-pulse 1.1s ease-in-out infinite;
}

.agent-live-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-summary {
  display: flex;
  width: fit-content;
  max-width: 100%;
  min-height: 36px;
  align-items: center;
  gap: 8px;
  padding: 5px 9px 5px 6px;
  border: 1px solid var(--plaza-border);
  border-radius: 9px;
  color: #8c5a18;
  background: var(--plaza-accent-soft);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.agent-summary:hover {
  color: #a65e00;
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.agent-summary-icon {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 7px;
  color: #fff;
  background: linear-gradient(145deg, var(--plaza-accent), var(--plaza-accent));
  font-size: 13px;
}

.agent-summary-arrow {
  flex: 0 0 auto;
  color: var(--plaza-accent);
}

.agent-tool-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.agent-tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--plaza-border);
  border-radius: 999px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.agent-tool-chip:hover {
  color: #a65e00;
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.agent-tool-chip i {
  width: 5px;
  height: 5px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--plaza-accent);
}

.agent-tool-chip-arrow {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--plaza-text-muted);
}

@keyframes agent-live-pulse {
  0%, 100% { opacity: 0.4; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}

.message-actions {
  display: flex;
  gap: 6px;
}

.message-actions button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: var(--plaza-text-muted);
  font-size: 11.5px;
  cursor: pointer;
  padding: 2px 4px;
}

.message-actions button:hover {
  color: var(--plaza-accent);
}

.thinking {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: center;
  min-width: 52px;
  height: 24px;
}

.thinking span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--plaza-text-muted);
  animation: pulse 1.1s infinite;
}

.thinking span:nth-child(2) {
  animation-delay: 0.16s;
}

.thinking span:nth-child(3) {
  animation-delay: 0.32s;
}

@keyframes pulse {
  0%, 80%, 100% { opacity: 0.35; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-3px); }
}
</style>
