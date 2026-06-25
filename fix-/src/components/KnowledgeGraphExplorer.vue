<script setup>
import { ref, reactive, shallowRef, onMounted, onBeforeUnmount, computed } from 'vue'
import { Graph } from '@antv/g6'
import { ElMessage } from 'element-plus'
import {
  searchDevices, getDeviceComponents, getComponentFaults, getFaultSolutions, getFaultCases,
  searchDiagnosisPaths,
} from '../api/graph'

const props = defineProps({
  // false = 管理端（可审核/拒绝）；true = 普通用户端（只读）
  readonly: { type: Boolean, default: true },
})

/* ---------- 节点类型视觉编码（暖色大地色系，5 类仍可区分） ---------- */
const TYPE = {
  device:    { label: '设备', fill: 'var(--plaza-accent-soft)', stroke: 'var(--plaza-accent)', size: 48 },
  component: { label: '部件', fill: '#eef4e2', stroke: '#5e8c3e', size: 34 },
  fault:     { label: '故障', fill: '#fdf2e0', stroke: '#df9226', size: 31 },
  solution:  { label: '方案', fill: '#f5ece8', stroke: '#a8605f', size: 27 },
  case:      { label: '案例', fill: '#e8f1ee', stroke: '#3f8c7c', size: 29 },
}
const SEVERITY = { 轻微: '#c9a23a', 一般: '#df9226', 严重: 'var(--plaza-accent)', 致命: '#c5402c' }
const REL = { OWNS: '拥有', CAUSES: '引发', HAS_SOLUTION: '方案', RECORDED: '案例' }
const LABEL_FONT = "'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace"

/* ---------- 内部状态 ---------- */
const PAGE_SIZE = 6          // 每次展开仅渲染一页，避免一次铺出整页节点
const containerRef = ref(null)
const wrapRef = ref(null)
const graph = shallowRef(null)
const nodeMap = new Map()   // key -> { id, data:{...} }
const edgeSet = new Set()   // edgeKey
const expanded = new Set()  // 已展开的节点 key
const childrenOf = new Map()// parentKey -> Set<childKey>（仅当前页）
const parentOf = new Map()  // childKey -> parentKey
const pagers = reactive({}) // parentKey -> { page, size, total }
const pagerPos = reactive({})// parentKey -> { x, y } 屏幕坐标
const pos = new Map()       // key -> [x,y] 持久化布局坐标（展开/翻页只重绘不重跑力导）
const ui = reactive({
  deviceKw: '', diagKw: '', loading: false,
  selected: null, showDetail: false, busyNode: '',
})
const stats = reactive({ nodes: 0, edges: 0 })

const key = (type, id) => `${type}:${id}`
const pages = (p) => Math.max(1, Math.ceil((p?.total || 0) / (p?.size || PAGE_SIZE)))

/* device/component 走通用展开；fault 单独处理（方案 + 案例） */
const CHILD = {
  device:    { child: 'component', rel: 'OWNS',   fetch: getDeviceComponents, labelKey: 'name' },
  component: { child: 'fault',     rel: 'CAUSES', fetch: getComponentFaults,  labelKey: 'name' },
}

/* ---------- 图数据写入 ---------- */
function nodeStyle() {
  return {
    size: (d) => d.data.size,
    fill: (d) => d.data.fill,
    stroke: (d) => d.data.stroke,
    lineWidth: 1.6,
    shadowColor: 'rgba(120,70,30,0.18)',
    shadowBlur: 8,
    shadowOffsetY: 2,
    labelText: (d) => d.data.label,
    labelFill: 'var(--plaza-text)',
    labelFontSize: 11,
    labelFontFamily: LABEL_FONT,
    labelPlacement: 'bottom',
    labelOffsetY: 4,
    labelBackground: true,
    labelBackgroundFill: 'rgba(255, 255, 255, 0.92)',
    labelBackgroundRadius: 4,
    labelBackgroundLineWidth: 1,
    labelBackgroundStroke: 'var(--plaza-border)',
    labelPadding: [2, 6],
  }
}

