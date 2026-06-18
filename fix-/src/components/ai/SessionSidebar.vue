<script setup>
import { computed, ref } from 'vue'
import { Delete, EditPen, Plus, Search } from '@element-plus/icons-vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['new', 'select', 'delete', 'close'])
const keyword = ref('')

const visibleSessions = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return props.sessions
  return props.sessions.filter((session) => (session.title || '').toLowerCase().includes(q))
})

function formatTime(timestamp) {
  const date = new Date(timestamp)
  const diff = Date.now() - date.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
  return date.toLocaleDateString('zh-CN')
}
</script>

<template>
  <aside class="session-sidebar" :class="{ open }">
    <div class="side-head">
      <div>
        <strong>对话历史</strong>
        <span>{{ sessions.length }} 条记录</span>
      </div>
      <button type="button" title="新对话" @click="emit('new')">
        <el-icon><Plus /></el-icon>
      </button>
    </div>

    <label class="history-search">
      <el-icon><Search /></el-icon>
      <input v-model="keyword" type="text" placeholder="搜索历史对话" />
    </label>

    <div class="session-list">
      <button
        v-for="session in visibleSessions"
        :key="session.id"
        type="button"
        class="session-item"
        :class="{ active: session.id === currentSessionId }"
        @click="emit('select', session.id)"
      >
        <span class="session-icon"><el-icon><EditPen /></el-icon></span>
        <span class="session-copy">
          <strong>{{ session.title || '新对话' }}</strong>
          <small>{{ formatTime(session.updatedAt) }}</small>
        </span>
        <span
          class="session-delete"
          title="删除"
          @click.stop="emit('delete', session.id)"
        >
          <el-icon><Delete /></el-icon>
        </span>
      </button>
      <div v-if="!visibleSessions.length" class="empty-history">暂无匹配记录</div>
    </div>
  </aside>
</template>

<style scoped>
.session-sidebar {
  --history-width: clamp(280px, 28vw, 320px);
  position: relative;
  width: 0;
  flex: 0 0 0;
  min-width: 0;
  box-sizing: border-box;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 0;
  background: rgba(255, 255, 255, 0.96);
  border-right: 0 solid transparent;
  box-shadow: 12px 0 28px rgba(15, 23, 42, 0);
  opacity: 0;
  overflow: hidden;
  transform: translateX(-18px);
  transition:
    flex-basis 0.24s ease,
    width 0.24s ease,
    padding 0.24s ease,
    border-color 0.24s ease,
    box-shadow 0.24s ease,
    opacity 0.18s ease,
    transform 0.24s ease;
}

.session-sidebar.open {
  width: var(--history-width);
  flex-basis: var(--history-width);
  padding: 16px;
  border-right: 1px solid var(--plaza-border);
  box-shadow: 12px 0 28px rgba(15, 23, 42, 0.08);
  opacity: 1;
  transform: translateX(0);
}

.session-sidebar > * {
  min-width: calc(var(--history-width) - 32px);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.side-head div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.side-head strong {
  color: var(--plaza-heading);
  font-size: 16px;
}

.side-head span {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.side-head button {
  width: 32px;
  height: 32px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: var(--plaza-accent);
  background: var(--plaza-bg-card);
  cursor: pointer;
}

.history-search {
  height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-input);
  color: var(--plaza-text-muted);
}

.history-search input {
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--plaza-text);
}

.session-list {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.session-item {
  width: 100%;
  display: grid;
  grid-template-columns: 30px 1fr 28px;
  gap: 8px;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.session-item:hover,
.session-item.active {
  border-color: var(--plaza-border);
  background: var(--plaza-accent-soft);
}

.session-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--plaza-bg-card);
  color: var(--plaza-accent);
}

.session-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-copy strong {
  overflow: hidden;
  color: var(--plaza-text);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-copy small {
  color: var(--plaza-text-muted);
  font-size: 12px;
}

.session-delete {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  color: var(--plaza-text-muted);
}

.session-delete:hover {
  color: var(--plaza-danger);
  background: var(--plaza-danger-soft);
}

.empty-history {
  padding: 28px 0;
  text-align: center;
  color: var(--plaza-text-muted);
  font-size: 13px;
}

@media (max-width: 860px) {
  .session-sidebar {
    --history-width: min(280px, 46vw);
  }
}
</style>
