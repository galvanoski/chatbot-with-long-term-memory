import { z } from 'zod'

export const threadMessageSchema = z.object({
  id: z.string(),
  role: z.enum(['user', 'assistant', 'system']),
  content: z.string(),
  created_at: z.string(),
  pending_approval: z.boolean().optional()
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
  parts: z.object({
    hook: z.string().optional(),
    body: z.string().optional(),
    cta: z.string().optional()
  }).optional()
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
  pending_copy: pendingCopySchema.optional()
})

export const threadStateSchema = z.object({
  status: z.enum(['active', 'awaiting_approval', 'published']),
  messages: z.array(threadMessageSchema),
  pending_copy: pendingCopySchema.optional()
})

export const threadActionResponseSchema = z.object({
  title: z.string().nullable().optional(),
  status: z.enum(['active', 'awaiting_approval', 'published']),
  messages: z.array(threadMessageSchema),
  pending_copy: pendingCopySchema.optional()
})

export const threadListResponseSchema = z.array(threadListItemSchema)
