<template>
  <div class="notification-page">
    <div class="page-header">
      <h2>通知中心</h2>
      <p class="subtitle">查看系统最近操作动态</p>
    </div>

    <div v-loading="loading" class="notification-list" v-if="notifications.length > 0">
      <div
        v-for="(item, index) in notifications"
        :key="index"
        class="notification-item"
        :class="{ unread: !item.isRead }"
        @click="handleRead(item)"
      >
        <div class="notification-icon" :class="item.type">
          <el-icon v-if="item.type === 'system'"><Setting /></el-icon>
          <el-icon v-else-if="item.type === 'user'"><User /></el-icon>
          <el-icon v-else><Bell /></el-icon>
        </div>
        <div class="notification-content">
          <div class="notification-title">{{ item.title }}</div>
          <div class="notification-desc">{{ item.content }}</div>
          <div class="notification-time">{{ item.createTime }}</div>
        </div>
        <div class="notification-right">
          <span class="unread-badge" v-if="!item.isRead"></span>
          <el-button
            class="del-btn"
            size="small"
            text
            type="danger"
            :icon="Delete"
            @click.stop="handleDelete(index)"
          />
        </div>
      </div>
    </div>

    <el-empty v-else-if="!loading" description="暂无动态" />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { gsap } from 'gsap'
import { Bell, Setting, User, Delete } from '@element-plus/icons-vue'
import { getRecentActivities } from '@/api/stat'

const notifications = ref([])
const loading = ref(false)

/** 根据操作描述推断通知图标类型 */
function guessType(action = '', status = '') {
  if (action.includes('用户') || action.includes('注册')) return 'user'
  if (action.includes('系统') || action.includes('管理') || status === 'approved') return 'system'
  return 'message'
}

/** ISO 时间字符串 → "YYYY/MM/DD HH:mm" */
function formatTime(isoStr) {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit'
    })
  } catch {
    return isoStr
  }
}

async function loadNotifications() {
  loading.value = true
  try {
    const res = await getRecentActivities(30)
    const list = res.data || []
    notifications.value = list.map(item => ({
      type: guessType(item.action, item.status),
      title: item.user || '系统',
      content: item.action || '',
      createTime: formatTime(item.time),
      status: item.status,
      isRead: false
    }))
    // 只做位移入场，不做透明度渐变，避免 stagger 造成"渐变"视觉
    if (list.length && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      await nextTick()
      gsap.from('.notification-item', { y: 16, duration: 0.35, stagger: 0.04, ease: 'power2.out' })
    }
  } catch {
    // 静默，不影响页面
  } finally {
    loading.value = false
  }
}

const handleRead = (item) => {
  item.isRead = true
}

const handleDelete = (index) => {
  notifications.value.splice(index, 1)
}

onMounted(() => {
  loadNotifications()
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  nextTick(() => {
    gsap.from('.page-header', { y: -14, opacity: 0, duration: 0.5 })
  })
})
</script>

<style scoped>
.notification-page {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 32px;
}

.page-header h2 {
  font-size: 28px;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 8px;
}

.subtitle {
  color: var(--plaza-text-muted);
  font-size: 14px;
}

.notification-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.notification-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
  background: var(--plaza-bg-card);
  border-radius: 12px;
  border: 1px solid var(--plaza-border);
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.notification-item:hover {
  border-color: var(--plaza-accent);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.notification-item.unread {
  border-left: 3px solid var(--plaza-accent);
}

.notification-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 20px;
}

.notification-icon.system {
  background: #e8f4ff;
  color: #1890ff;
}

.notification-icon.user {
  background: #f0fdf4;
  color: #52c41a;
}

.notification-icon.message {
  background: #fff7e6;
  color: #faad14;
}

.notification-content {
  flex: 1;
  min-width: 0;
}

.notification-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 6px;
}

.notification-desc {
  font-size: 14px;
  color: var(--plaza-text-muted);
  line-height: 1.5;
  margin-bottom: 8px;
}

.notification-time {
  font-size: 12px;
  color: var(--plaza-text-light);
}

/* 右侧：未读红点 + 删除按钮 */
.notification-right {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding-left: 4px;
}

.unread-badge {
  width: 8px;
  height: 8px;
  background: var(--plaza-accent);
  border-radius: 50%;
}

.del-btn {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.notification-item:hover .del-btn {
  opacity: 1;
}
</style>
