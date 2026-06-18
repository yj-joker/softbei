<script setup>
import { ref, onMounted } from 'vue'
import { Upload, Document, Plus, Check, Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { uploadMaintenanceManual } from '@/api/maintenanceManual'
import { searchDevices } from '@/api/graph'
import { notifyStore } from '@/stores/notifyStore'

const emit = defineEmits(['success'])

const fileInput = ref(null)
const dragOver = ref(false)
const uploadLoading = ref(false)
const uploadedFiles = ref([])

// 适用设备（本批次共用，应用到所有上传的文件）
const deviceOptions = ref([])
const selectedDeviceIds = ref([])

async function loadDevices() {
  try {
    const res = await searchDevices('', 50)
    if (res.code === '200' || res.code === 200) {
      deviceOptions.value = res.data || []
    }
  } catch (e) {
    console.warn('设备列表加载失败', e.message)
  }
}

onMounted(loadDevices)

function handleDragOver(e) {
  dragOver.value = true
}

function handleDragLeave() {
  dragOver.value = false
}

function handleDrop(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files).filter(f => validateFile(f))
  addFiles(files)
}

function handleFileSelect(e) {
  const files = Array.from(e.target.files).filter(f => validateFile(f))
  addFiles(files)
  e.target.value = ''
}

function validateFile(file) {
  const allowedTypes = ['.pdf']
  const ext = '.' + file.name.split('.').pop().toLowerCase()
  if (!allowedTypes.includes(ext)) {
    ElMessage.warning('仅支持 PDF 文件')
    return false
  }
  if (file.size > 100 * 1024 * 1024) {
    ElMessage.warning('文件大小不能超过 100MB')
    return false
  }
  return true
}

function addFiles(files) {
  files.forEach(file => {
    uploadedFiles.value.push({
      id: Date.now() + Math.random(),
      name: file.name,
      size: file.size,
      status: 'pending',
      file
    })
  })
}

function removeFile(id) {
  const index = uploadedFiles.value.findIndex(f => f.id === id)
  if (index > -1) uploadedFiles.value.splice(index, 1)
}

async function uploadFiles() {
  if (uploadedFiles.value.length === 0) {
    ElMessage.warning('请先选择要上传的文件')
    return
  }

  uploadLoading.value = true

  for (const item of uploadedFiles.value) {
    if (item.status === 'success') continue
    item.status = 'uploading'

    const formData = new FormData()
    formData.append('file', item.file)
    const nameWithoutExt = item.name.replace(/\.pdf$/i, '')
    formData.append('manualName', nameWithoutExt)
    formData.append('manualImage', '')
    formData.append('manualDesc', '')
    // 适用设备：每个 deviceId 作为重复表单字段，后端 @ModelAttribute 绑定为 List<String>
    selectedDeviceIds.value.forEach(id => formData.append('deviceIds', id))

    try {
      const res = await uploadMaintenanceManual(formData)
      if (res.code === '200' || res.code === 200) {
        item.status = 'success'
        // 登记后台「知识导入」任务：解析+向量化+图谱抽取是分钟级异步，完成后由 WebSocket 通知
        if (res.data?.id) {
          notifyStore.trackKnowledgeImport(res.data.id, `知识导入：${res.data.manualName || item.name || '手册'}`)
        }
        emit('success', res.data)
      } else {
        item.status = 'error'
        ElMessage.error(res.message || '上传失败')
      }
    } catch (e) {
      item.status = 'error'
      ElMessage.error('上传失败: ' + e.message)
    }
  }

  uploadLoading.value = false
  const successCount = uploadedFiles.value.filter(f => f.status === 'success').length
  if (successCount > 0) {
    ElMessage.success(`${successCount} 个文件上传成功`)
  }
  uploadedFiles.value = []
}

