import { assertOptionalString, assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const instruction = assertOptionalString(body?.instruction, 'instruction', 10000)

  try {
    const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/regenerate`, {
      method: 'POST',
      body: {
        user_id: userId,
        instruction
      }
    })

    return result
  } catch {
    throw backendUnavailableError()
  }
})
