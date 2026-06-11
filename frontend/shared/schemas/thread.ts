import { z } from 'zod'

export const tokenUsageSchema = z.object({
  input_tokens: z.number().int(),
  output_tokens: z.number().int(),
  model: z.string(),
  cost: z.number()
})

export const seoMetadataSchema = z.object({
  seo_title: z.string().optional(),
  focus_keyword: z.string().optional(),
  secondary_keywords: z.array(z.string()).optional(),
  meta_description: z.string().optional(),
  seo_description: z.string().optional(),
  url_slug: z.string().optional(),
  alt_text: z.string().optional(),
})

export const ragTraceEventSchema = z.object({
  stage: z.string(),
  timestamp: z.number().optional(),
  latency_ms: z.union([z.number(), z.string()]).optional(),
  docs: z.union([z.number(), z.string()]).optional(),
  query: z.string().optional(),
  status: z.string().optional(),
  tool: z.string().optional(),
  search_type: z.string().optional(),
  node: z.string().optional(),
  input_tokens: z.number().optional(),
  output_tokens: z.number().optional(),
  total_tokens: z.number().optional(),
  products: z.union([z.number(), z.array(z.object({
    sku: z.string(),
    name: z.string().optional(),
    category: z.string().optional(),
  }))]).optional(),
  memes: z.union([z.number(), z.array(z.object({
    text: z.string(),
  }))]).optional(),
  product_skus: z.array(z.string()).optional(),
  ltm_docs: z.number().optional(),
  brand_rules: z.array(z.string()).optional(),
  texts: z.array(z.string()).optional(),
  has_german: z.union([z.boolean(), z.string()]).optional(),
  hashtag_count: z.number().optional(),
  char_count: z.number().optional(),
  validation: z.record(z.string(), z.unknown()).optional(),
  elapsed_seconds: z.number().optional(),
  message_count: z.number().optional(),
  user_id: z.string().optional(),
  seo_title: z.string().optional(),
  focus_keyword: z.string().optional(),
  secondary_keywords: z.array(z.string()).optional(),
  meta_description: z.string().optional(),
  url_slug: z.string().optional(),
  alt_text: z.string().optional(),
}).catchall(z.unknown())

export const threadMessageSchema = z.object({
  id: z.string(),
  role: z.enum(['user', 'assistant', 'system']),
  content: z.string(),
  created_at: z.string(),
  pending_approval: z.boolean().optional(),
  rating: z.enum(['up', 'down']).nullish(),
  usage: tokenUsageSchema.nullish(),
  rag_trace: z.array(ragTraceEventSchema).nullish(),
  image_url: z.string().nullish(),
  seo_metadata: seoMetadataSchema.nullish(),
  is_image_prompt: z.boolean().optional(),
})

export const threadSourceSchema = z.object({
  label: z.string(),
  url: z.string().optional(),
  type: z.string().optional()
})

export const pendingCopySchema = z.object({
  content: z.string(),
  hashtags: z.array(z.string()),
  product_name: z.string().nullish(),
  product_url: z.string().nullish(),
  sources: z.array(threadSourceSchema).default([]),
  parts: z.record(z.string(), z.unknown()).optional()
})

export const threadListItemSchema = z.object({
  id: z.string(),
  title: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  status: z.string(),
  message_count: z.number().int()
})

export const threadDetailSchema = z.object({
  id: z.string(),
  user_id: z.string().optional(),
  title: z.string().nullable().default(null),
  created_at: z.string(),
  updated_at: z.string(),
  status: z.enum(['active', 'awaiting_approval', 'published']),
  messages: z.array(threadMessageSchema),
  pending_copy: pendingCopySchema.nullish()
})

export const threadStateSchema = z.object({
  status: z.enum(['active', 'awaiting_approval', 'published']),
  messages: z.array(threadMessageSchema),
  pending_copy: pendingCopySchema.nullish()
})

export const threadActionResponseSchema = z.object({
  title: z.string().nullable().optional(),
  status: z.enum(['active', 'awaiting_approval', 'published']),
  messages: z.array(threadMessageSchema),
  pending_copy: pendingCopySchema.nullish(),
})

export const threadListResponseSchema = z.array(threadListItemSchema)
