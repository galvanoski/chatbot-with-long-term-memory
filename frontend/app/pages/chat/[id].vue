<script setup lang="ts">
const route = useRoute()
const chatId = route.params.id as string

const feedbackInput = ref('')
const showFeedback = ref(false)

const chat = useGeekCatChat()

await chat.loadThread(chatId)

async function send() {
  if (!input.value.trim() || unref(chat.loading)) return
  const text = input.value
  input.value = ''
  await chat.sendMessage(text)
}

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
</script>

<template>
  <div class="flex-1 flex flex-col h-full min-h-0">
    <template v-if="chat.currentThread">
      <UChatMessages
        class="flex-1 overflow-y-auto px-4 py-4"
        :messages="(chat.messages as any)"
        :assistant="{ avatar: { src: '/favicon.ico' } }"
      />

      <!-- HITL: Approval Panel -->
      <div
        v-if="isAwaiting && pending"
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
            <div class="whitespace-pre-wrap text-sm">
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
            :status="isLoading ? 'streaming' : 'ready'"
            placeholder="Nachricht eingeben..."
            variant="subtle"
            class="[view-transition-name:chat-prompt]"
            @submit="send"
          >
            <template #footer>
              <UChatPromptSubmit color="neutral" size="sm" />
            </template>
          </UChatPrompt>
        </div>
      </div>
    </template>

    <template v-else-if="isLoading">
      <div class="flex-1 flex items-center justify-center">
        <ULog />
      </div>
    </template>

    <template v-else>
      <div class="flex-1 flex items-center justify-center text-muted">
        <p>Thread nicht gefunden.</p>
      </div>
    </template>
  </div>
</template>
