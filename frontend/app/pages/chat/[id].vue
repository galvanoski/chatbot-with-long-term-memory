<script setup lang="ts">
const route = useRoute()
const chatId = route.params.id as string
const initialPrompt = computed(() => typeof route.query.prompt === 'string' ? route.query.prompt : '')
const initialImagePrompt = computed(() => typeof route.query.imagePrompt === 'string' ? route.query.imagePrompt : '')
const initialSEO = computed(() => typeof route.query.seo === 'string' ? route.query.seo : '')
const debugEnabled = computed(() => route.query.debug === '1')

const initialPromptSent = ref(false)
const initialImagePromptSent = ref(false)
const initialSEOSent = ref(false)
const regenerateProcessing = ref(false)

const chat = useGeekCatChat()
const messageFeedback = chat.messageFeedback

await chat.loadThread(chatId)

async function send() {
  if (!input.value.trim() || unref(chat.loading) || unref(chat.sending)) return
  const text = input.value
  input.value = ''
  if (text.startsWith('Generate a logo prompt')) {
    return chat.generateImagePrompt(text)
  }
  if (text.startsWith('Generate SEO')) {
    return chat.generateSEO(text)
  }
  return chat.sendMessage(text)
}

async function handleSendClick() {
  await send()
}

async function retryLoadThread() {
  await chat.loadThread(chatId)
}

onMounted(async () => {
  if (initialImagePrompt.value && !initialImagePromptSent.value) {
    initialImagePromptSent.value = true
    await chat.generateImagePrompt(initialImagePrompt.value)
    await navigateTo(`/chat/${chatId}`, { replace: true })
    return
  }

  if (initialSEO.value && !initialSEOSent.value) {
    initialSEOSent.value = true
    await chat.generateSEO(initialSEO.value)
    await navigateTo(`/chat/${chatId}`, { replace: true })
    return
  }

  if (!initialPrompt.value || initialPromptSent.value) return
  if (currentThread.value?.messages?.length) return

  initialPromptSent.value = true
  input.value = initialPrompt.value
  await send()
  await navigateTo(`/chat/${chatId}`, { replace: true })
})

async function handleRegenerate(msg: any) {
  regenerateProcessing.value = true
  try {
    if (msg?.seo_metadata) {
      const allMessages = unref(chat.messages)
      const idx = allMessages.indexOf(msg)
      const userMsg = idx >= 0
        ? allMessages.slice(0, idx).reverse().find((m: any) => m.role === 'user')
        : null
      const instruction = userMsg ? messageText(userMsg) : 'Generate SEO for: '
      await chat.generateSEO(instruction)
    } else {
      const text = typeof msg === 'string' ? msg : messageText(msg)
      await chat.regenerateCopy(text || assistantResponsePreview.value || undefined)
    }
  } finally {
    regenerateProcessing.value = false
  }
}

async function copyMessageToClipboard(text: string) {
  if (!text) return
  await navigator.clipboard.writeText(text)
}

async function sendFeedback(msg: any, rating: 'up' | 'down') {
  const toast = useToast()
  const current = chat.messageFeedback.value[msg?.id]
  if (current === rating) {
    chat.setMessageFeedback(msg.id, null)
    toast.add({ title: 'Bewertung zurückgesetzt', duration: 2000 })
    return
  }
  chat.setMessageFeedback(msg.id, rating)
  const content = messageText(msg) || assistantResponsePreview.value || ''
  const ok = rating === 'up'
    ? await chat.approveCopy(content || undefined, undefined, msg.id)
    : await chat.rejectCopy(content || 'thumbs_down', msg.id)
  if (ok) {
    toast.add({ title: 'Feedback gespeichert', description: rating === 'up' ? 'Positive Bewertung' : 'Negative Bewertung', duration: 3000 })
  } else {
    chat.setMessageFeedback(msg.id, current || null)
    toast.add({ title: 'Fehler', description: unref(chat.error) || 'Feedback konnte nicht gespeichert werden', color: 'error', duration: 4000 })
  }
}

