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

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
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

async function streamAssistantText(
  finalMessages: UIMessage[],
  setMessages: (msgs: UIMessage[]) => void,
  signal?: AbortSignal
) {
  if (signal?.aborted) return

  const assistantIndex = [...finalMessages]
    .map((m, idx) => ({ role: m.role, idx }))
    .reverse()
    .find(x => x.role === 'assistant')?.idx

  if (assistantIndex === undefined) {
    setMessages(finalMessages)
    return
  }

  const assistant = finalMessages[assistantIndex]
  if (!assistant) {
    setMessages(finalMessages)
    return
  }
  const finalText = assistant?.parts?.[0]?.text ?? ''

  if (!finalText) {
    setMessages(finalMessages)
    return
  }

  const working: UIMessage[] = finalMessages.map(message => ({
    ...message,
    parts: [...message.parts]
  }))
  working[assistantIndex] = {
    ...assistant,
    parts: [{ type: 'text' as const, text: '' }]
  }
  setMessages([...working])

  const chunks = Math.min(120, Math.max(20, Math.ceil(finalText.length / 10)))
  const step = Math.max(1, Math.ceil(finalText.length / chunks))

  for (let cursor = step; cursor <= finalText.length; cursor += step) {
    if (signal?.aborted) return
    working[assistantIndex] = {
      ...assistant,
      parts: [{ type: 'text' as const, text: finalText.slice(0, cursor) }]
    }
    setMessages([...working])
    await sleep(18)
  }

  if (signal?.aborted) return
  setMessages(finalMessages)
}

export function transformMessages(msgs: Thread['messages']): UIMessage[] {
  return (msgs ?? []).filter(Boolean).map(m => ({
    ...m,
    name: m.role === 'assistant' ? 'The Geek Cat' : 'Du',
    parts: [{ type: 'text' as const, text: m.content || '' }]
  }))
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
          const delta = String((payload as { text?: unknown }).text ?? '')
          messages.value = messages.value.map(message => {
            if (message.id !== assistantStreamId) return message
            const currentText = message.parts?.[0]?.text ?? ''
            const nextText = `${currentText}${delta}`
            return {
              ...message,
              content: nextText,
              parts: [{ type: 'text' as const, text: nextText }]
            }
          })
          return
        }

        if (event === 'done' && payload && typeof payload === 'object') {
          const res = parseValidatedResponse(threadActionResponseSchema, payload, 'Send message response')
          currentThread.value!.messages = res.messages
          currentThread.value!.title = res.title ?? currentThread.value!.title
          currentThread.value!.status = res.status
          currentThread.value!.pending_copy = res.pending_copy
          currentThread.value!.updated_at = new Date().toISOString()
          upsertThreadListItem(currentThread.value!)

          const finalUiMessages = transformMessages(res.messages)
          messages.value = finalUiMessages
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
    edited?: { hook: string; body: string; cta: string },
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
          body: edited
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

  async function regenerateCopy(instruction?: string): Promise<boolean> {
    if (!currentThread.value) return false
    loading.value = true
    error.value = null
    try {
      const res = parseValidatedResponse(threadActionResponseSchema, await $fetch(
        `/api/threads/${currentThread.value.id}/regenerate`,
        {
          method: 'POST',
          body: instruction ? { instruction } : undefined
        }
      ), 'Regenerate response')

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
      error.value = errorValue instanceof Error ? errorValue.message : 'Failed to regenerate'
      return false
    } finally {
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
    fetchThreads,
    deleteThread,
    createThread,
    loadThread,
    sendMessage,
    approveCopy,
    rejectCopy,
    regenerateCopy,
    pollState,
    cancelMessageAnimation
  }
}
