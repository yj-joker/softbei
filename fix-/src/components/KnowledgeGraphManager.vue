<script setup>
import { ref, reactive, shallowRef, onMounted, onBeforeUnmount } from 'vue'
import { Graph } from '@antv/g6'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  searchDevices, getDeviceComponents, getComponentFaults, getFaultSolutions,
  deviceApi, componentApi, faultApi, solutionApi, createRelation,
} from '../api/graph'

/* ---------- 视觉编码（与浏览页一致：浅色圆形节点） ---------- */
const TYPE = {
  device:    { label: '设备', fill: '#f8ece2', stroke: '#c4602f', size: 50 },
  component: { label: '部件', fill: '#eef3e6', stroke: '#5e8c3e', size: 36 },
  fault:     { label: '故障', fill: '#fdeede', stroke: '#df7e2a', size: 33 },
  solution:  { label: '方案', fill: '#f5ece8', stroke: '#a8605f', size: 29 },
}
const SEVERITY = { 轻微: '#c9a23a', 一般: '#df9226', 严重: '#df7e2a', 致命: '#c5402c' }
const REL = { OWNS: '拥有', CAUSES: '引发', HAS_SOLUTION: '方案' }
const LABEL_FONT = "'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace"

// 父类型 → 子层级配置
const CHILD = {
  device:    { child: 'component', rel: 'OWNS',         fetch: getDeviceComponents, relType: 'DEVICE_OWNS_COMPONENT',   labelKey: 'name'  },
  component: { child: 'fault',     rel: 'CAUSES',       fetch: getComponentFaults,  relType: 'COMPONENT_CAUSES_FAULT', labelKey: 'name'  },
  fault:     { child: 'solution',  rel: 'HAS_SOLUTION', fetch: getFaultSolutions,   relType: 'FAULT_HAS_SOLUTION',     labelKey: 'title' },
}
const PAGE_SIZE = 6

/* ---------- 状态 ---------- */
const containerRef = ref(null)
const wrapRef = ref(null)
const graph = shallowRef(null)
const nodeMap = new Map()      // key -> { id, data }
const edgeSet = new Set()      // "src|tgt|rel"
const childrenOf = new Map()   // parentKey -> Set<childKey>（仅当前页）
const parentOf = new Map()     // childKey -> parentKey
const expanded = new Set()

const pagers = reactive({})    // parentKey -> { page, size, total }
const pagerPos = reactive({})  // parentKey -> { x, y }
const pos = new Map()          // key -> [x,y] 持久化布局坐标（展开/翻页时只重绘不重跑力导，避免节点糅合）
const stats = reactive({ nodes: 0, edges: 0 })
const ui = reactive({ deviceKw: '', loading: false, busyNode: '', selected: null, showDetail: false })

const key = (type, id) => `${type}:${id}`
const pages = (p) => Math.max(1, Math.ceil((p?.total || 0) / (p?.size || PAGE_SIZE)))
const childLabel = (type) => (CHILD[type] ? TYPE[CHILD[type].child].label : '')

/* ---------- 表单 ---------- */
const dlg = reactive({ show: false, mode: 'create', type: 'device', parentKey: '', saving: false })
const form = reactive({})       // 普通字段
const imagesText = ref('')      // 图片 URL（换行分隔）
// [字段, 标签, 类型/必填] — 1=必填, 'num' 'sev' 'dif' 'bool' 'area' 'imgs'
const FIELDS = {
  device:    [['name','名称',1],['code','编码'],['model','型号'],['location','位置'],['manufacturer','制造商'],['imageUrls','图片URL','imgs']],
  component: [['name','名称',1],['partNumber','零件号'],['specification','规格'],['supplier','供应商'],['lifecycle','寿命'],['unitPrice','单价','num'],['imageUrls','图片URL','imgs']],
  fault:     [['name','名称',1],['code','编码'],['severity','等级','sev'],['category','类别'],['reportedBy','上报人'],['description','描述','area'],['imageUrls','图片URL','imgs']],
  solution:  [['title','标题',1],['code','编码'],['difficulty','难度','dif'],['estimatedTime','预计耗时(分钟)','num'],['toolsRequired','所需工具'],['verified','已验证','bool'],['description','描述','area'],['imageUrls','图片URL','imgs']],
}
const SEV_OPTS = ['轻微', '一般', '严重', '致命']
const DIF_OPTS = ['简单', '中等', '复杂']
const API = { device: deviceApi, component: componentApi, fault: faultApi, solution: solutionApi }