function runSuggestion(action: string, msg: any) {
  const text = messageText(msg) || ''

  // Handle product-type-specific create_post actions: create_post_tshirt, create_post_mug, etc.
  const createPostMatch = action.match(/^create_post_(.+)$/)
  if (createPostMatch) {
    const productType = createPostMatch[1]
    chat.generateSocialPost(text, productType)
    return
  }

  switch (action) {
    case 'generate_image':
      chat.generateImage(text, msg.id)
      break
    case 'generate_image_prompt':
      chat.generateImagePrompt(text || 'a cat programmer logo', true)
      break
    case 'generate_seo':
      chat.generateSEO(text || '')
      break
    case 'create_post':
      chat.generateSocialPost(text || 'Create a social media post for this product')
      break
    case 'research_trends':
      input.value = text ? `Research trends for: ${text}` : 'Research current trends'
      send()
      break
    case 'copy_clipboard':
      copyDraftToClipboard(text)
      break
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
    // Keep messages that have text, image, or are streaming placeholders
    return hasMessageText(message) || isStreamingPlaceholder(message) || !!message.image_url
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
  () => unref(chat.messages).map(m => (m.parts?.[0]?.text ?? m.content ?? '') + (m.image_url || '')).join(''),
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
          <h1 class="chat-topbar-title">The Geek Cat</h1>
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
              <!-- No text yet: show shimmer loading text -->
              <template v-if="isStreamingPlaceholder(msg)">
                <div data-testid="chat-processing" class="chat-response-pending">
                  <span class="text-shimmer">{{ loadingStatusText }}</span>
                </div>
              </template>

              <!-- Text is arriving: show shimmer above partial text until done -->
              <template v-else-if="isSending && idx === latestAssistantIndex">
                <div data-testid="chat-processing" class="chat-response-pending mb-2">
                  <span class="text-shimmer">{{ loadingStatusText }}</span>
                </div>
                <p class="chat-assistant-text">{{ messageText(msg) }}</p>
              </template>

              <!-- Stream complete: show final plain text only -->
              <template v-if="msg.seo_metadata">
                <div class="space-y-2 text-sm">
                  <div v-if="msg.seo_metadata.seo_title" class="flex gap-2">
                    <span class="font-semibold shrink-0">SEO Title:</span>
                    <span class="text-default">{{ msg.seo_metadata.seo_title }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.focus_keyword" class="flex gap-2">
                    <span class="font-semibold shrink-0">Focus Keyword:</span>
                    <span class="text-default">{{ msg.seo_metadata.focus_keyword }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.secondary_keywords?.length" class="flex gap-2">
                    <span class="font-semibold shrink-0">Secondary Keywords:</span>
                    <span class="text-default">{{ msg.seo_metadata.secondary_keywords.join(', ') }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.meta_description" class="flex gap-2">
                    <span class="font-semibold shrink-0">Meta Description:</span>
                    <span class="text-default">{{ msg.seo_metadata.meta_description }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.url_slug" class="flex gap-2">
                    <span class="font-semibold shrink-0">URL Slug:</span>
                    <span class="font-mono text-default">{{ msg.seo_metadata.url_slug }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.alt_text" class="flex gap-2">
                    <span class="font-semibold shrink-0">Alt Text:</span>
                    <span class="text-default">{{ msg.seo_metadata.alt_text }}</span>
                  </div>
                  <div v-if="msg.seo_metadata.seo_description" class="mt-3 pt-3 border-t border-default/20">
                    <div class="seo-description text-default leading-relaxed" v-html="msg.seo_metadata.seo_description" />
                  </div>
                </div>
              </template>
              <p v-if="!msg.seo_metadata && messageText(msg)" class="chat-assistant-text">{{ messageText(msg) }}</p>
              <img
                v-if="msg.image_url"
                :src="msg.image_url"
                alt="Generated image"
                class="mt-3 rounded-lg max-w-full h-auto border border-default/20"
              />
              <div v-if="msg.image_url" class="flex gap-1 mt-1">
                <a
                  :href="msg.image_url"
                  download
                  class="chat-action-btn"
                  aria-label="Download image"
                  title="Download image"
                >
                  <UIcon name="i-lucide-download" class="size-4" />
                </a>
              </div>

              <div
                v-if="(hasMessageText(msg) || msg.image_url) && !isTemporaryMessage(msg)"
                data-testid="approval-panel"
                class="chat-message-actions"
              >
                <UTooltip text="Kopieren">
                  <button type="button" class="chat-action-btn" @click="copyDraftToClipboard(messageText(msg))" aria-label="Kopieren">
                    <UIcon name="i-lucide-copy" class="size-4" />
                  </button>
                </UTooltip>
                <UTooltip text="Genehmigen">
                  <button
                    data-testid="thumbs-up"
                    type="button"
                    class="chat-action-btn"
                    :class="{ 'chat-action-btn-active': messageFeedback[msg.id] === 'up' }"
                    :disabled="isLoading || isSending"
                    @click="sendFeedback(msg, 'up')"
                    aria-label="Genehmigen"
                  >
                    <UIcon name="i-lucide-thumbs-up" class="size-4" />
                  </button>
                </UTooltip>
                <UTooltip text="Ablehnen">
                  <button
                    data-testid="thumbs-down"
                    type="button"
                    class="chat-action-btn"
                    :class="{ 'chat-action-btn-active': messageFeedback[msg.id] === 'down' }"
                    @click="sendFeedback(msg, 'down')"
                    aria-label="Ablehnen"
                  >
                    <UIcon name="i-lucide-thumbs-down" class="size-4" />
                  </button>
                </UTooltip>
                <UTooltip text="Neu generieren">
                  <button
                    data-testid="regenerate-response"
                    type="button"
                    class="chat-action-btn"
                    :disabled="regenerateProcessing"
                    @click="handleRegenerate(msg)"
                    aria-label="Neu generieren"
                  >
                    <UIcon name="i-lucide-refresh-cw" class="size-4" :class="regenerateProcessing ? 'animate-spin' : ''" />
                  </button>
                </UTooltip>
                <UTooltip text="Bild generieren">
                  <button
                    type="button"
                    class="chat-action-btn"
                    :disabled="loading || sending"
                    @click="chat.generateImage(messageText(msg), msg.id)"
                    aria-label="Bild generieren"
                  >
                    <UIcon name="i-lucide-image" class="size-4" />
                  </button>
                </UTooltip>

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

                <div v-if="msg.usage || msg.is_image_prompt || msg.image_url" class="flex items-center gap-3 mt-1 text-[10px] text-muted select-none">
                  <template v-if="msg.usage">
                    <span>{{ msg.usage.model }}</span>
                    <span>⬆ {{ msg.usage.input_tokens }}</span>
                    <span>⬇ {{ msg.usage.output_tokens }}</span>
                    <span>${{ msg.usage.cost.toFixed(5) }}</span>
                  </template>
                  <template v-else>
                    <span>openai/gpt-5-mini</span>
                    <span class="text-muted/50">(usage data pending)</span>
                  </template>
                </div>

                <div v-if="msg.seo_metadata" class="w-full mt-2">
                  <span class="text-[10px] font-semibold uppercase tracking-wide text-muted/40 select-none">SEO</span>
                </div>

                <div v-if="msg.rag_trace?.length" class="w-full mt-2">
                  <details class="group">
                    <summary class="flex items-center gap-1.5 cursor-pointer text-[10px] font-semibold uppercase tracking-wide text-muted/60 hover:text-default select-none py-0.5">
                      <span class="text-xs font-mono leading-none group-open:block hidden">−</span>
                      <span class="text-xs font-mono leading-none group-open:hidden block">+</span>
                      Agent Trace
                    </summary>
                    <div class="mt-1.5 space-y-1.5">
                      <template v-for="(ev, ei) in msg.rag_trace" :key="ei">
                        <!-- agent_start -->
                        <details v-if="ev.stage === 'agent_start'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>📋 Start</span>
                            <span v-if="ev.query" class="truncate max-w-48 text-muted font-mono">{{ ev.query }}</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.user_id" class="flex gap-2"><span class="text-muted/50">user:</span><span class="text-muted/70">{{ ev.user_id }}</span></div>
                            <div v-if="ev.query" class="flex gap-2"><span class="text-muted/50">query:</span><span class="font-mono text-muted/70">{{ ev.query }}</span></div>
                          </div>
                        </details>

                        <details v-else-if="ev.stage === 'ltm_retrieve'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>💾 Memory Retrieval</span>
                            <span v-if="ev.docs !== undefined" class="text-muted">{{ ev.docs }} docs</span>
                            <span v-if="ev.latency_ms !== undefined" class="text-muted">{{ ev.latency_ms }}ms</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div class="flex gap-2"><span class="text-muted/50">docs:</span><span class="text-muted/70">{{ ev.docs }}</span></div>
                            <div v-if="ev.latency_ms !== undefined" class="flex gap-2"><span class="text-muted/50">latency:</span><span class="text-muted/70">{{ ev.latency_ms }}ms</span></div>
                            <div v-if="ev.texts?.length" class="space-y-1">
                              <span class="text-muted/50">memory excerpts:</span>
                              <div v-for="(t, ti) in ev.texts" :key="ti" class="ml-2 text-muted/70 truncate font-mono" :title="t">{{ t }}</div>
                            </div>
                          </div>
                        </details>

                        <!-- product_search / meme_search -->
                        <details v-else-if="ev.stage === 'product_search' || ev.stage === 'meme_search'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>{{ ev.stage === 'product_search' ? '🔍 Product Search' : '🔍 Meme Search' }}</span>
                            <span v-if="ev.docs !== undefined" class="text-muted">{{ ev.docs }} found</span>
                            <span v-if="ev.latency_ms !== undefined" class="text-muted">{{ ev.latency_ms }}ms</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.docs !== undefined" class="flex gap-2"><span class="text-muted/50">docs found:</span><span class="text-muted/70">{{ ev.docs }}</span></div>
                            <div v-if="ev.latency_ms !== undefined" class="flex gap-2"><span class="text-muted/50">latency:</span><span class="text-muted/70">{{ ev.latency_ms }}ms</span></div>
                            <div v-if="ev.products?.length" class="space-y-1">
                              <span class="text-muted/50">products:</span>
                              <div v-for="(p, pi) in ev.products" :key="pi" class="ml-2 flex items-center gap-1 text-muted/70">
                                <span class="font-mono">{{ p.sku }}</span>
                                <span v-if="p.name" class="truncate">— {{ p.name }}</span>
                                <span v-if="p.category" class="text-muted/50">({{ p.category }})</span>
                              </div>
                            </div>
                            <div v-if="ev.memes?.length" class="space-y-1">
                              <span class="text-muted/50">memes:</span>
                              <div v-for="(m, mi) in ev.memes" :key="mi" class="ml-2 text-muted/70 truncate font-mono" :title="m.text">{{ m.text }}</div>
                            </div>
                          </div>
                        </details>

                        <!-- context_inject -->
                        <details v-else-if="ev.stage === 'context_inject'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>📦 Context Assembly</span>
                            <span v-if="ev.latency_ms !== undefined" class="text-muted">{{ ev.latency_ms }}ms</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.ltm_docs !== undefined" class="flex gap-2"><span class="text-muted/50">LTM docs injected:</span><span class="text-muted/70">{{ ev.ltm_docs }}</span></div>
                            <div v-if="ev.brand_rules?.length" class="flex gap-2"><span class="text-muted/50">brand rules:</span><span class="text-muted/70">{{ ev.brand_rules.join(', ') }}</span></div>
                            <div v-if="ev.product_skus?.length" class="flex gap-2"><span class="text-muted/50">product SKUs:</span><span class="font-mono text-muted/70">{{ ev.product_skus.join(', ') }}</span></div>
                            <div v-if="ev.latency_ms !== undefined" class="flex gap-2"><span class="text-muted/50">latency:</span><span class="text-muted/70">{{ ev.latency_ms }}ms</span></div>
                          </div>
                        </details>

                        <!-- llm_generate -->
                        <details v-else-if="ev.stage === 'llm_generate'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>🤖 LLM Generation</span>
                            <span v-if="ev.node" class="font-mono text-muted">{{ ev.node }}</span>
                            <span v-if="ev.latency_ms !== undefined" class="text-muted">{{ ev.latency_ms }}ms</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.node" class="flex gap-2"><span class="text-muted/50">node:</span><span class="font-mono text-muted/70">{{ ev.node }}</span></div>
                            <div v-if="ev.latency_ms !== undefined" class="flex gap-2"><span class="text-muted/50">latency:</span><span class="text-muted/70">{{ ev.latency_ms }}ms</span></div>
                            <div v-if="ev.input_tokens !== undefined" class="flex gap-2"><span class="text-muted/50">input tokens:</span><span class="text-muted/70">{{ ev.input_tokens }}</span></div>
                            <div v-if="ev.output_tokens !== undefined" class="flex gap-2"><span class="text-muted/50">output tokens:</span><span class="text-muted/70">{{ ev.output_tokens }}</span></div>
                            <div v-if="ev.total_tokens !== undefined" class="flex gap-2"><span class="text-muted/50">total tokens:</span><span class="text-muted/70">{{ ev.total_tokens }}</span></div>
                          </div>
                        </details>

                        <!-- model_output -->
                        <details v-else-if="ev.stage === 'model_output'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>✅ Output Validation</span>
                            <span v-if="ev.has_german" class="text-green-600/80 dark:text-green-400/80">DE</span>
                            <span v-if="ev.hashtag_count !== undefined" class="text-muted">#{{ ev.hashtag_count }}</span>
                            <span v-if="ev.char_count" class="text-muted">{{ ev.char_count }}c</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div class="flex gap-2">
                              <span class="text-muted/50">German:</span>
                              <span :class="ev.has_german ? 'text-green-600/80 dark:text-green-400/80' : 'text-warning'">{{ ev.has_german ? '✅ yes' : '⚠️ no' }}</span>
                            </div>
                            <div v-if="ev.hashtag_count !== undefined" class="flex gap-2"><span class="text-muted/50">hashtags:</span><span class="text-muted/70">{{ ev.hashtag_count }}</span></div>
                            <div v-if="ev.char_count !== undefined" class="flex gap-2"><span class="text-muted/50">characters:</span><span class="text-muted/70">{{ ev.char_count }}</span></div>
                            <div v-if="ev.validation" class="space-y-0.5">
                              <span class="text-muted/50">validation details:</span>
                              <div v-for="(val, vkey) in ev.validation" :key="vkey" class="ml-2 text-muted/60"><span class="font-mono">{{ vkey }}:</span> {{ String(val) }}</div>
                            </div>
                          </div>
                        </details>

                        <!-- seo_generate -->
                        <details v-else-if="ev.stage === 'seo_generate'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>🔎 SEO Generation</span>
                            <span v-if="ev.focus_keyword" class="font-mono text-muted truncate max-w-32">{{ ev.focus_keyword }}</span>
                            <span v-if="ev.latency_ms !== undefined" class="text-muted">{{ ev.latency_ms }}ms</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.seo_title" class="flex gap-2"><span class="text-muted/50">SEO title:</span><span class="text-muted/70 font-semibold">{{ ev.seo_title }}</span></div>
                            <div v-if="ev.focus_keyword" class="flex gap-2"><span class="text-muted/50">focus keyword:</span><span class="font-mono text-muted/70">{{ ev.focus_keyword }}</span></div>
                            <div v-if="ev.secondary_keywords?.length" class="flex gap-2"><span class="text-muted/50">secondary keywords:</span><span class="text-muted/70">{{ ev.secondary_keywords.join(', ') }}</span></div>
                            <div v-if="ev.meta_description" class="flex gap-2"><span class="text-muted/50">meta description:</span><span class="text-muted/70">{{ ev.meta_description }}</span></div>
                            <div v-if="ev.url_slug" class="flex gap-2"><span class="text-muted/50">URL slug:</span><span class="font-mono text-muted/70">{{ ev.url_slug }}</span></div>
                            <div v-if="ev.alt_text" class="flex gap-2"><span class="text-muted/50">alt text:</span><span class="text-muted/70">{{ ev.alt_text }}</span></div>
                            <div v-if="ev.latency_ms !== undefined" class="flex gap-2"><span class="text-muted/50">latency:</span><span class="text-muted/70">{{ ev.latency_ms }}ms</span></div>
                            <div v-if="ev.input_tokens !== undefined" class="flex gap-2"><span class="text-muted/50">input tokens:</span><span class="text-muted/70">{{ ev.input_tokens }}</span></div>
                            <div v-if="ev.output_tokens !== undefined" class="flex gap-2"><span class="text-muted/50">output tokens:</span><span class="text-muted/70">{{ ev.output_tokens }}</span></div>
                          </div>
                        </details>

                        <!-- agent_complete -->
                        <details v-else-if="ev.stage === 'agent_complete'" class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span>🏁 Complete</span>
                            <span v-if="ev.elapsed_seconds !== undefined" class="text-muted">{{ ev.elapsed_seconds }}s</span>
                            <span v-if="ev.message_count" class="text-muted">{{ ev.message_count }} msgs</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-if="ev.elapsed_seconds !== undefined" class="flex gap-2"><span class="text-muted/50">elapsed:</span><span class="text-muted/70">{{ ev.elapsed_seconds }}s</span></div>
                            <div v-if="ev.message_count" class="flex gap-2"><span class="text-muted/50">messages:</span><span class="text-muted/70">{{ ev.message_count }}</span></div>
                          </div>
                        </details>

                        <!-- fallback -->
                        <details v-else class="border border-dashed border-default/40 rounded px-2 py-1">
                          <summary class="flex items-center gap-2 cursor-pointer text-[10px] text-toned hover:text-default select-none">
                            <span class="text-muted/70">{{ ev.stage }}</span>
                          </summary>
                          <div class="mt-1 space-y-0.5 text-[10px] text-toned">
                            <div v-for="(val, k) in ev" :key="k" class="flex gap-2">
                              <span class="text-muted/50">{{ k }}:</span><span class="text-muted/70">{{ typeof val === 'object' ? JSON.stringify(val) : String(val) }}</span>
                            </div>
                          </div>
                        </details>
                      </template>
                    </div>
                  </details>
                </div>

                <!-- Suggestion cards -->
                <div
                  v-if="msg.suggestions && msg.suggestions.length && !isTemporaryMessage(msg)"
                  class="w-full mt-4 space-y-2"
                >
                  <p class="text-xs font-semibold text-muted/60 uppercase tracking-wide">Next steps</p>
                  <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <button
                      v-for="(s, si) in msg.suggestions"
                      :key="si"
                      type="button"
                      class="prompt-suggestion-card"
                      @click="runSuggestion(s.action, msg)"
                    >
                      <UIcon :name="s.icon || 'i-lucide-arrow-right'" class="size-5 text-primary" />
                      <span class="text-sm font-medium">{{ s.label }}</span>
                      <span class="text-xs text-muted/70">{{ s.description }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div ref="scrollBottomRef" class="h-px" aria-hidden="true" />
      </div>
      <div class="chat-composer-shell">
        <div class="mx-auto w-full max-w-3xl space-y-2">
          <form class="chat-composer-form" @submit.prevent="send">
            <UButton
              icon="i-lucide-wand-sparkles"
              variant="ghost"
              color="neutral"
              class="rounded-full"
              :disabled="isLoading"
              @click="input = 'Generate a logo prompt for: '"
            />
            <UButton
              icon="i-lucide-search"
              variant="ghost"
              color="neutral"
              class="rounded-full"
              :disabled="isLoading"
              @click="input = 'Generate SEO for: '"
            />
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

<style>
.seo-description h1 {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 1rem 0 0.5rem;
  line-height: 1.3;
}
.seo-description h2 {
  font-size: 1.05rem;
  font-weight: 700;
  margin: 0.75rem 0 0.375rem;
  line-height: 1.3;
}
.seo-description h3 {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0.5rem 0 0.25rem;
}
.seo-description p {
  margin: 0.375rem 0;
  line-height: 1.6;
}
.seo-description ul,
.seo-description ol {
  margin: 0.375rem 0;
  padding-left: 1.25rem;
}
.seo-description li {
  margin: 0.125rem 0;
}
.seo-description a {
  color: var(--color-primary-500);
  text-decoration: underline;
}
.seo-description strong {
  font-weight: 700;
}
</style>