function buildGraph() {
  graph.value = new Graph({
    container: containerRef.value,
    autoResize: true,
    background: 'transparent',
    data: { nodes: [], edges: [] },
    node: {
      style: nodeStyle(),
      state: {
        active:   { lineWidth: 2.6, halo: true, haloStroke: 'var(--plaza-accent)', haloOpacity: 0.16 },
        selected: { lineWidth: 3, halo: true, haloStroke: 'var(--plaza-accent)', haloOpacity: 0.22 },
      },
    },
    edge: {
      style: {
        stroke: 'rgba(120,70,30,0.26)',
        lineWidth: 1.1,
        endArrow: true,
        endArrowType: 'vee',
        endArrowSize: 7,
        labelText: (d) => d.data.relLabel || '',
        labelFill: 'var(--plaza-text-muted)',
        labelFontSize: 9,
        labelFontFamily: LABEL_FONT,
        labelBackground: true,
        labelBackgroundFill: 'rgba(255, 255, 255, 0.9)',
        labelBackgroundRadius: 2,
      },
      state: { active: { stroke: 'var(--plaza-accent)', lineWidth: 2 } },
    },
    layout: {
      type: 'd3-force',
      link: { distance: 120, strength: 0.5 },
      collide: { radius: 42 },
      manyBody: { strength: -180 },
    },
    behaviors: [
      'zoom-canvas', 'drag-canvas', 'drag-element',
      { type: 'hover-activate', degree: 1 },
      { type: 'click-select', enable: true },
    ],
  })

  graph.value.on('node:click', (e) => {
    const id = e.target?.id
    if (!id) return
    const n = nodeMap.get(id)
    if (!n) return
    ui.selected = n.data
    ui.showDetail = true
    const t = n.data.type
    if (!expanded.has(id) && t !== 'solution' && t !== 'case') expand(id, 0)
  })
  graph.value.render()
  tickOverlay()
}

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

let renderTimer = null
/**
 * 始终跑力导（新节点会从各自的初始点「弹出」并被力导散开），
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
  clearTimeout(renderTimer)
  renderTimer = setTimeout(async () => {
    try {
      await graph.value.render()
      if (refit) graph.value.fitView()
    } catch (e) { /* ignore */ }
  }, 30)
}

function addNode(type, id, label, raw = {}) {
  if (!id) return null
  const k = key(type, id)
  if (nodeMap.has(k)) return k
  const conf = TYPE[type]
  const stroke = type === 'fault' && SEVERITY[raw.severity] ? SEVERITY[raw.severity] : conf.stroke
  nodeMap.set(k, {
    id: k,
    data: {
      type, rawId: id, label: (label || conf.label).slice(0, 16),
      fullLabel: label || conf.label,
      fill: conf.fill, stroke, size: conf.size,
      raw,
    },
  })
  return k
}

function addEdge(sourceKey, targetKey, rel) {
  if (!sourceKey || !targetKey) return
  edgeSet.add(`${sourceKey}|${targetKey}|${rel}`)
}

/* ---------- 列表归一化（兼容 List / PageResult，返回记录 + 总数） ---------- */
function rows(res) {
  const d = res?.data
  if (Array.isArray(d)) return { records: d, total: d.length }
  if (d && Array.isArray(d.records)) return { records: d.records, total: Number(d.total ?? d.records.length) }
  return { records: [], total: 0 }
}

/* ---------- 子树维护（分页时整页替换） ---------- */
function track(pk, ck) {
  if (!childrenOf.has(pk)) childrenOf.set(pk, new Set())
  childrenOf.get(pk).add(ck)
  parentOf.set(ck, pk)
}
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

