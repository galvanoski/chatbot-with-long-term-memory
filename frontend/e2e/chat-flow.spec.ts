import { expect, test } from '@playwright/test'

test('creates a chat and renders returned messages', async ({ page }) => {
  const threadId = 'thread-e2e-1'
  const prompt = 'Necesito copy para camiseta Bitcoin'
  const responseMessages = [
    {
      id: 'm1',
      role: 'user',
      content: prompt,
      created_at: '2026-06-03T12:00:00Z'
    },
    {
      id: 'm2',
      role: 'assistant',
      content: `Borrador para: ${prompt}`,
      created_at: '2026-06-03T12:00:01Z'
    }
  ]

  await page.route('**/api/threads', async (route) => {
    const method = route.request().method()

    if (method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([])
      })
      return
    }

    if (method === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: threadId,
          title: null,
          created_at: '2026-06-03T12:00:00Z',
          updated_at: '2026-06-03T12:00:00Z',
          status: 'active',
          messages: []
        })
      })
      return
    }

    await route.fallback()
  })

  await page.route(`**/api/threads/${threadId}/messages`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'awaiting_approval',
        messages: responseMessages,
        pending_copy: {
          content: `Borrador para: ${prompt}`,
          hashtags: ['#geekcat', '#bitcoin']
        }
      })
    })
  })

  await page.route(`**/api/threads/${threadId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: threadId,
        title: null,
        created_at: '2026-06-03T12:00:00Z',
        updated_at: '2026-06-03T12:00:01Z',
        status: 'active',
        messages: []
      })
    })
  })

  await page.goto('/')

  const homePrompt = page.getByPlaceholder('Sag mir, welchen Content du brauchst...')
  const homeSubmit = page.getByTestId('home-submit')
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-testid="home-submit"]') as { __vueParentComponent?: unknown } | null
    return Boolean(element?.__vueParentComponent)
  })
  await expect(homeSubmit).toBeVisible()
  await homePrompt.fill(prompt)
  await homeSubmit.click()

  await expect(page).toHaveURL(new RegExp(`/chat/${threadId}$`))
  await expect(page.getByTestId('chat-debug')).toContainText('"hasThread": true')
  await expect(page.getByTestId('chat-debug')).toContainText('"renderedMessageCount": 2')
  await expect(page.getByTestId('chat-messages').getByText(`Borrador para: ${prompt}`)).toBeVisible()
  await expect(page.getByTestId('approval-panel')).toBeVisible()
  await expect(page.getByTestId('pending-copy-content')).toContainText(`Borrador para: ${prompt}`)
  await expect(page.getByTestId('approval-panel').getByText('#bitcoin')).toBeVisible()
})