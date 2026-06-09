<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'

const chat = useGeekCatChat()
await chat.fetchThreads()
const route = useRoute()
const searchQuery = ref('')

const { groups } = useChatGroups(chat.threads)
const recentItems = computed(() => {
  const all = groups.value.flatMap(group => group.items)
  return all.slice(0, 20)
})

const activeThreadId = computed(() => route.params.id as string | undefined)

const runSearch = useDebounceFn(async () => {
  await chat.fetchThreads(searchQuery.value)
}, 220)

watch(searchQuery, () => {
  runSearch()
})

async function removeThread(threadId: string) {
  if (import.meta.client) {
    const confirmed = window.confirm('Delete this conversation?')
    if (!confirmed) return
  }

  const deleted = await chat.deleteThread(threadId)
  if (!deleted) return

  if (activeThreadId.value === threadId) {
    await navigateTo('/')
  }

  if (searchQuery.value.trim()) {
    await chat.fetchThreads(searchQuery.value)
  }
}

const quickLinks = [
  { label: 'New chat', icon: 'i-lucide-square-pen', to: '/' },
]

defineShortcuts({
  meta_o: () => navigateTo('/')
})
</script>

<template>
  <div class="app-shell">
    <aside class="left-rail">
      <div class="left-brand">The Geek Cat</div>

      <nav class="left-nav">
        <NuxtLink
          v-for="item in quickLinks"
          :key="item.label"
          :to="item.to"
          class="left-nav-item"
        >
          <UIcon :name="item.icon" class="size-4" />
          <span>{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <div class="left-section">
        <p class="left-section-title">Recents</p>
        <div class="left-search-wrap">
          <UInput
            v-model="searchQuery"
            icon="i-lucide-search"
            size="sm"
            placeholder="Search chats"
            class="left-search-input"
          />
        </div>
        <div class="left-recents">
          <div
            v-for="item in recentItems"
            :key="item.id"
            class="left-recent-row"
          >
            <NuxtLink
              :to="`/chat/${item.id}`"
              class="left-recent-item"
              :class="item.id === activeThreadId ? 'left-recent-item-active' : ''"
            >
              {{ item.label }}
            </NuxtLink>
            <UButton
              class="left-recent-delete"
              size="xs"
              color="neutral"
              variant="ghost"
              icon="i-lucide-trash-2"
              @click="removeThread(item.id)"
            />
          </div>
        </div>
      </div>
    </aside>

    <main class="main-surface">
      <slot />
    </main>
  </div>
</template>
