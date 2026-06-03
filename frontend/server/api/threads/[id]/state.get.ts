export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = getRouterParam(event, 'id')

  const state = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/state`, {
    params: { user_id: getUserId(event) }
  })

  return state
})
