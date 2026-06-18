<script setup>
import { computed, reactive, ref, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Microphone, VideoPause, MagicStick, UploadFilled } from '@element-plus/icons-vue'
import { uploadImage } from '@/api/user'
import { useAsrStream } from '@/composables/useAsrStream'
import { draftFromUpload } from '@/api/caseRecord'
import CaseSubmitDialog from '@/components/case/CaseSubmitDialog.vue'

// ---- 文字描述 ----
const rawText = ref('')

// ---- 图片（上传到 MinIO 拿公网 URL）----
const imageMap = reactive({})   // uid -> url
const uploadedImageUrls = computed(() => Object.values(imageMap).filter(Boolean))

async function handleImageUpload(option) {
  try {
    const res = await uploadImage(option.file)
    const url = res?.data
    if (!url) throw new Error('未返回图片地址')
    imageMap[option.file.uid] = url
    option.onSuccess?.(res)
  } catch (err) {
    ElMessage.error('图片上传失败：' + (err.message || ''))
    option.onError?.(err)
  }
}

function handleImageRemove(file) {
  const uid = file?.raw?.uid ?? file?.uid
  if (uid != null) delete imageMap[uid]
}

// ---- 文件（pdf/txt/docx，原始 File 交后端抽取）----
const fileList = ref([])
function handleFileChange(_file, files) {
  fileList.value = files
}
function handleFileRemove(_file, files) {
  fileList.value = files
}

// ---- 语音（实时流式 ASR：麦克风 → WS /weixiu/ai/asr-stream → 文本实时追加进描述）----
const { recording: isRecording, partial: asrPartial, start: startAsr, stop: stopAsr, cleanup: cleanupAsr } = useAsrStream()

async function toggleRecord() {
  if (isRecording.value) {
    stopAsr()
    return
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    ElMessage.warning('当前浏览器不支持录音')
    return
  }
  try {
    // 每定稿一句就追加到描述；实时未定稿文本由 asrPartial 单独展示
    await startAsr({ onFinal: (text) => { rawText.value = rawText.value ? `${rawText.value}${text}` : text } })
  } catch (err) {
    ElMessage.error(err.message || '无法开始语音输入')
  }
}

onUnmounted(() => { cleanupAsr() })

// ---- 生成草稿 ----
const generating = ref(false)
const caseDialog = ref(false)
const caseDraft = ref(null)

const hasMaterial = computed(
  () => rawText.value.trim() || uploadedImageUrls.value.length || fileList.value.length,
)

function deriveSourceType() {
  if (fileList.value.length) return 'file'
  if (uploadedImageUrls.value.length) return 'note_photo'
  return 'voice'  // 仅文字/语音转写
}

async function handleGenerate() {
  if (!hasMaterial.value) {
    ElMessage.warning('请至少填写文字描述、上传文件或图片')
    return
  }
  generating.value = true
  try {
    const fd = new FormData()
    fileList.value.forEach((f) => { if (f.raw) fd.append('files', f.raw, f.name) })
    uploadedImageUrls.value.forEach((url) => fd.append('imageUrls', url))
    if (rawText.value.trim()) fd.append('rawText', rawText.value.trim())
    fd.append('sourceType', deriveSourceType())

    const res = await draftFromUpload(fd)
    caseDraft.value = {
      ...(res?.data || {}),
      imageUrls: uploadedImageUrls.value,
    }
    caseDialog.value = true
  } catch (err) {
    ElMessage.error('AI 起草失败：' + (err.message || ''))
  } finally {
    generating.value = false
  }
}

function onSubmitted() {
  // 提交成功后清空，便于继续录入下一条
  rawText.value = ''
  fileList.value = []
  Object.keys(imageMap).forEach((k) => delete imageMap[k])
  caseDraft.value = null
}
</script>

<template>
  <div class="case-upload">
    <div class="cu-head">
      <h2>经验上传</h2>
      <p>上传文件、现场照片、文字描述或口述语音，AI 整理成结构化案例草稿，经审核后纳入知识图谱。</p>
    </div>

    <el-card class="cu-card" shadow="never">
      <!-- 文字 + 语音 -->
      <div class="cu-field">
        <div class="cu-label">
          <span>文字描述 / 语音口述</span>
          <span v-if="isRecording" class="asr-live"><span class="asr-dot" />{{ asrPartial || '聆听中…' }}</span>
          <el-button
            :type="isRecording ? 'danger' : 'primary'"
            text
            @click="toggleRecord"
          >
            <el-icon style="margin-right: 4px">
              <VideoPause v-if="isRecording" />
              <Microphone v-else />
            </el-icon>
            {{ isRecording ? '停止录音' : '语音输入' }}
          </el-button>
        </div>
        <el-input
          v-model="rawText"
          type="textarea"
          :rows="5"
          placeholder="描述这次检修的设备、故障现象、处理过程和经验心得。也可点击右上角“语音输入”口述，自动转写追加到此处。"
        />
      </div>

      <!-- 文件 -->
      <div class="cu-field">
        <div class="cu-label"><span>上传文件</span><em>支持 pdf / txt / docx / md</em></div>
        <el-upload
          :auto-upload="false"
          :file-list="fileList"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          accept=".pdf,.txt,.docx,.md"
          multiple
          drag
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
        </el-upload>
      </div>

      <!-- 图片 -->
      <div class="cu-field">
        <div class="cu-label"><span>现场照片 / 笔记拍照</span><em>将自动识别图中文字</em></div>
        <el-upload
          list-type="picture-card"
          :http-request="handleImageUpload"
          :on-remove="handleImageRemove"
          accept="image/*"
          multiple
        >
          <el-icon><Plus /></el-icon>
        </el-upload>
      </div>

      <div class="cu-actions">
        <el-button
          type="primary"
          size="large"
          :loading="generating"
          :disabled="!hasMaterial"
          @click="handleGenerate"
        >
          <el-icon style="margin-right: 6px"><MagicStick /></el-icon>
          AI 整理生成草稿
        </el-button>
      </div>
    </el-card>

    <CaseSubmitDialog v-model:visible="caseDialog" :draft="caseDraft" @submitted="onSubmitted" />
  </div>
</template>

<style scoped>
.case-upload {
  max-width: 860px;
  margin: 0 auto;
  padding: 8px 4px 32px;
}

.cu-head h2 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 700;
  color: var(--plaza-heading, #1f2937);
}

.cu-head p {
  margin: 0 0 18px;
  color: var(--plaza-text-muted, #6b7280);
  font-size: 14px;
  line-height: 1.6;
}

.cu-card {
  border-radius: 14px;
  border: 1px solid var(--plaza-border, #e5e7eb);
}

.cu-field {
  margin-bottom: 22px;
}

.cu-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.cu-label span {
  font-weight: 600;
  color: var(--plaza-heading, #374151);
}

.cu-label em {
  font-style: normal;
  font-size: 12px;
  color: var(--plaza-text-muted, #9ca3af);
}

/* 实时语音未定稿文本 */
.cu-label .asr-live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  margin-right: 10px;
  font-weight: 600;
  font-size: 12px;
  color: var(--plaza-accent, #c4602f);
  max-width: 320px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.cu-label .asr-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--plaza-accent, #c4602f);
  animation: asr-pulse 1s ease-in-out infinite;
}
@keyframes asr-pulse { 50% { opacity: 0.25; transform: scale(0.7); } }

.cu-actions {
  display: flex;
  justify-content: center;
  margin-top: 4px;
}
</style>
