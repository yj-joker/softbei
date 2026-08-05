<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, EditPen, Plus, Delete, WarningFilled } from '@element-plus/icons-vue'
import {
  attachNode,
  buildEditableForest,
  createEntity,
  removeEntity,
  setEntityTrust,
  updateEntity,
} from '@/utils/taskEvidenceCandidateGraph'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  readOnly: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'dirty'])

const typeMeta = {
  devices: { label: '设备', child: 'components', addLabel: '添加部件', relation: '' },
  components: { label: '部件', child: 'faults', addLabel: '添加故障', relation: '关联部件' },
  faults: { label: '故障', child: 'solutions', addLabel: '添加维修方案', relation: '关联故障' },
  solutions: { label: '维修方案', child: null, addLabel: '', relation: '维修方案' },
}
const typeOrder = ['devices', 'components', 'faults', 'solutions']
const editing = ref(null)
const editingLabel = ref('')

const forest = computed(() => buildEditableForest(props.modelValue))
const roots = computed(() => forest.value.roots)
const treeRows = computed(() => {
  const rows = []
  const visit = (nodes, depth = 0) => nodes.forEach((node) => {
    rows.push({ node, depth })
    visit(node.children || [], depth + 1)
  })
  visit(roots.value)
  return rows
})
const hasDevices = computed(() => Array.isArray(props.modelValue?.devices) && props.modelValue.devices.length > 0)

