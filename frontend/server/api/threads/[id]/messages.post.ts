import { assertNonEmptyString, assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const message = assertNonEmptyString(body?.message, 'message', 4000)

  try {
    const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/messages`, {
      method: 'POST',
      body: {
        user_id: userId,
        content: message
      }
    })

    return result
  } catch {
    throw backendUnavailableError()
  }
})
