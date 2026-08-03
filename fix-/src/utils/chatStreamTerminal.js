export const INCOMPLETE_STREAM_ERROR_NAME = 'IncompleteChatStreamError'
export const INCOMPLETE_STREAM_MESSAGE = '\u56de\u7b54\u5df2\u751f\u6210\uff0c\u4f46\u7ed3\u679c\u672a\u5b8c\u6574\u8fd4\u56de\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002'

export function ensureTerminalStreamEvent({ doneReceived = false, errorReceived = false } = {}) {
  if (doneReceived || errorReceived) return

  const error = new Error(INCOMPLETE_STREAM_MESSAGE)
  error.name = INCOMPLETE_STREAM_ERROR_NAME
  throw error
}
