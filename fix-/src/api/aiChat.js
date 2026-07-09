export function aiChatStream({ sessionId, message, images = [], thinking = false, context = undefined }, signal) {
  return fetch('/api/weixiu/ai/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    signal,
    body: JSON.stringify({
      session_id: sessionId,
      message,
      images,
      thinking,
      context,
    }),
  })
}
