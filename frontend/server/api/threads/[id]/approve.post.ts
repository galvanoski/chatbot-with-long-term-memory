import { assertOptionalString, assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)
  const body = await readBody(event)
  const editedCopy = assertOptionalString(body?.edited_copy, 'edited_copy', 12000)
  const feedback = assertOptionalString(body?.feedback, 'feedback', 1200)
  const editedParts = body?.edited_parts && typeof body.edited_parts === 'object'
    ? body.edited_parts
    : undefined

  if (body?.edited_parts !== undefined && !editedParts) {
    throw createError({ statusCode: 400, statusMessage: 'edited_parts must be an object' })
  }

  try {
    const result = await $fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/approve`, {
      method: 'POST',
      body: {
        user_id: userId,
        edited_copy: editedCopy,
        edited_parts: editedParts,
        feedback
      }
    })

    return result
  } catch {
    throw backendUnavailableError()
  }
})
