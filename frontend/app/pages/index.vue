<script setup lang="ts">
const input = ref('')
const loading = ref(false)

const chat = useGeekCatChat()

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'Guten Morgen'
  if (hour < 18) return 'Guten Tag'
  return 'Guten Abend'
})

async function onSubmit() {
  if (!input.value.trim() || loading.value) return
  const prompt = input.value
  input.value = ''
  loading.value = true

  const id = await chat.createThread()
  if (id) {
    const ok = await chat.sendMessage(prompt)
    if (ok) {
      navigateTo(`/chat/${id}`)
    }
  }
  loading.value = false
}

const suggestions = [
  { label: 'Erstelle einen Post für ein Bitcoin T-Shirt', icon: 'i-lucide-bitcoin' },
  { label: 'Schreib einen sarkastischen IT-Meme-Post', icon: 'i-lucide-terminal' },
  { label: 'Promote den HODL TIGHT Hoodie', icon: 'i-lucide-shirt' },
  { label: 'Erstelle einen Witz über Kubernetes und Katzen', icon: 'i-lucide-cloud' }
]
</script>

<template>
  <div class="flex-1 flex flex-col items-center justify-center gap-6 p-8">
    <div class="text-center">
      <h1 class="text-3xl sm:text-4xl text-highlighted font-bold">
        {{ greeting }}
      </h1>
      <p class="text-muted mt-2">The Geek Cat — Marketing Copy Generator</p>
    </div>

    <div class="w-full max-w-2xl">
      <UChatPrompt
        v-model="input"
        :status="loading ? 'streaming' : 'ready'"
        placeholder="Sag mir, welchen Content du brauchst..."
        class="[view-transition-name:chat-prompt]"
        variant="subtle"
        @submit="onSubmit"
      >
        <template #footer>
          <div class="flex items-center gap-1" />
          <UChatPromptSubmit color="neutral" size="sm" />
        </template>
      </UChatPrompt>
    </div>

    <div class="flex flex-wrap gap-2 justify-center max-w-xl">
      <UButton
        v-for="s in suggestions"
        :key="s.label"
        :icon="s.icon"
        :label="s.label"
        size="sm"
        color="neutral"
        variant="outline"
        class="rounded-full"
        @click="input = s.label"
      />
    </div>
  </div>
</template>
