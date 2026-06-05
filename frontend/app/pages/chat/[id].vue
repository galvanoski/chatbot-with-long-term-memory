<script setup lang="ts">
const route = useRoute()
const chatId = route.params.id as string
const initialPrompt = computed(() => typeof route.query.prompt === 'string' ? route.query.prompt : '')
const debugEnabled = computed(() => route.query.debug === '1')

const initialPromptSent = ref(false)
const regenerateProcessing = ref(false)

const chat = useGeekCatChat()

await chat.loadThread(chatId)

async function send() {
  if (!input.value.trim() || unref(chat.loading) || unref(chat.sending)) return
  const text = input.value
  input.value = ''
  return chat.sendMessage(text)
}

async function handleSendClick() {
  await send()
}

async function retryLoadThread() {
  await chat.loadThread(chatId)
}

onMounted(async () => {
  if (!initialPrompt.value || initialPromptSent.value) return
  if (currentThread.value?.messages?.length) return

  initialPromptSent.value = true
  input.value = initialPrompt.value
  await send()
  await navigateTo(`/chat/${chatId}`, { replace: true })
})

async function handleApprove(message: string) {
  await chat.approveCopy(message || assistantResponsePreview.value, 'thumbs_up')
}

async function handleReject(message: string) {
  const note = (message || assistantResponsePreview.value || 'thumbs_down').slice(0, 500)
  await chat.rejectCopy(note)
}

async function handleRegenerate(message: string) {
  regenerateProcessing.value = true
  try {
    await chat.regenerateCopy(message || assistantResponsePreview.value || undefined)
  } finally {
    regenerateProcessing.value = false
  }
}

async function copyMessageToClipboard(text: string) {
  if (!text) return
  await navigator.clipboard.writeText(text)
}

async function handleThumbsUp(msg: any) {
  if (!currentThread.value) return
  try {
    await $fetch(`/api/threads/${currentThread.value.id}/approve`, {
      method: 'POST',
      body: { feedback: 'thumbs_up' }
    })
  } catch {}
}

async function handleThumbsDown(msg: any) {
  if (!currentThread.value) return
  try {
    await $fetch(`/api/threads/${currentThread.value.id}/reject`, {
      method: 'POST',
      body: { feedback: 'thumbs_down' }
    })
  } catch {}
}

const input = ref('')
const isLoading = computed(() => unref(chat.loading))
const isSending = computed(() => unref(chat.sending))
const isAwaiting = computed(() => unref(chat.isAwaitingApproval))
const pending = computed(() => unref(chat.pendingCopy))
const currentThread = computed(() => unref(chat.currentThread))
const threadMessages = computed(() => unref(chat.messages))
const chatError = computed(() => unref(chat.error))
const showDebugPanel = computed(() => import.meta.dev && debugEnabled.value)
const threadTitle = computed(() => currentThread.value?.title || 'Neuer Chat')

function messageText(message: { parts?: Array<{ text?: string }>; content?: string }) {
  return message?.parts?.[0]?.text || message?.content || ''
}

function hasMessageText(message: { parts?: Array<{ text?: string }>; content?: string }) {
  return messageText(message).trim().length > 0
}

function isTemporaryMessage(message: { id?: string }) {
  return typeof message?.id === 'string' && message.id.startsWith('temp-')
}

function isStreamingPlaceholder(message: { id?: string; role?: string; parts?: Array<{ text?: string }>; content?: string }) {
  return message?.role === 'assistant' && isTemporaryMessage(message) && !hasMessageText(message)
}

const loadingStatusText = computed(() => {
  if (regenerateProcessing.value) return 'The agent is revising the response and preparing a better version.'
  if (isSending.value) return 'The agent is reviewing your request and drafting a response.'
  return 'The agent is working on the next response.'
})

const displayedMessages = computed(() => {
  return threadMessages.value.filter((message) => {
    if (message?.role !== 'assistant') return true
    // Keep the active streaming placeholder inline where the next assistant response will appear.
    return hasMessageText(message) || isStreamingPlaceholder(message)
  }).map((message, index) => ({
    ...message,
    _renderKey: `${message?.id || 'msg'}-${message?.role || 'unknown'}-${index}`
  }))
})

const latestAssistantIndex = computed(() => {
  for (let index = displayedMessages.value.length - 1; index >= 0; index--) {
    if (displayedMessages.value[index]?.role === 'assistant') return index
  }
  return -1
})

