<script setup>
import { ref, watch, onMounted } from 'vue'
import { Upload, Document, Plus, Check, Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { updateMaintenanceManual, getMaintenanceManualDetail } from '@/api/maintenanceManual'
import { searchDevices } from '@/api/graph'
import { notifyStore } from '@/stores/notifyStore'

const props = defineProps({
  visible: Boolean,
  manual: Object // { id, manualName, fileType, fileSize, ... }
})

const emit = defineEmits(['update:visible', 'success'])

const fileInput = ref(null)
const dragOver = ref(false)
const uploadLoading = ref(false)
const selectedFile = ref(null)

const manualNameInput = ref('')
const manualImageInput = ref('')
const manualDescInput = ref('')

// 适用设备
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

// 拉取手册详情以回填当前已关联设备（列表接口不含 devices）
async function loadLinkedDevices(id) {
  selectedDeviceIds.value = []
  if (!id) return
  try {
    const res = await getMaintenanceManualDetail(id)
    if (res.code === '200' || res.code === 200) {
      selectedDeviceIds.value = (res.data?.devices || []).map(d => d.deviceId).filter(Boolean)
    }
  } catch (e) {
    console.warn('已关联设备加载失败', e.message)
  }
}

watch(() => props.manual, (val) => {
  if (val) {
    manualNameInput.value = val.manualName || ''
    manualImageInput.value = val.manualImage || ''
    manualDescInput.value = val.manualDesc || ''
    selectedFile.value = null
    loadLinkedDevices(val.id)
  }
}, { immediate: true })

watch(() => props.visible, (val) => {
  if (val && props.manual) {
    manualNameInput.value = props.manual.manualName || ''
    manualImageInput.value = props.manual.manualImage || ''
    manualDescInput.value = props.manual.manualDesc || ''
    selectedFile.value = null
    loadLinkedDevices(props.manual.id)
  }
})

function handleDragOver(e) {
  dragOver.value = true
}

function handleDragLeave() {
  dragOver.value = false
}

function handleDrop(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files)
  if (files.length > 0) {
    selectFile(files[0])
  }
}

function handleFileSelect(e) {
  const files = Array.from(e.target.files)
  if (files.length > 0) {
    selectFile(files[0])
  }
  e.target.value = ''
}

