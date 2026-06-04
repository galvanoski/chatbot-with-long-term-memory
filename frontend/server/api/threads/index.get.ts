import { backendUnavailableError } from '../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const userId = getUserId(event)

  try {
    const threads = await $fetch(`${apiBaseUrl}/api/chat/threads`, {
      params: { user_id: userId }
    })

    return threads
  } catch {
    throw backendUnavailableError()
  }
})
