const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function assertUuid(value: unknown, fieldName: string): string {
  const text = String(value || '').trim()
  if (!UUID_V4_RE.test(text)) {
    throw createError({ statusCode: 400, statusMessage: `${fieldName} must be a valid UUID` })
  }
  return text
}

export function assertNonEmptyString(
  value: unknown,
  fieldName: string,
  maxLength: number
): string {
  const text = String(value || '').trim()
  if (!text) {
    throw createError({ statusCode: 400, statusMessage: `${fieldName} is required` })
  }
  if (text.length > maxLength) {
    throw createError({ statusCode: 400, statusMessage: `${fieldName} exceeds max length of ${maxLength}` })
  }
  return text
}

export function assertOptionalString(
  value: unknown,
  fieldName: string,
  maxLength: number
): string | undefined {
  if (value === undefined || value === null) {
    return undefined
  }
  const text = String(value).trim()
  if (!text) {
    throw createError({ statusCode: 400, statusMessage: `${fieldName} cannot be empty` })
  }
  if (text.length > maxLength) {
    throw createError({ statusCode: 400, statusMessage: `${fieldName} exceeds max length of ${maxLength}` })
  }
  return text
}

export function backendUnavailableError() {
  return createError({
    statusCode: 502,
    statusMessage: 'Backend unavailable'
  })
}
