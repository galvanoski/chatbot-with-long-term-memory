import { assertUuid, assertOptionalString, backendUnavailableError } from '../../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const prompt = String(body?.prompt || '').slice(0, 2000) || ''
  const sourceMessageId = assertOptionalString(body?.source_message_id, 'source_message_id', 100)

  const upstream = await fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/image/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      prompt,
      source_message_id: sourceMessageId
    })
  }).catch(() => null)

  if (!upstream || !upstream.ok || !upstream.body) {
    throw backendUnavailableError()
  }

  setHeader(event, 'Content-Type', 'text/event-stream')
  setHeader(event, 'Cache-Control', 'no-cache, no-transform')
  setHeader(event, 'Connection', 'keep-alive')
  setHeader(event, 'X-Accel-Buffering', 'no')

  const reader = upstream.body.getReader()
  const stream = new ReadableStream({
    async start(controller) {
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          if (value) controller.enqueue(value)
        }
      } catch {
        controller.close()
        return
      }
      controller.close()
    }
  })

  return sendStream(event, stream)
})
