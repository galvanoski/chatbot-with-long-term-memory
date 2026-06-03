import type { ThreadListItem } from '#shared/types/thread'
import { isToday, isYesterday, isThisWeek, isSameMonth } from 'date-fns'

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

    const today: ChatGroup['items'] = []
    const yesterday: ChatGroup['items'] = []
    const thisWeek: ChatGroup['items'] = []
    const earlierThisMonth: ChatGroup['items'] = []
    const older: ChatGroup['items'] = []

    for (const t of threads.value) {
      const item = {
        id: t.id,
        label: t.title || 'Untitled',
        title: t.title,
        to: `/chat/${t.id}`
      }
      const date = new Date(t.updated_at)
      if (isToday(date)) today.push(item)
      else if (isYesterday(date)) yesterday.push(item)
      else if (isThisWeek(date, { weekStartsOn: 1 })) thisWeek.push(item)
      else if (isSameMonth(date, new Date())) earlierThisMonth.push(item)
      else older.push(item)
    }

    const result: ChatGroup[] = []
    if (today.length) result.push({ id: 'today', label: 'Today', items: today })
    if (yesterday.length) result.push({ id: 'yesterday', label: 'Yesterday', items: yesterday })
    if (thisWeek.length) result.push({ id: 'this-week', label: 'This Week', items: thisWeek })
    if (earlierThisMonth.length) result.push({ id: 'earlier', label: 'Earlier this Month', items: earlierThisMonth })
    if (older.length) result.push({ id: 'older', label: 'Older', items: older })

    return result
  })

  return { groups }
}
