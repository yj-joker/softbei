<script setup>
import { ref, reactive, onMounted, computed, nextTick } from 'vue'
import { Search, Plus, Download, Delete, Upload, Document, Folder, Files } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getMaintenanceManualList, deleteMaintenanceManual } from '@/api/maintenanceManual'
import MaintenanceManualUpload from '@/components/MaintenanceManualUpload.vue'
import MaintenanceManualUpdate from '@/components/MaintenanceManualUpdate.vue'

const searchQuery = ref('')
const uploadDialogVisible = ref(false)
const updateDialogVisible = ref(false)
const currentManual = ref(null)
const uploadRef = ref(null)
const loading = ref(false)
const list = ref([])
const pagination = reactive({ page: 1, pageSize: 12, total: 0 })

// 分类选项
const categoryOptions = [
  { label: '所有分类', value: '' },
  { label: 'PDF', value: 'pdf' },
]
const selectedCategory = ref('')

async function loadList() {
  loading.value = true
  try {
    const res = await getMaintenanceManualList({
      page: pagination.page,
      size: pagination.pageSize,
      manualName: searchQuery.value || undefined,
    })
    if (res.code === '200' || res.code === 200) {
      list.value = res.data.records || res.data.list || []
      pagination.total = res.data.total || 0
    }
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadList()
})

function handleSearch() {
  pagination.page = 1
  loadList()
}

function openUploadDialog() {
  uploadDialogVisible.value = true
  nextTick(() => {
    uploadRef.value?.resetState()
  })
}

function handleUploadSuccess() {
  uploadDialogVisible.value = false
  loadList()
}

async function handleDownload(item) {
  const fileUrl = item.fileUrl
  if (!fileUrl) {
    ElMessage.error('文件链接不存在，请确认该手册的文件已上传并解析完成')
    return
  }
  const fileName = (item.manualName || 'document') + '.' + ((item.fileType || '.pdf').replace(/^\./, ''))
  // 通过 fetch 获取文件 Blob，再用 <a download> 强制下载，避免浏览器预览
  const response = await fetch(fileUrl)
  if (!response.ok) throw new Error('文件获取失败')
  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = blobUrl
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(blobUrl)
  ElMessage.success('下载成功')
}

async function handlePreview(item) {
  const fileUrl = item.fileUrl
  if (!fileUrl) {
    ElMessage.error('文件链接不存在，请确认该手册的文件已上传并解析完成')
    return
  }
  window.open(fileUrl, '_blank')
}

async function handleDelete(id) {
  try {
    await ElMessageBox.confirm('确定删除该手册吗？', '删除确认', { type: 'warning' })
    const res = await deleteMaintenanceManual(id)
    if (res.code === '200' || res.code === 200) {
      ElMessage.success('删除成功')
      loadList()
    }
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

function openUpdateDialog(item) {
  currentManual.value = item
  updateDialogVisible.value = true
}

async function handleUpdateSuccess(newData) {
  updateDialogVisible.value = false
  if (newData) {
    currentManual.value = newData
    const idx = list.value.findIndex(item => item.id === newData.id)
    if (idx > -1) list.value[idx] = newData
  } else {
    currentManual.value = null
  }
  loadList()
}

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleDateString('zh-CN')
}

const filteredList = computed(() => {
  let items = list.value
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    items = items.filter(item =>
      (item.manualName || '').toLowerCase().includes(q) ||
      (item.fileName || '').toLowerCase().includes(q)
    )
  }
  return items
})

// 根据文件类型返回图标颜色
function getFileTypeColor(fileType) {
  const type = (fileType || 'pdf').toLowerCase().replace(/^\./, '')
  if (type === 'pdf') return { bg: '#FEF6EE', accent: '#F97316', label: 'PDF' }
  if (type === 'docx' || type === 'doc') return { bg: '#F7ECE9', accent: '#A8605F', label: 'WORD' }
  if (type === 'xlsx' || type === 'xls') return { bg: '#EEF3E6', accent: '#5E8C3E', label: 'EXCEL' }
  if (type === 'txt') return { bg: '#F4F0E8', accent: '#6B5D4C', label: 'TXT' }
  return { bg: '#FEF6EE', accent: '#F97316', label: 'FILE' }
}
</script>

