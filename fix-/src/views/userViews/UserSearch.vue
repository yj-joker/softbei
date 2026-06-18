<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { gsap } from 'gsap'
import { Search, Folder, Star, Trophy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getMaintenanceManualList, getMaintenanceManualDetail, getMaintenanceManualRank, startMaintenanceManualRead, maintenanceManualHeartbeat, searchChapter } from '@/api/maintenanceManual'

const router = useRouter()

const loading = ref(false)
const searchQuery = ref('')
const list = ref([])
const recommendList = ref([])
const rankList = ref([])
const pagination = reactive({ page: 1, pageSize: 12, total: 0 })
const rankType = ref('total')
const currentReadSession = ref(null)
const heartbeatTimer = ref(null)
const HEARTBEAT_INTERVAL = 20000

const hotTags = ['发动机维修', '电气故障', '设备保养', '液压系统', '日常维护']

// ── Tab 状态 ──
const activeTab = ref('manual') // 'manual' | 'chapter'

// ── 章节搜索状态 ──
const chapterQuery = ref('')
const chapterResults = ref([])        // 原始列表
const chapterGroups = ref([])         // 分组列表
const chapterLoading = ref(false)
let chapterDebounceTimer = null
const searchTime = ref(0)             // 查询耗时 ms
const activeViewMode = ref('list')   // 'list' | 'group'
const chunkTypeFilter = ref('all')    // 'all' | 'text' | 'image' | 'table'

// ── 筛选后结果 ──
const filteredResults = computed(() => {
  if (chunkTypeFilter.value === 'all') return chapterResults.value
  return chapterResults.value.filter(r => r.chunkType === chunkTypeFilter.value)
})

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

async function loadRecommend() {
  try {
    const res = await getMaintenanceManualRank('week', 10)
    if (res.code === '200' || res.code === 200) {
      recommendList.value = res.data || []
    }
  } catch (e) {
    console.warn('周榜加载失败', e.message)
  }
}

async function loadRank() {
  try {
    const res = await getMaintenanceManualRank(rankType.value, 10)
    if (res.code === '200' || res.code === 200) {
      rankList.value = res.data || []
      // 补充 fileSize 和 createdAt
      await Promise.all(
        rankList.value.map(async (item) => {
          try {
            const detail = await getMaintenanceManualDetail(item.manualId)
            if (detail.code === '200' || detail.code === 200) {
              item.fileSize = detail.data?.fileSize
              item.createdAt = detail.data?.createdAt
            }
          } catch {
            // 静默失败，不阻断展示
          }
        })
      )
    }
  } catch (e) {
    console.warn('排行榜加载失败', e.message)
  }
}

onMounted(() => {
  loadRecommend()
  loadRank()
  if (!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    nextTick(() => {
      gsap.from('.page-header-card', { y: -18, opacity: 0, duration: 0.5, ease: 'power3.out' })
      gsap.from('.search-bar-card', { y: 22, opacity: 0, duration: 0.55, ease: 'power3.out', delay: 0.12 })
      gsap.from('.manual-panel .section-card', { y: 26, opacity: 0, duration: 0.5, ease: 'power3.out', stagger: 0.12, delay: 0.2 })
    })
  }
})

function handleSearch() {
  if (!searchQuery.value.trim()) return
  router.push({ path: '/user/search-result', query: { keyword: searchQuery.value } })
}

function handleSearchByTag(tag) {
  router.push({ path: '/user/search-result', query: { keyword: tag } })
}

function handleChapterSearch() {
  if (!chapterQuery.value.trim()) {
    chapterResults.value = []
    chapterGroups.value = []
    searchTime.value = 0
    return
  }
  clearTimeout(chapterDebounceTimer)
  chapterDebounceTimer = setTimeout(async () => {
    chapterLoading.value = true
    try {
      const res = await searchChapter({ query: chapterQuery.value })
      if (res.code === '200' || res.code === 200) {
        chapterResults.value = res.data?.results || []
        chapterGroups.value = res.data?.chapterGroups || []
        searchTime.value = res.data?.queryTimeMs || 0
      }
    } catch (e) {
      ElMessage.error('章节搜索失败: ' + e.message)
    } finally {
      chapterLoading.value = false
    }
  }, 300)
}

