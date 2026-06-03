export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')

  const thread = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}`, {
    params: { user_id: getUserId(event) }
  })

  return thread
})
