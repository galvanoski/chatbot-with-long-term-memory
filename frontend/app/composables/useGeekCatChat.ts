import type { Thread, ThreadListItem, ThreadState, SendMessageResponse, UIMessage } from '#shared/types/thread'
import {
  threadActionResponseSchema,
  threadDetailSchema,
  threadListResponseSchema,
  threadStateSchema,
} from '../../shared/schemas/thread'

function parseValidatedResponse<T>(schema: { parse: (value: unknown) => T }, value: unknown, label: string): T {
  try {
    return schema.parse(value)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown validation error'
    throw new Error(`${label} validation failed: ${message}`)
  }
}

type StreamEventName = 'start' | 'delta' | 'done' | 'error'

async function consumeSSEStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: StreamEventName, payload: unknown) => void | Promise<void>
) {
  const decoder = new TextDecoder()
  const reader = body.getReader()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    while (buffer.includes('\n\n')) {
      const boundary = buffer.indexOf('\n\n')
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      if (!frame.trim()) continue

      let event: StreamEventName = 'delta'
      const dataLines: string[] = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) {
          const nextEvent = line.slice(6).trim() as StreamEventName
          event = nextEvent
          continue
        }
        if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim())
        }
      }

      const dataRaw = dataLines.join('\n')
      let payload: unknown = dataRaw
      try {
        payload = JSON.parse(dataRaw)
      } catch {
        payload = dataRaw
      }

      await onEvent(event, payload)
    }
  }
}

export function transformMessages(msgs: Thread['messages']): UIMessage[] {
  return (msgs ?? []).filter(Boolean).map(m => {
    const normalizedContent = m.role === 'assistant' ? toPlainAssistantText(m.content || '') : (m.content || '')
    return {
      ...m,
      content: normalizedContent,
      name: m.role === 'assistant' ? 'The Geek Cat' : 'Du',
      parts: [{ type: 'text' as const, text: normalizedContent }]
    }
  })
}

function toPlainAssistantText(text: string): string {
  const raw = (text || '').trim()
  if (!raw) return ''

  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return text

    const payload = parsed as Record<string, unknown>
    const hook = String(payload.hook ?? '').trim()
    const body = String(payload.body ?? '').trim()
    const cta = String(payload.cta ?? '').trim()
    const hashtagsRaw = Array.isArray(payload.hashtags) ? payload.hashtags : []
    const hashtags = hashtagsRaw
      .map(tag => String(tag || '').trim())
      .filter(Boolean)
      .map(tag => tag.startsWith('#') ? tag : `#${tag}`)
      .join(' ')

    const blocks = [hook, body, cta, hashtags].filter(Boolean)
    return blocks.length ? blocks.join('\n\n') : text
  } catch {
    return text
  }
}

function looksLikeJsonText(text: string): boolean {
  const candidate = (text || '').trim()
  if (!candidate) return false
  if (!candidate.startsWith('{') && !candidate.startsWith('[')) return false
  try {
    JSON.parse(candidate)
    return true
  } catch {
    return false
  }
}

function preserveStreamedAssistantText(serverMessages: UIMessage[], streamedText: string): UIMessage[] {
  const normalizedStreamed = streamedText.trim()
  if (!normalizedStreamed) return serverMessages

  const lastAssistantIndex = [...serverMessages].map(m => m.role).lastIndexOf('assistant')
  if (lastAssistantIndex < 0) {
    return [
      ...serverMessages,
      {
        id: `stream-final-${Date.now()}`,
        role: 'assistant',
        name: 'The Geek Cat',
        content: streamedText,
        created_at: new Date().toISOString(),
        parts: [{ type: 'text' as const, text: streamedText }]
      }
    ]
  }

  const lastAssistant = serverMessages[lastAssistantIndex]!
  const serverText = (lastAssistant.parts?.[0]?.text ?? lastAssistant.content ?? '').trim()
  if (serverText) {
    // Prefer the final normalized backend response over any streamed intermediate buffer.
    return serverMessages
  }
  if (looksLikeJsonText(normalizedStreamed) && !looksLikeJsonText(serverText)) {
    // Keep backend-normalized plain text instead of raw streamed JSON.
    return serverMessages
  }
  if (serverText === normalizedStreamed) return serverMessages

  const updatedAssistant = {
    ...lastAssistant,
    content: streamedText,
    parts: [{ type: 'text' as const, text: streamedText }]
  }

  return [
    ...serverMessages.slice(0, lastAssistantIndex),
    updatedAssistant,
    ...serverMessages.slice(lastAssistantIndex + 1)
  ]
}

