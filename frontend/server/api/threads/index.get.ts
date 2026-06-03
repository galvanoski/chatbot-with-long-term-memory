export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const userId = getUserId(event)

  const threads = await $fetch(`${apiBaseUrl}/api/chat/threads`, {
    params: { user_id: userId }
  })

  return threads
})
