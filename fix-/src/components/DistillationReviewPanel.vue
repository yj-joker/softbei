<script setup>
import { ref, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { getTaskList, promoteToProcedure, skipPromotion } from '../api/task'

const props = defineProps({ jumpToId: { type: [String, Number], default: null } })
const router = useRouter()
const tasks = ref([])
const loading = ref(false)
const expanded = ref(new Set())
const busy = ref(new Set())

function setBusy(id, value) { const next = new Set(busy.value); value ? next.add(id) : next.delete(id); busy.value = next }
function toggle(task) { const next = new Set(expanded.value); next.has(task.id) ? next.delete(task.id) : next.add(task.id); expanded.value = next }
function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN') : '-' }
async function loadTasks() {
  loading.value = true
  try {
    const res = await getTaskList({ status: 'CLOSED', promotedProcedure: 'PENDING', page: 1, size: 50 })
    const all = res.data?.records || res.data?.list || []
    tasks.value = all.filter((task) => task.promotedProcedure === 'PENDING')
  } catch (error) { ElMessage.error(`加载待沉淀任务失败：${error.message || '请求异常'}`) }
  finally { loading.value = false }
}
async function promoteProcedure(task) {
  setBusy(task.id, true)
  try {
    const res = await promoteToProcedure(task.id)
    task.promotedProcedure = 'PROMOTED'
    ElMessage.success(`已生成草稿规程（ID: ${res.data}）`)
    try {
      await ElMessageBox.confirm('规程已生成草稿，可前往标准规程管理继续编辑后发布。', '沉淀成功', { confirmButtonText: '去编辑', cancelButtonText: '稍后', type: 'success' })
      router.push({ name: 'AdminProcedures', query: { edit: res.data } })
    } catch { /* 留在当前页 */ }
  } catch (error) { ElMessage.error(`沉淀规程失败：${error.message || ''}`) }
  finally { setBusy(task.id, false) }
}
async function skipProcedure(task) {
  try { await ElMessageBox.confirm(`确认跳过「${task.taskNumber}」的规程沉淀？`, '跳过确认', { confirmButtonText: '确认跳过', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  setBusy(task.id, true)
  try { await skipPromotion(task.id, 'procedure'); tasks.value = tasks.value.filter((item) => item.id !== task.id); ElMessage.success('已跳过规程沉淀') }
  catch (error) { ElMessage.error(`操作失败：${error.message || ''}`) }
  finally { setBusy(task.id, false) }
}
onMounted(loadTasks)
watch(() => props.jumpToId, async (id) => {
  if (id == null) return
  await nextTick()
  const task = tasks.value.find((item) => item.id == id)
  if (task) { expanded.value = new Set([...expanded.value, id]); document.querySelector(`[data-task-id="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }
})
</script>

<template>
  <section class="drp-root" aria-labelledby="drp-title">
    <header class="drp-head"><div><h2 id="drp-title">规程沉淀审核</h2><p>此处仅处理标准规程沉淀，不负责候选审核或知识图谱写入。</p></div><el-button :loading="loading" @click="loadTasks"><el-icon><Refresh /></el-icon>刷新</el-button></header>
    <div v-if="loading && !tasks.length" class="drp-state">正在加载待沉淀规程…</div>
    <div v-else-if="!tasks.length" class="drp-state">暂无待审核的规程沉淀项</div>
    <div v-else class="drp-cards">
      <article v-for="task in tasks" :key="task.id" :data-task-id="task.id" class="task-card" :class="{ busy: busy.has(task.id) }">
        <button class="card-header" type="button" :aria-expanded="expanded.has(task.id)" @click="toggle(task)"><span><b>{{ task.taskNumber || `任务 #${task.id}` }}</b><span class="tag">待沉淀规程</span></span><el-icon><component :is="expanded.has(task.id) ? ArrowUp : ArrowDown" /></el-icon></button>
        <div class="card-core"><span><small>设备</small>{{ task.deviceName || '-' }}</span><span><small>故障描述</small>{{ task.faultDescription || '-' }}</span><span><small>创建时间</small>{{ formatDate(task.createdAt) }}</span></div>
        <div v-if="expanded.has(task.id)" class="card-detail"><p>AI 规程线索将作为规程草稿供管理员继续编辑，候选审核请使用“任务证据候选”页。</p><pre v-if="task.procedureDraft">{{ JSON.stringify(task.procedureDraft, null, 2) }}</pre></div>
        <div class="card-actions"><el-button type="primary" :loading="busy.has(task.id)" @click="promoteProcedure(task)">沉淀规程</el-button><el-button type="danger" plain :loading="busy.has(task.id)" @click="skipProcedure(task)">跳过规程</el-button></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.drp-root { padding:20px 24px 24px; color:var(--plaza-text); }
.drp-head { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px; } h2 { margin:0; color:var(--plaza-heading); font-size:20px; } .drp-head p { margin:6px 0 0; color:var(--plaza-text-muted); font-size:12px; }
.drp-state { padding:72px 0; text-align:center; color:var(--plaza-text-muted); }
.drp-cards { display:flex; flex-direction:column; gap:12px; } .task-card { border:1px solid var(--plaza-border); border-radius:8px; background:var(--plaza-bg-card); overflow:hidden; } .task-card.busy { opacity:.6; pointer-events:none; }
.card-header { width:100%; display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border:0; background:transparent; color:var(--plaza-heading); cursor:pointer; text-align:left; } .card-header > span { display:flex; gap:10px; align-items:center; } .tag { padding:3px 8px; border-radius:12px; background:#fdf2e2; color:#b06b14; font-size:11px; }
.card-core { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; padding:0 18px 15px; } .card-core span { display:flex; flex-direction:column; gap:4px; } small { color:var(--plaza-text-muted); font-size:11px; } .card-detail { margin:0 18px 14px; padding:12px; border-top:1px solid var(--plaza-border); color:var(--plaza-text-muted); font-size:12px; } pre { white-space:pre-wrap; overflow:auto; }
.card-actions { display:flex; gap:8px; padding:0 18px 16px; } @media (max-width:640px) { .drp-root { padding:12px; } .drp-head { flex-direction:column; } .card-actions { flex-direction:column; } }
</style>
