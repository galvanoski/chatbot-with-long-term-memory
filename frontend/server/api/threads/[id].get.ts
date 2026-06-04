import { assertUuid, backendUnavailableError } from '../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')

  try {
    const thread = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}`, {
      params: { user_id: getUserId(event) }
    })

    return thread
  } catch {
    throw backendUnavailableError()
  }
})
