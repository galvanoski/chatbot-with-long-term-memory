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

async function submitFeedback() {
  const note = feedbackInput.value?.trim() || 'thumbs_down'
  const ok = await chat.rejectCopy(note)
  if (ok) {
    feedbackNotice.value = 'Danke! Dein Feedback wurde gespeichert und wird fuer Verbesserungen genutzt.'
    showFeedback.value = false
    feedbackInput.value = ''
  }
}

const input = ref('')
const isLoading = computed(() => unref(chat.loading))
const isAwaiting = computed(() => unref(chat.isAwaitingApproval))
const pending = computed(() => unref(chat.pendingCopy))
const currentThread = computed(() => unref(chat.currentThread))
const threadMessages = computed(() => unref(chat.messages))
const chatError = computed(() => unref(chat.error))
const showDebugPanel = computed(() => import.meta.dev && debugEnabled.value)

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

const draftSections = computed(() => {
  if (!pending.value) return []

  if (editMode.value) {
    return [
      { label: 'Hook', text: editableHook.value },
      { label: 'Body', text: editableBody.value },
      { label: 'CTA', text: editableCta.value }
    ].filter(section => section.text?.trim())
  }

  const parts = pending.value.parts
  if (parts?.hook || parts?.body || parts?.cta) {
    return [
      { label: 'Hook', text: parts.hook || '' },
      { label: 'Body', text: parts.body || '' },
      { label: 'CTA', text: parts.cta || '' }
    ].filter(section => section.text?.trim())
  }

  return [{ label: 'Entwurf', text: pending.value.content || '' }].filter(section => section.text?.trim())
})

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
        v-if="!isLoading && !threadMessages.length"
        class="mx-4 mt-3 rounded-lg border border-default bg-muted/25 px-4 py-3"
      >
        <p class="text-sm font-semibold">Los geht's</p>
        <p class="text-xs text-muted mt-1">Schreibe unten deinen Prompt oder starte mit einem Chip von der Startseite.</p>
      </div>

      <div
        v-if="isLoading"
        data-testid="chat-processing"
        class="mx-4 mt-3 rounded-lg border border-default bg-muted/40 px-3 py-2 text-xs text-muted"
      >
        Denke nach... Analysiere Produktkontext und erstelle Entwurf.
      </div>

      <UChatMessages
        :key="chatId"
        data-testid="chat-messages"
        class="flex-1 overflow-y-auto px-4 py-4"
        :messages="(displayedMessages as any)"
        :assistant="{ avatar: { src: '/favicon.ico' } }"
      />

      <!-- HITL: Approval Panel -->
      <div
        v-if="isAwaiting && pending"
        data-testid="approval-panel"
        class="border-t border-default bg-elevated px-4 py-3"
      >
        <div class="max-w-2xl mx-auto space-y-3">
          <div class="flex items-center gap-2">
            <UBadge color="warning" variant="soft" size="sm">
              Freigabe erforderlich
            </UBadge>
            <span class="text-sm text-muted">Das ist die finale Antwort. Reagiere direkt oder bearbeite optional.</span>
          </div>

          <div v-if="feedbackNotice" class="rounded-md border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">
            {{ feedbackNotice }}
          </div>

          <UCard variant="subtle">
            <div class="space-y-3">
              <div
                v-for="section in draftSections"
                :key="section.label"
                class="rounded-md border border-default/60 bg-default/60 px-3 py-2"
              >
                <p class="text-[11px] uppercase tracking-wide text-muted">{{ section.label }}</p>
                <p data-testid="pending-copy-content" class="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{{ section.text }}</p>
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <UButton size="sm" variant="soft" color="neutral" icon="i-lucide-copy" @click="copyDraftToClipboard">
                  Kopieren
                </UButton>
                <UButton
                  size="sm"
                  variant="soft"
                  color="neutral"
                  icon="i-lucide-pencil"
                  @click="editMode = !editMode"
                >
                  {{ editMode ? 'Bearbeitung ausblenden' : 'Bearbeiten' }}
                </UButton>
                <UButton
                  data-testid="thumbs-up"
                  size="sm"
                  variant="soft"
                  color="success"
                  icon="i-lucide-thumbs-up"
                  :loading="isLoading"
                  @click="handleApprove"
                >
                  Hilfreich
                </UButton>
                <UButton
                  data-testid="thumbs-down"
                  size="sm"
                  variant="soft"
                  color="error"
                  icon="i-lucide-thumbs-down"
                  :disabled="isLoading"
                  @click="handleReject"
                >
                  Unpassend
                </UButton>
              </div>

              <div v-if="editMode" class="grid gap-3 md:grid-cols-3">
                <div>
                  <p class="text-xs text-muted mb-1">Hook</p>
                  <UTextarea
                    v-model="editableHook"
                    data-testid="pending-hook"
                    autoresize
                    :rows="4"
                    placeholder="Kurzer, sarkastischer Hook"
                  />
                </div>

                <div>
                  <p class="text-xs text-muted mb-1">Body</p>
                  <UTextarea
                    v-model="editableBody"
                    data-testid="pending-body"
                    autoresize
                    :rows="6"
                    placeholder="Haupttext der Veroeffentlichung"
                  />
                </div>

                <div>
                  <p class="text-xs text-muted mb-1">CTA</p>
                  <UTextarea
                    v-model="editableCta"
                    data-testid="pending-cta"
                    autoresize
                    :rows="4"
                    placeholder="Kurzer CTA"
                  />
                </div>
              </div>

              <div class="flex justify-end">
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  @click="useGeneratedDraft"
                >
                  KI-Vorschlag wiederherstellen
                </UButton>
              </div>
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

          <div v-if="showFeedback" class="flex gap-2 items-start">
            <UTextarea
              v-model="feedbackInput"
              placeholder="Was war unpassend? Dieses Feedback wird fuer Verbesserungen gespeichert."
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
            placeholder="Beschreibe Produkt, Zielgruppe und gewuenschten Ton"
            variant="subtle"
            class="[view-transition-name:chat-prompt]"
            @submit="send"
          >
            <template #footer>
              <div class="flex w-full justify-end">
                <UChatPromptSubmit data-testid="chat-submit" :on-click="handleSendClick" color="neutral" size="sm" />
              </div>
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
