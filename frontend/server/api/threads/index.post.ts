export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const userId = getUserId(event)

  const thread = await $fetch(`${apiBaseUrl}/api/chat/threads`, {
    method: 'POST',
    body: { user_id: userId }
  })

  return thread
})
