import { assertUuid, backendUnavailableError } from '../../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const instruction = String(body?.instruction || '').slice(0, 2000) || 'a cat programmer logo'
  const silent = body?.silent === true

  const upstream = await fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/image-prompt/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      instruction,
      silent
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
