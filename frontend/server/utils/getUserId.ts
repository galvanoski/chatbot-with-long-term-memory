/**
 * Get or create a stable user ID for the current request.
 */
export function getUserId(event: any): string {
  const anonIdPattern = /^anon_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  const cookie = getCookie(event, 'geekcat_user_id')
  if (cookie && anonIdPattern.test(cookie)) {
    return cookie
  }

  const id = `anon_${crypto.randomUUID()}`
  setCookie(event, 'geekcat_user_id', id, {
    maxAge: 365 * 24 * 60 * 60,
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/'
  })
  return id
}
