import type { Thread, ThreadListItem, ThreadState, SendMessageResponse } from '#shared/types/thread'

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function streamAssistantText(
  finalMessages: any[],
  setMessages: (msgs: any[]) => void
) {
  const assistantIndex = [...finalMessages]
    .map((m, idx) => ({ role: m.role, idx }))
    .reverse()
    .find(x => x.role === 'assistant')?.idx

  if (assistantIndex === undefined) {
    setMessages(finalMessages)
    return
  }

  const assistant = finalMessages[assistantIndex]
  const finalText = assistant?.parts?.[0]?.text ?? ''

  if (!finalText) {
    setMessages(finalMessages)
    return
  }

  const working = finalMessages.map(m => ({ ...m }))
  working[assistantIndex] = {
    ...assistant,
    parts: [{ type: 'text' as const, text: '' }]
  }
  setMessages([...working])

  const chunks = Math.min(120, Math.max(20, Math.ceil(finalText.length / 10)))
  const step = Math.max(1, Math.ceil(finalText.length / chunks))

  for (let cursor = step; cursor <= finalText.length; cursor += step) {
    working[assistantIndex] = {
      ...assistant,
      parts: [{ type: 'text' as const, text: finalText.slice(0, cursor) }]
    }
    setMessages([...working])
    await sleep(18)
  }

  setMessages(finalMessages)
}

export function transformMessages(msgs: Thread['messages']): any[] {
  return (msgs ?? []).filter(Boolean).map(m => ({
    ...m,
    name: m.role === 'assistant' ? 'The Geek Cat' : 'Du',
    parts: [{ type: 'text' as const, text: m.content || '' }]
  }))
}

export function useGeekCatChat() {
  const threads = useState<ThreadListItem[]>('geekcat-threads', () => [])
  const currentThread = useState<Thread | null>('geekcat-current-thread', () => null)
  const messages = useState<any[]>('geekcat-messages', () => [])
  const loading = useState<boolean>('geekcat-loading', () => false)
  const error = useState<string | null>('geekcat-error', () => null)
  const polling = useState<boolean>('geekcat-polling', () => false)

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

  async function fetchThreads() {
    try {
      threads.value = await $fetch<ThreadListItem[]>('/api/threads')
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to load threads'
    }
  }

  async function createThread(): Promise<string | null> {
    loading.value = true
    error.value = null
    try {
      const thread = await $fetch<Thread>('/api/threads', { method: 'POST' })
      setFromThread(thread)
      return thread.id
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to create thread'
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadThread(id: string) {
    loading.value = true
    error.value = null
    try {
      setFromThread(await $fetch<Thread>(`/api/threads/${id}`))
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to load thread'
    } finally {
      loading.value = false
    }
  }

  async function sendMessage(text: string): Promise<boolean> {
    if (!currentThread.value) return false
    const previousThreadMessages = [...currentThread.value.messages]
    const previousUiMessages = [...messages.value]

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
    error.value = null
    try {
      const res = await $fetch<SendMessageResponse>(
        `/api/threads/${currentThread.value.id}/messages`,
        {
          method: 'POST',
          body: { message: text }
        }
      )
      currentThread.value.messages = res.messages
      if (res.title) {
        currentThread.value.title = res.title
      }
      currentThread.value.status = res.status
      currentThread.value.pending_copy = res.pending_copy
      currentThread.value.updated_at = new Date().toISOString()

      upsertThreadListItem(currentThread.value)
      const finalUiMessages = transformMessages(res.messages)
      await streamAssistantText(finalUiMessages, (next) => {
        messages.value = next
      })
      return true
    } catch (e: any) {
      currentThread.value.messages = previousThreadMessages
      messages.value = previousUiMessages
      error.value = e?.message ?? 'Failed to send message'
      return false
    } finally {
      loading.value = false
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
      const res = await $fetch<{ status: string; messages: Thread['messages'] }>(
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
      )
      currentThread.value.messages = res.messages
      currentThread.value.status = res.status as Thread['status']
      currentThread.value.pending_copy = undefined
      currentThread.value.updated_at = new Date().toISOString()
      messages.value = transformMessages(res.messages)
      upsertThreadListItem(currentThread.value)
      return true
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to approve'
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
      const res = await $fetch<{ status: string; messages: Thread['messages'] }>(
        `/api/threads/${currentThread.value.id}/reject`,
        {
          method: 'POST',
          body: { feedback }
        }
      )
      currentThread.value.messages = res.messages
      currentThread.value.status = res.status as Thread['status']
      currentThread.value.pending_copy = undefined
      currentThread.value.updated_at = new Date().toISOString()
      messages.value = transformMessages(res.messages)
      upsertThreadListItem(currentThread.value)
      return true
    } catch (e: any) {
      error.value = e?.message ?? 'Failed to reject'
      return false
    } finally {
      loading.value = false
    }
  }

  async function pollState(): Promise<ThreadState | null> {
    if (!currentThread.value) return null
    polling.value = true
    try {
      return await $fetch<ThreadState>(
        `/api/threads/${currentThread.value.id}/state`
      )
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
    error,
    polling,
    isAwaitingApproval,
    pendingCopy,
    fetchThreads,
    createThread,
    loadThread,
    sendMessage,
    approveCopy,
    rejectCopy,
    pollState
  }
}
