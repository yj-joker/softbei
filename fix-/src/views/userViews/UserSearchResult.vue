<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { gsap } from 'gsap'
import { Search, Download, Folder, ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getMaintenanceManualList, getMaintenanceManualDetail, startMaintenanceManualRead, maintenanceManualHeartbeat } from '@/api/maintenanceManual'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const searchQuery = ref(route.query.keyword || '')
const list = ref([])
const pagination = reactive({ page: 1, pageSize: 12, total: 0 })
const currentReadSession = ref(null)
const heartbeatTimer = ref(null)
const HEARTBEAT_INTERVAL = 20000

async function loadList() {
  if (!searchQuery.value.trim()) {
    list.value = []
    pagination.total = 0
    return
  }
  loading.value = true
  try {
    const res = await getMaintenanceManualList({
      page: pagination.page,
      size: pagination.pageSize,
      manualName: searchQuery.value,
    })
    if (res.code === '200' || res.code === 200) {
      list.value = res.data.records || res.data.list || []
      pagination.total = res.data.total || 0
      await nextTick()
      if (!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        const cards = document.querySelectorAll('.book-col')
        if (cards.length) gsap.from(cards, { y: 24, opacity: 0, duration: 0.5, ease: 'power3.out', stagger: 0.04 })
      }
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
  router.replace({ path: '/user/search-result', query: { keyword: searchQuery.value } })
  loadList()
}

function goBack() {
  router.back()
}

function stopHeartbeat() {
  if (heartbeatTimer.value) {
    clearInterval(heartbeatTimer.value)
    heartbeatTimer.value = null
  }
  currentReadSession.value = null
}

onBeforeUnmount(() => {
  stopHeartbeat()
})

async function handleRead(id) {
  // 开始阅读会话并启动心跳
  try {
    const startRes = await startMaintenanceManualRead(id)
    if (startRes.code === '200' || startRes.code === 200) {
      currentReadSession.value = startRes.data.readSessionId
      await maintenanceManualHeartbeat(currentReadSession.value)
      heartbeatTimer.value = setInterval(async () => {
        if (currentReadSession.value) {
          await maintenanceManualHeartbeat(currentReadSession.value)
        }
      }, HEARTBEAT_INTERVAL)
    }
  } catch (e) {
    console.warn('阅读会话创建失败', e.message)
  }

  // 打开文件
  try {
    const res = await getMaintenanceManualDetail(id)
    const fileUrl = res.data?.fileUrl
    if (!fileUrl) {
      ElMessage.error('文件链接不存在，请确认该手册已上传并解析完成')
      return
    }
    window.open(fileUrl, '_blank')
  } catch (e) {
    ElMessage.error('打开手册失败：' + (e.message || '请稍后重试'))
  }
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

function getFileTypeColor(fileType) {
  const type = (fileType || 'pdf').toLowerCase().replace(/^\./, '')
  if (type === 'pdf') return { bg: '#FEF6EE', accent: '#F97316', label: 'PDF' }
  if (type === 'docx' || type === 'doc') return { bg: '#F7ECE9', accent: '#A8605F', label: 'WORD' }
  if (type === 'xlsx' || type === 'xls') return { bg: '#EEF3E6', accent: '#5E8C3E', label: 'EXCEL' }
  if (type === 'txt') return { bg: '#F4F0E8', accent: '#6B5D4C', label: 'TXT' }
  return { bg: '#FEF6EE', accent: '#F97316', label: 'FILE' }
}

// 监听路由 query 变化（支持浏览器前进后退）
watch(() => route.query.keyword, (newVal) => {
  searchQuery.value = newVal || ''
  pagination.page = 1
  loadList()
})
</script>

<template>
  <div class="search-result">
    <!-- Search Bar -->
    <div class="search-bar-wrapper">
      <div class="search-bar-card">
        <el-button text class="back-btn" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
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

    <!-- Results -->
    <div v-loading="loading" class="results-area">
      <div v-if="!searchQuery.trim()" class="state-empty">
        <p>请输入搜索关键词</p>
      </div>
      <div v-else-if="list.length === 0 && !loading" class="state-empty">
        <p>未找到相关手册「{{ searchQuery }}」</p>
        <p class="hint">尝试其他关键词</p>
      </div>
      <template v-else>
        <div class="results-count">找到 {{ pagination.total }} 个结果</div>
        <el-row :gutter="20" class="book-row">
          <el-col
            v-for="item in list"
            :key="item.id"
            :xs="24"
            :sm="12"
            :md="8"
            :lg="6"
            class="book-col"
          >
            <div class="book-card" @click="handleRead(item.id)">
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
              <div class="book-info">
                <h4 class="book-title" :title="item.manualName">{{ item.manualName }}</h4>
                <p class="book-meta">
                  <span class="file-type-tag" :style="{ background: getFileTypeColor(item.fileType).bg, color: getFileTypeColor(item.fileType).accent }">{{ getFileTypeColor(item.fileType).label }}</span>
                  <span class="file-size">{{ formatSize(item.fileSize) }}</span>
                </p>
                <p class="book-date">{{ formatDate(item.createdAt) }}</p>
              </div>
            </div>
          </el-col>
        </el-row>
      </template>
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
  </div>
</template>

<style scoped>
.search-result {
  width: 90%;
  margin: 0 auto;
  padding: 8px 0 32px;
}

.search-bar-wrapper {
  margin: 8px 0 28px;
}
.search-bar-card {
  max-width: 640px;
  margin: 0 auto;
  background: var(--plaza-bg-card);
  border: 1.5px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  padding: 12px 16px;
  display: flex;
  gap: 10px;
  align-items: center;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.10);
}
.search-bar-card:focus-within {
  border-color: var(--plaza-accent);
}
.back-btn {
  font-size: 18px;
  color: var(--plaza-text-muted);
  flex-shrink: 0;
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

.results-area {
  min-height: 320px;
}
.state-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  color: var(--plaza-text-muted);
  font-size: 15px;
}
.state-empty .hint {
  font-size: 13px;
  margin-top: 8px;
  opacity: 0.7;
}
.results-count {
  font-size: 14px;
  color: var(--plaza-text-muted);
  margin-bottom: 20px;
}
.book-row { width: 100%; }
.book-col { margin-bottom: 20px; }

.book-card {
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  height: 100%;
  cursor: pointer;
  box-shadow: var(--plaza-shadow-organic);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}
.book-card:hover {
  border-color: var(--plaza-accent);
  box-shadow: var(--plaza-shadow-organic-hover);
  transform: translateY(-4px);
}
.book-card:hover .file-icon-badge { transform: scale(1.06) rotate(-2deg); }
.file-icon-badge { transition: transform 0.25s cubic-bezier(.22,1,.36,1); }

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
.file-icon-badge .file-icon-svg {
  width: 52px;
  height: 68px;
}

.book-info {
  padding: 16px 16px 12px;
  flex: 1;
}
.book-title {
  font-family: var(--font-display);
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--plaza-heading);
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
}
.file-size { font-size: 12px; color: var(--plaza-text-muted); }
.book-date { font-size: 12px; color: var(--plaza-text-muted); }

.pagination-bar {
  margin-top: 28px;
  display: flex;
  justify-content: center;
}
</style>