function changeRankType(type) {
  rankType.value = type
  loadRank()
}

// ── 章节搜索：直接打开 PDF（不创建阅读会话） ──
async function openChapterInPdf(item) {
  let fileUrl = item.sourceFileUrl
  if (!fileUrl) {
    ElMessage.error('文件链接不存在，请确认该手册已上传并解析完成')
    return
  }
  if (item.page) {
    fileUrl = `${fileUrl}#page=${item.page}`
  }
  window.open(fileUrl, '_blank')
}

// ── 章节搜索：内容类型过滤切换 ──
function setChunkTypeFilter(type) {
  chunkTypeFilter.value = type
}

// ── 章节搜索：视图模式切换 ──
function setViewMode(mode) {
  activeViewMode.value = mode
}

// ── 章节搜索：分数显示格式化 ──
function formatScore(score) {
  if (!score) return ''
  return Math.round(score * 100)
}

// ── 章节搜索：chunkType 标签 ──
function getChunkTypeLabel(chunkType) {
  const map = { text: '文本', image: '图片', table: '表格' }
  return map[chunkType] || chunkType || '文本'
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
  if (type === 'pdf') return { bg: 'rgba(58,44,32,0.1)', accent: '#5a4a38', label: 'PDF' }
  if (type === 'docx' || type === 'doc') return { bg: 'rgba(168,96,95,0.12)', accent: '#a8605f', label: 'WORD' }
  if (type === 'xlsx' || type === 'xls') return { bg: 'rgba(34,197,94,0.1)', accent: '#22c55e', label: 'EXCEL' }
  if (type === 'txt') return { bg: 'rgba(107,93,76,0.12)', accent: '#6b5d4c', label: 'TXT' }
  return { bg: 'rgba(58,44,32,0.1)', accent: '#5a4a38', label: 'FILE' }
}

function getRankBadgeClass(index) {
  if (index === 0) return 'rank-gold'
  if (index === 1) return 'rank-silver'
  if (index === 2) return 'rank-bronze'
  return ''
}
</script>

