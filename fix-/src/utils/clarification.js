export function normalizeClarification(raw) {
  if (!raw || typeof raw !== 'object') return null
  const alternatives = Array.isArray(raw.alternatives) ? raw.alternatives : []
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
    options,
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
