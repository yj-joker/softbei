const EDITABLE_KEYS = new Set(['source', 'section', 'page', 'text'])

function isStructuredProvenance(ref) {
  if (!ref || typeof ref !== 'object' || Array.isArray(ref)) return false
  if (ref.source === 'answer_feedback') return true
  return Object.keys(ref).some((key) => !EDITABLE_KEYS.has(key))
}

function formatEvidenceRefs(refs) {
  return refs.map((ref) => {
    if (ref?.text) return ref.text
    return [ref?.source, ref?.section, ref?.page ? `第${ref.page}页` : '']
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .join(' ｜ ')
  }).filter(Boolean).join('\n')
}

function parseEditableEvidenceRefs(value) {
  const text = String(value || '').trim()
  if (!text) return []
  if (text.startsWith('[')) {
    const parsed = JSON.parse(text)
    if (!Array.isArray(parsed)) throw new Error('evidence refs must be an array')
    return parsed
  }
  return text.split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/[|｜]/).map((part) => part.trim()).filter(Boolean)
      if (parts.length >= 2) {
        const pageMatch = parts[2]?.match(/\d+/)
        return {
          source: parts[0],
          section: parts[1],
          ...(pageMatch ? { page: Number(pageMatch[0]) } : {}),
        }
      }
      return { text: line }
    })
}

export function splitEvidenceRefsForEditing(refs) {
  const values = Array.isArray(refs) ? refs : []
  const preserved = values.filter(isStructuredProvenance)
  const editable = values.filter((ref) => !isStructuredProvenance(ref))
  return { preserved, text: formatEvidenceRefs(editable) }
}

export function mergeEvidenceRefsForSave(value, preserved = []) {
  return [
    ...(Array.isArray(preserved) ? preserved : []),
    ...parseEditableEvidenceRefs(value),
  ]
}
