const TIMELINE_EVENTS = new Set([
  'status',
  'tool',
  'retrieval_start',
  'retrieval_route',
  'retrieval_quality',
  'retrieval_supplement',
  'retrieval_expand',
  'retrieval_done',
  'verification',
  'error',
])

const TEXT = {
  initial: '\u6b63\u5728\u7406\u89e3\u95ee\u9898...',
  completed: '\u5df2\u5b8c\u6210\u68c0\u7d22',
  failed: '\u68c0\u7d22\u8fc7\u7a0b\u9047\u5230\u95ee\u9898',
  runningPrefix: '\u6b63\u5728',
  runningSuffix: '...',
  start: '\u67e5\u8be2\u77e5\u8bc6\u5e93',
  routeText: '\u67e5\u627e\u76f8\u5173\u6587\u5b57\u5185\u5bb9',
  routeTable: '\u67e5\u627e\u76f8\u5173\u8868\u683c',
  routeImage: '\u67e5\u627e\u76f8\u5173\u56fe\u7247',
  routeKeyword: '\u7528\u5173\u952e\u8bcd\u8865\u5145\u67e5\u627e',
  routeGeneric: '\u67e5\u627e\u5019\u9009\u8d44\u6599',
  qualityCheck: '\u5224\u65ad\u8d44\u6599\u662f\u5426\u591f\u7528',
  supplement: '\u8865\u5145\u67e5\u627e\u8d44\u6599',
  expand: '\u8865\u5168\u76f8\u5173\u6bb5\u843d',
  done: '\u627e\u5230\u53ef\u53c2\u8003\u8d44\u6599',
  verification: '\u6838\u5bf9\u56de\u7b54\u4f9d\u636e',
  tool: '\u6267\u884c\u4efb\u52a1',
  analyzed: '\u5206\u6790\u95ee\u9898',
  organizingProgress: '\u6b63\u5728\u6574\u7406\u56de\u7b54...',
  countHit: '\u627e\u5230',
  itemUnit: '\u6761',
  candidate: '\u5019\u9009',
  evidence: '\u8bc1\u636e',
  material: '\u8d44\u6599',
  elapsed: '\u7528\u65f6',
  found: '\u627e\u5230',
  quality: '\u8d28\u91cf',
  reason: '\u539f\u56e0',
  route: '\u8303\u56f4',
  query: '\u95ee\u9898',
  expanded: '\u8865\u5168',
  relaxed: '\u6269\u5927\u8303\u56f4',
  textMaterial: '\u6587\u5b57\u5185\u5bb9',
  tableMaterial: '\u8868\u683c',
  imageMaterial: '\u56fe\u7247',
  keywordMaterial: '\u5173\u952e\u8bcd',
  genericMaterial: '\u7efc\u5408\u8d44\u6599',
  reasonMissingRequiredType: '\u7f3a\u5c11\u5fc5\u8981\u7c7b\u578b\u7684\u8d44\u6599',
  reasonMissingImage: '\u56fe\u7247\u8d44\u6599\u4e0d\u8db3',
  reasonMissingTable: '\u8868\u683c\u8d44\u6599\u4e0d\u8db3',
  reasonLowTopScore: '\u6700\u76f8\u5173\u8d44\u6599\u5339\u914d\u5ea6\u504f\u4f4e',
  reasonMediumTopScore: '\u6700\u76f8\u5173\u8d44\u6599\u5339\u914d\u5ea6\u4e00\u822c',
  reasonFewCandidates: '\u5019\u9009\u8d44\u6599\u8f83\u5c11',
  reasonTypeIncomplete: '\u8d44\u6599\u7c7b\u578b\u4e0d\u591f\u5b8c\u6574',
  reasonNeedMore: '\u9700\u8981\u66f4\u591a\u8d44\u6599',
  needReview: '\u9700\u8981\u590d\u6838',
  noIssue: '\u672a\u53d1\u73b0\u660e\u663e\u95ee\u9898',
}

const GRADE_LABEL = {
  high: '\u9ad8',
  medium: '\u4e2d\u7b49',
  low: '\u4f4e',
}