const latestAssistantMessage = computed(() => {
  for (let index = displayedMessages.value.length - 1; index >= 0; index--) {
    const message = displayedMessages.value[index]
    if (message?.role === 'assistant') return message
  }
  return null
})

const assistantResponsePreview = computed(() => {
  if (!latestAssistantMessage.value) return ''
  return messageText(latestAssistantMessage.value)
})

async function copyDraftToClipboard(text: string) {
  const normalized = (text || '').trim()
  if (!normalized) return
  await navigator.clipboard.writeText(normalized)
}

watch(assistantResponsePreview, () => {
  // Keep helper state lightweight; per-message actions should not rewrite prior responses.
})

const scrollAreaRef = ref<HTMLElement | null>(null)
const scrollBottomRef = ref<HTMLElement | null>(null)

function scrollToBottom() {
  const sentinel = scrollBottomRef.value
  if (sentinel) {
    sentinel.scrollIntoView({ block: 'end' })
    return
  }
  const el = scrollAreaRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => unref(chat.messages).map(m => m.parts?.[0]?.text ?? m.content ?? '').join(''),
  () => nextTick(scrollToBottom)
)

onMounted(() => nextTick(scrollToBottom))

const debugState = computed(() => JSON.stringify({
  chatId,
  loading: isLoading.value,
  error: chatError.value,
  hasThread: Boolean(currentThread.value),
  initialPromptQueued: Boolean(initialPrompt.value),
  threadStatus: currentThread.value?.status ?? null,
  storedMessageCount: currentThread.value?.messages?.length ?? 0,
  renderedMessageCount: threadMessages.value.length,
  awaitingApproval: isAwaiting.value
}, null, 2))
</script>

