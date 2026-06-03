export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')
  const userId = getUserId(event)
  const body = await readBody(event)

  const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/reject`, {
    method: 'POST',
    body: {
      user_id: userId,
      feedback: body.feedback
    }
  })

  return result
})
