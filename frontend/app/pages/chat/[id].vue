<script setup lang="ts">
const route = useRoute()
const chatId = route.params.id as string
const initialPrompt = computed(() => typeof route.query.prompt === 'string' ? route.query.prompt : '')
const debugEnabled = computed(() => route.query.debug === '1')

const feedbackInput = ref('')
const showFeedback = ref(false)
const initialPromptSent = ref(false)
const editableHook = ref('')
const editableBody = ref('')
const editableCta = ref('')
const editMode = ref(false)
const feedbackNotice = ref('')
const feedbackProcessing = ref(false)
const regenerateProcessing = ref(false)

const chat = useGeekCatChat()

await chat.loadThread(chatId)

async function send() {
  if (!input.value.trim() || unref(chat.loading) || unref(chat.sending) || isAwaiting.value) return
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

async function handleApprove() {
  const edited = editMode.value
    ? {
        hook: editableHook.value,
        body: editableBody.value,
        cta: editableCta.value
      }
    : undefined
  const ok = await chat.approveCopy(edited, 'thumbs_up')
  if (ok) {
    feedbackNotice.value = 'Danke! Dein positives Feedback wurde gespeichert.'
    showFeedback.value = false
  }
}

function handleReject() {
  showFeedback.value = true
}

async function handleRegenerate() {
  regenerateProcessing.value = true
  try {
    const ok = await chat.regenerateCopy()
    if (ok) {
      feedbackNotice.value = 'Neue Variante wurde erstellt.'
      showFeedback.value = false
    }
  } finally {
    regenerateProcessing.value = false
  }
}

async function submitFeedback() {
  const note = feedbackInput.value?.trim() || 'thumbs_down'
  feedbackProcessing.value = true
  try {
    const ok = await chat.rejectCopy(note)
    if (ok) {
      feedbackNotice.value = 'Danke! Neue Version basierend auf deinem Feedback wurde erstellt.'
      showFeedback.value = false
      feedbackInput.value = ''
    }
  } finally {
    feedbackProcessing.value = false
  }
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

const displayedMessages = computed(() => {
  const items = [...threadMessages.value]
  if (!isAwaiting.value || !pending.value || items.length === 0) {
    return items
  }

  const lastMessage = items[items.length - 1]
  const lastText = lastMessage?.parts?.[0]?.text?.trim()
  const pendingText = pending.value.content?.trim()
  const isDuplicateDraft = lastMessage?.role === 'assistant' && !!lastText && lastText === pendingText

  return isDuplicateDraft ? items.slice(0, -1) : items
})

function messageText(message: { parts?: Array<{ text?: string }>; content?: string }) {
  return message?.parts?.[0]?.text || message?.content || ''
}

function useGeneratedDraft() {
  if (!pending.value) return
  editableHook.value = pending.value.parts?.hook ?? ''
  editableBody.value = pending.value.parts?.body ?? pending.value.content ?? ''
  editableCta.value = pending.value.parts?.cta ?? ''
}

function getDraftPreview() {
  if (!pending.value) return ''
  if (!editMode.value) return pending.value.content
  return [editableHook.value, editableBody.value, editableCta.value].filter(Boolean).join('\n\n')
}

async function copyDraftToClipboard() {
  const text = getDraftPreview().trim()
  if (!text) return
  await navigator.clipboard.writeText(text)
  feedbackNotice.value = 'Entwurf in die Zwischenablage kopiert.'
}

watch(pending, (next) => {
  if (!next) {
    editableHook.value = ''
    editableBody.value = ''
    editableCta.value = ''
    editMode.value = false
    showFeedback.value = false
    return
  }

  editableHook.value = next.parts?.hook ?? ''
  editableBody.value = next.parts?.body ?? ''
  editableCta.value = next.parts?.cta ?? ''

  if (!next.parts?.hook && !next.parts?.body && !next.parts?.cta && next.content) {
    const lines = next.content.split('\n').filter(Boolean)
    editableHook.value = lines[0] ?? ''
    editableBody.value = lines.slice(1).join('\n').trim()
    editableCta.value = ''
  }
}, { immediate: true })
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

      <div class="chat-scroll-area">
        <div
          v-if="!isLoading && !threadMessages.length"
          class="mx-auto w-full max-w-3xl rounded-lg border border-default bg-muted/25 px-4 py-3"
        >
          <p class="text-sm font-semibold">Los geht's</p>
          <p class="text-xs text-muted mt-1">Schreibe unten deinen Prompt oder starte mit einem Chip von der Startseite.</p>
        </div>

        <div
          v-if="isLoading"
          data-testid="chat-processing"
          class="mx-auto mb-4 w-full max-w-3xl rounded-lg border border-default bg-muted/40 px-3 py-2 text-xs text-muted"
        >
          Der Agent analysiert gerade Kontext und formuliert eine Antwort...
        </div>

        <div :key="chatId" data-testid="chat-messages" class="mx-auto w-full max-w-3xl space-y-6 pb-8">
          <div
            v-for="msg in displayedMessages"
            :key="msg.id"
            class="chat-row"
            :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <div v-if="msg.role === 'user'" class="chat-user-bubble">
              {{ messageText(msg) }}
            </div>

            <div v-else class="chat-assistant-block">
              <p class="chat-assistant-text">{{ messageText(msg) }}</p>
            </div>
          </div>

          <div v-if="isAwaiting && pending" data-testid="approval-panel" class="chat-row justify-start">
            <div class="chat-assistant-block space-y-4">
              <p data-testid="pending-copy-content" class="chat-assistant-text">{{ getDraftPreview() }}</p>

              <div v-if="feedbackNotice" class="rounded-md border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">
                {{ feedbackNotice }}
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <UButton size="sm" variant="ghost" color="neutral" icon="i-lucide-copy" @click="copyDraftToClipboard">
                  Copy
                </UButton>
                <UButton
                  data-testid="regenerate-response"
                  size="sm"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-refresh-cw"
                  :loading="regenerateProcessing || isLoading || isSending"
                  :disabled="regenerateProcessing || isLoading || isSending || isAwaiting"
                  @click="handleRegenerate"
                >
                  Regenerate
                </UButton>
                <UButton
                  size="sm"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-pencil"
                  @click="editMode = !editMode"
                >
                  {{ editMode ? 'Hide edit' : 'Edit' }}
                </UButton>
                <UButton
                  data-testid="thumbs-up"
                  size="sm"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-thumbs-up"
                  :loading="isLoading || isSending"
                  :disabled="isLoading || isSending || isAwaiting"
                  @click="handleApprove"
                />
                <UButton
                  data-testid="thumbs-down"
                  size="sm"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-thumbs-down"
                  :disabled="isLoading || isSending || feedbackProcessing || isAwaiting"
                  @click="handleReject"
                />
              </div>

              <div v-if="editMode" class="grid gap-3 md:grid-cols-3">
                <div>
                  <p class="text-xs text-muted mb-1">Hook</p>
                  <UTextarea v-model="editableHook" data-testid="pending-hook" autoresize :rows="4" />
                </div>

                <div>
                  <p class="text-xs text-muted mb-1">Body</p>
                  <UTextarea v-model="editableBody" data-testid="pending-body" autoresize :rows="6" />
                </div>

                <div>
                  <p class="text-xs text-muted mb-1">CTA</p>
                  <UTextarea v-model="editableCta" data-testid="pending-cta" autoresize :rows="4" />
                </div>
              </div>

              <div v-if="pending.hashtags?.length" class="flex flex-wrap gap-1">
                <UBadge
                  v-for="tag in pending.hashtags"
                  :key="tag"
                  color="neutral"
                  variant="outline"
                  size="sm"
                >
                  {{ tag }}
                </UBadge>
              </div>

              <div v-if="pending.sources?.length" class="space-y-2" data-testid="chat-sources">
                <p class="text-xs font-semibold uppercase tracking-wide text-muted">Sources</p>
                <div class="flex flex-wrap gap-2">
                  <a
                    v-for="source in pending.sources"
                    :key="`${source.label}-${source.url || ''}`"
                    :href="source.url || '#'
                    "
                    class="inline-flex items-center gap-1 rounded-full border border-default px-2 py-1 text-xs text-toned hover:bg-muted/40"
                    :target="source.url ? '_blank' : undefined"
                    :rel="source.url ? 'noopener noreferrer' : undefined"
                    @click.prevent="!source.url"
                  >
                    <UIcon name="i-lucide-link" class="size-3" />
                    <span>{{ source.label }}</span>
                  </a>
                </div>
              </div>

              <div v-if="showFeedback" class="space-y-2">
                <div
                  v-if="feedbackProcessing || isLoading"
                  class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
                >
                  Agent verarbeitet dein Feedback und erstellt gerade eine neue Version...
                </div>

                <div class="flex gap-2 items-start">
                  <UTextarea
                    v-model="feedbackInput"
                    placeholder="Was war unpassend?"
                    size="sm"
                    class="flex-1"
                    :disabled="feedbackProcessing || isLoading"
                  />
                  <UButton
                    size="sm"
                    color="neutral"
                    :loading="feedbackProcessing || isLoading"
                    :disabled="feedbackProcessing || isLoading"
                    @click="submitFeedback"
                  >
                    {{ feedbackProcessing || isLoading ? 'Regenerating...' : 'Send' }}
                  </UButton>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Input -->
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
              :disabled="isLoading || isSending || isAwaiting"
              @keydown.enter.exact.prevent="send"
            />
            <UButton icon="i-lucide-mic" variant="ghost" color="neutral" class="rounded-full" />
            <UButton
              data-testid="chat-submit"
              icon="i-lucide-arrow-up"
              color="neutral"
              class="rounded-full"
              :loading="isLoading || isSending"
              :disabled="isLoading || isSending || isAwaiting || !input.trim()"
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