const TOOL_LABEL = {
  knowledge_retrieval: TEXT.start,
  knowledge_inventory: '\u67e5\u770b\u77e5\u8bc6\u5e93\u6587\u4ef6',
  java_graph_diagnosis_path: '\u67e5\u8be2\u6545\u969c\u8bca\u65ad\u8def\u5f84',
  java_graph_device_search: '\u67e5\u8be2\u8bbe\u5907\u5173\u7cfb',
  recall_conversation_detail: '\u53c2\u8003\u5386\u53f2\u5bf9\u8bdd',
  procedure_recommend: '\u5339\u914d\u7ef4\u4fee\u6d41\u7a0b',
  causal_follow_up: '\u6839\u636e\u8ffd\u95ee\u6536\u655b\u6839\u56e0',
}

const REASON_LABEL = {
  missing_required_type: TEXT.reasonMissingRequiredType,
  missing_image: TEXT.reasonMissingImage,
  missing_table: TEXT.reasonMissingTable,
  low_top_score: TEXT.reasonLowTopScore,
  medium_top_score: TEXT.reasonMediumTopScore,
  too_few_candidates: TEXT.reasonFewCandidates,
  insufficient_types: TEXT.reasonTypeIncomplete,
}

function joinParts(parts) {
  return parts.filter((item) => item !== undefined && item !== null && item !== '').join(' · ')
}

function asList(value) {
  return Array.isArray(value) ? value.filter(Boolean) : []
}

function unique(items) {
  return [...new Set(items.filter(Boolean))]
}

function routeTitle(route = '') {
  const value = String(route || '').toLowerCase()
  if (value.includes('table')) return TEXT.routeTable
  if (value.includes('image')) return TEXT.routeImage
  if (value.includes('keyword')) return TEXT.routeKeyword
  if (value.includes('text') || value.includes('semantic')) return TEXT.routeText
  return TEXT.routeGeneric
}

function routeLabel(route = '') {
  const value = String(route || '').toLowerCase()
  if (value.includes('table')) return TEXT.tableMaterial
  if (value.includes('image')) return TEXT.imageMaterial
  if (value.includes('keyword')) return TEXT.keywordMaterial
  if (value.includes('text') || value.includes('semantic')) return TEXT.textMaterial
  return TEXT.genericMaterial
}

function routeListLabel(routes) {
  return unique(asList(routes).map(routeLabel)).join(', ')
}

function toolTitle(toolName = '') {
  return TOOL_LABEL[String(toolName || '')] || TEXT.tool
}

function reasonTitle(reason = '') {
  const value = String(reason || '').toLowerCase()
  if (!value) return ''
  if (REASON_LABEL[value]) return REASON_LABEL[value]
  if (value.includes('image')) return TEXT.reasonMissingImage
  if (value.includes('table')) return TEXT.reasonMissingTable
  if (value.includes('required')) return TEXT.reasonMissingRequiredType
  if (value.includes('type')) return TEXT.reasonTypeIncomplete
  if (value.includes('few') || value.includes('count')) return TEXT.reasonFewCandidates
  if (value.includes('score') || value.includes('low')) return TEXT.reasonLowTopScore
  return TEXT.reasonNeedMore
}

function reasonListLabel(reasons) {
  return unique(asList(reasons).map(reasonTitle)).join(', ')
}