function labelOf(node) {
  return node?.name || node?.title || `未命名${typeMeta[node?.type]?.label || '节点'}`
}
function isTrusted(node) {
  return node?.type === 'solutions'
    ? node.verified === true && String(node.sourceType || '').toLowerCase() === 'confirmed'
    : node.confirmed === true
}
function publish(next) {
  if (props.readOnly) return
  emit('update:modelValue', next)
  emit('dirty', next)
}
function add(category, parentId) {
  if (props.readOnly) return
  publish(createEntity(props.modelValue, category, parentId))
}
function startEdit(node) {
  if (props.readOnly) return
  editing.value = { id: node.id, type: node.type }
  editingLabel.value = labelOf(node)
}
function cancelEdit() {
  editing.value = null
  editingLabel.value = ''
}
function saveEdit() {
  if (props.readOnly) return
  const target = editing.value
  const value = editingLabel.value.trim()
  if (!target || !value) {
    ElMessage.warning('请输入节点名称')
    return
  }
  publish(updateEntity(props.modelValue, target.type, target.id, target.type === 'solutions' ? { title: value } : { name: value }))
  cancelEdit()
}
async function remove(node) {
  if (props.readOnly) return
  try {
    await ElMessageBox.confirm(`删除“${labelOf(node)}”及其关联关系？`, '删除节点', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    publish(removeEntity(props.modelValue, node.id))
  } catch (_) {
    // The user cancelled the confirmation dialog.
  }
}
function toggleTrust(node) {
  if (props.readOnly) return
  publish(setEntityTrust(props.modelValue, node.type, node.id, !isTrusted(node)))
}
function parentOptions(category) {
  if (category === 'components') return props.modelValue?.devices || []
  if (category === 'faults') return props.modelValue?.components || []
  if (category === 'solutions') return props.modelValue?.faults || []
  return []
}
function attach(nodeId, parentId) {
  if (props.readOnly || !parentId) return
  publish(attachNode(props.modelValue, nodeId, parentId))
}
function relationLabel(node) {
  return typeMeta[node.type]?.relation || ''
}
</script>

<template>
  <section class="tege-root" aria-label="候选关系图谱编辑器">
    <header class="tege-head">
      <div>
        <p class="tege-eyebrow">可编辑候选图谱</p>
        <h3>设备 - 部件 - 故障 - 维修方案</h3>
      </div>
      <el-button v-if="!readOnly" type="primary" @click="add('devices')"><el-icon><Plus /></el-icon>添加设备</el-button>
    </header>

    <el-empty v-if="!hasDevices" description="暂无设备，请先添加设备" :image-size="72">
      <el-button v-if="!readOnly" type="primary" @click="add('devices')"><el-icon><Plus /></el-icon>添加设备</el-button>
    </el-empty>

    <div v-else class="tege-tree">
      <article v-for="row in treeRows" :key="row.node.id" class="tege-node" :class="[`tege-${row.node.type}`, row.depth ? 'tege-child' : '']" :style="{ '--tege-depth': row.depth }">
        <span v-if="row.depth" class="tege-connector"></span><span v-if="row.depth" class="tege-branch"></span>
        <div class="tege-node-main">
          <span class="tege-type">{{ row.depth ? `${relationLabel(row.node)} / ` : '' }}{{ typeMeta[row.node.type].label }}</span>
          <template v-if="editing?.id === row.node.id"><el-input v-model="editingLabel" size="small" :disabled="readOnly" :aria-label="`${typeMeta[row.node.type].label}名称`" @keyup.enter="saveEdit" /></template>
          <strong v-else>{{ labelOf(row.node) }}</strong>
          <el-tag size="small" :type="isTrusted(row.node) ? 'success' : 'warning'">{{ row.node.type === 'solutions' ? (isTrusted(row.node) ? '人工验证' : '待验证') : (isTrusted(row.node) ? '人工确认' : '待确认') }}</el-tag>
        </div>
        <div v-if="!readOnly" class="tege-actions">
          <el-button text size="small" title="编辑" @click="editing?.id === row.node.id ? saveEdit() : startEdit(row.node)"><el-icon><EditPen /></el-icon></el-button>
          <el-button v-if="typeMeta[row.node.type].child" text size="small" type="primary" :title="typeMeta[row.node.type].addLabel" @click="add(typeMeta[row.node.type].child, row.node.id)"><el-icon><Plus /></el-icon></el-button>
          <el-button text size="small" :type="isTrusted(row.node) ? 'success' : 'warning'" @click="toggleTrust(row.node)"><el-icon><CircleCheck /></el-icon>{{ row.node.type === 'solutions' ? (isTrusted(row.node) ? '已验证' : '人工验证') : (isTrusted(row.node) ? '已确认' : '人工确认') }}</el-button>
          <el-button text size="small" type="danger" title="删除" @click="remove(row.node)"><el-icon><Delete /></el-icon></el-button>
        </div>
      </article>
    </div>

    <section v-if="forest.unlinked.components.length || forest.unlinked.faults.length || forest.unlinked.solutions.length" class="tege-issues">
      <h4><el-icon><WarningFilled /></el-icon>未关联节点</h4>
      <p>选择合法父节点后将调用挂接关系；设备缺失时请先添加设备。</p>
      <div v-for="category in typeOrder.slice(1)" :key="category" class="tege-issue-list">
        <div v-for="node in forest.unlinked[category]" :key="node.id" class="tege-issue-row">
          <span>{{ typeMeta[category].label }}：{{ labelOf(node) }}</span>
          <el-select :disabled="readOnly" size="small" placeholder="选择父节点" @change="attach(node.id, $event)"><el-option v-for="parent in parentOptions(category)" :key="parent.id" :label="parent.name || parent.title" :value="parent.id" /></el-select>
        </div>
      </div>
    </section>

    <section v-if="forest.conflicts.length" class="tege-issues tege-conflicts">
      <h4><el-icon><WarningFilled /></el-icon>关系冲突</h4>
      <p>以下节点拥有多个同层父节点，请重新选择一个父节点完成挂接。</p>
      <div v-for="node in forest.conflicts" :key="node.id" class="tege-issue-row">
        <span>{{ typeMeta[node.type].label }}：{{ labelOf(node) }}</span>
        <el-select :disabled="readOnly" size="small" placeholder="选择父节点" @change="attach(node.id, $event)"><el-option v-for="parent in parentOptions(node.type)" :key="parent.id" :label="parent.name || parent.title" :value="parent.id" /></el-select>
      </div>
    </section>

    <el-button v-if="editing" text @click="cancelEdit">取消编辑</el-button>
  </section>
</template>

<style scoped>
.tege-root { color:var(--plaza-text); }
.tege-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px; }
.tege-eyebrow { margin:0 0 4px; color:var(--plaza-accent); font-size:12px; font-weight:700; }
.tege-head h3, .tege-issues h4 { margin:0; color:var(--plaza-heading); }
.tege-tree { display:flex; flex-direction:column; gap:8px; overflow:auto; padding:2px 0; }
.tege-node { position:relative; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; min-width:0; margin-left:calc(var(--tege-depth, 0) * var(--tege-indent)); padding:10px 12px; border:1px solid var(--plaza-border); border-left:4px solid var(--plaza-accent); border-radius:7px; background:var(--plaza-bg-card); }
.tege-child { }
.tege-components { border-left-color:#4b7f8f; }.tege-faults { border-left-color:#bd7443; }.tege-solutions { border-left-color:#7a65a8; }
.tege-connector { position:absolute; left:calc(-1 * var(--tege-indent)); top:-13px; height:32px; border-left:2px solid var(--plaza-border); }
.tege-branch { position:absolute; left:calc(-1 * var(--tege-indent)); top:19px; width:var(--tege-indent); border-top:2px solid var(--plaza-border); }
.tege-node-main { display:flex; min-width:0; align-items:center; gap:8px; flex-wrap:wrap; }.tege-node-main strong { min-width:0; overflow-wrap:anywhere; }.tege-type { color:var(--plaza-text-muted); font-size:12px; }.tege-actions { display:flex; align-items:center; flex-wrap:wrap; justify-content:flex-end; gap:2px; }.tege-actions .el-button + .el-button { margin-left:0; }
.tege-issues { margin-top:18px; padding:14px; border:1px dashed var(--plaza-border); border-radius:7px; background:var(--plaza-bg-card); }.tege-issues h4 { display:flex; gap:6px; align-items:center; }.tege-issues p { margin:7px 0 10px; color:var(--plaza-text-muted); font-size:13px; }.tege-conflicts { border-color:var(--el-color-danger-light-5); }.tege-issue-list { display:flex; flex-direction:column; gap:8px; }.tege-issue-row { display:grid; grid-template-columns:minmax(0,1fr) minmax(180px,260px); align-items:center; gap:10px; padding:8px 0; border-top:1px solid var(--plaza-border); }.tege-issue-row span { min-width:0; overflow-wrap:anywhere; }
@media (max-width: 720px) { .tege-head { flex-direction:column; }.tege-node { grid-template-columns:1fr; --tege-indent:16px; }.tege-actions { justify-content:flex-start; }.tege-issue-row { grid-template-columns:1fr; } }
@media (min-width: 721px) { .tege-node { --tege-indent:28px; } }
</style>
