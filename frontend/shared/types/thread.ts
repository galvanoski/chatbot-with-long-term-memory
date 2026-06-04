export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  pending_approval?: boolean
}

export interface Thread {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  status: 'active' | 'awaiting_approval' | 'published'
  messages: Message[]
  pending_copy?: {
    content: string
    hashtags: string[]
    product_name?: string
    product_url?: string
    parts?: {
      hook?: string
      body?: string
      cta?: string
    }
  }
}

export interface ThreadListItem {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  status: string
  message_count: number
}

export interface ThreadState {
  status: 'active' | 'awaiting_approval' | 'published'
  pending_copy?: {
    content: string
    hashtags: string[]
    product_name?: string
    product_url?: string
    parts?: {
      hook?: string
      body?: string
      cta?: string
    }
  }
  messages: Message[]
}

export interface SendMessageResponse {
  title?: string
  status: 'awaiting_approval' | 'published' | 'active'
  messages: Message[]
  pending_copy?: {
    content: string
    hashtags: string[]
    product_name?: string
    product_url?: string
    parts?: {
      hook?: string
      body?: string
      cta?: string
    }
  }
}