function formatSeconds(ms) {
  const value = Number(ms || 0)
  if (!Number.isFinite(value) || value <= 0) return ''
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`
}

function latestRetrievalDone(steps) {
  return [...(steps || [])].reverse().find((step) => step.event === 'retrieval_done')
}

export function isAgentTimelineEvent(eventName) {
  return TIMELINE_EVENTS.has(eventName)
}

export function createInitialAgentProgress() {
  return { text: TEXT.initial, running: true }
}

export function createAgentTimelineStep(event, index = 0) {
  const eventName = event?.event || 'status'
  const data = event?.data || {}
  const base = {
    id: `${Date.now()}-${index}-${eventName}`,
    event: eventName,
    title: TEXT.analyzed,
    detail: '',
    status: 'done',
    rawData: data,
  }

  if (eventName === 'status') {
    return {
      ...base,
      title: TEXT.analyzed,
      detail: '',
      status: 'running',
    }
  }

  if (eventName === 'tool') {
    const toolName = data.tool || data.name || ''
    return {
      ...base,
      title: toolTitle(toolName),
      detail: '',
      status: 'running',
    }
  }

  if (eventName === 'retrieval_start') {
    return {
      ...base,
      title: TEXT.start,
      detail: joinParts([
        asList(data.routes).length ? `${TEXT.route}\uff1a${routeListLabel(data.routes)}` : '',
        data.query ? `${TEXT.query}\uff1a${data.query}` : '',
      ]),
      status: 'running',
    }
  }

  if (eventName === 'retrieval_route') {
    return {
      ...base,
      title: routeTitle(data.route || data.sourceRoute),
      detail: joinParts([
        `${TEXT.countHit} ${Number(data.candidateCount || 0)} ${TEXT.itemUnit}`,
        data.relaxed ? TEXT.relaxed : '',
      ]),
      status: data.skipped ? 'skipped' : 'done',
    }
  }

  if (eventName === 'retrieval_quality') {
    const grade = GRADE_LABEL[data.grade] || data.grade || '-'
    const reasons = reasonListLabel(data.reasons)
    return {
      ...base,
      title: TEXT.qualityCheck,
      detail: joinParts([
        `${TEXT.quality}\uff1a${grade}`,
        data.candidateCount !== undefined ? `${TEXT.candidate}\uff1a${data.candidateCount}` : '',
        reasons ? `${TEXT.reason}\uff1a${reasons}` : '',
      ]),
      status: data.grade === 'low' ? 'warn' : 'done',
    }
  }

  if (eventName === 'retrieval_supplement') {
    return {
      ...base,
      title: TEXT.supplement,
      detail: joinParts([
        asList(data.routes).length ? `${TEXT.route}\uff1a${routeListLabel(data.routes)}` : '',
        reasonListLabel(data.reasons) ? `${TEXT.reason}\uff1a${reasonListLabel(data.reasons)}` : '',
      ]),
      status: 'running',
    }
  }

  if (eventName === 'retrieval_expand') {
    return {
      ...base,
      title: TEXT.expand,
      detail: joinParts([
        `${TEXT.expanded} ${Number(data.expandedCount || 0)} ${TEXT.itemUnit}`,
        data.totalCount !== undefined ? `${TEXT.material}\uff1a${data.totalCount}` : '',
      ]),
      status: 'done',
    }
  }

  if (eventName === 'retrieval_done') {
    return {
      ...base,
      title: TEXT.done,
      detail: joinParts([
        `${TEXT.material}\uff1a${Number(data.selectedCount || 0)} ${TEXT.itemUnit}`,
        data.finalQuality ? `${TEXT.quality}\uff1a${GRADE_LABEL[data.finalQuality] || data.finalQuality}` : '',
      ]),
      status: 'done',
    }
  }

  if (eventName === 'verification') {
    const summary = data.summary || {}
    const issueCount = Number(summary.grounding_unverified || 0) + Number(summary.graph_unverified || 0) + Number(summary.safety_missing || 0)
    return {
      ...base,
      title: TEXT.verification,
      detail: issueCount ? `${TEXT.needReview}\uff1a${issueCount}` : TEXT.noIssue,
      status: issueCount ? 'warn' : 'done',
    }
  }

  if (eventName === 'error') {
    return {
      ...base,
      title: TEXT.failed,
      detail: data.message || '',
      status: 'error',
    }
  }

  return base
}

export function createProgressSummary(message, doneData = {}) {
  const steps = Array.isArray(message?.agentSteps) ? message.agentSteps : []
  const status = message?.status || 'done'
  if (status === 'error') return { text: TEXT.failed, running: false }
  if (status === 'streaming') {
    const latest = steps[steps.length - 1]
    if (!latest) return createInitialAgentProgress()
    if (latest.status === 'error') return { text: TEXT.failed, running: false }
    if (latest.event === 'retrieval_done') return { text: TEXT.organizingProgress, running: true }
    return { text: `${TEXT.runningPrefix}${latest.title}${TEXT.runningSuffix}`, running: true }
  }

  if (!steps.length) return { text: '', running: false }

  const done = latestRetrievalDone(steps)
  const selectedCount = Number(done?.rawData?.selectedCount || 0)
  const elapsed = formatSeconds(doneData.latency_ms || doneData.latencyMs || message?.latencyMs)
  const text = joinParts([
    `${TEXT.completed}${elapsed ? `\uff0c${TEXT.elapsed} ${elapsed}` : ''}`,
    selectedCount ? `${TEXT.found} ${selectedCount} ${TEXT.itemUnit}${TEXT.evidence}` : '',
  ])
  return { text, running: false }
}
