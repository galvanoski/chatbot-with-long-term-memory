<script setup lang="ts">
const input = ref('')
const loading = ref(false)
const isMounted = ref(false)

const chat = useGeekCatChat()

onMounted(() => {
  isMounted.value = true
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'Guten Morgen'
  if (hour < 18) return 'Guten Tag'
  return 'Guten Abend'
})

const workflowSteps = [
  '1) Waehle einen der Chips unten oder schreibe einen eigenen Prompt.',
  '2) Pruefe den KI-Entwurf im Chat.',
  '3) Bearbeite Hook/Body/CTA und gib zur Veroeffentlichung frei.'
]

async function onSubmit() {
  if (!input.value.trim() || loading.value) return
  const prompt = input.value
  input.value = ''
  loading.value = true

  try {
    const id = await chat.createThread()
    if (id) {
      await navigateTo({
        path: `/chat/${id}`,
        query: { prompt }
      })
    }
  } finally {
    loading.value = false
  }
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
    <span v-if="isMounted" data-testid="home-mounted" class="hidden" />

    <div class="text-center">
      <h1 class="text-3xl sm:text-4xl text-highlighted font-bold">
        {{ greeting }}
      </h1>
      <p class="text-muted mt-2">The Geek Cat - KI-Generator fuer sarkastische Marketing-Posts</p>
    </div>

    <div class="w-full max-w-2xl">
      <UChatPrompt
        v-model="input"
        data-testid="home-prompt"
        :status="loading ? 'streaming' : 'ready'"
        placeholder="Beispiel: Promote den HODL TIGHT Hoodie fuer Blockchain Engineers"
        class="[view-transition-name:chat-prompt]"
        variant="subtle"
        @submit="onSubmit"
      >
        <template #footer>
          <div class="flex items-center gap-1" />
          <UChatPromptSubmit data-testid="home-submit" :on-click="onSubmit" color="neutral" size="sm" />
        </template>
      </UChatPrompt>

      <UCard variant="soft" class="mt-4">
        <template #header>
          <p class="text-sm font-semibold">So funktioniert es</p>
        </template>
        <ul class="space-y-1 text-sm text-toned">
          <li v-for="step in workflowSteps" :key="step">{{ step }}</li>
        </ul>
      </UCard>
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
