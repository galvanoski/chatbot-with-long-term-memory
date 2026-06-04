import { assertUuid, backendUnavailableError } from '../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)

  try {
    const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}`, {
      method: 'DELETE',
      params: { user_id: userId }
    })

    return result
  } catch {
    throw backendUnavailableError()
  }
})
