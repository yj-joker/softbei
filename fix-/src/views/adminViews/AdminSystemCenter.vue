<script setup>
import { computed, onMounted, nextTick, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { useRoute, useRouter } from 'vue-router'
import AdminNotify from './AdminNotify.vue'
import AdminUsers from './AdminUsers.vue'

const route = useRoute()
const router = useRouter()

const tabs = [
  {
    name: 'users',
    label: '用户管理',
    desc: '维护系统账号、角色与人员基础信息',
    component: AdminUsers,
  },
  {
    name: 'notify',
    label: '通知中心',
    desc: '查看任务生成、知识导入与系统消息',
    component: AdminNotify,
  },
]

const tabNames = tabs.map((tab) => tab.name)
const activeTab = ref(tabNames.includes(route.query.tab) ? route.query.tab : 'users')
const activeInfo = computed(() => tabs.find((tab) => tab.name === activeTab.value) || tabs[0])

watch(
  () => route.query.tab,
  (tab) => {
    if (tabNames.includes(tab) && tab !== activeTab.value) {
      activeTab.value = tab
    }
  },
)

watch(activeTab, (tab) => {
  if (route.query.tab === tab) return
  router.replace({
    path: '/admin/system',
    query: { ...route.query, tab },
  })
})

onMounted(() => {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  nextTick(() => {
    gsap.timeline({ defaults: { ease: 'power3.out' } })
      .from('.center-header', { y: -14, opacity: 0, duration: 0.5 })
      .from('.center-tabs', { y: -8, opacity: 0, duration: 0.4 }, '-=0.28')
      .from('.center-content', { y: 18, opacity: 0, duration: 0.5 }, '-=0.2')
  })
})
</script>

<template>
  <section class="admin-center-page">
    <header class="center-header">
      <div>
        <h2>系统管理</h2>
        <p>{{ activeInfo.desc }}</p>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="center-tabs">
      <el-tab-pane
        v-for="tab in tabs"
        :key="tab.name"
        :label="tab.label"
        :name="tab.name"
      />
    </el-tabs>

    <main class="center-content">
      <component :is="activeInfo.component" />
    </main>
  </section>
</template>

<style scoped>
.admin-center-page {
  max-width: 1280px;
  margin: 0 auto;
}

.center-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.center-header h2 {
  color: var(--plaza-heading);
  font-size: 22px;
  font-weight: 800;
  line-height: 1.25;
}

.center-header p {
  margin-top: 6px;
  color: var(--plaza-text-muted);
  font-size: 14px;
}

.center-tabs {
  margin-bottom: 16px;
}

.center-tabs :deep(.el-tabs__header) {
  margin: 0;
  border-bottom: 1px solid var(--plaza-border);
}

.center-tabs :deep(.el-tabs__item) {
  color: var(--plaza-text-muted);
  font-weight: 600;
}

.center-tabs :deep(.el-tabs__item.is-active) {
  color: var(--plaza-accent);
}

.center-tabs :deep(.el-tabs__active-bar) {
  background: var(--plaza-accent);
}

.center-content {
  min-width: 0;
}
</style>
