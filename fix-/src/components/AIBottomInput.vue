<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Close,
  Document,
  Microphone,
  Picture,
  Position,
  VideoPause,
} from '@element-plus/icons-vue'
import { useAsrStream } from '@/composables/useAsrStream'
import { uploadImage } from '@/api/user'
import { extractUploadedImageUrl } from '@/utils/upload'

const props = defineProps({
  generating: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'stop'])

const inputValue = ref('')
const uploadedFiles = ref([])
const imageInput = ref(null)
const textInput = ref(null)
const isThinking = ref(false)

// 实时语音识别（流式）：recording=录音中，partial=未定稿实时文本
const { recording: isRecording, partial: asrPartial, start: startAsr, stop: stopAsr, cleanup: cleanupAsr } = useAsrStream()

const hasUploading = computed(() => uploadedFiles.value.some((file) => file.status === 'uploading'))
const hasActiveFile = computed(() => uploadedFiles.value.some((file) => file.status === 'success' || file.status === 'uploading'))
const thinkingActive = computed(() => props.generating || isThinking.value)
const canSend = computed(() => {
  return !props.generating
    && (inputValue.value.trim().length > 0 || hasActiveFile.value)
})

function pickImage() {
  imageInput.value?.click()
}

async function startImageUpload(item, file) {
  try {
    const res = await uploadImage(file)
    const url = extractUploadedImageUrl(res)
    if (!url) throw new Error('上传接口未返回图片地址')
    item.url = url
    item.status = 'success'
    return item
  } catch (error) {
    item.status = 'error'
    ElMessage.error(`图片上传失败：${error.message || '请稍后再试'}`)
    return null
  }
}

async function handleImageUpload(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return

  for (const file of files) {
    if (!file.type.startsWith('image/')) continue
    const item = {
      id: `${Date.now()}-${file.name}`,
      name: file.name,
      type: 'image',
      url: URL.createObjectURL(file),
      status: 'uploading',
      uploadPromise: null,
    }
    item.uploadPromise = startImageUpload(item, file)
    uploadedFiles.value.push(item)
  }
}

function handleDocClick() {
  ElMessage.info('当前对话暂未接入文档上传，建议先使用图片或文字描述。')
}

function removeFile(id) {
  uploadedFiles.value = uploadedFiles.value.filter((file) => file.id !== id)
}

function handleSend() {
  if (!canSend.value) return
  emit('send', {
    text: inputValue.value,
    files: uploadedFiles.value.filter((file) => file.status !== 'error'),
    thinking: true,
  })
  inputValue.value = ''
  uploadedFiles.value = []
  isThinking.value = false
}

async function toggleRecording() {
  if (isRecording.value) {
    stopAsr()
  } else {
    try {
      // 每定稿一句就追加进输入框；实时未定稿文本通过 asrPartial 单独展示
      await startAsr({ onFinal: (text) => { inputValue.value += text } })
      textInput.value?.focus()
    } catch (error) {
      ElMessage.error(error.message || '无法开始语音输入')
    }
  }
}

onUnmounted(() => {
  cleanupAsr()
})
</script>