function selectFile(file) {
  if (!validateFile(file)) return
  selectedFile.value = {
    name: file.name,
    size: file.size,
    file
  }
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

function removeFile() {
  selectedFile.value = null
}

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function handleUpdate() {
  if (!manualNameInput.value.trim()) {
    ElMessage.warning('请输入手册名称')
    return
  }

  uploadLoading.value = true

  const formData = new FormData()
  formData.append('id', props.manual.id)
  formData.append('manualName', manualNameInput.value.trim())
  formData.append('manualImage', manualImageInput.value.trim())
  formData.append('manualDesc', manualDescInput.value.trim())
  // 适用设备：编辑场景总是表达完整意图。非空则逐个追加；为空时追加一个空串作为
  // “已携带但清空”的信号（后端按空白过滤后即清空全部关联），以区别于“未携带=不改动”。
  if (selectedDeviceIds.value.length > 0) {
    selectedDeviceIds.value.forEach(id => formData.append('deviceIds', id))
  } else {
    formData.append('deviceIds', '')
  }
  if (selectedFile.value) {
    formData.append('file', selectedFile.value.file)
  }

  // 换了文件才会触发后台重新解析（解析+向量化+图谱抽取是分钟级异步）
  const reparsing = !!selectedFile.value

  try {
    const res = await updateMaintenanceManual(formData)
    if (res.code === '200' || res.code === 200) {
      if (reparsing) {
        // 登记后台「知识导入」任务，解析完成后由 WebSocket 通知，避免管理员误以为已就绪
        const manualId = res.data?.id || props.manual.id
        if (manualId) {
          notifyStore.trackKnowledgeImport(manualId, `知识更新：${manualNameInput.value.trim() || '手册'}`)
        }
        ElMessage.success('已提交更新，新文件正在后台解析，完成后会通知您')
      } else {
        ElMessage.success('更新成功')
      }
      emit('success', res.data)
      emit('update:visible', false)
    } else {
      ElMessage.error(res.message || '更新失败')
    }
  } catch (e) {
    ElMessage.error('更新失败: ' + (e.message || '请稍后重试'))
  } finally {
    uploadLoading.value = false
  }
}

function triggerSelect() {
  fileInput.value?.click()
}

function handleClose() {
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="更新手册"
    width="500px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:visible', $event)"
    @close="handleClose"
  >
    <div class="manual-update">
      <!-- 当前信息 -->
      <div v-if="manual" class="current-info">
        <div class="current-label">当前手册</div>
        <div class="current-name">{{ manual.manualName }}</div>
        <div class="current-meta">{{ manual.fileType?.toUpperCase() }} · {{ formatSize(manual.fileSize) }}</div>
      </div>

      <!-- 手册名称 -->
      <div class="form-item">
        <label class="form-label">手册名称</label>
        <el-input
          v-model="manualNameInput"
          placeholder="请输入手册名称"
          clearable
        />
      </div>

      <!-- 手册封面地址 -->
      <div class="form-item">
        <label class="form-label">封面地址</label>
        <el-input
          v-model="manualImageInput"
          placeholder="请输入封面图片URL"
          clearable
        />
      </div>

      <!-- 手册简介 -->
      <div class="form-item">
        <label class="form-label">手册简介</label>
        <el-input
          v-model="manualDescInput"
          type="textarea"
          :rows="3"
          placeholder="请输入手册简介"
          clearable
        />
      </div>

      <!-- 适用设备 -->
      <div class="form-item">
        <label class="form-label">适用设备 <span class="optional">(可选，通用手册可清空)</span></label>
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

      <!-- 文件选择 -->
      <div class="form-item">
        <label class="form-label">更换文件 <span class="optional">(可选，不选则保留原文件)</span></label>
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
            style="display:none"
            @change="handleFileSelect"
          />
          <div v-if="!selectedFile" class="drop-content">
            <div class="drop-icon">
              <el-icon><Plus /></el-icon>
            </div>
            <p class="drop-text">点击或拖拽 PDF 文件到此处</p>
            <p class="drop-hint">不选择则保持原文件</p>
          </div>
          <div v-else class="selected-file">
            <div class="file-icon">
              <el-icon><Document /></el-icon>
            </div>
            <div class="file-info">
              <span class="file-name">{{ selectedFile.name }}</span>
              <span class="file-size">{{ formatSize(selectedFile.size) }}</span>
            </div>
            <button class="remove-btn" @click.stop="removeFile">
              <el-icon><Close /></el-icon>
            </button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button type="primary" :loading="uploadLoading" @click="handleUpdate">
          <el-icon v-if="!uploadLoading"><Upload /></el-icon>
          {{ uploadLoading ? '更新中...' : '确认更新' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.manual-update {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.current-info {
  background: var(--plaza-bg);
  border-radius: var(--plaza-radius);
  padding: 16px;
  border: 1px solid var(--plaza-border);
}

.current-label {
  font-size: 12px;
  color: var(--plaza-text-muted);
  margin-bottom: 8px;
}

.current-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 4px;
}

.current-meta {
  font-size: 12px;
  color: var(--plaza-text-muted);
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--plaza-text);
}

.form-label .optional {
  font-weight: 400;
  color: var(--plaza-text-muted);
}

.drop-zone {
  border: 2px dashed var(--plaza-border);
  border-radius: 12px;
  padding: 24px 16px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.drop-zone:hover,
.drop-zone.drag-over {
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.drop-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.drop-icon {
  width: 44px;
  height: 44px;
  background: var(--plaza-accent-soft);
  color: var(--plaza-accent);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  margin-bottom: 8px;
}

.drop-text {
  font-size: 14px;
  color: var(--plaza-text);
  font-weight: 500;
}

.drop-hint {
  font-size: 12px;
  color: var(--plaza-text-muted);
}

.selected-file {
  display: flex;
  align-items: center;
  gap: 12px;
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
  text-align: left;
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

.dialog-footer .el-button--primary {
  background: linear-gradient(135deg, #F59E0B, #D97706);
  border-color: #F59E0B;
  color: #fff;
  font-weight: 500;
}
.dialog-footer .el-button--primary:hover {
  background: linear-gradient(135deg, #D97706, #B45309);
  border-color: #D97706;
}
</style>