<template>
  <div class="chat-shell">
    <div
      v-if="showDebugPanel"
      data-testid="chat-debug"
      class="border-b border-default bg-muted/30 px-4 py-3"
    >
      <div class="mx-auto max-w-2xl">
        <p class="text-xs font-semibold uppercase tracking-wide text-muted">
          Chat Debug
        </p>
        <pre class="mt-2 overflow-x-auto text-xs text-toned">{{ debugState }}</pre>
      </div>
    </div>

    <template v-if="currentThread">
      <div v-if="chatError" class="border-b border-default bg-warning/10 px-4 py-3">
        <div class="mx-auto flex max-w-3xl items-center justify-between gap-3">
          <UAlert
            color="warning"
            variant="soft"
            icon="i-lucide-triangle-alert"
            :title="chatError"
            class="flex-1"
          />
          <UButton size="sm" color="neutral" variant="outline" @click="retryLoadThread">
            Retry
          </UButton>
        </div>
      </div>

      <div class="chat-topbar">
        <div class="chat-topbar-inner">
          <h1 class="chat-topbar-title">ChatGPT</h1>
        </div>
      </div>

      <div ref="scrollAreaRef" class="chat-scroll-area">
        <div
          v-if="!isLoading && !threadMessages.length"
          class="mx-auto w-full max-w-3xl rounded-lg border border-default bg-muted/25 px-4 py-3"
        >
          <p class="text-sm font-semibold">Los geht's</p>
          <p class="text-xs text-muted mt-1">Schreibe unten deinen Prompt oder starte mit einem Chip von der Startseite.</p>
        </div>

        <div :key="chatId" data-testid="chat-messages" class="mx-auto w-full max-w-3xl space-y-6 pb-8">
          <div
            v-for="(msg, idx) in displayedMessages"
            :key="msg._renderKey"
            class="chat-row"
            :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <div v-if="msg.role === 'user'" class="chat-user-bubble">
              {{ messageText(msg) }}
            </div>

            <div v-else class="chat-assistant-block">
              <!-- No text yet: show only the loading indicator -->
              <template v-if="isStreamingPlaceholder(msg)">
                <div data-testid="chat-processing" class="chat-response-pending">
                  <div class="chat-thinking-indicator">
                    <span class="dot" /><span class="dot" /><span class="dot" />
                  </div>
                  <p class="chat-processing-text">{{ loadingStatusText }}</p>
                </div>
              </template>

              <!-- Text is arriving: show loading indicator above the partial text until done -->
              <template v-else-if="isSending && idx === latestAssistantIndex">
                <div data-testid="chat-processing" class="chat-response-pending mb-2">
                  <div class="chat-thinking-indicator">
                    <span class="dot" /><span class="dot" /><span class="dot" />
                  </div>
                  <p class="chat-processing-text">{{ loadingStatusText }}</p>
                </div>
                <p class="chat-assistant-text">{{ messageText(msg) }}</p>
              </template>

              <!-- Stream complete: show final plain text only -->
              <p v-else class="chat-assistant-text">{{ messageText(msg) }}</p>

              <div
                v-if="hasMessageText(msg) && !isTemporaryMessage(msg)"
                data-testid="approval-panel"
                class="chat-message-actions"
              >
                <button type="button" class="chat-action-btn" @click="copyDraftToClipboard(messageText(msg))" aria-label="Copy response">
                  <UIcon name="i-lucide-copy" class="size-4" />
                </button>
                <button
                  data-testid="thumbs-up"
                  type="button"
                  class="chat-action-btn"
                  :disabled="isLoading || isSending"
                  @click="handleApprove(messageText(msg))"
                  aria-label="Approve response"
                >
                  <UIcon name="i-lucide-thumbs-up" class="size-4" />
                </button>
                <button
                  data-testid="thumbs-down"
                  type="button"
                  class="chat-action-btn"
                  @click="handleReject(messageText(msg))"
                  aria-label="Reject response"
                >
                  <UIcon name="i-lucide-thumbs-down" class="size-4" />
                </button>
                <button
                  data-testid="regenerate-response"
                  type="button"
                  class="chat-action-btn"
                  :disabled="regenerateProcessing"
                  @click="handleRegenerate(messageText(msg))"
                  aria-label="Regenerate response"
                >
                  <UIcon name="i-lucide-refresh-cw" class="size-4" :class="regenerateProcessing ? 'animate-spin' : ''" />
                </button>
                <button type="button" class="chat-action-btn" aria-label="More actions">
                  <UIcon name="i-lucide-ellipsis" class="size-4" />
                </button>

                <div v-if="idx === latestAssistantIndex && pending?.sources?.length" class="chat-inline-sources" data-testid="chat-sources">
                  <a
                    v-for="source in pending.sources"
                    :key="`${source.label}-${source.url || ''}`"
                    :href="source.url || '#'
                    "
                    class="chat-source-link"
                    :target="source.url ? '_blank' : undefined"
                    :rel="source.url ? 'noopener noreferrer' : undefined"
                  >
                    <UIcon name="i-lucide-link" class="size-3" />
                    <span>{{ source.label }}</span>
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div ref="scrollBottomRef" class="h-px" aria-hidden="true" />
      </div>
      <div class="chat-composer-shell">
        <div class="mx-auto w-full max-w-3xl">
          <form class="chat-composer-form" @submit.prevent="send">
            <UButton icon="i-lucide-plus" variant="ghost" color="neutral" class="rounded-full" />
            <UChatPrompt
              v-model="input"
              data-testid="chat-prompt"
              autoresize
              :rows="1"
              :maxrows="6"
              placeholder="Ask anything"
              class="flex-1"
              :disabled="isLoading || isSending"
              @keydown.enter.exact.prevent="send"
            />
            <UButton icon="i-lucide-mic" variant="ghost" color="neutral" class="rounded-full" />
            <UButton
              data-testid="chat-submit"
              icon="i-lucide-arrow-up"
              color="neutral"
              class="rounded-full"
              :loading="isLoading || isSending"
              :disabled="isLoading || isSending || !input.trim()"
              @click="handleSendClick"
            />
          </form>
        </div>
      </div>
    </template>

    <template v-else-if="isLoading">
      <div class="flex-1 flex items-center justify-center">
        <UIcon name="i-lucide-loader-circle" class="size-8 text-muted animate-spin" />
      </div>
    </template>

    <template v-else>
      <div data-testid="chat-empty-state" class="flex-1 flex items-center justify-center px-4 text-muted">
        <div class="mx-auto w-full max-w-md space-y-3 text-center">
          <UAlert
            v-if="chatError"
            color="warning"
            variant="soft"
            icon="i-lucide-triangle-alert"
            :title="chatError"
          />
          <UAlert
            v-else
            color="neutral"
            variant="soft"
            icon="i-lucide-message-circle-off"
            title="Thread nicht gefunden"
          />
          <UButton color="neutral" variant="outline" @click="retryLoadThread">
            Retry
          </UButton>
        </div>
      </div>
    </template>
  </div>
</template>
