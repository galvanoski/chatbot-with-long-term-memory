export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  pending_approval?: boolean
  rating?: 'up' | 'down' | null
  usage?: {
    input_tokens: number
    output_tokens: number
    model: string
    cost: number
  }
  rag_trace?: Array<Record<string, unknown>> | null
  image_url?: string | null
  is_image_prompt?: boolean
  seo_metadata?: {
    seo_title?: string
    focus_keyword?: string
    secondary_keywords?: string[]
    meta_description?: string
    seo_description?: string
    url_slug?: string
    alt_text?: string
  } | null
  suggestions?: Array<{
    action: string
    label: string
    icon: string
    description: string
  }> | null
}

export interface UIMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  name: string
  content: string
  parts: Array<{
    type: 'text'
    text: string
  }>
  created_at: string
  pending_approval?: boolean
  rating?: 'up' | 'down' | null
  usage?: {
    input_tokens: number
    output_tokens: number
    model: string
    cost: number
  }
  rag_trace?: Array<Record<string, unknown>> | null
  image_url?: string | null
  is_image_prompt?: boolean
  seo_metadata?: {
    seo_title?: string
    focus_keyword?: string
    secondary_keywords?: string[]
    meta_description?: string
    seo_description?: string
    url_slug?: string
    alt_text?: string
  } | null
  suggestions?: Array<{
    action: string
    label: string
    icon: string
    description: string
  }> | null
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
    product_name?: string | null
    product_url?: string | null
    sources?: Array<{
      label: string
      url?: string
      type?: string
    }>
    parts?: Record<string, unknown>
  } | null
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
    product_name?: string | null
    product_url?: string | null
    sources?: Array<{
      label: string
      url?: string
      type?: string
    }>
    parts?: Record<string, unknown>
  } | null
  messages: Message[]
}

export interface SendMessageResponse {
  title?: string
  status: 'awaiting_approval' | 'published' | 'active'
  messages: Message[]
  pending_copy?: {
    content: string
    hashtags: string[]
    product_name?: string | null
    product_url?: string | null
    sources?: Array<{
      label: string
      url?: string
      type?: string
    }>
    parts?: Record<string, unknown>
  } | null
}