/* ---------- 展开 / 分页（每次仅渲染一页） ---------- */
async function expand(pk, page = 0) {
  const n = nodeMap.get(pk)
  if (!n || ui.busyNode) return
  const { type, rawId } = n.data
  if (type === 'solution' || type === 'case') return
  ui.busyNode = pk
  try {
    if (type === 'fault') {
      const [solRes, caseRes] = await Promise.all([
        getFaultSolutions(rawId, { page, size: PAGE_SIZE }),
        getFaultCases(rawId, { page, size: PAGE_SIZE }),
      ])
      const sol = rows(solRes)
      const cas = rows(caseRes)
      snapshotPositions()
      clearChildren(pk)
      const childKeys = []
      sol.records.forEach((s) => { const sk = addNode('solution', s.id, s.title, s); addEdge(pk, sk, 'HAS_SOLUTION'); track(pk, sk); childKeys.push(sk) })
      cas.records.forEach((c) => { const ck = addNode('case', c.id, c.title || c.caseTitle || c.summary || '经验案例', c); addEdge(pk, ck, 'RECORDED'); track(pk, ck); childKeys.push(ck) })
      placeChildren(pk, childKeys)
      const total = Math.max(sol.total, cas.total)
      if (total > PAGE_SIZE) pagers[pk] = { page, size: PAGE_SIZE, total }
      else delete pagers[pk]
      if (!sol.records.length && !cas.records.length && page === 0) ElMessage.info('未发现关联方案或案例')
    } else {
      const conf = CHILD[type]
      const { records, total } = rows(await conf.fetch(rawId, { page, size: PAGE_SIZE }))
      snapshotPositions()
      clearChildren(pk)
      const childKeys = []
      records.forEach((r) => { const ck = addNode(conf.child, r.id, r[conf.labelKey], r); addEdge(pk, ck, conf.rel); track(pk, ck); childKeys.push(ck) })
      placeChildren(pk, childKeys)
      if (total > PAGE_SIZE) pagers[pk] = { page, size: PAGE_SIZE, total }
      else delete pagers[pk]
      if (!records.length && page === 0) ElMessage.info(`未发现关联${TYPE[conf.child].label}`)
    }
    expanded.add(pk)
    syncGraph()
  } catch (err) {
    ElMessage.error('展开失败：' + (err.message || '请求异常'))
  } finally {
    ui.busyNode = ''
  }
}
function repage(pk, delta) {
  const p = pagers[pk]
  if (!p || ui.busyNode) return
  const np = Math.min(pages(p) - 1, Math.max(0, p.page + delta))
  if (np !== p.page) expand(pk, np)
}

/* ---------- 分页器跟随节点定位 ---------- */
let raf = null
function tickOverlay() {
  const keys = Object.keys(pagers)
  const rect = wrapRef.value?.getBoundingClientRect()
  if (keys.length && graph.value && rect) {
    for (const k of keys) {
      try {
        const wp = graph.value.getElementPosition(k)
        const c = graph.value.getClientByCanvas(wp)
        const r = (nodeMap.get(k)?.data.size || 30) / 2
        pagerPos[k] = { x: c[0] - rect.left, y: c[1] - rect.top + r + 11 }
      } catch (e) { /* 尚未渲染 */ }
    }
  }
  raf = requestAnimationFrame(tickOverlay)
}

/* ---------- 顶部操作 ---------- */
async function onSearchDevices() {
  ui.loading = true
  try {
    const { records: list } = rows(await searchDevices(ui.deviceKw.trim(), 30))
    if (!list.length) { ElMessage.info('未搜索到设备'); return }
    list.forEach((d) => addNode('device', d.id, d.name, d))
    syncGraph(true)
  } catch (err) {
    ElMessage.error('设备搜索失败：' + (err.message || ''))
  } finally { ui.loading = false }
}

async function onDiagnose() {
  const kw = ui.diagKw.trim()
  if (!kw) { ElMessage.info('请输入故障/部件描述'); return }
  ui.loading = true
  try {
    const res = await searchDiagnosisPaths({ faultDescription: kw, componentDescription: kw })
    const { records: recs } = rows(res)
    if (!recs.length) { ElMessage.info('未召回诊断路径'); return }
    recs.forEach((r) => {
      let prev = null
      if (r.deviceId) prev = addNode('device', r.deviceId, r.deviceName, { name: r.deviceName })
      if (r.componentId) {
        const ck = addNode('component', r.componentId, r.componentName, { name: r.componentName, imageUrls: r.componentImageUrls })
        if (prev) addEdge(prev, ck, 'OWNS'); prev = ck
      }
      let fk = null
      if (r.faultId) {
        fk = addNode('fault', r.faultId, r.faultName, { name: r.faultName, severity: r.faultSeverity, imageUrls: r.faultImageUrls })
        if (prev) addEdge(prev, fk, 'CAUSES')
      }
      ;(r.solutions || []).forEach((s) => {
        const sk = addNode('solution', s.id, s.title, { title: s.title, estimatedTime: s.estimatedTime, verified: s.verified })
        if (fk) addEdge(fk, sk, 'HAS_SOLUTION')
      })
    })
    syncGraph(true)
    ElMessage.success(`召回 ${recs.length} 条诊断路径`)
  } catch (err) {
    ElMessage.error('诊断搜索失败：' + (err.message || ''))
  } finally { ui.loading = false }
}