function resetState() {
  uploadedFiles.value = []
  selectedDeviceIds.value = []
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function triggerSelect() {
  fileInput.value?.click()
}

defineExpose({ uploadFiles, resetState })
</script>

<template>
  <div class="manual-upload">
    <!-- 适用设备（可选，本批次共用） -->
    <div class="device-field">
      <label class="device-label">
        适用设备 <span class="optional">(可选，本批次全部文件共用；通用手册可不选)</span>
      </label>
      <el-select
        v-model="selectedDeviceIds"
        multiple
        filterable
        collapse-tags
        collapse-tags-tooltip
        placeholder="选择该手册适用的设备"
        style="width: 100%"
      >
        <el-option
          v-for="d in deviceOptions"
          :key="d.id"
          :label="d.name + (d.model ? ' / ' + d.model : '')"
          :value="d.id"
        />
      </el-select>
    </div>

    <!-- 拖拽区域 -->
    <div
      class="drop-zone"
      :class="{ 'drag-over': dragOver }"
      @dragover.prevent="handleDragOver"
      @dragleave="handleDragLeave"
      @drop.prevent="handleDrop"
      @click="triggerSelect"
    >
      <input
        ref="fileInput"
        type="file"
        accept=".pdf"
        multiple
        style="display:none"
        @change="handleFileSelect"
      />
      <div class="drop-content">
        <div class="drop-icon">
          <el-icon><Plus /></el-icon>
        </div>
        <p class="drop-text">拖拽文件到此处，或 <span class="highlight">点击选择</span></p>
        <p class="drop-hint">支持 PDF，单个文件不超过 100MB</p>
      </div>
    </div>

    <!-- 文件列表 -->
    <div v-if="uploadedFiles.length > 0" class="file-list">
      <div v-for="item in uploadedFiles" :key="item.id" class="file-item">
        <div class="file-icon">
          <el-icon><Document /></el-icon>
        </div>
        <div class="file-info">
          <span class="file-name">{{ item.name }}</span>
          <span class="file-size">{{ formatSize(item.size) }}</span>
        </div>
        <div class="file-status">
          <span v-if="item.status === 'pending'" class="status-dot pending"></span>
          <span v-else-if="item.status === 'uploading'" class="status-dot uploading"></span>
          <span v-else-if="item.status === 'success'" class="status-dot success">
            <el-icon><Check /></el-icon>
          </span>
          <span v-else-if="item.status === 'error'" class="status-dot error">
            <el-icon><Close /></el-icon>
          </span>
        </div>
        <button class="remove-btn" @click.stop="removeFile(item.id)">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </div>

    <!-- 上传按钮 -->
    <div v-if="uploadedFiles.length > 0" class="upload-action">
      <el-button
        type="primary"
        size="large"
        :loading="uploadLoading"
        :disabled="uploadedFiles.length === 0 || uploadLoading"
        @click="uploadFiles"
      >
        <el-icon v-if="!uploadLoading"><Upload /></el-icon>
        {{ uploadLoading ? '上传中...' : '开始上传' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.manual-upload {
  width: 100%;
}

.device-field {
  margin-bottom: 16px;
}
.device-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--plaza-text);
  margin-bottom: 8px;
}
.device-label .optional {
  font-weight: 400;
  color: var(--plaza-text-muted);
}

.drop-zone {
  border: 2px dashed var(--plaza-border);
  border-radius: 14px;
  padding: 40px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-bottom: 16px;
}
.drop-zone:hover,
.drop-zone.drag-over {
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}
.drop-icon {
  width: 52px;
  height: 52px;
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  margin: 0 auto 16px;
}
.drop-text {
  font-size: 14px;
  color: var(--plaza-text-muted);
  margin-bottom: 6px;
}
.drop-text .highlight {
  color: var(--plaza-accent);
  font-weight: 600;
}
.drop-hint {
  font-size: 12px;
  color: var(--plaza-text-muted);
  opacity: 0.7;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
  max-height: 260px;
  overflow-y: auto;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  background: var(--plaza-bg);
  border-radius: 10px;
}
.file-icon {
  width: 34px;
  height: 34px;
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.file-info {
  flex: 1;
  min-width: 0;
}
.file-name {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--plaza-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.file-size {
  font-size: 12px;
  color: var(--plaza-text-muted);
}
.file-status {
  width: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.status-dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}
.status-dot.pending {
  background: rgba(156, 163, 175, 0.2);
}
.status-dot.uploading {
  border: 2px solid var(--plaza-accent);
  border-top-color: transparent;
  animation: spin 0.8s linear infinite;
}
.status-dot.success {
  background: var(--app-success, #22c55e);
  color: #fff;
}
.status-dot.error {
  background: var(--el-color-danger);
  color: #fff;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.remove-btn {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: var(--plaza-text-muted);
  border-radius: 6px;
  display: flex;
  align-items: center;
  transition: all 0.15s;
}
.remove-btn:hover {
  background: rgba(239, 68, 68, 0.1);
  color: var(--el-color-danger);
}

.upload-action {
  display: flex;
  justify-content: center;
}
</style>