<template>
  <div class="admin-knowledge">
    <!-- Page Header Card -->
    <div class="page-header-card">
      <div class="page-header">
        <div class="page-title-area">
          <h2 class="page-title">知识库管理</h2>
          <p class="page-desc">维修手册管理与 PDF 上传</p>
        </div>
        <el-button type="primary" class="upload-btn" @click="openUploadDialog">
          <el-icon><Upload /></el-icon>
          上传手册
        </el-button>
      </div>
    </div>

    <!-- Search Bar — 悬浮卡片风格 -->
    <div class="search-bar-wrapper">
      <div class="search-bar-card">
        <div class="search-bar-inner">
          <el-input
            v-model="searchQuery"
            placeholder="搜索手册名称..."
            class="search-input"
            clearable
            @keyup.enter="handleSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-button type="primary" class="search-btn" @click="handleSearch">
            <el-icon><Search /></el-icon>
            搜索
          </el-button>
        </div>
      </div>
    </div>

    <!-- 电子书架网格 -->
    <div v-loading="loading" class="knowledge-bookshelf">
      <!-- 空状态 -->
      <div v-if="filteredList.length === 0" class="empty-state">
        <div class="empty-shelf">
          <div class="shelf-icon-wrapper">
            <el-icon class="empty-icon"><Folder /></el-icon>
          </div>
          <h3 class="empty-title">暂无手册数据</h3>
          <p class="empty-hint">点击右上角「上传手册」添加第一本维修手册</p>
        </div>
      </div>

      <!-- 书架网格 -->
      <el-row v-else :gutter="20" class="book-row">
        <el-col
          v-for="item in filteredList"
          :key="item.id"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
          class="book-col"
        >
          <div class="book-card" :class="`file-${(item.fileType || 'pdf').toLowerCase()}`" @click="handlePreview(item)">
            <!-- 文件图标区域 -->
            <div class="book-cover">
              <div
                class="file-icon-badge"
                :style="{
                  background: getFileTypeColor(item.fileType).bg,
                  borderColor: getFileTypeColor(item.fileType).accent + '30'
                }"
              >
                <svg v-if="(item.fileType || 'pdf').toLowerCase().replace(/^\./, '') === 'pdf'" class="file-icon-svg" viewBox="0 0 48 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 4h24l12 12v44H8V4z" fill="#FEF6EE" stroke="#F97316" stroke-width="1.5"/>
                  <path d="M28 4v12h12" fill="#F3E8DC" stroke="#F97316" stroke-width="1.5"/>
                  <rect x="14" y="26" width="20" height="3" rx="1.5" fill="#F97316" opacity="0.6"/>
                  <rect x="14" y="33" width="16" height="3" rx="1.5" fill="#F97316" opacity="0.4"/>
                  <rect x="14" y="40" width="18" height="3" rx="1.5" fill="#F97316" opacity="0.25"/>
                  <text x="16" y="22" font-family="sans-serif" font-size="8" fill="#F97316" font-weight="700">PDF</text>
                </svg>
                <svg v-else-if="(item.fileType || 'doc').toLowerCase().replace(/^\./, '').includes('doc')" class="file-icon-svg" viewBox="0 0 48 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 4h24l12 12v44H8V4z" fill="#F7ECE9" stroke="#A8605F" stroke-width="1.5"/>
                  <path d="M28 4v12h12" fill="#ECD9D6" stroke="#A8605F" stroke-width="1.5"/>
                  <rect x="14" y="26" width="20" height="3" rx="1.5" fill="#A8605F" opacity="0.6"/>
                  <rect x="14" y="33" width="16" height="3" rx="1.5" fill="#A8605F" opacity="0.4"/>
                  <rect x="14" y="40" width="18" height="3" rx="1.5" fill="#A8605F" opacity="0.25"/>
                  <text x="12" y="22" font-family="sans-serif" font-size="7" fill="#A8605F" font-weight="700">WORD</text>
                </svg>
                <svg v-else class="file-icon-svg" viewBox="0 0 48 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 4h24l12 12v44H8V4z" fill="#F4F0E8" stroke="#B3A692" stroke-width="1.5"/>
                  <path d="M28 4v12h12" fill="#ECE3D4" stroke="#B3A692" stroke-width="1.5"/>
                  <rect x="14" y="26" width="20" height="3" rx="1.5" fill="#B3A692" opacity="0.6"/>
                  <rect x="14" y="33" width="16" height="3" rx="1.5" fill="#B3A692" opacity="0.4"/>
                  <text x="16" y="22" font-family="sans-serif" font-size="8" fill="#6B5D4C" font-weight="700">FILE</text>
                </svg>
              </div>
            </div>

            <!-- 书籍信息 -->
            <div class="book-info">
              <h4 class="book-title" :title="item.manualName">
                {{ item.manualName }}
              </h4>
              <p class="book-meta">
                <span
                  class="file-type-tag"
                  :style="{
                    background: getFileTypeColor(item.fileType).bg,
                    color: getFileTypeColor(item.fileType).accent
                  }"
                >{{ getFileTypeColor(item.fileType).label }}</span>
                <span class="file-size">{{ formatSize(item.fileSize) }}</span>
              </p>
              <p class="book-date">{{ formatDate(item.createdAt) }}</p>
            </div>

            <!-- 书架底部操作区 -->
            <div class="book-actions">
              <el-button size="small" class="action-btn download-btn" @click.stop="handleDownload(item)">
                <el-icon><Download /></el-icon>
                下载
              </el-button>
              <el-button size="small" class="action-btn update-btn" @click.stop="openUpdateDialog(item)">
                <el-icon><Upload /></el-icon>
                更新
              </el-button>
              <el-button size="small" class="action-btn delete-btn" @click.stop="handleDelete(item.id)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 分页 -->
    <div v-if="pagination.total > pagination.pageSize" class="pagination-bar">
      <el-pagination
        v-model:current-page="pagination.page"
        :page-size="pagination.pageSize"
        :total="pagination.total"
        layout="prev, pager, next, total"
        @current-change="loadList"
      />
    </div>

    <!-- 上传弹窗 -->
    <el-dialog
      v-model="uploadDialogVisible"
      title="上传维修手册"
      width="600px"
      :close-on-click-modal="false"
    >
      <MaintenanceManualUpload ref="uploadRef" @success="handleUploadSuccess" />
    </el-dialog>

    <!-- 更新弹窗 -->
    <MaintenanceManualUpdate
      v-model:visible="updateDialogVisible"
      :manual="currentManual"
      @success="handleUpdateSuccess"
    />
  </div>
