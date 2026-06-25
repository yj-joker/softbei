<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import {
  ArrowDownBold,
  CircleCheck,
  Close,
  Collection,
  Connection,
  Document,
  Loading,
  Warning,
} from '@element-plus/icons-vue'

const props = defineProps({
  message: { type: Object, default: null },
  focus: { type: Object, default: () => ({ toolId: '', nonce: 0 }) },
})

defineEmits(['close'])

const rootEl = ref(null)

/* 逐条展开：记录当前展开的内层条目 id */
const opened = reactive(new Set())
function toggleItem(key) {
  if (opened.has(key)) opened.delete(key)
  else opened.add(key)
}
function isOpen(key) {
  return opened.has(key)
}

/* 来自左侧气泡的「点进某个工具」请求：展开并滚动到对应工具 */
watch(
  () => props.focus?.nonce,
  () => {
    const id = props.focus?.toolId
    if (!id) return
    opened.add(id)
    nextTick(() => {
      const el = rootEl.value?.querySelector(`[data-tool-id="${id}"]`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  },
  { immediate: true },
)

/* 长正文折叠 */
const CLAMP_LEN = 150
const docExpanded = reactive(new Set())
function docKey(scope, i) {
  return `${scope}#${i}`
}
function toggleDoc(key) {
  if (docExpanded.has(key)) docExpanded.delete(key)
  else docExpanded.add(key)
}
function isDocOpen(key) {
  return docExpanded.has(key)
}
function isLong(text) {
  return (text || '').length > CLAMP_LEN
}

/* 相关度分档（用于高亮 + 等级文案） */
function scoreClass(s) {
  if (s === null || s === undefined) return ''
  if (s >= 0.75) return 'sc-hi'
  if (s >= 0.5) return 'sc-mid'
  return 'sc-lo'
}
function scoreLabel(s) {
  return { 'sc-hi': '高', 'sc-mid': '中', 'sc-lo': '低' }[scoreClass(s)] || ''
}

/* 工具正文里给 LLM 看的「回答要求」指令段不展示给用户 */
function cleanText(t) {
  const text = t || ''
  const idx = text.indexOf('【回答要求】')
  return idx >= 0 ? text.slice(0, idx).trim() : text
}

const EVENT_LABELS = {
  tool: '工具调用',
  retrieval_route: '检索路由',
  retrieval_done: '检索完成',
  retrieval_quality: '质量评估',
  verification: '依据核对',
  status: '状态更新',
  message: '消息',
}
const STATUS_LABELS = {
  done: '已完成',
  running: '进行中',
  warn: '需复核',
  error: '失败',
}
const RAW_LABELS = {
  tool: '工具',
  toolName: '工具',
  candidateCount: '候选数',
  selectedCount: '采用数',
  grade: '质量等级',
  skipped: '是否跳过',
  route: '检索路由',
  source: '来源',
  query: '查询语句',
  keyword: '关键词',
  count: '数量',
  total: '总数',
  durationMs: '耗时(ms)',
  reason: '原因',
  score: '分值',
  status: '状态',
}

function fmtVal(v) {
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'object') {
    const text = JSON.stringify(v)
    return text.length > 160 ? `${text.slice(0, 160)}…` : text
  }
  return String(v)
}
function rawRows(raw) {
  if (!raw || typeof raw !== 'object') return []
  return Object.entries(raw)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => ({ k: RAW_LABELS[k] || k, v: fmtVal(v) }))
}
function note(text) {
  return [{ k: '说明', v: text }]
}
function pretty(raw) {
  try {
    return JSON.stringify(raw, null, 2)
  } catch {
    return String(raw)
  }
}
function hasRaw(raw) {
  if (raw === null || raw === undefined) return false
  if (Array.isArray(raw)) return raw.some(hasRaw)
  if (typeof raw === 'object') return Object.keys(raw).length > 0
  return raw !== ''
}

const TYPE_LABEL = { text: '正文', table: '表格', image: '图片', image_summary: '图片' }
function typeLabel(t) {
  return TYPE_LABEL[t] || t || '正文'
}
/* 工具步骤上由 tool_result 事件挂载的结构化返回内容 */
function resultItemsOf(step) {
  return Array.isArray(step?.result?.items) ? step.result.items : []
}

const steps = computed(() =>
  Array.isArray(props.message?.agentSteps) ? props.message.agentSteps : [],
)

const isRunning = computed(() => props.message?.status === 'streaming')

const timeline = computed(() =>
  steps.value
    .filter((step) => step.event !== 'status')
    .map((step, index, list) => {
      const isLatest = index === list.length - 1
      let status = step.status || 'done'
      if (status === 'running' && (!isRunning.value || !isLatest)) status = 'done'
      return { ...step, displayStatus: status }
    }),
)

function stepDetails(step) {
  const rows = []
  if (step.event) rows.push({ k: '事件', v: EVENT_LABELS[step.event] || step.event })
  rows.push({ k: '状态', v: STATUS_LABELS[step.displayStatus] || step.displayStatus })
  if (step.detail) rows.push({ k: '说明', v: step.detail })
  // 核对依据这类步骤不展开内部字段（has_issues / summary 等噪音）
  if (step.event !== 'verification') rows.push(...rawRows(step.rawData))
  return rows
}

const toolSteps = computed(() =>
  steps.value.filter((step) => step.event === 'tool'),
)

const retrievalDone = computed(() =>
  [...steps.value].reverse().find((step) => step.event === 'retrieval_done'),
)

const qualityStep = computed(() =>
  [...steps.value].reverse().find((step) => step.event === 'retrieval_quality'),
)

const verificationStep = computed(() =>
  [...steps.value].reverse().find((step) => step.event === 'verification'),
)

const routeSteps = computed(() =>
  steps.value.filter((step) => step.event === 'retrieval_route' && !step.rawData?.skipped),
)

const uniqueTools = computed(() => {
  const seen = new Set()
  return toolSteps.value.filter((step) => {
    const key = step.rawData?.tool || step.title
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
})

const selectedEvidenceCount = computed(() =>
  Number(retrievalDone.value?.rawData?.selectedCount || 0),
)

const candidateCount = computed(() =>
  routeSteps.value.reduce(
    (total, step) => total + Number(step.rawData?.candidateCount || 0),
    0,
  ),
)

const graphToolCount = computed(() =>
  uniqueTools.value.filter((step) =>
    String(step.rawData?.tool || '').startsWith('java_graph_'),
  ).length,
)

const evidenceImages = computed(() =>
  (props.message?.evidenceImages || []).filter((item) => item?.imageUrl),
)

const qualityLabel = computed(() => {
  const grade = qualityStep.value?.rawData?.grade
  return { high: '高', medium: '中等', low: '偏低' }[grade] || ''
})

const latencyText = computed(() => {
  const ms = Number(props.message?.latencyMs || 0)
  if (!ms) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
})

const summaryCards = computed(() => [
  selectedEvidenceCount.value
    ? {
        id: 'sum-selected',
        label: '采用依据',
        value: `${selectedEvidenceCount.value} 条`,
        icon: Document,
        rows: note(`Agent 最终采用了 ${selectedEvidenceCount.value} 条检索依据用于作答。`),
        raw: retrievalDone.value?.rawData,
      }
    : null,
  candidateCount.value
    ? {
        id: 'sum-candidate',
        label: '候选资料',
        value: `${candidateCount.value} 条`,
        icon: Collection,
        rows: note(`各检索路由共召回 ${candidateCount.value} 条候选资料参与排序与筛选。`),
        raw: routeSteps.value.map((step) => step.rawData),
      }
    : null,
  graphToolCount.value
    ? {
        id: 'sum-graph',
        label: '图谱查询',
        value: `${graphToolCount.value} 次`,
        icon: Connection,
        rows: note(`本次调用知识图谱查询接口 ${graphToolCount.value} 次。`),
        raw: uniqueTools.value
          .filter((step) => String(step.rawData?.tool || '').startsWith('java_graph_'))
          .map((step) => step.rawData),
      }
    : null,
].filter(Boolean))

const resultItems = computed(() => [
  qualityLabel.value
    ? {
        id: 'res-quality',
        label: '资料质量',
        value: qualityLabel.value,
        rows: note(`基于检索资料覆盖度与相关性的综合评估：${qualityLabel.value}。`),
        raw: qualityStep.value?.rawData,
      }
    : null,
  verificationStep.value
    ? {
        id: 'res-verify',
        label: '依据核对',
        value: verificationStep.value.status === 'warn' ? '需要复核' : '已完成',
        warn: verificationStep.value.status === 'warn',
        rows: verificationStep.value.detail
          ? [{ k: '说明', v: verificationStep.value.detail }]
          : [],
        raw: verificationStep.value.rawData,
      }
    : null,
  latencyText.value
    ? {
        id: 'res-latency',
        label: '处理耗时',
        value: latencyText.value,
        rows: note(`本次 Agent 端到端处理总耗时 ${latencyText.value}。`),
        raw: { latencyMs: Number(props.message?.latencyMs || 0) },
      }
    : null,
].filter(Boolean))
</script>

<template>
  <aside ref="rootEl" class="agent-panel" aria-label="Agent 工作过程">
    <header class="agent-head">
      <div>
        <span class="agent-kicker">AGENT ACTIVITY</span>
        <h2>Agent 工作过程</h2>
      </div>
      <div class="agent-head-actions">
        <span class="agent-state" :class="{ running: isRunning }">
          <i />
          {{ isRunning ? '正在执行' : '执行完成' }}
        </span>
        <button type="button" aria-label="收起 Agent 工作过程" @click="$emit('close')">
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </header>

    <div class="agent-body">
      <!-- 执行轨迹 -->
      <section class="agent-section">
        <div class="section-heading">
          <span class="sec-title">执行轨迹</span>
          <span class="sec-aside"><b>{{ timeline.length }} STEP</b></span>
        </div>
        <div class="sec-body">
          <div class="agent-timeline">
            <article
              v-for="step in timeline"
              :key="step.id"
              class="timeline-step"
              :class="[`is-${step.displayStatus}`, { 'is-open': isOpen(step.id) }]"
            >
              <button type="button" class="step-row" :aria-expanded="isOpen(step.id)" @click="toggleItem(step.id)">
                <span class="step-icon">
                  <el-icon v-if="step.displayStatus === 'running'"><Loading /></el-icon>
                  <el-icon v-else-if="step.displayStatus === 'warn' || step.displayStatus === 'error'">
                    <Warning />
                  </el-icon>
                  <el-icon v-else><CircleCheck /></el-icon>
                </span>
                <span class="step-copy">
                  <b>{{ step.title }}</b>
                  <small v-if="step.detail">{{ step.detail }}</small>
                </span>
                <el-icon class="row-caret" :class="{ open: isOpen(step.id) }"><ArrowDownBold /></el-icon>
              </button>
              <div v-if="isOpen(step.id)" class="item-detail">
                <!-- 工具调用：只展示该工具返回的实际内容（正文，不展示 JSON） -->
                <template v-if="step.event === 'tool'">
                  <span class="detail-sub">返回内容</span>
                  <div v-if="resultItemsOf(step).length" class="evidence-list">
                    <article
                      v-for="(ev, i) in resultItemsOf(step)"
                      :key="i"
                      class="evidence-doc"
                      :class="scoreClass(ev.score)"
                    >
                      <header>
                        <b>{{ ev.title }}</b>
                        <span v-if="ev.score != null" class="ev-score" :class="scoreClass(ev.score)">
                          相关度{{ scoreLabel(ev.score) }}
                        </span>
                      </header>
                      <div v-if="ev.type || ev.page" class="ev-meta">
                        <span v-if="ev.type" class="ev-tag">{{ typeLabel(ev.type) }}</span>
                        <span v-if="ev.page">P{{ ev.page }}</span>
                      </div>
                      <p
                        class="ev-content"
                        :class="{ clamped: isLong(ev.content) && !isDocOpen(docKey(step.id, i)) }"
                      >{{ ev.content }}</p>
                      <button
                        v-if="isLong(ev.content)"
                        type="button"
                        class="ev-toggle"
                        @click="toggleDoc(docKey(step.id, i))"
                      >{{ isDocOpen(docKey(step.id, i)) ? '收起' : '展开全文' }}</button>
                    </article>
                  </div>
                  <pre v-else-if="step.result && step.result.text" class="detail-text">{{ cleanText(step.result.text) }}</pre>
                  <p v-else class="detail-empty">该工具暂无可展示的返回内容</p>
                </template>

                <!-- 其它步骤：仅摘要，不展示原始 JSON -->
                <template v-else>
                  <dl>
                    <div v-for="(row, i) in stepDetails(step)" :key="i">
                      <dt>{{ row.k }}</dt>
                      <dd>{{ row.v }}</dd>
                    </div>
                  </dl>
                </template>
              </div>
            </article>
          </div>
        </div>
      </section>

      <!-- 检索结果 -->
      <section v-if="summaryCards.length" class="agent-section">
        <div class="section-heading">
          <span class="sec-title">检索结果</span>
          <span class="sec-aside"><b>LIVE DATA</b></span>
        </div>
        <div class="sec-body">
          <div class="summary-grid">
            <button
              v-for="item in summaryCards"
              :key="item.id"
              type="button"
              class="summary-card"
              :class="{ 'is-open': isOpen(item.id) }"
              :aria-expanded="isOpen(item.id)"
              @click="toggleItem(item.id)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span>
                <b>{{ item.value }}</b>
                <small>{{ item.label }}</small>
              </span>
            </button>
          </div>
          <template v-for="item in summaryCards" :key="`${item.id}-d`">
            <div v-if="isOpen(item.id)" class="item-detail">
              <span class="detail-title">{{ item.label }}</span>
              <dl v-if="item.rows.length">
                <div v-for="(row, i) in item.rows" :key="i">
                  <dt>{{ row.k }}</dt>
                  <dd>{{ row.v }}</dd>
                </div>
              </dl>
              <p v-if="!item.rows.length && !evidenceOf(item.raw).length" class="detail-empty">无更多调用信息</p>
            </div>
          </template>
        </div>
      </section>

      <!-- 调用能力 -->
      <section v-if="uniqueTools.length" class="agent-section">
        <div class="section-heading">
          <span class="sec-title">调用能力</span>
          <span class="sec-aside"><b>{{ uniqueTools.length }} TOOLS</b></span>
        </div>
        <div class="sec-body">
          <div class="tool-list">
            <button
              v-for="tool in uniqueTools"
              :key="tool.id"
              type="button"
              class="tool-chip"
              :class="{ 'is-open': isOpen(tool.id) }"
              :aria-expanded="isOpen(tool.id)"
              @click="toggleItem(tool.id)"
            >
              <i />
              {{ tool.title }}
            </button>
          </div>
          <template v-for="tool in uniqueTools" :key="`${tool.id}-d`">
            <div v-if="isOpen(tool.id)" class="item-detail" :data-tool-id="tool.id">
              <span class="detail-title">{{ tool.title }}</span>
              <div v-if="resultItemsOf(tool).length" class="evidence-list">
                <article
                  v-for="(ev, i) in resultItemsOf(tool)"
                  :key="i"
                  class="evidence-doc"
                  :class="scoreClass(ev.score)"
                >
                  <header>
                    <b>{{ ev.title }}</b>
                    <span v-if="ev.score != null" class="ev-score" :class="scoreClass(ev.score)">
                      相关度{{ scoreLabel(ev.score) }}
                    </span>
                  </header>
                  <div v-if="ev.type || ev.page" class="ev-meta">
                    <span v-if="ev.type" class="ev-tag">{{ typeLabel(ev.type) }}</span>
                    <span v-if="ev.page">P{{ ev.page }}</span>
                  </div>
                  <p
                    class="ev-content"
                    :class="{ clamped: isLong(ev.content) && !isDocOpen(docKey('t-' + tool.id, i)) }"
                  >{{ ev.content }}</p>
                  <button
                    v-if="isLong(ev.content)"
                    type="button"
                    class="ev-toggle"
                    @click="toggleDoc(docKey('t-' + tool.id, i))"
                  >{{ isDocOpen(docKey('t-' + tool.id, i)) ? '收起' : '展开全文' }}</button>
                </article>
              </div>
              <pre v-else-if="tool.result && tool.result.text" class="detail-text">{{ cleanText(tool.result.text) }}</pre>
              <p v-else class="detail-empty">该工具暂无可展示的返回内容</p>
            </div>
          </template>
        </div>
      </section>

      <!-- 证据图片 -->
      <section v-if="evidenceImages.length" class="agent-section">
        <div class="section-heading">
          <span class="sec-title">证据图片</span>
          <span class="sec-aside"><b>{{ evidenceImages.length }} ITEMS</b></span>
        </div>
        <div class="sec-body">
          <div class="evidence-grid">
            <figure v-for="(item, index) in evidenceImages.slice(0, 4)" :key="`${item.imageUrl}-${index}`">
              <img :src="item.imageUrl" :alt="item.caption || item.sectionTitle || '检索证据'" />
              <figcaption>
                {{ item.caption || item.sectionTitle || `证据 ${index + 1}` }}
                <small v-if="item.page">P{{ item.page }}</small>
              </figcaption>
            </figure>
          </div>
        </div>
      </section>

      <!-- 执行结论 -->
      <section v-if="resultItems.length" class="agent-section agent-result">
        <div class="section-heading">
          <span class="sec-title">执行结论</span>
          <span class="sec-aside"><b>VERIFIED</b></span>
        </div>
        <div class="sec-body">
          <div class="result-list">
            <button
              v-for="item in resultItems"
              :key="item.id"
              type="button"
              class="result-card"
              :class="{ 'is-open': isOpen(item.id) }"
              :aria-expanded="isOpen(item.id)"
              @click="toggleItem(item.id)"
            >
              <small>{{ item.label }}</small>
              <b :class="{ warn: item.warn }">{{ item.value }}</b>
            </button>
          </div>
          <template v-for="item in resultItems" :key="`${item.id}-d`">
            <div v-if="isOpen(item.id)" class="item-detail">
              <span class="detail-title">{{ item.label }}</span>
              <dl v-if="item.rows.length">
                <div v-for="(row, i) in item.rows" :key="i">
                  <dt>{{ row.k }}</dt>
                  <dd>{{ row.v }}</dd>
                </div>
              </dl>
              <p v-if="!item.rows.length" class="detail-empty">无更多调用信息</p>
            </div>
          </template>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.agent-panel {
  display: flex;
  width: 350px;
  min-width: 350px;
  height: 100%;
  flex-direction: column;
  overflow: hidden;
  border-left: 1px solid var(--plaza-border);
  color: var(--plaza-text);
  background: var(--plaza-bg);
}

.agent-head {
  display: flex;
  min-height: 78px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 15px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  color: var(--plaza-heading);
  background:
    radial-gradient(circle at 82% 20%, var(--signal-soft), transparent 26%),
    linear-gradient(145deg, #ffffff, var(--plaza-bg-card));
}

.agent-kicker,
.section-heading b {
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.12em;
}

.agent-kicker {
  color: var(--plaza-accent);
}

.agent-head h2 {
  margin-top: 3px;
  color: var(--plaza-heading);
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 800;
}

.agent-head-actions,
.agent-state {
  display: flex;
  align-items: center;
}

.agent-head-actions {
  gap: 7px;
}

.agent-state {
  gap: 6px;
  color: var(--plaza-text);
  font-size: 9px;
}

.agent-state i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #33c778;
  box-shadow: 0 0 0 3px rgba(51, 199, 120, 0.12);
}

.agent-state.running i {
  background: var(--plaza-accent);
  box-shadow: 0 0 0 3px var(--plaza-accent-soft-strong);
  animation: agent-pulse 1.2s ease-in-out infinite;
}

.agent-head button {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  color: var(--plaza-text);
  background: rgba(0, 0, 0, 0.04);
  cursor: pointer;
}

.agent-head button:hover {
  color: var(--plaza-accent);
  border-color: var(--plaza-accent);
}

.agent-body {
  min-height: 0;
  flex: 1;
  padding: 12px;
  overflow-y: auto;
}

.agent-section {
  padding: 13px;
  border: 1px solid var(--plaza-border);
  border-radius: 11px;
  background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
}

.agent-section + .agent-section {
  margin-top: 9px;
}

.section-heading {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.section-heading .sec-title {
  color: var(--plaza-heading);
  font-size: 11px;
  font-weight: 800;
}

.section-heading b {
  color: var(--plaza-text-muted);
}

.sec-aside {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.sec-body {
  padding-top: 11px;
}

/* ===== 内层逐条展开详情 ===== */
.item-detail {
  margin-top: 7px;
  padding: 9px 10px;
  border: 1px dashed var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-input);
}

.detail-title {
  display: block;
  margin-bottom: 6px;
  color: #b96700;
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.item-detail dl {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.item-detail dl > div {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
}

.item-detail dt {
  color: var(--plaza-text-muted);
  font-size: 8px;
  line-height: 1.5;
}

.item-detail dd {
  margin: 0;
  color: var(--plaza-text);
  font-size: 9px;
  line-height: 1.5;
  word-break: break-word;
}

.detail-empty {
  margin: 0;
  color: var(--plaza-text-muted);
  font-size: 8px;
}

.detail-sub {
  display: block;
  margin: 8px 0 5px;
  color: var(--plaza-text-muted);
  font-family: var(--font-mono);
  font-size: 7px;
  font-weight: 800;
  letter-spacing: 0.1em;
}

.detail-json {
  margin: 0;
  max-height: 220px;
  padding: 8px 9px;
  overflow: auto;
  border: 1px solid var(--plaza-border);
  border-radius: 6px;
  color: var(--plaza-heading);
  background: var(--plaza-bg);
  font-family: var(--font-mono);
  font-size: 9px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  tab-size: 2;
}

.detail-text {
  margin: 0;
  max-height: 360px;
  padding: 9px 10px;
  overflow: auto;
  border: 1px solid var(--plaza-border);
  border-left: 2px solid var(--plaza-accent);
  border-radius: 6px;
  color: var(--plaza-text);
  background: var(--plaza-bg-card);
  font-family: inherit;
  font-size: 10px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 工具返回的正文卡片 ===== */
.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.evidence-doc {
  padding: 8px 9px;
  border: 1px solid var(--plaza-border);
  border-left: 2px solid var(--plaza-accent);
  border-radius: 6px;
  background: var(--plaza-bg-card);
}

.evidence-doc header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.evidence-doc header b {
  color: var(--plaza-heading);
  font-size: 10px;
  font-weight: 800;
}

.ev-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
  color: var(--plaza-text-muted);
  font-size: 7px;
}

.ev-tag {
  padding: 1px 5px;
  border-radius: 4px;
  color: var(--plaza-accent-hover);
  background: var(--plaza-accent-soft);
  font-weight: 800;
}

.ev-content {
  margin: 6px 0 0;
  color: var(--plaza-text);
  font-size: 10px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.ev-content.clamped {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: normal;
}

.ev-toggle {
  margin-top: 5px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #b96700;
  font-size: 9px;
  font-weight: 800;
  cursor: pointer;
}

.ev-toggle:hover {
  color: var(--plaza-accent);
  text-decoration: underline;
}

/* 相关度分档高亮 */
.evidence-doc.sc-hi { border-left-color: var(--plaza-accent); }
.evidence-doc.sc-mid { border-left-color: var(--plaza-accent); }
.evidence-doc.sc-lo { border-left-color: var(--plaza-border-strong); }

.ev-score {
  flex: 0 0 auto;
  padding: 1px 6px;
  border-radius: 999px;
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 800;
}

.ev-score.sc-hi { color: #8a3d12; background: var(--plaza-accent-soft-strong); }
.ev-score.sc-mid { color: var(--plaza-accent-hover); background: var(--plaza-accent-soft); }
.ev-score.sc-lo { color: var(--plaza-text-muted); background: rgba(0, 0, 0, 0.06); }


/* ===== 执行轨迹 ===== */
.agent-timeline {
  position: relative;
  display: flex;
  flex-direction: column;
}

.agent-timeline::before {
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 11px;
  width: 1px;
  background: var(--plaza-border);
  content: '';
}

.timeline-step {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
}

.step-row {
  display: grid;
  width: 100%;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 6px 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.step-icon {
  display: grid;
  width: 23px;
  height: 23px;
  place-items: center;
  border: 1px solid #cfe7da;
  border-radius: 50%;
  color: #1d9a5d;
  background: #effaf4;
  font-size: 12px;
}

.timeline-step.is-running .step-icon {
  color: #b96700;
  border-color: #f2c98e;
  background: #fff6e8;
}

.timeline-step.is-running .step-icon .el-icon {
  animation: agent-spin 0.9s linear infinite;
}

.timeline-step.is-warn .step-icon,
.timeline-step.is-error .step-icon {
  color: #c54e36;
  border-color: #f0c7bf;
  background: #fff3f1;
}

.step-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.step-copy b {
  color: var(--plaza-text);
  font-size: 10px;
  font-weight: 750;
}

.timeline-step.is-running .step-copy b {
  color: #ac6200;
}

.step-copy small {
  margin-top: 2px;
  color: var(--plaza-text-muted);
  font-size: 8px;
  line-height: 1.55;
}

.row-caret {
  color: var(--plaza-text-muted);
  font-size: 10px;
  transition: transform 0.25s ease;
}

.row-caret.open {
  transform: rotate(180deg);
}

.timeline-step .item-detail {
  margin-left: 33px;
}

/* ===== 检索结果 ===== */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.summary-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  padding: 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--plaza-accent-hover);
  background: var(--plaza-accent-soft);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: border-color 0.18s ease;
}

.summary-card:hover,
.summary-card.is-open {
  border-color: var(--plaza-accent);
}

.summary-card > .el-icon {
  font-size: 16px;
}

.summary-card span {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.summary-card b {
  color: var(--plaza-heading);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 800;
}

.summary-card small {
  overflow: hidden;
  color: var(--plaza-text-muted);
  font-size: 7px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 调用能力 ===== */
.tool-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 7px;
  border: 1px solid var(--plaza-border);
  border-radius: 7px;
  color: var(--plaza-text);
  background: var(--plaza-bg-input);
  cursor: pointer;
  font: inherit;
  font-size: 8px;
  transition: border-color 0.18s ease, background 0.18s ease;
}

.tool-chip:hover,
.tool-chip.is-open {
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}

.tool-chip i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--plaza-accent);
}

/* ===== 证据图片 ===== */
.evidence-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.evidence-grid figure {
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--plaza-border);
  border-radius: 8px;
  background: var(--plaza-bg-input);
}

.evidence-grid img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
}

.evidence-grid figcaption {
  display: flex;
  justify-content: space-between;
  gap: 5px;
  padding: 6px;
  overflow: hidden;
  color: var(--plaza-text-muted);
  font-size: 7px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-grid figcaption small {
  color: #b96700;
  font-weight: 800;
}

/* ===== 执行结论 ===== */
.result-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.result-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 8px;
  border: 0;
  border-left: 2px solid var(--plaza-accent);
  background: var(--plaza-accent-soft);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: filter 0.18s ease;
}

.result-card:hover,
.result-card.is-open {
  filter: brightness(0.97);
}

.result-card small {
  color: var(--plaza-text-muted);
  font-size: 7px;
}

.result-card b {
  margin-top: 2px;
  overflow: hidden;
  color: var(--plaza-heading);
  font-size: 10px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-card b.warn {
  color: #c54e36;
}

@keyframes agent-spin {
  to { transform: rotate(360deg); }
}

@keyframes agent-pulse {
  50% { opacity: 0.45; }
}

@media (max-width: 1080px) {
  .agent-panel {
    position: absolute;
    inset: 0 0 0 auto;
    z-index: 30;
    box-shadow: -18px 0 40px rgba(20, 24, 32, 0.16);
  }
}

@media (max-width: 620px) {
  .agent-panel {
    width: min(100%, 350px);
    min-width: min(100%, 350px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-state.running i,
  .timeline-step.is-running .step-icon .el-icon {
    animation: none;
  }
}
</style>
