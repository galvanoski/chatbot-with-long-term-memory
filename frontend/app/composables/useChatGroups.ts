import type { ThreadListItem } from '#shared/types/thread'
import { format, isToday, isYesterday } from 'date-fns'
import { de } from 'date-fns/locale'

interface ChatGroup {
  id: string
  label: string
  items: Array<{
    id: string
    label: string
    title: string | null
    to: string
  }>
}

export function useChatGroups(threads: Ref<ThreadListItem[]>) {
  const groups = computed<ChatGroup[]>(() => {
    if (!threads.value?.length) return []

    const sortedThreads = [...threads.value].sort((a, b) => {
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

    const grouped = new Map<string, ChatGroup>()

    for (const t of sortedThreads) {
      const date = new Date(t.updated_at)
      const dateKey = format(date, 'yyyy-MM-dd')
      const timeLabel = format(date, 'HH:mm')
      const item = {
        id: t.id,
        label: (t.title && t.title.trim()) || `Konversation ${timeLabel}`,
        title: t.title,
        to: `/chat/${t.id}`
      }

      if (!grouped.has(dateKey)) {
        const label = isToday(date)
          ? 'Heute'
          : isYesterday(date)
            ? 'Gestern'
            : format(date, 'd. MMMM yyyy', { locale: de })
        grouped.set(dateKey, {
          id: dateKey,
          label,
          items: []
        })
      }

      grouped.get(dateKey)!.items.push(item)
    }

    return Array.from(grouped.values())
  })

  return { groups }
}
