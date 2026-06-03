<script setup lang="ts">
const sidebarOpen = ref(false)

const chat = useGeekCatChat()
await chat.fetchThreads()

const { groups } = useChatGroups(chat.threads)

const items = computed(() => groups.value?.flatMap((group) => {
  return [{
    label: group.label,
    type: 'label' as const
  }, ...group.items.map(item => ({
    label: item.label,
    slot: 'chat' as const,
    to: `/chat/${item.id}`,
    icon: undefined,
    class: { 'text-muted': !item.title }
  }))]
}))

defineShortcuts({
  meta_o: () => navigateTo('/')
})
</script>

<template>
  <UDashboardGroup unit="rem">
    <UDashboardSidebar
      id="default"
      v-model:open="sidebarOpen"
      :min-size="12"
      collapsible
      resizable
      class="border-r-0 py-4 dark:[--ui-bg-elevated:var(--ui-color-neutral-900)]"
    >
      <template #header="{ collapsed }">
        <NuxtLink v-if="!collapsed" to="/" class="flex items-end gap-0.5">
          <span class="text-xl font-bold text-highlighted">The Geek Cat</span>
        </NuxtLink>
        <UDashboardSidebarCollapse class="ms-auto" />
      </template>

      <template #default="{ collapsed }">
        <UNavigationMenu
          :items="[{
            label: 'New chat',
            to: '/',
            kbds: ['meta', 'o'],
            icon: 'i-lucide-circle-plus'
          }]"
          :collapsed="collapsed"
          orientation="vertical"
        />

        <UNavigationMenu
          v-if="!collapsed"
          :items="items"
          :collapsed="collapsed"
          orientation="vertical"
        />
      </template>
    </UDashboardSidebar>

    <div class="flex-1 flex m-4 lg:ml-0 rounded-lg ring ring-default bg-default/75 shadow min-w-0 overflow-hidden">
      <slot />
    </div>
  </UDashboardGroup>
</template>
