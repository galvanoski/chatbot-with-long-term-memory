<script setup lang="ts">
const chat = useGeekCatChat()
await chat.fetchThreads()

const { groups } = useChatGroups(chat.threads)
const recentItems = computed(() => {
  const all = groups.value.flatMap(group => group.items)
  return all.slice(0, 20)
})

const quickLinks = [
  { label: 'New chat', icon: 'i-lucide-square-pen', to: '/' },
  { label: 'Search chats', icon: 'i-lucide-search', to: '/' },
  { label: 'Library', icon: 'i-lucide-book-open', to: '/' },
  { label: 'Projects', icon: 'i-lucide-folder', to: '/' },
  { label: 'Apps', icon: 'i-lucide-layout-grid', to: '/' },
  { label: 'Codex', icon: 'i-lucide-cpu', to: '/' },
  { label: 'More', icon: 'i-lucide-ellipsis', to: '/' }
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
        <div class="left-recents">
          <NuxtLink
            v-for="item in recentItems"
            :key="item.id"
            :to="`/chat/${item.id}`"
            class="left-recent-item"
          >
            {{ item.label }}
          </NuxtLink>
        </div>
      </div>
    </aside>

    <main class="main-surface">
      <slot />
    </main>
  </div>
</template>
