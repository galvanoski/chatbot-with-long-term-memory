/**
 * Get or create a stable user ID for the current request.
 */
export function getUserId(event: any): string {
  const session = getHeader(event, 'x-user-id')
  if (session) return session

  const cookie = getCookie(event, 'geekcat_user_id')
  if (cookie) return cookie

  const id = `anon_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
  setCookie(event, 'geekcat_user_id', id, {
    maxAge: 365 * 24 * 60 * 60,
    httpOnly: true,
    sameSite: 'lax',
    path: '/'
  })
  return id
}