function fitView() { graph.value?.fitView() }
function relayout() { graph.value?.layout() }
function clearAll() {
  nodeMap.clear(); edgeSet.clear(); expanded.clear()
  childrenOf.clear(); parentOf.clear(); pos.clear()
  for (const k of Object.keys(pagers)) delete pagers[k]
  for (const k of Object.keys(pagerPos)) delete pagerPos[k]
  ui.selected = null; ui.showDetail = false
  syncGraph()
}

const detailRows = computed(() => {
  const d = ui.selected; if (!d) return []
  const r = d.raw || {}
  const map = {
    device: [['编码', r.code], ['型号', r.model], ['位置', r.location], ['制造商', r.manufacturer]],
    component: [['编号', r.partNumber], ['规格', r.specification], ['供应商', r.supplier], ['寿命', r.lifecycle]],
    fault: [['等级', r.severity], ['类别', r.category], ['编码', r.code], ['描述', r.description]],
    solution: [['预计耗时', r.estimatedTime ? r.estimatedTime + ' 分钟' : null], ['难度', r.difficulty], ['工具', r.toolsRequired], ['描述', r.description || r.summary]],
    case: [['故障', r.faultName], ['摘要', r.summary], ['经验', r.experienceSummary], ['结果', r.result], ['标签', Array.isArray(r.tags) ? r.tags.join('，') : r.tags]],
  }
  return (map[d.type] || []).filter(([, v]) => v != null && v !== '')
})

onMounted(buildGraph)
onBeforeUnmount(() => { clearTimeout(renderTimer); cancelAnimationFrame(raf); graph.value?.destroy() })
</script>

<template>
  <div class="kg-root" :class="{ 'is-readonly': readonly }">
    <!-- 顶部标题栏 -->
    <header class="kg-head">
      <div class="kg-title">
        <span class="led" /><span class="t-main">知识图谱</span><span class="t-sub">KNOWLEDGE&nbsp;GRAPH</span>
        <span class="t-mode" :class="readonly ? 'm-ro' : 'm-rw'">{{ readonly ? '只读浏览' : '管理浏览' }}</span>
      </div>
      <div class="kg-readout">
        <span>节点 <b>{{ stats.nodes }}</b></span><i />
        <span>关系 <b>{{ stats.edges }}</b></span>
      </div>
    </header>

    <div class="kg-body">
      <!-- 左侧控制台 -->
      <aside class="kg-console">
        <div class="panel">
          <div class="panel-h">设备入口</div>
          <el-input v-model="ui.deviceKw" placeholder="设备名 / 编码 / 型号（空=全部）" size="small"
                    @keyup.enter="onSearchDevices" clearable />
          <button class="hud-btn" :disabled="ui.loading" @click="onSearchDevices">检索设备</button>
        </div>

        <div class="panel">
          <div class="panel-h">诊断路径</div>
          <el-input v-model="ui.diagKw" type="textarea" :rows="2" placeholder="描述故障现象 / 部件，召回链路子图"
                    @keyup.enter.exact.prevent="onDiagnose" />
          <button class="hud-btn b-amber" :disabled="ui.loading" @click="onDiagnose">语义召回</button>
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
              <!-- 卫星节点 -->
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
              <!-- 中心设备节点 -->
              <g class="eg-node eg-core" style="--d:0s">
                <circle cx="140" cy="104" r="24" style="fill: var(--plaza-accent-soft); stroke: var(--plaza-accent)" />
                <text x="140" y="108" class="eg-core-t">设备</text>
              </g>
            </svg>
          </div>
          <div class="eg-copy">
            <h3>知识图谱待探索</h3>
            <p>从左侧 <b>检索设备</b> 或 <b>语义召回</b> 开始构建关联</p>
            <div class="eg-hints">
              <span><i />点击节点逐层展开</span>
              <span><i />子项超过一页带 ‹ › 翻页</span>
            </div>
          </div>
        </div>
        <div v-if="ui.loading" class="kg-scan" />

        <!-- 分页器浮层（跟随节点定位，仅当该节点子项超过一页时出现） -->
        <div class="pager-layer">
          <div
            v-for="(p, k) in pagers"
            :key="k"
            v-show="pagerPos[k]"
            class="pager"
            :style="pagerPos[k] ? { left: pagerPos[k].x + 'px', top: pagerPos[k].y + 'px' } : {}"
          >
            <button class="pg-btn" :disabled="p.page <= 0 || !!ui.busyNode" @click.stop="repage(k, -1)">‹</button>
            <span class="pg-num">{{ p.page + 1 }}/{{ pages(p) }}</span>
            <button class="pg-btn" :disabled="p.page + 1 >= pages(p) || !!ui.busyNode" @click.stop="repage(k, 1)">›</button>
          </div>
        </div>
      </main>

      <!-- 右侧详情抽屉 -->
      <transition name="slide">
        <aside v-if="ui.showDetail && ui.selected" class="kg-detail">
          <span class="corner tl" /><span class="corner br" />
          <div class="d-head">
            <span class="d-type" :style="{ color: ui.selected.stroke }">{{ TYPE[ui.selected.type]?.label }}</span>
            <button class="d-close" @click="ui.showDetail=false">关闭</button>
          </div>
          <h3 class="d-name">{{ ui.selected.fullLabel }}</h3>
          <dl class="d-attrs">
            <template v-for="(row,i) in detailRows" :key="i">
              <dt>{{ row[0] }}</dt><dd>{{ row[1] }}</dd>
            </template>
            <p v-if="!detailRows.length" class="d-empty">无更多属性</p>
          </dl>

          <div v-if="(ui.selected.raw?.imageUrls||[]).length" class="d-imgs">
            <img v-for="(u,i) in ui.selected.raw.imageUrls" :key="i" :src="u" alt="" />
          </div>

          <p v-if="!['solution','case'].includes(ui.selected.type)" class="d-hint">点击节点可展开下一层关联</p>
        </aside>
      </transition>
    </div>
  </div>
