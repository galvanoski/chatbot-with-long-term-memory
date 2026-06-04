<script setup lang="ts">
const route = useRoute()
const chatId = route.params.id as string
const initialPrompt = computed(() => typeof route.query.prompt === 'string' ? route.query.prompt : '')

const feedbackInput = ref('')
const showFeedback = ref(false)
const initialPromptSent = ref(false)

const chat = useGeekCatChat()

await chat.loadThread(chatId)

async function send() {
  if (!input.value.trim() || unref(chat.loading)) return
  const text = input.value
  input.value = ''
  return chat.sendMessage(text)
}

async function handleSendClick() {
  await send()
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
  await chat.approveCopy()
}

function handleReject() {
  showFeedback.value = true
}

async function submitFeedback() {
  await chat.rejectCopy(feedbackInput.value)
  showFeedback.value = false
  feedbackInput.value = ''
}

const input = ref('')
const isLoading = computed(() => unref(chat.loading))
const isAwaiting = computed(() => unref(chat.isAwaitingApproval))
const pending = computed(() => unref(chat.pendingCopy))
const currentThread = computed(() => unref(chat.currentThread))
const threadMessages = computed(() => unref(chat.messages))
const chatError = computed(() => unref(chat.error))
const showDebugPanel = import.meta.dev
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
  <div class="flex-1 flex flex-col h-full min-h-0">
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
      <div
        v-if="isLoading"
        data-testid="chat-processing"
        class="mx-4 mt-3 rounded-lg border border-default bg-muted/40 px-3 py-2 text-xs text-muted"
      >
        Thinking... Analyzing context and preparing response.
      </div>

      <UChatMessages
        :key="chatId"
        data-testid="chat-messages"
        class="flex-1 overflow-y-auto px-4 py-4"
        :messages="(threadMessages as any)"
        :assistant="{ avatar: { src: '/favicon.ico' } }"
      />

      <!-- HITL: Approval Panel -->
      <div
        v-if="isAwaiting && pending"
        data-testid="approval-panel"
        class="border-t border-default p-4 bg-elevated"
      >
        <div class="max-w-2xl mx-auto space-y-3">
          <div class="flex items-center gap-2">
            <UBadge color="warning" variant="soft" size="sm">
              Freigabe erforderlich
            </UBadge>
            <span class="text-sm text-muted">Der Copy ist bereit zur Veröffentlichung</span>
          </div>

          <UCard variant="subtle">
            <div data-testid="pending-copy-content" class="whitespace-pre-wrap text-sm">
              {{ pending.content }}
            </div>
            <div v-if="pending.hashtags?.length" class="flex flex-wrap gap-1 mt-2">
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
          </UCard>

          <div class="flex items-center gap-2">
            <UButton
              color="success"
              icon="i-lucide-check"
              :loading="isLoading"
              @click="handleApprove"
            >
              Veröffentlichen
            </UButton>
            <UButton
              color="error"
              variant="soft"
              icon="i-lucide-x"
              :disabled="isLoading"
              @click="handleReject"
            >
              Ablehnen
            </UButton>
          </div>

          <div v-if="showFeedback" class="flex gap-2 items-start">
            <UTextarea
              v-model="feedbackInput"
              placeholder="Feedback für den Copywriter..."
              size="sm"
              class="flex-1"
            />
            <UButton
              size="sm"
              color="neutral"
              :loading="isLoading"
              @click="submitFeedback"
            >
              Senden
            </UButton>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div class="border-t border-default p-4">
        <div class="max-w-2xl mx-auto">
          <UChatPrompt
            v-model="input"
            data-testid="chat-prompt"
            :status="isLoading ? 'streaming' : 'ready'"
            placeholder="Nachricht eingeben..."
            variant="subtle"
            class="[view-transition-name:chat-prompt]"
            @submit="send"
          >
            <template #footer>
              <UChatPromptSubmit data-testid="chat-submit" :on-click="handleSendClick" color="neutral" size="sm" />
            </template>
          </UChatPrompt>
        </div>
      </div>
    </template>

    <template v-else-if="isLoading">
      <div class="flex-1 flex items-center justify-center">
        <UIcon name="i-lucide-loader-circle" class="size-8 text-muted animate-spin" />
      </div>
    </template>

    <template v-else>
      <div data-testid="chat-empty-state" class="flex-1 flex items-center justify-center text-muted">
        <p>Thread nicht gefunden.</p>
      </div>
    </template>
  </div>
</template>
