<script setup lang="ts">
const input = ref('')
const loading = ref(false)

const chat = useGeekCatChat()
const chatError = computed(() => chat.error.value)


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

  try {
    const id = await chat.createThread()
    if (id) {
      const isImagePrompt = prompt.startsWith('Generate a logo prompt')
      const isSEO = prompt.startsWith('Generate SEO')
      const query = isImagePrompt ? { imagePrompt: prompt } : isSEO ? { seo: prompt } : { prompt }
      await navigateTo({
        path: `/chat/${id}`,
        query
      })
    }
  } finally {
    loading.value = false
  }
}

const suggestionsSocial = [
  { label: 'Erstelle einen Post für ein Bitcoin T-Shirt', icon: 'i-lucide-bitcoin' },
  { label: 'Schreib einen sarkastischen IT-Meme-Post', icon: 'i-lucide-terminal' },
  { label: 'Promote den HODL TIGHT Hoodie', icon: 'i-lucide-shirt' },
  { label: 'Witz über Kubernetes und Katzen', icon: 'i-lucide-cloud' },
]

const suggestionsProduct = [
  { label: 'Generiere ein Logo: programmer cat', icon: 'i-lucide-wand-sparkles' },
  { label: 'Generiere ein Bild: Katze auf Tastatur', icon: 'i-lucide-image' },
  { label: 'SEO für: Debugging Katze Mug', icon: 'i-lucide-search' },
]
</script>

<template>
  <div class="flex-1 flex flex-col items-center justify-center gap-6 p-8">
    <UAlert
      v-if="chatError"
      class="w-full max-w-2xl"
      color="warning"
      variant="soft"
      icon="i-lucide-triangle-alert"
      :title="chatError"
    />

    <div class="text-center">
      <h1 class="text-3xl sm:text-4xl text-highlighted font-bold">
        {{ greeting }}
      </h1>
      <p class="text-muted mt-2">The Geek Cat — KI-Generator für sarkastische Marketing-Posts &amp; Produktbilder</p>
    </div>

    <div class="w-full max-w-2xl">
      <UChatPrompt
        v-model="input"
        data-testid="home-prompt"
        :status="loading ? 'streaming' : 'ready'"
        :disabled="loading"
        placeholder="z.B. Promote den HODL TIGHT Hoodie für Blockchain Engineers"
        class="[view-transition-name:chat-prompt]"
        variant="subtle"
        @submit="onSubmit"
      >
        <template #footer>
          <div class="flex items-center gap-1" />
          <UChatPromptSubmit data-testid="home-submit" :on-click="onSubmit" color="neutral" size="sm" />
        </template>
      </UChatPrompt>

      <div class="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <UCard variant="soft">
          <template #header>
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-megaphone" class="size-4 text-primary" />
              <p class="text-sm font-semibold">Flow A — Social Media Post</p>
            </div>
          </template>
          <ol class="space-y-1 text-sm text-toned list-decimal list-inside">
            <li>Post-Idee eingeben (oder Chip klicken)</li>
            <li>Copywriter erstellt deutschen Post mit Hook + Body + CTA</li>
            <li>Optional: Bild zum Post generieren (Button 🖼)</li>
            <li>Post genehmigen &amp; veröffentlichen</li>
          </ol>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <UButton
              v-for="s in suggestionsSocial"
              :key="s.label"
              :icon="s.icon"
              :label="s.label"
              size="xs"
              color="neutral"
              variant="outline"
              class="rounded-full"
              @click="input = s.label"
            />
          </div>
        </UCard>

        <UCard variant="soft">
          <template #header>
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-shirt" class="size-4 text-primary" />
              <p class="text-sm font-semibold">Flow B — Produkt &amp; Merch</p>
            </div>
          </template>
          <ol class="space-y-1 text-sm text-toned list-decimal list-inside">
            <li>Bild-Idee eingeben (oder Chip klicken)</li>
            <li>Image Prompt Generator erstellt detailierten Prompt</li>
            <li>Riverflow V2.5 generiert das Produktbild</li>
            <li>SEO-Metadaten für Shop (Titel, Keywords, Slug)</li>
            <li>Optional: Copywriter erstellt passenden Social Post</li>
          </ol>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <UButton
              v-for="s in suggestionsProduct"
              :key="s.label"
              :icon="s.icon"
              :label="s.label"
              size="xs"
              color="neutral"
              variant="outline"
              class="rounded-full"
              @click="input = s.label"
            />
          </div>
        </UCard>
      </div>
    </div>
  </div>
</template>