</template>

<style scoped>
.kg-root{
  --bg:var(--plaza-bg); --card:var(--plaza-bg-card); --line:var(--plaza-border); --line-soft:var(--plaza-panel-bg);
  --primary:var(--plaza-accent); --slate:var(--plaza-heading); --mut:var(--plaza-text-muted); --amber:#df9226;
  --shadow:0 2px 12px rgba(120,70,30,.07),0 1px 3px rgba(120,70,30,.04);
  --shadow-lg:0 10px 32px rgba(120,70,30,.14);
  position:absolute; inset:0; display:flex; flex-direction:column;
  background:
    radial-gradient(900px 500px at 78% -8%, var(--plaza-accent-soft), transparent 60%),
    radial-gradient(700px 460px at 6% 112%, rgba(224,152,47,.09), transparent 60%),
    var(--bg);
  color:var(--slate);
  font-family:var(--font-body);
  overflow:hidden;
}
.mono{font-family:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace}

/* 标题栏 */
.kg-head{display:flex;align-items:center;justify-content:space-between;padding:13px 22px;
  border-bottom:1px solid var(--line); background:rgba(255,255,255,.85); backdrop-filter:blur(6px)}
.kg-title{display:flex;align-items:center;gap:10px}
.led{width:8px;height:8px;border-radius:50%;background:var(--primary);box-shadow:0 0 0 3px var(--plaza-accent-soft-strong);animation:pulse 2.4s infinite}
@keyframes pulse{50%{opacity:.45}}
.t-main{font-family:var(--font-display);font-size:19px;font-weight:700;letter-spacing:.3px;color:var(--slate)}
.t-sub{font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:10px;color:var(--plaza-text-muted);letter-spacing:2px}
.t-mode{margin-left:8px;font-size:11px;font-weight:600;padding:2px 10px;border-radius:20px;border:1px solid}
.m-ro{color:var(--primary);border-color:var(--plaza-accent);background:var(--plaza-accent-soft)}
.m-rw{color:#d97706;border-color:rgba(245,158,11,.4);background:rgba(245,158,11,.1)}
.kg-readout{display:flex;align-items:center;gap:14px;font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:12px;color:var(--mut)}
.kg-readout b{color:var(--primary);font-size:14px;font-weight:700}
.kg-readout i{width:1px;height:14px;background:var(--line)}

.kg-body{flex:1;display:flex;min-height:0;position:relative}

/* 左控制台 */
.kg-console{width:248px;flex-shrink:0;padding:16px;display:flex;flex-direction:column;gap:13px;
  border-right:1px solid var(--line); background:rgba(255,255,255,.5); overflow-y:auto}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px;display:flex;flex-direction:column;gap:9px;box-shadow:var(--shadow)}
