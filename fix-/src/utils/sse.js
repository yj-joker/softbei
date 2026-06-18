function trimLeadingBreaks(text) {
  return text.replace(/^[\r\n]+/, '')
}

function findJsonEnd(text) {
  const start = text.search(/\S/)
  if (start === -1 || text[start] !== '{') return -1

  let depth = 0
  let inString = false
  let escaped = false

  for (let i = start; i < text.length; i += 1) {
    const ch = text[i]

    if (inString) {
      if (escaped) {
        escaped = false
      } else if (ch === '\\') {
        escaped = true
      } else if (ch === '"') {
        inString = false
      }
      continue
    }

    if (ch === '"') inString = true
    else if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth === 0) return i + 1
    }
  }

  return -1
}

function normalizeEvent(payload) {
  const text = payload.trim()
  if (!text || text === '[DONE]') return null

  try {
    const json = JSON.parse(text)
    if (json && typeof json === 'object') {
      if (json.event) return json
      if (typeof json.content === 'string') return { event: 'token', data: { content: json.content } }
      if (typeof json.message === 'string') return { event: 'token', data: { content: json.message } }
    }
    return null
  } catch {
    if (text.startsWith('{')) return null
    return { event: 'token', data: { content: text } }
  }
}

function eventFromSseBlock(block) {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.trimStart().startsWith('data:'))
    .map((line) => line.replace(/^\s*data:\s?/, ''))
    .join('\n')

  return normalizeEvent(data || block)
}

function consumeDataLine(rest, onEvent) {
  const match = rest.match(/^\s*data:\s?/)
  if (!match) return null

  const afterPrefix = rest.slice(match[0].length)
  const jsonEnd = findJsonEnd(afterPrefix)
  if (jsonEnd === -1) return null

  const event = normalizeEvent(afterPrefix.slice(0, jsonEnd))
  if (event) onEvent(event)
  return rest.slice(match[0].length + jsonEnd).replace(/^[ \t]*(?:\r?\n)*/, '')
}

function consumeJson(rest, onEvent) {
  const start = rest.search(/\S/)
  if (start === -1 || rest[start] !== '{') return null

  const jsonEnd = findJsonEnd(rest.slice(start))
  if (jsonEnd === -1) return null

  const event = normalizeEvent(rest.slice(start, start + jsonEnd))
  if (event) onEvent(event)
  return rest.slice(start + jsonEnd).replace(/^[ \t]*(?:\r?\n)*/, '')
}

export function readSseEvents(buffer, onEvent) {
  let rest = trimLeadingBreaks(buffer)

  while (rest) {
    const dataLineRest = consumeDataLine(rest, onEvent)
    if (dataLineRest !== null) {
      rest = trimLeadingBreaks(dataLineRest)
      continue
    }

    const jsonRest = consumeJson(rest, onEvent)
    if (jsonRest !== null) {
      rest = trimLeadingBreaks(jsonRest)
      continue
    }

    const boundaryMatch = /\r?\n\r?\n/.exec(rest)
    if (!boundaryMatch) break

    const boundary = boundaryMatch.index
    const block = rest.slice(0, boundary)
    const event = eventFromSseBlock(block)
    if (event) onEvent(event)
    rest = trimLeadingBreaks(rest.slice(boundary + boundaryMatch[0].length))
  }

  return rest
}

export function flushSseEvents(buffer, onEvent) {
  readSseEvents(buffer, onEvent)
}

export function readSseChunk(buffer, onData) {
  return readSseEvents(buffer, (event) => {
    if (event.event === 'token') onData(event.data?.content || '')
    if (event.event === 'error') onData(`\n[错误] ${event.data?.message || '生成失败'}`)
  })
}

export function flushSseBuffer(buffer, onData) {
  flushSseEvents(buffer, (event) => {
    if (event.event === 'token') onData(event.data?.content || '')
    if (event.event === 'error') onData(`\n[错误] ${event.data?.message || '生成失败'}`)
  })
}
