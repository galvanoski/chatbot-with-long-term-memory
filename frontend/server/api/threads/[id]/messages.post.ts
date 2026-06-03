export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')
  const userId = getUserId(event)
  const body = await readBody(event)

  const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    body: {
      user_id: userId,
      content: body.message
    }
  })

  return result
})