/* ---------- G6 ---------- */
function buildGraph() {
  graph.value = new Graph({
    container: containerRef.value,
    autoResize: true,
    background: 'transparent',
    data: { nodes: [], edges: [] },
    node: {
      style: {
        size: (d) => d.data.size,
        fill: (d) => d.data.fill,
        stroke: (d) => (d.data.unverified ? '#df9226' : d.data.stroke),
        lineWidth: (d) => (d.data.unverified ? 2.4 : 1.6),
        lineDash: (d) => (d.data.unverified ? [5, 4] : 0),
        shadowColor: 'rgba(120,80,50,0.16)', shadowBlur: 8, shadowOffsetY: 2,
        labelText: (d) => d.data.label,
        labelFill: '#3a2c20', labelFontSize: 11, labelFontFamily: LABEL_FONT,
        labelPlacement: 'bottom', labelOffsetY: 4,
        labelBackground: true, labelBackgroundFill: 'rgba(255,253,248,0.92)',
        labelBackgroundRadius: 4, labelBackgroundLineWidth: 1,
        labelBackgroundStroke: '#e7dcc9', labelPadding: [2, 6],
      },
      state: {
        active:   { lineWidth: 2.6, halo: true, haloStroke: '#c4602f', haloOpacity: 0.14 },
        selected: { lineWidth: 3, halo: true, haloStroke: '#c4602f', haloOpacity: 0.2 },
      },
    },
    edge: {
      style: {
        stroke: 'rgba(120,80,50,0.26)', lineWidth: 1.1,
        endArrow: true, endArrowType: 'vee', endArrowSize: 7,
        labelText: (d) => d.data.relLabel || '',
        labelFill: '#6b5d4c', labelFontSize: 9, labelFontFamily: LABEL_FONT,
        labelBackground: true, labelBackgroundFill: 'rgba(255,253,248,0.9)', labelBackgroundRadius: 2,
      },
      state: { active: { stroke: '#c4602f', lineWidth: 2 } },
    },
    layout: { type: 'd3-force', link: { distance: 120, strength: 0.5 }, collide: { radius: 44 }, manyBody: { strength: -200 } },
    behaviors: ['zoom-canvas', 'drag-canvas', 'drag-element', { type: 'hover-activate', degree: 1 }, { type: 'click-select' }],
  })
  graph.value.on('node:click', (e) => {
    const id = e.target?.id
    const n = id && nodeMap.get(id)
    if (!n) return
    ui.selected = n.data
    ui.showDetail = true
    if (!expanded.has(id) && CHILD[n.data.type]) expand(id, 0)
  })
  graph.value.render()
  tickOverlay()
}

/* ---------- 数据写入 ---------- */
/* 快照当前所有节点坐标（含拖拽后的位置），供下次重绘复用 */
function snapshotPositions() {
  if (!graph.value) return
  for (const id of nodeMap.keys()) {
    try { const p = graph.value.getElementPosition(id); if (p) pos.set(id, [p[0], p[1]]) } catch (e) { /* 未渲染 */ }
  }
}

/* 给一页新子节点各自分配一个不同的初始坐标（父节点周围环上，带轻微抖动），
 * 避免新节点重合在同一点导致力导无法分开（重合节点受力为 0）。 */
function placeChildren(pk, childKeys) {
  const n = childKeys.length
  if (!n) return
  const pp = pos.get(pk) || [0, 0]
  const gp = pos.get(parentOf.get(pk))
  const base = gp ? Math.atan2(pp[1] - gp[1], pp[0] - gp[0]) : -Math.PI / 2
  const R = 170
  childKeys.forEach((ck, i) => {
    let a
    if (!gp) a = base + (2 * Math.PI * i) / n
    else {
      const arc = Math.min(Math.PI * 1.6, Math.max(0.8, (n - 1) * 0.5))
      a = n === 1 ? base : base - arc / 2 + (arc * i) / (n - 1)
    }
    const jitter = (i % 2 === 0 ? 1 : -1) * 6
    pos.set(ck, [pp[0] + (R + jitter) * Math.cos(a), pp[1] + (R + jitter) * Math.sin(a)])
  })
}

/**
 * 数据写入：始终跑力导（新节点会从各自的初始点「弹出」并被力导散开），
 * 已有节点用当前坐标热启动 → 不会被重新布局打散糅合。
 */
function syncGraph(refit = false) {
  snapshotPositions()
  stats.nodes = nodeMap.size
  stats.edges = edgeSet.size
  const nodes = [...nodeMap.values()].map((n) =>
    pos.has(n.id) ? { ...n, style: { x: pos.get(n.id)[0], y: pos.get(n.id)[1] } } : n,
  )
  const edges = [...edgeSet].map((k) => {
    const [source, target, rel] = k.split('|')
    return { id: k, source, target, data: { relLabel: REL[rel] || '' } }
  })
  graph.value.setData({ nodes, edges })
  graph.value.render().then(() => { if (refit) graph.value.fitView() }).catch(() => {})
}

