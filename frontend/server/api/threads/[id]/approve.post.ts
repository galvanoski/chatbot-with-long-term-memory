export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')
  const userId = getUserId(event)

  const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/approve`, {
    method: 'POST',
    body: { user_id: userId }
  })

  return result
})