.panel-h{font-size:12px;font-weight:700;color:var(--slate);letter-spacing:.5px}
.panel-h::before{content:'';display:inline-block;width:3px;height:12px;background:var(--primary);border-radius:2px;margin-right:7px;vertical-align:-1px}
.ops{flex-direction:row;gap:8px}
.hud-btn{background:var(--primary);color:#fff;border:1px solid var(--primary);border-radius:8px;padding:9px;font-weight:600;font-size:13px;letter-spacing:.5px;cursor:pointer;transition:.18s;box-shadow:0 2px 8px var(--plaza-accent-soft-strong)}
.hud-btn:hover{background:var(--plaza-accent-hover);box-shadow:0 4px 14px var(--plaza-accent)}
.hud-btn:disabled{opacity:.5;cursor:not-allowed;box-shadow:none}
.b-amber{background:#fff;color:#d97706;border-color:#fcd9a6;box-shadow:none} .b-amber:hover{background:#fff7ed}
.b-warn{background:#fff;color:#d97706;border-color:#fcd9a6;box-shadow:none} .b-warn:hover{background:#fff7ed}
.mini{flex:1;background:#fff;color:var(--slate);border:1px solid var(--line);border-radius:8px;padding:8px;font-weight:600;font-size:12px;cursor:pointer;transition:.15s}
.mini:hover{border-color:var(--primary);color:var(--primary);background:var(--plaza-panel-bg)}
.mini.danger:hover{border-color:#ef4444;color:#ef4444;background:#fef2f2}
.legend{margin-top:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px;box-shadow:var(--shadow)}
.lg-h{font-size:12px;font-weight:700;color:var(--mut);margin-bottom:9px}
.lg-row{display:flex;align-items:center;gap:9px;font-size:13px;padding:3px 0;color:var(--slate)}
.dot{width:13px;height:13px;border-radius:50%;border:2px solid}
.dot-uv{background:#fff7ed;border:2px dashed var(--amber)}

/* 画布 */
.kg-canvas-wrap{flex:1;position:relative;min-width:0;background:
  linear-gradient(180deg,rgba(255,255,255,.4),rgba(248,249,255,.2))}
.grid-overlay{position:absolute;inset:0;pointer-events:none;opacity:.7;
  background-image:linear-gradient(var(--plaza-accent-soft) 1px,transparent 1px),linear-gradient(90deg,var(--plaza-accent-soft) 1px,transparent 1px);
  background-size:40px 40px;mask-image:radial-gradient(circle at 50% 45%,#000 60%,transparent 100%)}
.kg-canvas{position:absolute;inset:0}
.kg-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;pointer-events:none;text-align:center}

/* —— 待探索引导插画 —— */
.eg-art{position:relative;width:280px;height:214px}
.eg-aura{position:absolute;left:50%;top:50%;width:170px;height:170px;transform:translate(-50%,-48%);border-radius:50%;
  background:radial-gradient(circle,var(--plaza-accent-soft-strong),transparent 68%);filter:blur(4px);animation:eg-breathe 4.5s ease-in-out infinite}
.eg-svg{position:relative;width:280px;height:214px;overflow:visible}
.eg-links path{fill:none;stroke:var(--plaza-accent);stroke-width:1.4;stroke-dasharray:4 6;stroke-linecap:round;
  animation:eg-flow 6s linear infinite,eg-fade-in .8s .1s both}
.eg-node{transform-box:fill-box;transform-origin:center;animation:eg-pop .6s var(--d,0s) both cubic-bezier(.22,1,.36,1)}
.eg-node circle{stroke-width:2;filter:drop-shadow(0 3px 6px rgba(120,70,30,.14))}
.eg-node text{font-family:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace;font-size:11px;font-weight:600;fill:var(--slate);text-anchor:middle}
.eg-core-t{font-size:13px;font-weight:700;fill:var(--plaza-accent)}
/* 卫星节点轻微浮动（入场后） */
.eg-node:not(.eg-core){animation:eg-pop .6s var(--d,0s) both cubic-bezier(.22,1,.36,1),eg-float 5s var(--d,0s) ease-in-out infinite 1s}
@keyframes eg-pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes eg-float{0%,100%{transform:translateY(0)}50%{transform:translateY(var(--fx,3px))}}
@keyframes eg-flow{to{stroke-dashoffset:-100}}
@keyframes eg-breathe{0%,100%{opacity:.55;transform:translate(-50%,-48%) scale(.92)}50%{opacity:1;transform:translate(-50%,-48%) scale(1.06)}}
@keyframes eg-fade-in{from{opacity:0}to{opacity:1}}

.eg-copy{display:flex;flex-direction:column;align-items:center;gap:7px;animation:eg-rise .6s .35s both ease-out}
.eg-copy h3{font-family:var(--font-display,inherit);font-size:19px;font-weight:700;color:var(--slate);letter-spacing:.5px}
.eg-copy p{color:var(--mut);font-size:14px;line-height:1.7} .eg-copy b{color:var(--primary);font-weight:700}
.eg-hints{display:flex;gap:16px;margin-top:4px;flex-wrap:wrap;justify-content:center}
.eg-hints span{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--plaza-text-muted)}
.eg-hints i{width:5px;height:5px;border-radius:50%;background:var(--primary);opacity:.7}
@keyframes eg-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}

@media (prefers-reduced-motion:reduce){
  .eg-aura,.eg-links path,.eg-node,.eg-node:not(.eg-core),.eg-copy{animation:none!important}
}
.kg-scan{position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,transparent,var(--primary),transparent);animation:scan 1.4s linear infinite;pointer-events:none}
@keyframes scan{0%{top:0;opacity:0}10%{opacity:1}100%{top:100%;opacity:0}}

/* 分页器浮层 */
.pager-layer{position:absolute;inset:0;pointer-events:none;z-index:5}
.pager{position:absolute;transform:translate(-50%,0);display:flex;align-items:center;gap:2px;padding:2px;background:#fff;border:1px solid var(--line);border-radius:20px;box-shadow:0 3px 10px rgba(120,80,50,.16);pointer-events:auto;user-select:none}
.pg-btn{width:20px;height:20px;border:none;background:transparent;color:var(--primary);font-size:15px;font-weight:700;line-height:1;cursor:pointer;border-radius:50%;display:flex;align-items:center;justify-content:center;transition:.12s}
.pg-btn:hover:not(:disabled){background:var(--plaza-panel-bg)}
.pg-btn:disabled{color:var(--plaza-border-strong);cursor:not-allowed}
.pg-num{font-family:'JetBrains Mono','IBM Plex Mono',monospace;font-size:11px;color:var(--mut);min-width:30px;text-align:center}

/* 详情抽屉 */
.kg-detail{position:absolute;right:0;top:0;bottom:0;width:300px;padding:18px;
  background:var(--card);border-left:1px solid var(--line);box-shadow:var(--shadow-lg);overflow-y:auto}
.corner{position:absolute;width:13px;height:13px;border:2px solid var(--primary);opacity:.5}
.corner.tl{top:9px;left:9px;border-right:0;border-bottom:0} .corner.br{bottom:9px;right:9px;border-left:0;border-top:0}
.d-head{display:flex;justify-content:space-between;align-items:center}
.d-type{font-size:12px;font-weight:700;letter-spacing:.5px}
.d-close{background:#f5f7fb;border:1px solid var(--line);color:var(--mut);border-radius:6px;width:24px;height:24px;cursor:pointer;line-height:1}
.d-close:hover{color:var(--slate);border-color:var(--primary)}
.d-name{font-size:18px;font-weight:700;margin:10px 0 8px;color:var(--slate)}
.badge-uv{display:inline-block;font-size:11px;color:#d97706;background:#fff7ed;border:1px solid #fcd9a6;padding:3px 9px;border-radius:6px;margin-bottom:8px}
.d-attrs{margin:14px 0;display:grid;grid-template-columns:62px 1fr;gap:8px 10px}
.d-attrs dt{color:var(--mut);font-size:12px}
.d-attrs dd{margin:0;color:var(--slate);font-size:13px;word-break:break-all}
.d-empty{color:var(--mut);font-size:13px;grid-column:1/3}
.d-imgs{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.d-imgs img{width:72px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--line)}
.d-actions{display:flex;gap:8px;margin-top:16px}
.act{flex:1;padding:9px;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;border:1px solid;transition:.15s}
.act.ok{background:#f0fdf4;color:#16a34a;border-color:#bbf7d0} .act.ok:hover{background:#dcfce7}
.act.no{background:#fef2f2;color:#dc2626;border-color:#fecaca} .act.no:hover{background:#fee2e2}
.act:disabled{opacity:.5;cursor:not-allowed}
.d-hint{margin-top:14px;font-size:12px;color:var(--mut)}

.slide-enter-active,.slide-leave-active{transition:transform .28s ease,opacity .28s}
.slide-enter-from,.slide-leave-to{transform:translateX(40px);opacity:0}
</style>
