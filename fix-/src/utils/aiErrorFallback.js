export const AI_FALLBACK_MESSAGE = '\u62b1\u6b49\uff0cAI \u5bf9\u8bdd\u670d\u52a1\u6682\u65f6\u9047\u5230\u95ee\u9898\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002'

const TECHNICAL_ERROR_PATTERNS = [
  /###\s*Error\s+querying\s+database/i,
  /\bjava\.[\w.]*Exception\b/i,
  /\borg\.springframework\b/i,
  /\bSQLSyntaxErrorException\b/i,
  /\bSQLException\b/i,
  /\bbad SQL grammar\b/i,
  /\bUnknown column\b/i,
  /\bdefaultParameterMap\b/i,
  /\bmapper\/[\w/]+Mapper\.java\b/i,
  /\bMemoryFactMapper\b/i,
  /\bSELECT\s+.+\s+FROM\b/i,
  /\bCaused by:\b/i,
  /\bat\s+[\w.$]+\(.*\.java:\d+\)/i,
]

export function isTechnicalErrorText(value) {
  const text = String(value || '')
  return TECHNICAL_ERROR_PATTERNS.some((pattern) => pattern.test(text))
}

export function sanitizeAiErrorMessage(value, fallback = AI_FALLBACK_MESSAGE) {
  const text = String(value || '').trim()
  if (!text || isTechnicalErrorText(text)) return fallback
  return text.length > 120 ? fallback : text
}

export function sanitizeAiContent(value, fallback = AI_FALLBACK_MESSAGE) {
  const text = String(value || '').trim()
  if (!text) return text
  return isTechnicalErrorText(text) ? fallback : text
}