</template>

<style scoped>
.admin-knowledge {
  width: 90%;
  margin: 0 auto;
  padding: 8px 0 32px;
}

/* ── Page Header Card ── */
.page-header-card {
  margin-top: -10px;
  width: 100%;
  background: #fff;
  border-radius: 12px;
  padding: 24px 28px 52px;
  margin-bottom: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06), 0 4px 12px rgba(0, 0, 0, 0.04);
}

/* ── Page Header ── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.page-title {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--plaza-text);
  margin-bottom: 6px;
  letter-spacing: -0.02em;
}
.page-desc {
  font-size: 14px;
  color: var(--plaza-text-muted);
}
.upload-btn {
  background: var(--plaza-accent);
  border-color: var(--plaza-accent);
  border-radius: var(--plaza-radius);
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: background 0.2s ease;
}
.upload-btn:hover {
  background: var(--plaza-accent-hover);
  border-color: var(--plaza-accent-hover);
}

/* ── Search Bar — 悬浮卡片风格 ── */
.search-bar-wrapper {
  position: relative;
  z-index: 10;
  margin-top: -28px;
  margin-bottom: 28px;
}
.search-bar-card {
  max-width: 640px;
  margin: 0 auto;
  background: var(--plaza-bg-card);
  border: 1.5px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  padding: 16px 20px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.10), 0 1px 4px rgba(0, 0, 0, 0.06);
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}
.search-bar-card:focus-within {
  border-color: var(--plaza-accent);
  box-shadow: 0 0 0 4px var(--plaza-accent-soft), var(--plaza-shadow-organic);
}
.search-bar-inner {
  display: flex;
  gap: 12px;
  align-items: center;
}
.search-input {
  flex: 1;
}
.search-btn {
  background: var(--plaza-accent);
  border-color: var(--plaza-accent);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
}
.search-btn:hover {
  background: var(--plaza-accent-hover);
  border-color: var(--plaza-accent-hover);
}

