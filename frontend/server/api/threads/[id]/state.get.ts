import { assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')

  try {
    const state = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/state`, {
      params: { user_id: getUserId(event) }
    })

    return state
  } catch {
    throw backendUnavailableError()
  }
})
