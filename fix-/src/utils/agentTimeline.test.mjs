import assert from 'node:assert/strict'
import {
  createAgentTimelineStep,
  createInitialAgentProgress,
  createProgressSummary,
  isAgentTimelineEvent,
} from './agentTimeline.js'

const start = createAgentTimelineStep({
  event: 'retrieval_start',
  data: { query: 'bearing overheating', intent: 'diagnosis', routes: ['text_vector', 'keyword'] },
}, 0)

assert.equal(isAgentTimelineEvent('retrieval_start'), true)
assert.equal(start.title, '\u67e5\u8be2\u77e5\u8bc6\u5e93')
assert.doesNotMatch(start.detail, /text_vector|diagnosis/)
assert.equal(start.status, 'running')

const tool = createAgentTimelineStep({
  event: 'tool',
  data: { tool: 'knowledge_retrieval' },
}, 1)

assert.equal(tool.title, '\u67e5\u8be2\u77e5\u8bc6\u5e93')
assert.doesNotMatch(tool.title, /knowledge_retrieval/)

const toolLabels = [
  ['knowledge_inventory', '\u67e5\u770b\u77e5\u8bc6\u5e93\u6587\u4ef6'],
  ['java_graph_diagnosis_path', '\u67e5\u8be2\u6545\u969c\u8bca\u65ad\u8def\u5f84'],
  ['java_graph_device_search', '\u67e5\u8be2\u8bbe\u5907\u5173\u7cfb'],
  ['recall_conversation_detail', '\u53c2\u8003\u5386\u53f2\u5bf9\u8bdd'],
  ['procedure_recommend', '\u5339\u914d\u7ef4\u4fee\u6d41\u7a0b'],
]

for (const [toolName, expectedTitle] of toolLabels) {
  const step = createAgentTimelineStep({ event: 'tool', data: { tool: toolName } }, 10)
  assert.equal(step.title, expectedTitle)
  assert.doesNotMatch(step.title, /_/)
}

const route = createAgentTimelineStep({
  event: 'retrieval_route',
  data: { route: 'table_vector', candidateCount: 3, limit: 50 },
}, 2)

assert.equal(route.title, '\u67e5\u627e\u76f8\u5173\u8868\u683c')
assert.match(route.detail, /3/)
assert.equal(route.status, 'done')

const quality = createAgentTimelineStep({
  event: 'retrieval_quality',
  data: { stage: 'first_pass', grade: 'medium', candidateCount: 8, reasons: ['medium_top_score'] },
}, 3)

assert.equal(quality.title, '\u5224\u65ad\u8d44\u6599\u662f\u5426\u591f\u7528')
assert.match(quality.detail, /\u4e2d\u7b49/)
assert.doesNotMatch(quality.detail, /medium_top_score/)

const progress = createProgressSummary({
  status: 'streaming',
  agentSteps: [start, tool, route, quality],
})

assert.equal(createInitialAgentProgress().running, true)
assert.match(progress.text, /\u6b63\u5728\u5224\u65ad\u8d44\u6599\u662f\u5426\u591f\u7528/)
assert.equal(progress.running, true)

const completed = createProgressSummary({ status: 'done', agentSteps: [start, route, quality] }, { latency_ms: 2400 })
assert.match(completed.text, /\u5df2\u5b8c\u6210\u68c0\u7d22/)
assert.equal(completed.running, false)
