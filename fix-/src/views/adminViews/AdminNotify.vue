<template>
  <div class="notification-page">
    <div class="page-header">
      <h2>通知中心</h2>
      <p class="subtitle">查看系统通知和消息</p>
    </div>

    <div class="notification-list" v-if="notifications.length > 0">
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
        <div class="notification-status" v-if="!item.isRead">
          <span class="unread-badge"></span>
        </div>
      </div>
    </div>

    <el-empty v-else description="暂无通知" />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { gsap } from 'gsap'
import { Bell, Setting, User } from '@element-plus/icons-vue'

const notifications = ref([
  {
    type: 'system',
    title: '系统更新通知',
    content: '系统将于今晚10点进行版本更新，预计维护30分钟。',
    createTime: '2026-05-21 09:30',
    isRead: false
  },
  {
    type: 'user',
    title: '新用户注册',
    content: '您有一条新的用户注册申请待审批。',
    createTime: '2026-05-20 15:20',
    isRead: false
  },
  {
    type: 'system',
    title: '知识库更新',
    content: '知识库已更新，新增10条检修案例。',
    createTime: '2026-05-19 11:00',
    isRead: true
  }
])

const handleRead = (item) => {
  item.isRead = true
}

onMounted(() => {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  nextTick(() => {
    gsap.timeline({ defaults: { ease: 'power3.out' } })
      .from('.page-header', { y: -14, opacity: 0, duration: 0.5 })
      .from('.notification-item', { y: 22, opacity: 0, duration: 0.5, stagger: 0.08 }, '-=0.25')
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
  transition: all 0.2s ease;
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

.notification-status {
  display: flex;
  align-items: center;
  padding-left: 12px;
}

.unread-badge {
  width: 8px;
  height: 8px;
  background: var(--plaza-accent);
  border-radius: 50%;
}
</style>