function addNode(type, id, label, raw = {}, extra = {}) {
  if (!id) return null
  const k = key(type, id)
  const conf = TYPE[type]
  if (nodeMap.has(k)) {
    const d = nodeMap.get(k).data
    d.raw = raw; d.fullLabel = label || conf.label; d.label = (label || conf.label).slice(0, 16)
    if (extra.unverified != null) d.unverified = extra.unverified
    return k
  }
  const stroke = type === 'fault' && SEVERITY[raw.severity] ? SEVERITY[raw.severity] : conf.stroke
  nodeMap.set(k, {
    id: k,
    data: {
      type, rawId: id, label: (label || conf.label).slice(0, 16), fullLabel: label || conf.label,
      fill: conf.fill, stroke, size: conf.size, unverified: !!extra.unverified, raw,
    },
  })
  return k
}
function addEdge(s, t, rel) { if (s && t) edgeSet.add(`${s}|${t}|${rel}`) }

function removeSubtree(k) {
  const kids = childrenOf.get(k)
  if (kids) for (const c of [...kids]) removeSubtree(c)
  childrenOf.delete(k); expanded.delete(k); delete pagers[k]; delete pagerPos[k]
  parentOf.delete(k); nodeMap.delete(k); pos.delete(k)
  for (const e of [...edgeSet]) if (e.startsWith(k + '|') || e.includes('|' + k + '|')) edgeSet.delete(e)
}
function clearChildren(pk) {
  const kids = childrenOf.get(pk)
  if (kids) for (const c of [...kids]) removeSubtree(c)
  childrenOf.set(pk, new Set())
}

function rows(res) {
  const d = res?.data
  if (Array.isArray(d)) return { records: d, total: d.length }
  if (d && Array.isArray(d.records)) return { records: d.records, total: Number(d.total ?? d.records.length) }
  return { records: [], total: 0 }
}

/* ---------- 展开 / 分页 ---------- */
async function expand(pk, page = 0) {
  const n = nodeMap.get(pk)
  const conf = n && CHILD[n.data.type]
  if (!conf || ui.busyNode) return
  ui.busyNode = pk
  try {
    const res = await conf.fetch(n.data.rawId, { page, size: PAGE_SIZE })
    const { records, total } = rows(res)
    snapshotPositions()
    clearChildren(pk)
    const childKeys = []
    records.forEach((r) => {
      const ck = addNode(conf.child, r.id, r[conf.labelKey], r, { unverified: r.verified === false })
      addEdge(pk, ck, conf.rel)
      childrenOf.get(pk).add(ck); parentOf.set(ck, pk); childKeys.push(ck)
    })
    placeChildren(pk, childKeys)
    if (total > PAGE_SIZE) pagers[pk] = { page, size: PAGE_SIZE, total }
    else delete pagers[pk]
    expanded.add(pk)
    if (!records.length && page === 0) ElMessage.info(`未发现关联${TYPE[conf.child].label}`)
    syncGraph()
  } catch (err) {
    ElMessage.error('展开失败：' + (err.message || '请求异常'))
  } finally { ui.busyNode = '' }
}
function repage(pk, delta) {
  const p = pagers[pk]
  if (!p || ui.busyNode) return
  const np = Math.min(pages(p) - 1, Math.max(0, p.page + delta))
  if (np !== p.page) expand(pk, np)
}

/* ---------- 顶部操作 ---------- */
async function loadDevices() {
  ui.loading = true
  try {
    const { records } = rows(await searchDevices(ui.deviceKw.trim(), 40))
    if (!records.length) { ElMessage.info('未搜索到设备'); return }
    records.forEach((d) => addNode('device', d.id, d.name, d))
    syncGraph(true)
  } catch (err) { ElMessage.error('设备检索失败：' + (err.message || '')) }
  finally { ui.loading = false }
}
function fitView() { graph.value?.fitView() }
function relayout() { graph.value?.layout() }
function clearAll() {
  nodeMap.clear(); edgeSet.clear(); childrenOf.clear(); parentOf.clear(); expanded.clear()
  pos.clear()
  for (const k of Object.keys(pagers)) delete pagers[k]
  for (const k of Object.keys(pagerPos)) delete pagerPos[k]
  ui.selected = null; ui.showDetail = false
  syncGraph()
}

/* ---------- 分页 pill 跟随节点 ---------- */
let raf = null
function tickOverlay() {
  const keys = Object.keys(pagers)
  const rect = wrapRef.value?.getBoundingClientRect()
  if (keys.length && graph.value && rect) {
    for (const k of keys) {
      try {
        const wp = graph.value.getElementPosition(k)    // 世界坐标
        const c = graph.value.getClientByCanvas(wp)     // 屏幕坐标
        const r = (nodeMap.get(k)?.data.size || 30) / 2
        pagerPos[k] = { x: c[0] - rect.left, y: c[1] - rect.top + r + 11 }
      } catch (e) { /* 尚未渲染 */ }
    }
  }
  raf = requestAnimationFrame(tickOverlay)
}