<template>
  <footer class="ai-composer">
    <div v-if="uploadedFiles.length" class="attachment-strip">
      <div
        v-for="file in uploadedFiles"
        :key="file.id"
        class="attachment"
        :class="file.status"
      >
        <img v-if="file.type === 'image'" :src="file.url" :alt="file.name" />
        <div class="attachment-meta">
          <strong>{{ file.name }}</strong>
          <span v-if="file.status === 'uploading'">上传中</span>
          <span v-else-if="file.status === 'error'">上传失败</span>
          <span v-else>已上传</span>
        </div>
        <button type="button" title="移除" @click="removeFile(file.id)">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </div>

    <div class="composer-panel">
      <div class="composer-tools">
        <input
          ref="imageInput"
          type="file"
          accept="image/*"
          multiple
          hidden
          @change="handleImageUpload"
        />
        <button type="button" :class="{ active: thinkingActive }" title="伪思考状态" @click="isThinking = !isThinking">
          <span class="think-dot" />
          <span>思考</span>
        </button>
        <button type="button" title="上传图片" @click="pickImage">
          <el-icon><Picture /></el-icon>
          <span>图片</span>
        </button>
        <button type="button" title="上传文档" @click="handleDocClick">
          <el-icon><Document /></el-icon>
          <span>文档</span>
        </button>
      </div>

      <div class="composer-row">
        <textarea
          ref="textInput"
          v-model="inputValue"
          rows="2"
          :placeholder="isRecording ? '正在听，请说话…' : '描述设备现象、故障代码、检修步骤，AI 会结合知识库给出建议'"
          @keydown.enter.exact.prevent="handleSend"
        />

        <button
          type="button"
          class="round-btn"
          :class="{ recording: isRecording }"
          :title="isRecording ? '停止录音' : '语音输入'"
          @click="toggleRecording"
        >
          <el-icon><Microphone /></el-icon>
        </button>

        <button
          v-if="generating"
          type="button"
          class="round-btn stop"
          title="停止生成"
          @click="emit('stop')"
        >
          <el-icon><VideoPause /></el-icon>
        </button>
        <button
          v-else
          type="button"
          class="round-btn send"
          :disabled="!canSend"
          title="发送"
          @click="handleSend"
        >
          <el-icon><Position /></el-icon>
        </button>
      </div>

      <div class="composer-hint">
        <span v-if="isRecording" class="asr-live"><span class="asr-dot" />{{ asrPartial || '聆听中…' }}</span>
        <span v-else-if="hasUploading">点击发送后，图片上传会计入 AI 思考时间</span>
        <span v-else>Enter 发送，Shift + Enter 换行</span>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.ai-composer {
  flex-shrink: 0;
  padding: 8px 24px 12px;
  background: linear-gradient(180deg, rgba(246, 248, 251, 0), var(--plaza-bg) 26%);
}

/* 实时语音未定稿文本 */
.asr-live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--plaza-accent);
  font-weight: 600;
}
.asr-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--plaza-accent);
  animation: asr-pulse 1s ease-in-out infinite;
}
@keyframes asr-pulse { 50% { opacity: 0.25; transform: scale(0.7); } }

.attachment-strip {
  max-width: 920px;
  margin: 0 auto 6px;
  display: flex;
  gap: 8px;
  overflow-x: auto;
}

.attachment {
  min-width: 230px;
  display: grid;
  grid-template-columns: 44px 1fr 24px;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-card);
}

.attachment.error {
  border-color: rgba(239, 68, 68, 0.34);
}

.attachment img {
  width: 44px;
  height: 36px;
  border-radius: 6px;
  object-fit: cover;
}

.attachment-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.attachment-meta strong {
  overflow: hidden;
  color: var(--plaza-text);
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-meta span {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.attachment button,
.composer-tools button,
.round-btn {
  border: 0;
  cursor: pointer;
  font-family: inherit;
}

.attachment button {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  background: transparent;
  color: var(--plaza-text-muted);
}

.attachment button:hover {
  color: var(--plaza-danger);
  background: var(--plaza-danger-soft);
}

.composer-panel {
  max-width: 920px;
  margin: 0 auto;
  border: 1px solid var(--plaza-border);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: var(--plaza-shadow-organic);
  overflow: hidden;
}

.composer-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--plaza-border);
}

.composer-tools button {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 8px;
  border-radius: 7px;
  background: transparent;
  color: var(--plaza-text-muted);
  font-size: 12.5px;
}

.composer-tools button:hover,
.composer-tools button.active {
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
}

.think-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}

.composer-row {
  display: grid;
  grid-template-columns: 1fr 36px 36px;
  align-items: end;
  gap: 6px;
  padding: 8px 10px 6px;
}

textarea {
  width: 100%;
  min-height: 44px;
  max-height: 120px;
  resize: vertical;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--plaza-text);
  font-size: 14px;
  line-height: 1.55;
}

textarea::placeholder {
  color: var(--plaza-text-muted);
}

.round-btn {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--plaza-bg-input);
  color: var(--plaza-text-muted);
}

.round-btn:hover {
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
}

.round-btn.recording {
  background: var(--plaza-danger-soft);
  color: var(--plaza-danger);
}

.round-btn.send {
  background: var(--plaza-accent);
  color: #fff;
}

.round-btn.send:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.round-btn.stop {
  background: var(--plaza-danger);
  color: #fff;
}

.composer-hint {
  min-height: 18px;
  padding: 0 10px 6px;
  color: var(--plaza-text-muted);
  font-size: 11.5px;
}
</style>
