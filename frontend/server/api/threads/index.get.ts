import { assertOptionalString, backendUnavailableError } from '../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const userId = getUserId(event)
  const q = assertOptionalString(getQuery(event).q, 'q', 120)

  try {
    const threads = await $fetch(`${apiBaseUrl}/api/chat/threads`, {
      params: q ? { user_id: userId, q } : { user_id: userId }
    })

    return threads
  } catch {
    throw backendUnavailableError()
  }
})