<template>
  <div class="user-search">
    <!-- Page Header Card -->
    <div class="page-header-card">
      <div class="page-header">
        <div class="page-title-area">
          <h2 class="page-title">智能检索</h2>
          <p class="page-desc">搜索并阅读管理员上传的维修手册</p>
        </div>
        <!-- Tab 切换 -->
        <div class="search-mode-tabs">
          <button
            class="mode-tab"
            :class="{ active: activeTab === 'manual' }"
            @click="activeTab = 'manual'"
          >手册搜索</button>
          <button
            class="mode-tab"
            :class="{ active: activeTab === 'chapter' }"
            @click="activeTab = 'chapter'"
          >章节搜索</button>
        </div>
      </div>
    </div>

    <!-- Search Bar — 悬浮卡片风格（位于 header 与内容之间，向上浮入 header 区域） -->
    <div class="search-bar-wrapper" :class="{ 'is-chapter': activeTab === 'chapter' }">
      <div class="search-bar-card">
        <!-- 手册搜索框 -->
        <div class="search-bar-inner manual-search-bar">
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
        <!-- 章节搜索框 -->
        <div class="search-bar-inner chapter-search-bar">
          <el-input
            v-model="chapterQuery"
            placeholder="输入章节关键词，智能检索相关内容..."
            class="search-input"
            clearable
            @input="handleChapterSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-button type="primary" class="search-btn" @click="handleChapterSearch">
            <el-icon><Search /></el-icon>
            搜索
          </el-button>
        </div>
        <div class="hot-tags">
          <span class="hot-label">热门搜索：</span>
          <span
            v-for="tag in hotTags"
            :key="tag"
            class="hot-tag"
            @click="handleSearchByTag(tag)"
          >
            {{ tag }}
          </span>
        </div>
      </div>
    </div>

    <!-- 滑动内容区 -->
    <div class="content-slide-wrapper" :class="{ 'is-chapter': activeTab === 'chapter' }">
      <!-- 手册搜索面板 -->
      <div class="content-panel manual-panel">
        <!-- 推荐专区 -->
        <div class="section-card">
          <div class="section-header">
            <el-icon class="section-icon"><Star /></el-icon>
            <h3 class="section-title">为你推荐</h3>
          </div>
          <div v-if="recommendList.length" class="recommend-scroll">
            <div
              v-for="item in recommendList"
              :key="item.id"
              class="recommend-card"
              @click="handleRead(item.manualId)"
            >
              <div class="recommend-cover">
                <svg v-if="(item.fileType || 'pdf').toLowerCase().replace(/^\./, '') === 'pdf'" class="file-icon-svg" viewBox="0 0 48 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 4h24l12 12v44H8V4z" fill="#f7f2e8" stroke="#5a4a38" stroke-width="1.5"/>
                  <path d="M28 4v12h12" fill="#ece3d4" stroke="#5a4a38" stroke-width="1.5"/>
                  <rect x="14" y="26" width="20" height="3" rx="1.5" fill="#5a4a38" opacity="0.6"/>
                  <rect x="14" y="33" width="16" height="3" rx="1.5" fill="#5a4a38" opacity="0.4"/>
                  <rect x="14" y="40" width="18" height="3" rx="1.5" fill="#5a4a38" opacity="0.25"/>
                  <text x="16" y="22" font-family="sans-serif" font-size="8" fill="#5a4a38" font-weight="700">PDF</text>
                </svg>
                <svg v-else class="file-icon-svg" viewBox="0 0 48 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 4h24l12 12v44H8V4z" fill="#F4F0E8" stroke="#B3A692" stroke-width="1.5"/>
                  <path d="M28 4v12h12" fill="#ECE3D4" stroke="#B3A692" stroke-width="1.5"/>
                  <rect x="14" y="26" width="20" height="3" rx="1.5" fill="#B3A692" opacity="0.6"/>
                  <rect x="14" y="33" width="16" height="3" rx="1.5" fill="#B3A692" opacity="0.4"/>
                  <text x="12" y="22" font-family="sans-serif" font-size="7" fill="#6B5D4C" font-weight="700">FILE</text>
                </svg>
              </div>
              <div class="recommend-info">
                <h4 class="recommend-name">{{ item.manualName }}</h4>
                <p class="recommend-desc">{{ item.manualDesc || '暂无简介' }}</p>
              </div>
            </div>
          </div>
          <div v-else class="recommend-empty">
            <el-icon class="empty-icon"><Star /></el-icon>
            <p>暂无推荐手册</p>
          </div>
        </div>

        <!-- 排行榜 -->
        <div class="section-card">
          <div class="section-header">
            <el-icon class="section-icon"><Trophy /></el-icon>
            <h3 class="section-title">热门排行榜</h3>
          </div>
          <div class="rank-tabs">
            <button
              v-for="t in ['day', 'week', 'month', 'total']"
              :key="t"
              class="rank-tab"
              :class="{ active: rankType === t }"
              @click="changeRankType(t)"
            >
              {{ { day: '日榜', week: '周榜', month: '月榜', total: '总榜' }[t] }}
            </button>
          </div>
          <div v-if="rankList.length" class="rank-list">
            <div class="rank-list-header">
              <span class="rank-col-rank">排名</span>
              <span class="rank-col-name">手册名称</span>
              <span class="rank-col-size">大小</span>
              <span class="rank-col-date">日期</span>
              <span class="rank-col-action">操作</span>
            </div>
            <div
              v-for="(item, index) in rankList"
              :key="item.manualId"
              class="rank-row"
              @click="handleRead(item.manualId)"
            >
              <div class="rank-col-rank">
                <span class="rank-badge" :class="getRankBadgeClass(index)">{{ index + 1 }}</span>
              </div>
              <div class="rank-col-name">
                <span class="file-type-badge" :style="{ background: getFileTypeColor(item.fileType).bg, color: getFileTypeColor(item.fileType).accent }">
                  {{ getFileTypeColor(item.fileType).label }}
                </span>
                <span class="rank-manual-name">{{ item.manualName }}</span>
              </div>
              <div class="rank-col-size">{{ formatSize(item.fileSize) }}</div>
              <div class="rank-col-date">{{ formatDate(item.createdAt) }}</div>
              <div class="rank-col-action">
                <button class="rank-read-btn">阅读</button>
              </div>
            </div>
          </div>
          <div v-else class="rank-empty">暂无排行数据</div>
        </div>
      </div>

      <!-- 章节搜索面板 -->
      <div class="content-panel chapter-panel">
        <!-- 章节搜索结果 -->
        <div class="section-card">
          <div class="section-header chapter-result-header">
            <div class="result-header-left">
              <el-icon class="section-icon"><Folder /></el-icon>
              <h3 class="section-title">章节搜索结果</h3>
            </div>
            <!-- 视图切换 + 类型筛选 -->
            <div v-if="chapterResults.length || chapterGroups.length" class="result-controls">
              <div class="view-mode-tabs">
                <button class="view-tab" :class="{ active: activeViewMode === 'list' }" @click="setViewMode('list')">列表</button>
                <button class="view-tab" :class="{ active: activeViewMode === 'group' }" @click="setViewMode('group')">按章节分组</button>
              </div>
              <div class="chunk-type-filter">
                <button v-for="t in ['all','text','image','table']" :key="t" class="filter-btn" :class="{ active: chunkTypeFilter === t }" @click="setChunkTypeFilter(t)">
                  {{ { all:'全部', text:'文本', image:'图片', table:'表格' }[t] }}
                </button>
              </div>
            </div>
          </div>

          <!-- 搜索耗时 -->
          <div v-if="searchTime > 0" class="search-meta">
            找到 {{ filteredResults.length }} 条结果，耗时 {{ searchTime }}ms
          </div>

          <!-- 加载中 -->
          <div v-if="chapterLoading" class="chapter-loading">
            <el-icon class="is-loading"><Search /></el-icon>
            <span>搜索中...</span>
          </div>

          <!-- 列表视图 -->
          <div v-else-if="activeViewMode === 'list' && filteredResults.length" class="chapter-results">
            <div
              v-for="(item, index) in filteredResults"
              :key="index"
              class="chapter-result-item"
              @click="openChapterInPdf(item)"
            >
              <div class="result-item-top">
                <span class="chunk-type-tag" :class="'chunk-' + (item.chunkType || 'text')">
                  {{ getChunkTypeLabel(item.chunkType) }}
                </span>
                <span class="score-badge">{{ formatScore(item.score) }}% 匹配</span>
              </div>
              <div class="chapter-result-manual">
                <img v-if="item.manualImage" :src="item.manualImage" class="manual-thumb" />
                {{ item.manualName || '未知手册' }}
              </div>
              <div class="chapter-result-chapter">
                {{ item.sectionTitle || '未知章节' }}
                <span v-if="item.pageRange" class="page-range">({{ item.pageRange }})</span>
              </div>
              <div v-if="item.matchedText || item.contextBefore || item.contextAfter" class="chapter-result-snippet">
                <span v-if="item.contextBefore" class="ctx context-before">{{ item.contextBefore }}</span>
                <mark class="highlight">{{ item.matchedText }}</mark>
                <span v-if="item.contextAfter" class="ctx context-after">{{ item.contextAfter }}</span>
              </div>
            </div>
          </div>

          <!-- 分组视图 -->
          <div v-else-if="activeViewMode === 'group' && chapterGroups.length" class="chapter-group-list">
            <div v-for="group in chapterGroups" :key="group.manualId + '-' + group.sectionTitle" class="chapter-group-block">
              <div class="group-header" @click="openChapterInPdf(group.hits?.[0])">
                <div class="group-header-left">
                  <span class="group-manual">{{ group.manualName }}</span>
                  <span class="group-section">{{ group.sectionTitle }}</span>
                </div>
                <div class="group-header-right">
                  <span class="group-hits">{{ group.hitCount }}处命中</span>
                  <span v-if="group.pageRange" class="group-page-range">{{ group.pageRange }}</span>
                </div>
              </div>
              <div class="group-hits-list">
                <div
                  v-for="(hit, idx) in (group.hits || [])"
                  :key="idx"
                  class="chapter-result-item chapter-result-item--sm"
                  @click="openChapterInPdf(hit)"
                >
                  <div class="result-item-top">
                    <span class="chunk-type-tag" :class="'chunk-' + (hit.chunkType || 'text')">
                      {{ getChunkTypeLabel(hit.chunkType) }}
                    </span>
                    <span class="score-badge">{{ formatScore(hit.score) }}%</span>
                    <span v-if="hit.page" class="page-badge">P.{{ hit.page }}</span>
                  </div>
                  <div v-if="hit.matchedText || hit.contextBefore || hit.contextAfter" class="chapter-result-snippet">
                    <span v-if="hit.contextBefore" class="ctx context-before">{{ hit.contextBefore }}</span>
                    <mark class="highlight">{{ hit.matchedText }}</mark>
                    <span v-if="hit.contextAfter" class="ctx context-after">{{ hit.contextAfter }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 空状态 -->
          <div v-else-if="!chapterLoading" class="chapter-empty">
            <el-icon class="empty-icon"><Search /></el-icon>
            <p>{{ chapterQuery ? '未找到相关章节，试试其他关键词' : '输入关键词开始章节搜索' }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-search {
  width: 90%;
  margin: 0 auto;
  padding: 8px 0 32px;
}

/* ── Page Header Card ── */
.page-header-card {
  position: relative;
  z-index: 1;
  overflow: hidden;
  margin-top: -10px;
  width: 100%;
  background:
    radial-gradient(120% 140% at 100% 0%, var(--plaza-accent-soft), transparent 55%),
    linear-gradient(180deg, #fffefb, var(--plaza-bg-card));
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  padding: 26px 30px 54px;
  margin-bottom: 0;
  box-shadow: var(--plaza-shadow-organic);
}
.page-header-card::after {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: var(--plaza-accent-grad);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.page-title {
  font-family: var(--font-display);
  font-size: 1.9rem;
  font-weight: 700;
  color: var(--plaza-heading);
  margin-bottom: 6px;
  letter-spacing: -0.01em;
}
.page-desc {
  font-size: 14px;
  color: var(--plaza-text-muted);
}

/* ── Mode Tab（手册搜索 | 章节搜索） ── */
.search-mode-tabs {
  display: flex;
  gap: 4px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 20px;
  padding: 3px;
  flex-shrink: 0;
}
.mode-tab {
  padding: 6px 20px;
  border-radius: 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--plaza-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s ease;
}
.mode-tab.active {
  background: var(--plaza-accent);
  color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.mode-tab:not(.active):hover {
  color: var(--plaza-accent);
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
}
.search-bar-inner {
  display: flex;
  gap: 12px;
  align-items: center;
}

/* 两个搜索框默认显示手册，Tab 切换时显示章节 */
.chapter-search-bar {
  display: none;
}
.search-bar-wrapper.is-chapter .manual-search-bar {
  display: none;
}
.search-bar-wrapper.is-chapter .chapter-search-bar {
  display: flex;
}
.search-bar-wrapper.is-chapter .hot-tags {
  display: none;
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

/* ── 热门标签 ── */
.hot-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.hot-label {
  font-size: 13px;
  color: var(--plaza-text-muted);
}
.hot-tag {
  padding: 4px 12px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 16px;
  font-size: 13px;
  color: var(--plaza-text-muted);
  cursor: pointer;
  transition: all 0.2s ease;
}
.hot-tag:hover {
  border-color: var(--plaza-accent);
  color: var(--plaza-accent);
}

/* ── 通用区块卡片 ── */
.section-card {
  background: var(--plaza-bg-card);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius-lg);
  padding: 20px 24px;
  margin-bottom: 24px;
  max-width: 960px;
  margin-left: auto;
  margin-right: auto;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.section-icon {
  font-size: 20px;
  color: var(--plaza-accent);
}
.section-title {
  font-family: var(--font-display);
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--plaza-heading);
}

/* ── 推荐横向滚动 ── */
.recommend-scroll {
  display: flex;
  gap: 16px;
  overflow-x: auto;
  padding-bottom: 8px;
}
.recommend-card {
  flex-shrink: 0;
  width: 180px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius);
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.recommend-card:hover {
  border-color: var(--plaza-accent);
  transform: translateY(-2px);
}
.recommend-cover {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 80px;
  margin-bottom: 12px;
}
.file-icon-svg {
  width: 40px;
  height: 52px;
}
.recommend-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.recommend-desc {
  font-size: 12px;
  color: var(--plaza-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.4;
}
.recommend-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: var(--plaza-text-muted);
  gap: 8px;
}
.recommend-empty .empty-icon {
  font-size: 32px;
  opacity: 0.4;
}
.recommend-empty p {
  font-size: 14px;
}

/* ── 排行榜 ── */
.rank-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.rank-tab {
  padding: 6px 16px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 20px;
  font-size: 13px;
  color: var(--plaza-text-muted);
  cursor: pointer;
  transition: all 0.2s ease;
}
.rank-tab.active {
  background: var(--plaza-accent-soft);
  border-color: var(--plaza-accent);
  color: var(--plaza-accent);
  font-weight: 500;
}

.rank-list {
  width: 100%;
}

.rank-list-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius) var(--plaza-radius) 0 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--plaza-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.rank-row {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  background: var(--plaza-bg-card);
  border-left: 1px solid var(--plaza-border);
  border-right: 1px solid var(--plaza-border);
  border-bottom: 1px solid var(--plaza-border);
  cursor: pointer;
  transition: all 0.15s ease;
}
.rank-row:last-child {
  border-radius: 0 0 var(--plaza-radius) var(--plaza-radius);
  border-bottom: 1px solid var(--plaza-border);
}
.rank-row:hover {
  background: var(--plaza-accent-soft);
}
.rank-row:hover .rank-read-btn {
  opacity: 1;
}

.rank-col-rank {
  width: 60px;
  flex-shrink: 0;
  display: flex;
  justify-content: center;
}
.rank-col-name {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.rank-col-size {
  width: 80px;
  flex-shrink: 0;
  text-align: center;
  font-size: 13px;
  color: var(--plaza-text-muted);
}
.rank-col-date {
  width: 80px;
  flex-shrink: 0;
  text-align: center;
  font-size: 13px;
  color: var(--plaza-text-muted);
}
.rank-col-action {
  width: 60px;
  flex-shrink: 0;
  display: flex;
  justify-content: center;
}

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  color: var(--plaza-text-muted);
}
.rank-badge.rank-gold {
  background: linear-gradient(135deg, #F59E0B, #D97706);
  border-color: #F59E0B;
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.3);
}
.rank-badge.rank-silver {
  background: linear-gradient(135deg, #b8a78f, #9c8a70);
  border-color: #b8a78f;
  color: #fff;
  box-shadow: 0 2px 8px rgba(156, 138, 112, 0.3);
}
.rank-badge.rank-bronze {
  background: linear-gradient(135deg, #D97706, #92400E);
  border-color: #D97706;
  color: #fff;
  box-shadow: 0 2px 8px rgba(217, 119, 6, 0.3);
}

.file-type-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.rank-manual-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--plaza-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-read-btn {
  opacity: 0;
  padding: 4px 12px;
  background: var(--plaza-accent);
  border: none;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  color: #fff;
  cursor: pointer;
  transition: opacity 0.15s ease, background 0.15s ease;
}
.rank-read-btn:hover {
  background: var(--plaza-accent-hover);
}

.rank-empty {
  text-align: center;
  padding: 32px;
  color: var(--plaza-text-muted);
  font-size: 14px;
}

/* ── 滑动内容区 ── */
.content-slide-wrapper {
  position: relative;
  overflow: hidden;
}

.content-panel {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.chapter-panel {
  display: none;
}

.content-slide-wrapper.is-chapter .manual-panel {
  display: none;
}

.content-slide-wrapper.is-chapter .chapter-panel {
  display: block;
}

/* ── 章节搜索结果 ── */
.chapter-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px;
  color: var(--plaza-text-muted);
}

.chapter-results {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chapter-result-item {
  padding: 14px 16px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius);
  cursor: pointer;
  transition: all 0.15s ease;
}

.chapter-result-item:hover {
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.chapter-result-manual {
  font-size: 13px;
  color: var(--plaza-accent);
  font-weight: 600;
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.chapter-result-chapter {
  font-size: 14px;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 6px;
}

.chapter-result-snippet {
  font-size: 13px;
  color: var(--plaza-text-muted);
  line-height: 1.5;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
}

.chapter-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: var(--plaza-text-muted);
  gap: 8px;
}

.chapter-empty .empty-icon {
  font-size: 32px;
  opacity: 0.4;
}

.chapter-empty p {
  font-size: 14px;
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

/* ── 章节搜索结果增强 ── */
.chapter-result-header {
  flex-wrap: wrap;
  gap: 12px;
}
.result-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.result-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

/* 视图切换 */
.view-mode-tabs {
  display: flex;
  gap: 4px;
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 16px;
  padding: 3px;
}
.view-tab {
  padding: 4px 14px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--plaza-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s ease;
}
.view-tab.active {
  background: var(--plaza-accent);
  color: #fff;
}
.view-tab:not(.active):hover {
  color: var(--plaza-accent);
}

/* 内容类型筛选 */
.chunk-type-filter {
  display: flex;
  gap: 6px;
}
.filter-btn {
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 500;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  cursor: pointer;
  transition: all 0.2s ease;
}
.filter-btn.active {
  background: var(--plaza-accent-soft);
  border-color: var(--plaza-accent);
  color: var(--plaza-accent);
}

/* 搜索耗时 */
.search-meta {
  font-size: 12px;
  color: var(--plaza-text-muted);
  margin-bottom: 14px;
  padding: 0 2px;
}

/* 结果卡片顶部行 */
.result-item-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

/* 内容类型标签 */
.chunk-type-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  letter-spacing: 0.03em;
}
.chunk-text { background: var(--plaza-accent-soft); color: var(--plaza-accent); }
.chunk-image { background: rgba(224,152,47,0.14); color: #b9791b; }
.chunk-table { background: rgba(94,140,62,0.14); color: #4e7a32; }

/* 匹配分数 */
.score-badge {
  font-size: 11px;
  color: var(--plaza-text-muted);
  font-weight: 500;
}

/* 页码 */
.page-badge {
  font-size: 11px;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg);
  padding: 1px 6px;
  border-radius: 8px;
  border: 1px solid var(--plaza-border);
}

/* 手册缩略图 */
.manual-thumb {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  object-fit: cover;
  margin-right: 4px;
  vertical-align: middle;
}

/* 高亮 */
.highlight {
  background: rgba(249,115,22,0.15);
  color: #ea580c;
  border-radius: 2px;
  padding: 0 2px;
}

/* 上下文 */
.ctx {
  color: var(--plaza-text-muted);
  font-size: 12px;
}
.context-before { margin-right: 4px; }
.context-after { margin-left: 4px; }

/* 页码范围 */
.page-range {
  font-size: 12px;
  color: var(--plaza-text-muted);
  font-weight: 400;
}

/* 分组视图 */
.chapter-group-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.chapter-group-block {
  border: 1px solid var(--plaza-border);
  border-radius: var(--plaza-radius);
  overflow: hidden;
}
.group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--plaza-bg);
  border-bottom: 1px solid var(--plaza-border);
  cursor: pointer;
  transition: background 0.15s ease;
}
.group-header:hover {
  background: var(--plaza-accent-soft);
}
.group-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.group-manual {
  font-size: 13px;
  font-weight: 600;
  color: var(--plaza-text);
}
.group-section {
  font-size: 13px;
  color: var(--plaza-accent);
  font-weight: 500;
}
.group-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.group-hits {
  font-size: 12px;
  font-weight: 600;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  padding: 2px 10px;
  border-radius: 12px;
}
.group-page-range {
  font-size: 12px;
  color: var(--plaza-text-muted);
}
.group-hits-list {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.chapter-result-item--sm {
  padding: 10px 12px;
}
</style>