/* ---------- CRUD ---------- */
function openCreate(type, parentKey = '') {
  dlg.mode = 'create'; dlg.type = type; dlg.parentKey = parentKey
  for (const k of Object.keys(form)) delete form[k]
  imagesText.value = ''
  if (type === 'fault') form.severity = '一般'
  if (type === 'solution') { form.difficulty = '中等'; form.verified = true }
  dlg.show = true
}
function openEdit() {
  const d = ui.selected; if (!d) return
  dlg.mode = 'edit'; dlg.type = d.type; dlg.parentKey = ''
  for (const k of Object.keys(form)) delete form[k]
  Object.assign(form, { ...(d.raw || {}), id: d.rawId })
  imagesText.value = (d.raw?.imageUrls || []).join('\n')
  dlg.show = true
}
function buildDTO() {
  const dto = {}
  FIELDS[dlg.type].forEach(([f, , t]) => {
    if (t === 'imgs') {
      const arr = imagesText.value.split('\n').map((s) => s.trim()).filter(Boolean)
      if (arr.length) dto.imageUrls = arr
    } else if (form[f] !== undefined && form[f] !== '') dto[f] = form[f]
  })
  if (dlg.mode === 'edit' && form.id) dto.id = form.id
  return dto
}

async function submitForm() {
  const [reqKey, reqLabel] = FIELDS[dlg.type][0]
  if (!form[reqKey]) { ElMessage.warning('请填写' + reqLabel); return }
  dlg.saving = true
  try {
    const api = API[dlg.type]
    const dto = buildDTO()
    if (dlg.mode === 'edit') {
      await api.update(dto)
      const k = key(dlg.type, form.id)
      const n = nodeMap.get(k)
      if (n) {
        const lk = dlg.type === 'solution' ? 'title' : 'name'
        n.data.raw = { ...n.data.raw, ...dto }
        if (dto[lk]) { n.data.fullLabel = dto[lk]; n.data.label = dto[lk].slice(0, 16) }
        if (dlg.type === 'solution') n.data.unverified = dto.verified === false
        if (dlg.type === 'fault' && SEVERITY[dto.severity]) n.data.stroke = SEVERITY[dto.severity]
        if (ui.selected?.rawId === form.id) ui.selected = n.data
      }
      syncGraph()
      ElMessage.success('已保存修改')
    } else {
      const res = await api.save(dto)
      const newId = res?.data?.id || res?.data
      const pk = dlg.parentKey
      if (pk && newId) {
        const parent = nodeMap.get(pk)
        await createRelation(parent.data.rawId, newId, CHILD[parent.data.type].relType)
        const total = (pagers[pk]?.total ?? (childrenOf.get(pk)?.size || 0)) + 1
        await expand(pk, Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)) // 跳到能看见新节点的末页
      } else if (!pk && dlg.type === 'device' && newId) {
        addNode('device', newId, dto.name, { ...dto, id: newId }); syncGraph()
      }
      ElMessage.success('新增成功')
    }
    dlg.show = false
  } catch (err) {
    ElMessage.error('保存失败：' + (err.message || '请求异常'))
  } finally { dlg.saving = false }
}

