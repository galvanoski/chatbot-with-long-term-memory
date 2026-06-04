# Marketing RAG + HITL Blueprint

## Objective
Raise business value by grounding generation in product sarcasm, brand examples, and campaign goals while keeping human control at high-impact points.

## What to vectorize (priority order)

1. Product catalog with sarcastic source lines (high impact)
- sku
- name
- category
- audience
- sarcastic_legend
- product_notes
- constraints (optional legal/compliance notes)

2. Brand voice examples (high impact)
- good examples
- rejected examples
- corrected versions from reviewer
- tags: sarcasm_level, platform, topic

3. Post-performance memory (medium/high impact)
- published copy
- platform
- KPI summary (ctr, saves, comments, conversions)
- audience segment

4. Marketing playbook snippets (medium impact)
- your own concise tactical rules by channel
- avoid large generic internet marketing dumps

## Product document schema (recommended)

Use this JSON for each vectorized product chunk:

```json
{
  "id": "sku-hoodie-hodl-tight",
  "text": "HODL TIGHT Hoodie. Sarkastische Aussage ueber Volatilitaet und Tech-Nerd-Coping.",
  "metadata": {
    "sku": "HODL-TIGHT-HOODIE",
    "name": "HODL TIGHT Hoodie",
    "category": "hoodie",
    "audience": "crypto engineers",
    "sarcastic_legend": "When markets crash, at least your hoodie is stable.",
    "platform_fit": ["instagram", "x"],
    "language": "de",
    "source": "catalog"
  }
}
```

## Brand example schema (recommended)

```json
{
  "id": "brand-example-001",
  "text": "Code compiles. Katze urteilt trotzdem.",
  "metadata": {
    "label": "good",
    "sarcasm_level": 8,
    "platform": "instagram",
    "topic": "dev-life",
    "language": "de",
    "source": "brand_examples"
  }
}
```

## Strategic HITL points

1. Brief stage (before generation)
- Human selects:
  - campaign objective
  - sarcasm intensity (1-10)
  - platform
  - audience segment

2. Retrieval stage
- Show top product context snippets and let human force one snippet if needed.

3. Draft stage
- Show structured draft:
  - hook
  - body
  - cta
  - hashtags
- Human can edit only a section instead of rewriting full text.

4. Publish gate
- Final approve/reject + reason
- Save reason as training signal for future retrieval and style ranking.

## Copywriter structured output contract (v1)

```json
{
  "hook": "string",
  "body": "string",
  "cta": "string",
  "hashtags": ["#tag1", "#tag2", "#tag3"],
  "style_notes": {
    "sarcasm_level": "1-10",
    "used_product_legend": true
  }
}
```

## Why generic marketing docs are optional

Generic marketing theory is low-signal versus your own product and brand examples.
Use short curated playbook notes, not large unspecific corpora.

## Immediate implementation checklist

1. Load product catalog with sarcastic_legend metadata.
2. Load 50-200 approved brand examples.
3. Capture reviewer feedback labels (good/needs-edit/reject).
4. Track KPI metadata for published posts.
5. Keep HITL for:
- brief choices
- final approval
- feedback labeling
