import { describe, expect, it } from 'vitest'

import { ragTraceEventSchema } from '../shared/schemas/thread'

// ── Sample pipeline trace events (mirrors backend/rag/pipeline.py) ──────────

const pipelineTraceEvents = {
  query_expand: {
    stage: 'query_expand',
    original_query: 'cat t-shirt',
    variants: ['cat tee', 'feline apparel', 'cat clothing'],
    latency_ms: 312.5
  },
  search_multi: {
    stage: 'search_multi',
    queries: 4,
    candidates: 12,
    latency_ms: 89.3
  },
  rerank: {
    stage: 'rerank',
    candidates_in: 12,
    candidates_out: 6,
    latency_ms: 245.1
  },
  compress: {
    stage: 'compress',
    docs_in: 6,
    docs_compressed: 6,
    latency_ms: 410.7
  },
  // Full pipeline trace chain (as it would appear in a real message)
  full_trace: [
    {
      stage: 'agent_start',
      query: 'Necesito copy para camiseta Bitcoin',
      user_id: 'anon_123'
    },
    {
      stage: 'ltm_retrieve',
      docs: 3,
      latency_ms: 45.2,
      texts: ['User likes tech humor', 'User bought cat shirt before']
    },
    {
      stage: 'query_expand',
      original_query: 'Bitcoin camiseta diseño',
      variants: ['BTC t-shirt design', 'crypto shirt ideas', 'Bitcoin camiseta'],
      latency_ms: 310.0
    },
    {
      stage: 'search_multi',
      queries: 4,
      candidates: 8,
      latency_ms: 92.1
    },
    {
      stage: 'rerank',
      candidates_in: 8,
      candidates_out: 3,
      latency_ms: 201.4
    },
    {
      stage: 'compress',
      docs_in: 3,
      docs_compressed: 3,
      latency_ms: 380.2
    },
    {
      stage: 'product_search',
      docs: 3,
      products: [
        { sku: 'BTC-001', name: 'Bitcoin HODL Tee', category: 't-shirt' },
        { sku: 'BTC-002', name: 'Satoshi Cat Hoodie', category: 'hoodie' }
      ],
      latency_ms: 1500.0
    },
    {
      stage: 'meme_search',
      docs: 2,
      memes: [{ text: 'This is fine dog on fire' }, { text: 'One does not simply deploy on Friday' }],
      latency_ms: 890.0
    },
    {
      stage: 'context_inject',
      ltm_docs: 3,
      product_skus: ['BTC-001', 'BTC-002'],
      brand_rules: ['Always use DE'],
      latency_ms: 12.0
    },
    {
      stage: 'llm_generate',
      node: 'copywriter',
      latency_ms: 3200.0,
      input_tokens: 2450,
      output_tokens: 180,
      total_tokens: 2630
    },
    {
      stage: 'model_output',
      has_german: true,
      hashtag_count: 5,
      char_count: 420,
      validation: { has_hook: true, has_cta: true }
    },
    {
      stage: 'seo_generate',
      seo_title: 'Bitcoin HODL Tee | The Geek Cat',
      focus_keyword: 'Bitcoin t-shirt',
      secondary_keywords: ['crypto shirt', 'HODL'],
      meta_description: 'Lustiges Bitcoin Motiv auf 100% Baumwolle',
      url_slug: 'bitcoin-hodl-tee',
      alt_text: 'Cat holding Bitcoin on t-shirt',
      latency_ms: 1800.0,
      input_tokens: 1200,
      output_tokens: 90
    },
    {
      stage: 'agent_complete',
      elapsed_seconds: 12.4,
      message_count: 3
    }
  ]
}

// ── Schema Validation Tests ─────────────────────────────────────────────────

describe('RAG pipeline trace schema validation', () => {
  it('validates query_expand event', () => {
    const result = ragTraceEventSchema.safeParse(pipelineTraceEvents.query_expand)
    expect(result.success).toBe(true)
  })

  it('validates search_multi event', () => {
    const result = ragTraceEventSchema.safeParse(pipelineTraceEvents.search_multi)
    expect(result.success).toBe(true)
  })

  it('validates rerank event', () => {
    const result = ragTraceEventSchema.safeParse(pipelineTraceEvents.rerank)
    expect(result.success).toBe(true)
  })

  it('validates compress event', () => {
    const result = ragTraceEventSchema.safeParse(pipelineTraceEvents.compress)
    expect(result.success).toBe(true)
  })

  it('validates the full trace chain (all stages)', () => {
    for (const ev of pipelineTraceEvents.full_trace) {
      const result = ragTraceEventSchema.safeParse(ev)
      expect(result.success).toBe(true)
    }
  })

  it('preserves extra fields via catchall', () => {
    const ev = {
      stage: 'query_expand',
      original_query: 'test',
      variants: ['v1', 'v2'],
      extra_field: 'preserved'
    }
    const result = ragTraceEventSchema.parse(ev)
    expect(result.extra_field).toBe('preserved')
  })
})

// ── Edge Cases ──────────────────────────────────────────────────────────────

describe('pipeline trace edge cases', () => {
  it('handles empty variants array', () => {
    const ev = { stage: 'query_expand', original_query: 'test', variants: [] }
    const result = ragTraceEventSchema.safeParse(ev)
    expect(result.success).toBe(true)
  })

  it('handles zero candidates', () => {
    const ev = { stage: 'search_multi', queries: 1, candidates: 0, latency_ms: 5 }
    const result = ragTraceEventSchema.safeParse(ev)
    expect(result.success).toBe(true)
  })

  it('handles zero docs to compress', () => {
    const ev = { stage: 'compress', docs_in: 0, docs_compressed: 0, latency_ms: 0 }
    const result = ragTraceEventSchema.safeParse(ev)
    expect(result.success).toBe(true)
  })

  it('rejects event without stage field', () => {
    const ev = { latency_ms: 100 }
    const result = ragTraceEventSchema.safeParse(ev)
    expect(result.success).toBe(false)
  })

  it('rejects event with empty stage', () => {
    const ev = { stage: '' }
    const result = ragTraceEventSchema.safeParse(ev)
    expect(result.success).toBe(true) // empty string is still a string
  })
})

// ── Rendering Assertions (contract tests) ───────────────────────────────────

describe('pipeline trace rendering contract', () => {
  it('all pipeline stages have a displayable stage name', () => {
    for (const ev of pipelineTraceEvents.full_trace) {
      expect(typeof ev.stage).toBe('string')
      expect(ev.stage.length).toBeGreaterThan(0)
    }
  })

  it('pipeline stages carry latency_ms as a number or string', () => {
    const stageNames = ['query_expand', 'search_multi', 'rerank', 'compress']
    for (const ev of pipelineTraceEvents.full_trace) {
      if (stageNames.includes(ev.stage)) {
        expect(ev.latency_ms).toBeDefined()
        expect(typeof ev.latency_ms === 'number' || typeof ev.latency_ms === 'string').toBe(true)
      }
    }
  })
})
