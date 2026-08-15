export function normalizeClarification(raw) {
  if (!raw || typeof raw !== 'object') return null
  const alternatives = Array.isArray(raw.alternatives) && raw.alternatives.length
    ? raw.alternatives
    : Array.isArray(raw.candidates)
      ? raw.candidates
      : []
  const options = Array.isArray(raw.options) && raw.options.length
    ? raw.options
    : alternatives.map((item) => ({
      id: item.id,
      label: item.label || [item.value, item.unit].filter(Boolean).join(' '),
      text: item.value || item.label || '',
    }))
  return {
    ...raw,
    clarificationId: raw.clarificationId || raw.clarification_id || '',
    status: ['awaiting', 'reasked'].includes(raw.status) ? 'awaiting_answer' : raw.status,
    question: raw.question || raw.route_snapshot?.clarification_question || '',
    options,
  }
}

const CLARIFICATION_PRESENTATIONS = Object.freeze({
  evidence_conflict: {
    title: '证据参数冲突',
    hint: '请选择适用值或版本',
  },
  llm_slot_clarification: {
    title: '补充现场信息',
    hint: '请选择最符合现场情况的一项',
  },
  graph_observation: {
    title: '补充现场信息',
    hint: '请选择最符合现场情况的一项',
  },
  document_selection: {
    title: '确认查询范围',
    hint: '请选择适用的设备、文档或章节',
  },
  slot_disambiguation: {
    title: '确认查询范围',
    hint: '请选择适用的章节或装配场景',
  },
  diagnostic_cause: {
    title: '候选根因收敛',
    hint: '请选择一个现场现象',
  },
})

/** 根据反问类型返回用户可见文案，避免查询范围反问落入诊断默认文案。 */
export function getClarificationPresentation(raw, canAnswer = true) {
  const clarification = normalizeClarification(raw)
  const kind = clarification?.kind || ''
  const presentation = CLARIFICATION_PRESENTATIONS[kind] || {
    title: '补充查询信息',
    hint: '请选择最符合当前问题的一项',
  }
  return {
    title: presentation.title,
    hint: canAnswer ? presentation.hint : '已提交，正在收敛',
  }
}


export function buildClarificationContext(raw, optionId) {
  const clarification = normalizeClarification(raw)
  if (clarification?.kind === 'evidence_conflict') {
    return {
      clarification_id: clarification.clarificationId,
      selected_clarification_option_id: optionId,
    }
  }
  return {
    diagnostic_follow_up: raw ? JSON.parse(JSON.stringify(raw)) : null,
    selected_option_id: optionId,
  }
}