export function useGeekCatChat() {
  const threads = useState<ThreadListItem[]>('geekcat-threads', () => [])
  const currentThread = useState<Thread | null>('geekcat-current-thread', () => null)
  const messages = useState<UIMessage[]>('geekcat-messages', () => [])
  const loading = useState<boolean>('geekcat-loading', () => false)
  const sending = useState<boolean>('geekcat-sending', () => false)
  const error = useState<string | null>('geekcat-error', () => null)
  const polling = useState<boolean>('geekcat-polling', () => false)
  const animationController = shallowRef<AbortController | null>(null)

  const isAwaitingApproval = computed(() => currentThread.value?.status === 'awaiting_approval')
  const pendingCopy = computed(() => currentThread.value?.pending_copy)

  const messageFeedback = useState<Record<string, 'up' | 'down'>>('geekcat-message-feedback', () => ({}))
  const ragTraces = useState<Record<string, unknown[]>>('geekcat-rag-traces', () => ({}))

  function setMessageFeedback(id: string, rating: 'up' | 'down' | null) {
    if (rating === null) {
      delete messageFeedback.value[id]
    } else {
      messageFeedback.value[id] = rating
    }
  }

  function storeRagTrace(trace: unknown[] | undefined) {
    if (!trace || !currentThread.value) return
    const msgs = currentThread.value.messages
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') {
        ragTraces.value[msgs[i].id] = trace
        return
      }
    }
  }

  function upsertThreadListItem(thread: Thread) {
    const index = threads.value.findIndex(t => t.id === thread.id)
    const next: ThreadListItem = {
      id: thread.id,
      title: thread.title,
      created_at: thread.created_at,
      updated_at: thread.updated_at,
      status: thread.status,
      message_count: thread.messages.length
    }

    if (index >= 0) {
      threads.value[index] = next
    } else {
      threads.value.push(next)
    }

    threads.value.sort((a, b) => {
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })
  }

  function setFromThread(t: Thread) {
    currentThread.value = t
    messages.value = transformMessages(t.messages)
    upsertThreadListItem(t)
  }

  function cancelMessageAnimation() {
    animationController.value?.abort()
    animationController.value = null
  }

  async function fetchThreads(query = '') {
    try {
      const q = query.trim()
      threads.value = parseValidatedResponse(
        threadListResponseSchema,
        await $fetch('/api/threads', { params: q ? { q } : undefined }),
        'Thread list'
      )
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to load threads'
    }
  }

  async function deleteThread(threadId: string): Promise<boolean> {
    try {
      await $fetch(`/api/threads/${threadId}`, { method: 'DELETE' })
      threads.value = threads.value.filter(thread => thread.id !== threadId)
      if (currentThread.value?.id === threadId) {
        currentThread.value = null
        messages.value = []
      }
      return true
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to delete thread'
      return false
    }
  }

  async function createThread(): Promise<string | null> {
    loading.value = true
    error.value = null
    try {
      const thread = parseValidatedResponse(threadDetailSchema, await $fetch('/api/threads', { method: 'POST' }), 'Thread create')
      setFromThread(thread)
      return thread.id
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to create thread'
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadThread(id: string) {
    loading.value = true
    error.value = null
    try {
      setFromThread(parseValidatedResponse(threadDetailSchema, await $fetch(`/api/threads/${id}`), 'Thread detail'))
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to load thread'
    } finally {
      loading.value = false
    }
  }

  async function sendMessage(text: string): Promise<boolean> {
    if (!currentThread.value || sending.value) return false
    const previousThreadMessages = [...currentThread.value.messages]
    const previousUiMessages = [...messages.value]
    cancelMessageAnimation()
    const controller = new AbortController()
    animationController.value = controller

    const optimisticUserMessage = {
      id: `temp-user-${Date.now()}`,
      role: 'user' as const,
      content: text,
      created_at: new Date().toISOString()
    }
    currentThread.value.messages = [...currentThread.value.messages, optimisticUserMessage]
    messages.value = [
      ...previousUiMessages,
      {
        ...optimisticUserMessage,
        name: 'Du',
        parts: [{ type: 'text' as const, text: text }]
      }
    ]

    loading.value = true
    sending.value = true
    error.value = null
    try {
      const streamResponse = await fetch(`/api/threads/${currentThread.value.id}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      })

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error('Streaming endpoint unavailable')
      }

      const assistantStreamId = `temp-assistant-${Date.now()}`
      messages.value = [
        ...messages.value,
        {
          id: assistantStreamId,
          role: 'assistant',
          name: 'The Geek Cat',
          content: '',
          created_at: new Date().toISOString(),
          parts: [{ type: 'text' as const, text: '' }]
        }
      ]

      await consumeSSEStream(streamResponse.body, async (event, payload) => {
        if (controller.signal.aborted) {
          return
        }

        if (event === 'delta' && typeof payload === 'object' && payload && 'text' in payload) {
          const deltaText = String((payload as { text?: unknown }).text ?? '')
          if (!deltaText) {
            return
          }

          messages.value = messages.value.map((message) => {
            if (message.id !== assistantStreamId) return message
            const current = message.parts?.[0]?.text ?? message.content ?? ''
            const next = `${current}${deltaText}`
            return { ...message, content: next, parts: [{ type: 'text' as const, text: next }] }
          })
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const streamedAssistantText = messages.value.find(message => message.id === assistantStreamId)?.parts?.[0]?.text ?? ''
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'Send message response')
          currentThread.value!.messages = res.messages
          currentThread.value!.title = res.title ?? currentThread.value!.title
          currentThread.value!.status = res.status
          currentThread.value!.pending_copy = res.pending_copy
          currentThread.value!.updated_at = new Date().toISOString()
          upsertThreadListItem(currentThread.value!)
          storeRagTrace(res.rag_trace)

          const finalUiMessages = preserveStreamedAssistantText(transformMessages(res.messages), streamedAssistantText)
          messages.value = finalUiMessages
          return
        }

        if (event === 'image_url' && payload && typeof payload === 'object') {
          const payloadObj = payload as { url?: unknown; message_id?: unknown }
          const url = String(payloadObj.url ?? '')
          if (url) {
            messages.value = messages.value.map((message) => {
              if (message.id === assistantStreamId) {
                return { ...message, image_url: url }
              }
              return message
            })
          }
          return
        }

        if (event === 'error') {
          const detail = typeof payload === 'object' && payload && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'Streaming failed')
            : 'Streaming failed'
          throw new Error(detail)
        }
      })

      return true
    } catch (errorValue: unknown) {
      currentThread.value.messages = previousThreadMessages
      messages.value = previousUiMessages
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to send message'
      return false
    } finally {
      if (animationController.value === controller) {
        animationController.value = null
      }
      loading.value = false
      sending.value = false
    }
  }

  async function approveCopy(
    edited?: { hook: string; body: string; cta: string } | string,
    feedback?: string
  ): Promise<boolean> {
    if (!currentThread.value) return false
    loading.value = true
    error.value = null
    try {
      const res = parseValidatedResponse(threadActionResponseSchema, await $fetch(
        `/api/threads/${currentThread.value.id}/approve`,
        {
          method: 'POST',
          body: typeof edited === 'string'
            ? {
                edited_copy: edited,
                feedback
              }
            : edited
              ? {
                  edited_parts: edited,
                  edited_copy: [edited.hook, edited.body, edited.cta].filter(Boolean).join('\n\n'),
                  feedback
                }
              : feedback
                ? { feedback }
                : undefined
        }
      ), 'Approve response')
      currentThread.value.messages = res.messages
      currentThread.value.status = res.status as Thread['status']
      currentThread.value.pending_copy = undefined
      currentThread.value.updated_at = new Date().toISOString()
      messages.value = transformMessages(res.messages)
      upsertThreadListItem(currentThread.value)
      return true
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to approve'
      return false
    } finally {
      loading.value = false
    }
  }

  async function rejectCopy(feedback: string): Promise<boolean> {
    if (!currentThread.value) return false
    loading.value = true
    error.value = null
    try {
      const res = parseValidatedResponse(threadActionResponseSchema, await $fetch(
        `/api/threads/${currentThread.value.id}/reject`,
        {
          method: 'POST',
          body: { feedback }
        }
      ), 'Reject response')
      currentThread.value.messages = res.messages
      currentThread.value.status = res.status as Thread['status']
      currentThread.value.pending_copy = res.pending_copy
      if (res.title) {
        currentThread.value.title = res.title
      }
      currentThread.value.updated_at = new Date().toISOString()
      messages.value = transformMessages(res.messages)
      upsertThreadListItem(currentThread.value)
      return true
    } catch (errorValue: unknown) {
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to reject'
      return false
    } finally {
      loading.value = false
    }
  }

  async function generateImagePrompt(instruction: string): Promise<boolean> {
    if (!currentThread.value || loading.value) return false
    const previousUiMessages = [...messages.value]
    cancelMessageAnimation()
    const controller = new AbortController()
    animationController.value = controller

    const optimisticUserMessage = {
      id: `temp-user-${Date.now()}`,
      role: 'user' as const,
      content: instruction,
      created_at: new Date().toISOString()
    }
    messages.value = [
      ...previousUiMessages,
      { ...optimisticUserMessage, name: 'Du', parts: [{ type: 'text' as const, text: instruction }] }
    ]

    loading.value = true
    error.value = null
    try {
      const streamResponse = await fetch(`/api/threads/${currentThread.value.id}/image-prompt/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction })
      })

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error('Image prompt endpoint unavailable')
      }

      const assistantStreamId = `temp-image-${Date.now()}`
      messages.value = [
        ...messages.value,
        {
          id: assistantStreamId,
          role: 'assistant',
          name: 'The Geek Cat',
          content: '',
          created_at: new Date().toISOString(),
          parts: [{ type: 'text' as const, text: '' }]
        }
      ]

      await consumeSSEStream(streamResponse.body, async (event, payload) => {
        if (controller.signal.aborted) return

        if (event === 'delta' && typeof payload === 'object' && payload && 'text' in payload) {
          const deltaText = String((payload as { text?: unknown }).text ?? '')
          if (!deltaText) return

          messages.value = messages.value.map((message) => {
            if (message.id !== assistantStreamId) return message
            const current = message.parts?.[0]?.text ?? message.content ?? ''
            const next = `${current}${deltaText}`
            return { ...message, content: next, parts: [{ type: 'text' as const, text: next }] }
          })
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const streamedAssistantText = messages.value.find(m => m.id === assistantStreamId)?.parts?.[0]?.text ?? ''
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'Image prompt response')
          currentThread.value!.messages = res.messages
          currentThread.value!.title = res.title ?? currentThread.value!.title
          currentThread.value!.status = res.status
          currentThread.value!.updated_at = new Date().toISOString()
          upsertThreadListItem(currentThread.value!)
          storeRagTrace(res.rag_trace)

          const finalUiMessages = preserveStreamedAssistantText(transformMessages(res.messages), streamedAssistantText)
          messages.value = finalUiMessages
          return
        }

        if (event === 'error') {
          const detail = typeof payload === 'object' && payload && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'Image prompt generation failed')
            : 'Image prompt generation failed'
          throw new Error(detail)
        }
      })

      return true
    } catch (errorValue: unknown) {
      messages.value = previousUiMessages
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to generate image prompt'
      return false
    } finally {
      if (animationController.value === controller) {
        animationController.value = null
      }
      loading.value = false
    }
  }

  async function generateSEO(instruction: string): Promise<boolean> {
    if (!currentThread.value || loading.value) return false
    const previousUiMessages = [...messages.value]
    cancelMessageAnimation()
    const controller = new AbortController()
    animationController.value = controller

    const optimisticUserMessage = {
      id: `temp-user-${Date.now()}`,
      role: 'user' as const,
      content: instruction,
      created_at: new Date().toISOString()
    }
    messages.value = [
      ...previousUiMessages,
      { ...optimisticUserMessage, name: 'Du', parts: [{ type: 'text' as const, text: instruction }] }
    ]

    loading.value = true
    error.value = null
    try {
      const streamResponse = await fetch(`/api/threads/${currentThread.value.id}/seo/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction })
      })

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error('SEO endpoint unavailable')
      }

      const assistantStreamId = `temp-seo-${Date.now()}`
      messages.value = [
        ...messages.value,
        {
          id: assistantStreamId,
          role: 'assistant',
          name: 'The Geek Cat',
          content: '',
          created_at: new Date().toISOString(),
          parts: [{ type: 'text' as const, text: '' }]
        }
      ]

      await consumeSSEStream(streamResponse.body, async (event, payload) => {
        if (controller.signal.aborted) return

        if (event === 'delta' && typeof payload === 'object' && payload && 'text' in payload) {
          const deltaText = String((payload as { text?: unknown }).text ?? '')
          if (!deltaText) return
          messages.value = messages.value.map((message) => {
            if (message.id !== assistantStreamId) return message
            const current = message.parts?.[0]?.text ?? message.content ?? ''
            const next = `${current}${deltaText}`
            return { ...message, content: next, parts: [{ type: 'text' as const, text: next }] }
          })
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const streamedAssistantText = messages.value.find(m => m.id === assistantStreamId)?.parts?.[0]?.text ?? ''
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'SEO response')
          currentThread.value!.messages = res.messages
          currentThread.value!.title = res.title ?? currentThread.value!.title
          currentThread.value!.status = res.status
          currentThread.value!.updated_at = new Date().toISOString()
          upsertThreadListItem(currentThread.value!)
          storeRagTrace(res.rag_trace)
          const finalUiMessages = preserveStreamedAssistantText(transformMessages(res.messages), streamedAssistantText)
          messages.value = finalUiMessages
          return
        }

        if (event === 'error') {
          const detail = typeof payload === 'object' && payload && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'SEO generation failed')
            : 'SEO generation failed'
          throw new Error(detail)
        }
      })

      return true
    } catch (errorValue: unknown) {
      messages.value = previousUiMessages
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to generate SEO'
      return false
    } finally {
      if (animationController.value === controller) {
        animationController.value = null
      }
      loading.value = false
    }
  }

  async function generateImage(prompt: string, sourceMessageId?: string): Promise<boolean> {
    if (!currentThread.value || loading.value) return false
    const previousUiMessages = [...messages.value]
    cancelMessageAnimation()
    const controller = new AbortController()
    animationController.value = controller

    const optimisticUserMessage = {
      id: `temp-user-${Date.now()}`,
      role: 'user' as const,
      content: prompt || 'Generate image',
      created_at: new Date().toISOString()
    }
    messages.value = [
      ...previousUiMessages,
      { ...optimisticUserMessage, name: 'Du', parts: [{ type: 'text' as const, text: prompt || 'Generate image' }] }
    ]

    loading.value = true
    error.value = null
    try {
      const streamResponse = await fetch(`/api/threads/${currentThread.value.id}/image/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, source_message_id: sourceMessageId })
      })

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error('Image endpoint unavailable')
      }

      const assistantStreamId = `temp-image-${Date.now()}`
      messages.value = [
        ...messages.value,
        {
          id: assistantStreamId,
          role: 'assistant',
          name: 'The Geek Cat',
          content: '',
          created_at: new Date().toISOString(),
          parts: [{ type: 'text' as const, text: '' }]
        }
      ]

      await consumeSSEStream(streamResponse.body, async (event, payload) => {
        if (controller.signal.aborted) return

        if (event === 'delta' && typeof payload === 'object' && payload && 'text' in payload) {
          const deltaText = String((payload as { text?: unknown }).text ?? '')
          if (!deltaText) return
          messages.value = messages.value.map((message) => {
            if (message.id !== assistantStreamId) return message
            const current = message.parts?.[0]?.text ?? message.content ?? ''
            const next = `${current}${deltaText}`
            return { ...message, content: next, parts: [{ type: 'text' as const, text: next }] }
          })
          return
        }

        if (event === 'image_url' && payload && typeof payload === 'object') {
          const payloadObj = payload as { url?: unknown; message_id?: unknown }
          const url = String(payloadObj.url ?? '')
          const messageId = String(payloadObj.message_id ?? '')
          if (url) {
            messages.value = messages.value.map((message) => {
              // Try streaming ID first, then persisted message_id
              if (message.id === assistantStreamId || (messageId && message.id === messageId)) {
                return { ...message, image_url: url }
              }
              return message
            })
          }
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const streamedAssistantText = messages.value.find(m => m.id === assistantStreamId)?.parts?.[0]?.text ?? ''
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'Image response')
          currentThread.value!.messages = res.messages
          currentThread.value!.title = res.title ?? currentThread.value!.title
          currentThread.value!.status = res.status
          currentThread.value!.updated_at = new Date().toISOString()
          upsertThreadListItem(currentThread.value!)
          storeRagTrace(res.rag_trace)
          const finalUiMessages = preserveStreamedAssistantText(transformMessages(res.messages), streamedAssistantText)
          messages.value = finalUiMessages
          return
        }

        if (event === 'error') {
          const detail = typeof payload === 'object' && payload && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'Image generation failed')
            : 'Image generation failed'
          throw new Error(detail)
        }
      })

      return true
    } catch (errorValue: unknown) {
      messages.value = previousUiMessages
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to generate image'
      return false
    } finally {
      if (animationController.value === controller) {
        animationController.value = null
      }
      loading.value = false
    }
  }

  async function regenerateCopy(instruction?: string): Promise<boolean> {
    if (!currentThread.value) return false
    const previousThreadMessages = [...currentThread.value.messages]
    const previousUiMessages = [...messages.value]
    cancelMessageAnimation()
    const controller = new AbortController()
    animationController.value = controller

    loading.value = true
    error.value = null
    try {
      const streamResponse = await fetch(`/api/threads/${currentThread.value.id}/regenerate/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction }),
        signal: controller.signal
      })

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error('Streaming endpoint unavailable')
      }

      const assistantStreamId = `temp-regen-${Date.now()}`
      messages.value = [
        ...messages.value,
        {
          id: assistantStreamId,
          role: 'assistant',
          name: 'The Geek Cat',
          content: '',
          created_at: new Date().toISOString(),
          parts: [{ type: 'text' as const, text: '' }]
        }
      ]

      await consumeSSEStream(streamResponse.body, async (event, payload) => {
        if (controller.signal.aborted) return

        if (event === 'delta' && typeof payload === 'object' && payload && 'text' in payload) {
          // Keep the inline loading placeholder visible until the final normalized response arrives.
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const streamedAssistantText = messages.value.find(message => message.id === assistantStreamId)?.parts?.[0]?.text ?? ''
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'Regenerate response')
          currentThread.value!.messages = res.messages
          currentThread.value!.status = res.status as Thread['status']
          currentThread.value!.pending_copy = res.pending_copy
          if (res.title) {
            currentThread.value!.title = res.title
          }
          currentThread.value!.updated_at = new Date().toISOString()
          storeRagTrace(res.rag_trace)
          messages.value = preserveStreamedAssistantText(transformMessages(res.messages), streamedAssistantText)
          upsertThreadListItem(currentThread.value!)
          return
        }

        if (event === 'error') {
          const detail = typeof payload === 'object' && payload && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'Streaming failed')
            : 'Streaming failed'
          throw new Error(detail)
        }
      })

      return true
    } catch (errorValue: unknown) {
      currentThread.value.messages = previousThreadMessages
      messages.value = previousUiMessages
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to regenerate'
      return false
    } finally {
      if (animationController.value === controller) {
        animationController.value = null
      }
      loading.value = false
    }
  }

  async function pollState(): Promise<ThreadState | null> {
    if (!currentThread.value) return null
    polling.value = true
    try {
      return parseValidatedResponse(threadStateSchema, await $fetch(
        `/api/threads/${currentThread.value.id}/state`
      ), 'Thread state')
    } catch {
      return null
    } finally {
      polling.value = false
    }
  }

  return {
    threads,
    currentThread,
    messages,
    loading,
    sending,
    error,
    polling,
    isAwaitingApproval,
    pendingCopy,
    messageFeedback,
    setMessageFeedback,
    ragTraces,
    fetchThreads,
    deleteThread,
    createThread,
    loadThread,
    sendMessage,
    approveCopy,
    rejectCopy,
    regenerateCopy,
    pollState,
    cancelMessageAnimation,
    generateImagePrompt,
    generateSEO,
    generateImage
  }
}
