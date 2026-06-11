import { assertUuid, backendUnavailableError } from '../../../utils/requestValidation'

export default defineEventHandler(async (event) => {
  const { apiBaseUrl } = useRuntimeConfig().public
  const threadId = assertUuid(getRouterParam(event, 'id'), 'threadId')
  const userId = getUserId(event)

  const formData = await readFormData(event)
  formData.append('user_id', userId)

  const upstream = await fetch(`${apiBaseUrl}/api/chat/threads/${threadId}/upload-image?user_id=${userId}`, {
    method: 'POST',
    body: formData
  }).catch(() => null)

  if (!upstream || !upstream.ok) {
    throw backendUnavailableError()
  }

  return await upstream.json()
})
