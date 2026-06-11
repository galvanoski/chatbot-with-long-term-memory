import { assertNonEmptyString, assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const feedback = assertNonEmptyString(body?.feedback, 'feedback', 1200)
  const messageId = assertOptionalString(body?.message_id, 'message_id', 64)

  try {
    const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/reject`, {
      method: 'POST',
      body: {
        user_id: userId,
        feedback,
        message_id: messageId
      }
    })

    return result
  } catch {
    throw backendUnavailableError()
  }
})