async function removeSelected() {
  const d = ui.selected; if (!d) return
  try {
    await ElMessageBox.confirm(`确认删除${TYPE[d.type].label}「${d.fullLabel}」及其下级关联？此操作不可撤销。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await API[d.type].remove(d.rawId)
    const k = key(d.type, d.rawId)
    const pk = parentOf.get(k)
    ui.showDetail = false; ui.selected = null
    if (pk) {
      const p = pagers[pk]
      const target = p ? Math.min(p.page, Math.max(0, Math.ceil((p.total - 1) / p.size) - 1)) : 0
      await expand(pk, target)
    } else { removeSubtree(k); syncGraph() }
    ElMessage.success('已删除')
  } catch (err) { ElMessage.error('删除失败：' + (err.message || '')) }
}

/* ---------- 详情属性 ---------- */
function detailRows() {
  const d = ui.selected; if (!d) return []
  const r = d.raw || {}
  const map = {
    device: [['编码', r.code], ['型号', r.model], ['位置', r.location], ['制造商', r.manufacturer]],
    component: [['零件号', r.partNumber], ['规格', r.specification], ['供应商', r.supplier], ['寿命', r.lifecycle], ['单价', r.unitPrice]],
    fault: [['等级', r.severity], ['类别', r.category], ['编码', r.code], ['描述', r.description]],
    solution: [['预计耗时', r.estimatedTime ? r.estimatedTime + ' 分钟' : null], ['难度', r.difficulty], ['工具', r.toolsRequired], ['已验证', r.verified == null ? null : (r.verified ? '是' : '否')], ['描述', r.description]],
  }
  return (map[d.type] || []).filter(([, v]) => v != null && v !== '')
}

onMounted(() => { buildGraph(); loadDevices() })
onBeforeUnmount(() => { cancelAnimationFrame(raf); graph.value?.destroy() })
</script>

<template>
  <div class="kg-root">
    <header class="kg-head">
      <div class="kg-title">
        <span class="led" /><span class="t-main">知识图谱管理</span>
        <span class="t-sub">KNOWLEDGE&nbsp;GRAPH&nbsp;·&nbsp;ADMIN</span>
        <span class="t-mode">可编辑 · CRUD</span>
      </div>
      <div class="kg-readout">
        <span>节点 <b>{{ stats.nodes }}</b></span><i /><span>关系 <b>{{ stats.edges }}</b></span>
      </div>
    </header>

    <div class="kg-body">
      <!-- 左控制台 -->
      <aside class="kg-console">
        <div class="panel">
          <div class="panel-h">设备入口</div>
          <el-input v-model="ui.deviceKw" placeholder="设备名 / 编码 / 型号（空=全部）" size="small"
                    @keyup.enter="loadDevices" clearable />
          <button class="hud-btn" :disabled="ui.loading" @click="loadDevices">检索设备</button>
          <button class="hud-btn ghost" @click="openCreate('device')">新增设备</button>
        </div>

        <div class="panel ops">
          <button class="mini" @click="fitView">适配</button>
          <button class="mini" @click="relayout">重排</button>
          <button class="mini danger" @click="clearAll">清空</button>
        </div>

        <div class="legend">
          <div class="lg-h">图例</div>
          <div v-for="(v,k) in TYPE" :key="k" class="lg-row">
            <span class="dot" :style="{ background: v.fill, borderColor: v.stroke }" />{{ v.label }}
          </div>
          <div class="lg-row"><span class="dot dot-uv" />未验证方案</div>
          <p class="lg-tip">点击节点逐层展开；节点下方 <b>‹ n/N ›</b> 可翻页；点选后右侧可改/删。</p>
        </div>
      </aside>

      <!-- 画布 -->
      <main ref="wrapRef" class="kg-canvas-wrap">
        <div class="grid-overlay" />
        <div ref="containerRef" class="kg-canvas" />
        <div v-if="!stats.nodes" class="kg-empty">
          <div class="eg-art" aria-hidden="true">
            <span class="eg-aura" />
            <svg viewBox="0 0 280 214" class="eg-svg">
              <g class="eg-links">
                <path d="M140,104 L70,54" />
                <path d="M140,104 L210,54" />
                <path d="M140,104 L70,160" />
                <path d="M140,104 L210,160" />
              </g>
              <g class="eg-node" style="--d:.18s;--fx:-3px">
                <circle cx="70" cy="54" r="15" fill="#eef4e2" stroke="#5e8c3e" />
                <text x="70" y="84">部件</text>
              </g>
              <g class="eg-node" style="--d:.28s;--fx:3px">
                <circle cx="210" cy="54" r="15" fill="#fdf2e0" stroke="#df9226" />
                <text x="210" y="84">故障</text>
              </g>
              <g class="eg-node" style="--d:.38s;--fx:-3px">
                <circle cx="70" cy="160" r="15" fill="#f5ece8" stroke="#a8605f" />
                <text x="70" y="190">方案</text>
              </g>
              <g class="eg-node" style="--d:.48s;--fx:3px">
                <circle cx="210" cy="160" r="15" fill="#e8f1ee" stroke="#3f8c7c" />
                <text x="210" y="190">案例</text>
              </g>
              <g class="eg-node eg-core" style="--d:0s">
                <circle cx="140" cy="104" r="24" fill="#f8ece2" stroke="#c4602f" />
                <text x="140" y="108" class="eg-core-t">设备</text>
              </g>
            </svg>
          </div>
          <div class="eg-copy">
            <h3>知识图谱待编辑</h3>
            <p>从 <b>检索设备</b> 开始，点击节点逐层展开</p>
            <div class="eg-hints">
              <span><i />节点下方 ‹ › 翻页</span>
              <span><i />点选后右侧可改 / 删</span>
            </div>
          </div>
        </div>
        <div v-if="ui.loading" class="kg-scan" />

        <!-- 分页 pill 浮层（跟随节点定位） -->
        <div class="pager-layer">
          <div v-for="(p,k) in pagers" :key="k" v-show="pagerPos[k]" class="pager"
               :style="pagerPos[k] ? { left: pagerPos[k].x + 'px', top: pagerPos[k].y + 'px' } : {}">
            <button class="pg-btn" :disabled="p.page<=0 || !!ui.busyNode" @click.stop="repage(k,-1)">‹</button>
            <span class="pg-num">{{ p.page + 1 }}/{{ pages(p) }}</span>
            <button class="pg-btn" :disabled="p.page+1>=pages(p) || !!ui.busyNode" @click.stop="repage(k,1)">›</button>
          </div>
        </div>
      </main>

      <!-- 右侧详情 / 操作 -->
      <transition name="slide">
        <aside v-if="ui.showDetail && ui.selected" class="kg-detail">
          <span class="corner tl" /><span class="corner br" />
          <div class="d-head">
            <span class="d-type" :style="{ color: ui.selected.stroke }">{{ TYPE[ui.selected.type]?.label }}</span>
            <button class="d-close" @click="ui.showDetail=false">关闭</button>
          </div>
          <h3 class="d-name">{{ ui.selected.fullLabel }}</h3>
          <span v-if="ui.selected.unverified" class="badge-uv">未验证 · 手册自动抽取</span>

          <dl class="d-attrs">
            <template v-for="(row,i) in detailRows()" :key="i"><dt>{{ row[0] }}</dt><dd>{{ row[1] }}</dd></template>
            <p v-if="!detailRows().length" class="d-empty">无更多属性</p>
          </dl>

          <div v-if="(ui.selected.raw?.imageUrls||[]).length" class="d-imgs">
            <img v-for="(u,i) in ui.selected.raw.imageUrls" :key="i" :src="u" alt="" />
          </div>

          <div class="d-actions">
            <button class="act edit" @click="openEdit">修改</button>
            <button class="act del" @click="removeSelected">删除</button>
          </div>
          <button v-if="CHILD[ui.selected.type]" class="act add"
                  @click="openCreate(CHILD[ui.selected.type].child, ui.selected.id)">
            新增{{ childLabel(ui.selected.type) }}
          </button>
        </aside>
      </transition>
    </div>

    <!-- 新增 / 编辑 弹窗 -->
    <el-dialog v-model="dlg.show" :title="(dlg.mode==='create'?'新增':'修改') + TYPE[dlg.type].label" width="460px"
               append-to-body align-center>
      <el-form label-width="96px" @submit.prevent>
        <el-form-item v-for="f in FIELDS[dlg.type]" :key="f[0]" :label="f[1]" :required="f[2]===1">
          <el-select v-if="f[2]==='sev'" v-model="form[f[0]]" placeholder="选择等级" style="width:100%">
            <el-option v-for="o in SEV_OPTS" :key="o" :label="o" :value="o" />
          </el-select>
          <el-select v-else-if="f[2]==='dif'" v-model="form[f[0]]" placeholder="选择难度" style="width:100%">
            <el-option v-for="o in DIF_OPTS" :key="o" :label="o" :value="o" />
          </el-select>
          <el-switch v-else-if="f[2]==='bool'" v-model="form[f[0]]" />
          <el-input-number v-else-if="f[2]==='num'" v-model="form[f[0]]" :min="0" controls-position="right" style="width:100%" />
          <el-input v-else-if="f[2]==='area'" v-model="form[f[0]]" type="textarea" :rows="3" />
          <el-input v-else-if="f[2]==='imgs'" v-model="imagesText" type="textarea" :rows="2" placeholder="每行一个图片 URL" />
          <el-input v-else v-model="form[f[0]]" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <button class="dlg-btn cancel" @click="dlg.show=false">取消</button>
        <button class="dlg-btn ok" :disabled="dlg.saving" @click="submitForm">{{ dlg.saving ? '保存中…' : '保存' }}</button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kg-root{
  --bg:#f4ecdf; --card:#fffdf8; --line:#e7dcc9; --primary:#c4602f; --slate:#3a2c20; --mut:#6b5d4c; --amber:#df9226;
  --shadow:0 2px 12px rgba(120,80,50,.07),0 1px 3px rgba(120,80,50,.04);
  --shadow-lg:0 8px 30px rgba(120,80,50,.12);
  position:absolute; inset:0; display:flex; flex-direction:column;
  background:
    radial-gradient(900px 500px at 78% -8%, rgba(196,96,47,.07), transparent 60%),
    radial-gradient(700px 460px at 6% 112%, rgba(168,96,95,.05), transparent 60%),
    var(--bg);
  color:var(--slate);
  font-family:'Public Sans','Inter',-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
  overflow:hidden;
}
.kg-head{display:flex;align-items:center;justify-content:space-between;padding:13px 22px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.85);backdrop-filter:blur(6px)}
.kg-title{display:flex;align-items:center;gap:10px}
.led{width:8px;height:8px;border-radius:50%;background:var(--primary);box-shadow:0 0 0 3px rgba(196,96,47,.18);animation:pulse 2.4s infinite}
@keyframes pulse{50%{opacity:.45}}
.t-main{font-size:18px;font-weight:700;letter-spacing:.5px}
.t-sub{font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:10px;color:#b3a692;letter-spacing:2px}
.t-mode{margin-left:8px;font-size:11px;font-weight:600;padding:2px 10px;border-radius:20px;border:1px solid;color:#b06b14;border-color:rgba(223,146,38,.4);background:rgba(223,146,38,.12)}
.kg-readout{display:flex;align-items:center;gap:14px;font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:12px;color:var(--mut)}
.kg-readout b{color:var(--primary);font-size:14px;font-weight:700}
.kg-readout i{width:1px;height:14px;background:var(--line)}

.kg-body{flex:1;display:flex;min-height:0;position:relative}

.kg-console{width:244px;flex-shrink:0;padding:16px;display:flex;flex-direction:column;gap:13px;border-right:1px solid var(--line);background:rgba(255,255,255,.5);overflow-y:auto}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px;display:flex;flex-direction:column;gap:9px;box-shadow:var(--shadow)}
.panel-h{font-size:12px;font-weight:700;letter-spacing:.5px}
.panel-h::before{content:'';display:inline-block;width:3px;height:12px;background:var(--primary);border-radius:2px;margin-right:7px;vertical-align:-1px}
.ops{flex-direction:row;gap:8px}
.hud-btn{background:var(--primary);color:#fff;border:1px solid var(--primary);border-radius:8px;padding:9px;font-weight:600;font-size:13px;letter-spacing:.5px;cursor:pointer;transition:.18s;box-shadow:0 2px 8px rgba(196,96,47,.28)}
.hud-btn:hover{background:#a54d22}
.hud-btn:disabled{opacity:.5;cursor:not-allowed;box-shadow:none}
.hud-btn.ghost{background:#fff;color:var(--primary);box-shadow:none}
.hud-btn.ghost:hover{background:#f8efe3}
.mini{flex:1;background:#fff;color:var(--slate);border:1px solid var(--line);border-radius:8px;padding:8px;font-weight:600;font-size:12px;cursor:pointer;transition:.15s}
.mini:hover{border-color:var(--primary);color:var(--primary);background:#f8efe3}
.mini.danger:hover{border-color:#c5402c;color:#c5402c;background:#fbeae6}
.legend{margin-top:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px;box-shadow:var(--shadow)}
.lg-h{font-size:12px;font-weight:700;color:var(--mut);margin-bottom:9px}
.lg-row{display:flex;align-items:center;gap:9px;font-size:13px;padding:3px 0}
.dot{width:13px;height:13px;border-radius:50%;border:2px solid}
.dot-uv{background:#fdf2e2;border:2px dashed var(--amber)}
.lg-tip{margin:8px 0 0;font-size:11px;color:#b3a692;line-height:1.6}
.lg-tip b{color:var(--primary)}

.kg-canvas-wrap{flex:1;position:relative;min-width:0;background:linear-gradient(180deg,rgba(255,253,248,.4),rgba(244,236,223,.2))}
.grid-overlay{position:absolute;inset:0;pointer-events:none;opacity:.7;background-image:linear-gradient(rgba(196,96,47,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(196,96,47,.05) 1px,transparent 1px);background-size:40px 40px;mask-image:radial-gradient(circle at 50% 45%,#000 60%,transparent 100%)}
.kg-canvas{position:absolute;inset:0}
.kg-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;pointer-events:none;text-align:center}
.eg-art{position:relative;width:280px;height:214px}
.eg-aura{position:absolute;left:50%;top:50%;width:170px;height:170px;transform:translate(-50%,-48%);border-radius:50%;background:radial-gradient(circle,rgba(196,96,47,.16),transparent 68%);filter:blur(4px);animation:eg-breathe 4.5s ease-in-out infinite}
.eg-svg{position:relative;width:280px;height:214px;overflow:visible}
.eg-links path{fill:none;stroke:rgba(196,96,47,.4);stroke-width:1.4;stroke-dasharray:4 6;stroke-linecap:round;animation:eg-flow 6s linear infinite,eg-fade-in .8s .1s both}
.eg-node{transform-box:fill-box;transform-origin:center;animation:eg-pop .6s var(--d,0s) both cubic-bezier(.22,1,.36,1)}
.eg-node circle{stroke-width:2;filter:drop-shadow(0 3px 6px rgba(120,70,30,.14))}
.eg-node text{font-family:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace;font-size:11px;font-weight:600;fill:var(--slate);text-anchor:middle}
.eg-core-t{font-size:13px;font-weight:700;fill:#c4602f}
.eg-node:not(.eg-core){animation:eg-pop .6s var(--d,0s) both cubic-bezier(.22,1,.36,1),eg-float 5s var(--d,0s) ease-in-out infinite 1s}
@keyframes eg-pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes eg-float{0%,100%{transform:translateY(0)}50%{transform:translateY(var(--fx,3px))}}
@keyframes eg-flow{to{stroke-dashoffset:-100}}
@keyframes eg-breathe{0%,100%{opacity:.55;transform:translate(-50%,-48%) scale(.92)}50%{opacity:1;transform:translate(-50%,-48%) scale(1.06)}}
@keyframes eg-fade-in{from{opacity:0}to{opacity:1}}
.eg-copy{display:flex;flex-direction:column;align-items:center;gap:7px;animation:eg-rise .6s .35s both ease-out}
.eg-copy h3{font-family:var(--font-display,inherit);font-size:19px;font-weight:700;color:var(--slate);letter-spacing:.5px}
.eg-copy p{color:var(--mut);font-size:14px;line-height:1.7}.eg-copy b{color:var(--primary);font-weight:700}
.eg-hints{display:flex;gap:16px;margin-top:4px;flex-wrap:wrap;justify-content:center}
.eg-hints span{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#b3a692}
.eg-hints i{width:5px;height:5px;border-radius:50%;background:var(--primary);opacity:.7}
@keyframes eg-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@media (prefers-reduced-motion:reduce){.eg-aura,.eg-links path,.eg-node,.eg-node:not(.eg-core),.eg-copy{animation:none!important}}
.kg-scan{position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,transparent,var(--primary),transparent);animation:scan 1.4s linear infinite;pointer-events:none}
@keyframes scan{0%{top:0;opacity:0}10%{opacity:1}100%{top:100%;opacity:0}}

/* 分页 pill */
.pager-layer{position:absolute;inset:0;pointer-events:none;z-index:5}
.pager{position:absolute;transform:translate(-50%,0);display:flex;align-items:center;gap:2px;padding:2px;background:#fff;border:1px solid var(--line);border-radius:20px;box-shadow:0 3px 10px rgba(120,80,50,.16);pointer-events:auto;user-select:none}
.pg-btn{width:20px;height:20px;border:none;background:transparent;color:var(--primary);font-size:15px;font-weight:700;line-height:1;cursor:pointer;border-radius:50%;display:flex;align-items:center;justify-content:center;transition:.12s}
.pg-btn:hover:not(:disabled){background:#f3e7d9}
.pg-btn:disabled{color:#d6c8b2;cursor:not-allowed}
.pg-num{font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:11px;color:var(--mut);min-width:30px;text-align:center}

/* 详情 */
.kg-detail{position:absolute;right:0;top:0;bottom:0;width:300px;padding:18px;background:var(--card);border-left:1px solid var(--line);box-shadow:var(--shadow-lg);overflow-y:auto;z-index:6}
.corner{position:absolute;width:13px;height:13px;border:2px solid var(--primary);opacity:.5}
.corner.tl{top:9px;left:9px;border-right:0;border-bottom:0}.corner.br{bottom:9px;right:9px;border-left:0;border-top:0}
.d-head{display:flex;justify-content:space-between;align-items:center}
.d-type{font-size:12px;font-weight:700;letter-spacing:.5px}
.d-close{background:#f6efe3;border:1px solid var(--line);color:var(--mut);border-radius:6px;width:24px;height:24px;cursor:pointer;line-height:1}
.d-close:hover{color:var(--slate);border-color:var(--primary)}
.d-name{font-size:18px;font-weight:700;margin:10px 0 8px}
.badge-uv{display:inline-block;font-size:11px;color:#b06b14;background:#fdf2e2;border:1px solid #f0d2a0;padding:3px 9px;border-radius:6px;margin-bottom:8px}
.d-attrs{margin:14px 0;display:grid;grid-template-columns:64px 1fr;gap:8px 10px}
.d-attrs dt{color:var(--mut);font-size:12px}
.d-attrs dd{margin:0;color:var(--slate);font-size:13px;word-break:break-all}
.d-empty{color:var(--mut);font-size:13px;grid-column:1/3}
.d-imgs{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.d-imgs img{width:72px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--line)}
.d-actions{display:flex;gap:8px;margin-top:18px}
.act{flex:1;padding:9px;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;border:1px solid;transition:.15s}
.act.edit{background:#f8ece2;color:#a54d22;border-color:#e8c9af}.act.edit:hover{background:#f1ddc8}
.act.del{background:#fbeae6;color:#c5402c;border-color:#f0c4b8}.act.del:hover{background:#f6d8cf}
.act.add{width:100%;margin-top:10px;background:#eef3e6;color:#4d7530;border-color:#cad9b3}.act.add:hover{background:#e2ecd2}

.slide-enter-active,.slide-leave-active{transition:transform .28s ease,opacity .28s}
.slide-enter-from,.slide-leave-to{transform:translateX(40px);opacity:0}

.dlg-btn{padding:8px 18px;border-radius:8px;font-weight:600;font-size:13px;cursor:pointer;border:1px solid;transition:.15s}
.dlg-btn.cancel{background:#fff;color:var(--mut);border-color:var(--line);margin-right:8px}
.dlg-btn.cancel:hover{color:var(--slate)}
.dlg-btn.ok{background:var(--primary);color:#fff;border-color:var(--primary)}
.dlg-btn.ok:hover{background:#a54d22}
.dlg-btn.ok:disabled{opacity:.6;cursor:not-allowed}
</style>
