import { describe, expect, it } from 'vitest'

import { transformMessages } from '../app/composables/useGeekCatChat'

describe('transformMessages', () => {
  it('maps backend messages into Nuxt UI chat messages', () => {
    const result = transformMessages([
      {
        id: '1',
        role: 'user',
        content: 'Hola',
        created_at: '2026-06-03T10:00:00Z'
      },
      {
        id: '2',
        role: 'assistant',
        content: 'Respuesta',
        created_at: '2026-06-03T10:00:01Z'
      }
    ])

    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({
      id: '1',
      role: 'user',
      name: 'Du',
      parts: [{ type: 'text', text: 'Hola' }]
    })
    expect(result[1]).toMatchObject({
      id: '2',
      role: 'assistant',
      name: 'The Geek Cat',
      parts: [{ type: 'text', text: 'Respuesta' }]
    })
  })

  it('drops nullish messages safely', () => {
    const result = transformMessages([
      null,
      undefined,
      {
        id: '3',
        role: 'assistant',
        content: '',
        created_at: '2026-06-03T10:00:02Z'
      }
    ] as any)

    expect(result).toHaveLength(1)
    expect(result[0].parts).toEqual([{ type: 'text', text: '' }])
  })
})