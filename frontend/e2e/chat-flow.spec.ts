import { expect, test } from '@playwright/test'

test('creates a chat and renders returned messages', async ({ page }) => {
  const threadId = '11111111-1111-4111-8111-111111111111'
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

  const homePrompt = page.getByRole('textbox').first()
  const homeSubmit = page.getByTestId('home-submit')
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-testid="home-submit"]') as { __vueParentComponent?: unknown } | null
    return Boolean(element?.__vueParentComponent)
  })
  await expect(homeSubmit).toBeVisible()
  await homePrompt.fill(prompt)
  await homeSubmit.click()

  await expect(page).toHaveURL(new RegExp(`/chat/${threadId}$`))
  await expect(page.getByTestId('chat-messages').locator('.chat-user-bubble').first()).toContainText(prompt)
  await expect(page.getByTestId('approval-panel')).toBeVisible()
  await expect(page.getByTestId('pending-copy-content')).toContainText(`Borrador para: ${prompt}`)
  await expect(page.getByTestId('approval-panel').getByText('#bitcoin')).toBeVisible()
  await expect(page.getByTestId('thumbs-up')).toBeVisible()
  await expect(page.getByTestId('thumbs-down')).toBeVisible()
})

test('disables the composer while a thread is awaiting approval', async ({ page }) => {
  const threadId = '22222222-2222-4222-8222-222222222222'

  await page.route('**/api/threads', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([])
    })
  })

  await page.route(`**/api/threads/${threadId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: threadId,
        title: 'Awaiting approval thread',
        created_at: '2026-06-03T12:00:00Z',
        updated_at: '2026-06-03T12:00:01Z',
        status: 'awaiting_approval',
        messages: [
          {
            id: 'm1',
            role: 'user',
            content: 'Bitte eine neue Kampagne',
            created_at: '2026-06-03T12:00:00Z'
          },
          {
            id: 'm2',
            role: 'assistant',
            content: 'Borrador para: Bitte eine neue Kampagne',
            created_at: '2026-06-03T12:00:01Z'
          }
        ],
        pending_copy: {
          content: 'Borrador para: Bitte eine neue Kampagne',
          hashtags: ['#geekcat'],
          parts: {
            hook: 'Hook',
            body: 'Body',
            cta: 'CTA'
          }
        }
      })
    })
  })

  await page.goto(`/chat/${threadId}`)

  await expect(page.getByTestId('approval-panel')).toBeVisible()
  await expect(page.getByTestId('chat-prompt')).toBeDisabled()
  await expect(page.getByTestId('chat-submit')).toBeDisabled()
})

test('shows a visible error and retry action when thread loading fails', async ({ page }) => {
  const threadId = '33333333-3333-4333-8333-333333333333'

  await page.route('**/api/threads', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([])
    })
  })

  await page.route(`**/api/threads/${threadId}`, async (route) => {
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Backend unavailable' })
    })
  })

  await page.goto(`/chat/${threadId}`)

  await expect(page.getByTestId('chat-empty-state')).toBeVisible()
  await expect(page.getByRole('button', { name: /retry/i })).toBeVisible()
})