/* ── 电子书架 ── */
.knowledge-bookshelf {
  min-height: 320px;
}
.book-row {
  width: 100%;
}
.book-col {
  margin-bottom: 20px;
}

/* ── 书籍卡片 ── */
.book-card {
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  height: 100%;
  transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
  cursor: pointer;
}
.book-card:hover {
  border-color: var(--plaza-accent);
  box-shadow: var(--plaza-shadow-organic-hover);
  transform: translateY(-2px);
}

/* ── 书籍封面（图标区）── */
.book-cover {
  background: var(--plaza-bg);
  padding: 28px 20px 20px;
  display: flex;
  justify-content: center;
  align-items: center;
}
.file-icon-badge {
  width: 80px;
  height: 100px;
  border-radius: 8px;
  border: 1.5px solid;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.file-icon-svg {
  width: 52px;
  height: 68px;
}

/* ── 书籍信息 ── */
.book-info {
  padding: 16px 16px 12px;
  flex: 1;
}
.book-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.4;
  min-height: 2.8em;
}
.book-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.file-type-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
  letter-spacing: 0.05em;
}
.file-size {
  font-size: 12px;
  color: var(--plaza-text-muted);
}
.book-date {
  font-size: 12px;
  color: var(--plaza-text-muted);
}

/* ── 书籍操作区 ── */
.book-actions {
  padding: 12px 16px;
  border-top: 1px solid var(--plaza-border);
  display: flex;
  gap: 8px;
  background: var(--plaza-bg);
}
.action-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border-radius: var(--plaza-radius);
  font-size: 13px;
  transition: all 0.15s ease;
}
.download-btn {
  background: var(--plaza-accent-soft);
  border-color: transparent;
  color: var(--plaza-accent);
}
.download-btn:hover {
  background: var(--plaza-accent);
  color: #fff;
  border-color: var(--plaza-accent);
}
.update-btn {
  flex: 0 0 auto;
  min-width: 70px;
  background: linear-gradient(135deg, #F59E0B, #D97706);
  border-color: #F59E0B;
  color: #fff;
  padding: 8px 12px;
  font-weight: 500;
}
.update-btn:hover {
  background: linear-gradient(135deg, #D97706, #B45309);
  border-color: #D97706;
}
.delete-btn {
  flex: 0 0 40px;
  background: transparent;
  border-color: var(--plaza-border);
  color: var(--plaza-text-muted);
  padding: 8px;
}
.delete-btn:hover {
  background: #FEF2F2;
  border-color: #fca5a5;
  color: #ef4444;
}

/* ── 空状态 ── */
.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 360px;
  width: 100%;
}
.empty-shelf {
  text-align: center;
}
.shelf-icon-wrapper {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--plaza-bg-card);
  border: 1.5px dashed var(--plaza-border);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 20px;
}
.empty-icon {
  font-size: 36px;
  color: var(--plaza-text-muted);
  opacity: 0.4;
}
.empty-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 8px;
}
.empty-hint {
  font-size: 13px;
  color: var(--plaza-text-muted);
}

/* ── 分页 ── */
.pagination-bar {
  margin-top: 28px;
  display: flex;
  justify-content: center;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .admin-knowledge {
    padding: 8px 0 20px;
  }
  .page-header-card {
    padding: 18px 16px 46px;
  }
  .page-header {
    flex-direction: column;
    gap: 16px;
    align-items: stretch;
  }
  .upload-btn {
    align-self: flex-end;
    width: fit-content;
  }
  .search-bar-inner {
    flex-wrap: wrap;
  }
  .search-input {
    min-width: 0;
  }
  .search-btn {
    width: 100%;
    justify-content: center;
  }
}
</style>