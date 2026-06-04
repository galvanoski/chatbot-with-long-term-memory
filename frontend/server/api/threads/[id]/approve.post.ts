export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')
  const userId = getUserId(event)
  const body = await readBody(event)

  const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/approve`, {
    method: 'POST',
    body: {
      user_id: userId,
      edited_copy: body?.edited_copy,
      edited_parts: body?.edited_parts,
      feedback: body?.feedback
    }
  })

  return result
})
