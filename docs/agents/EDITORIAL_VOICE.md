# Editorial voice — what the agents actually prescribe

Sources (read before changing the prompt):
`agency-agents/marketing/marketing-linkedin-content-creator.md`,
`marketing/marketing-content-creator.md`,
`marketing/marketing-twitter-engager.md`,
`academic/academic-narratologist.md`,
`design/design-visual-storyteller.md`.

## Rules we borrow, and what each one changes in the pipeline

| Agent rule | Where it lives now |
|---|---|
| "Hook in the first line… nothing else matters if this fails" (LinkedIn Creator) | `hook` must name something concrete — a number, a platform, a cost — so it could only be about this project |
| "Specificity over inspiration… concrete stories, real numbers" (LinkedIn Creator) | `key_features` prefer the detail a developer repeats the next day ("100+ databases in a 25 MB binary") |
| "Every post must have a defensible point of view" (LinkedIn Creator) | `verdict` is a stance: who it fits, who should skip it |
| "Story arc: beginning, middle, end with natural flow" (Twitter Engager) | `scene` → `tension` → `turn`, rendered as three paragraphs before the spec block |
| "Clear narrative structure (beginning, middle, end)" (Visual Storyteller) | the card headline states one complete thought, never a truncated phrase |
| Three-act / Kishōtenketsu (Narratologist) | the post opens with a situation the reader recognises, not with a product name |
| "Never generic advice: be specific" (Narratologist) | ban list in the prompt: revolutionary, seamless, game-changing, unleash, empower |

## Why the long post came back

Telegram caps a **photo caption at 1024 characters**. Publishing the card as a
photo silently deletes the story — the reader gets a spec sheet, not a piece.
The channel format is therefore:

1. **the long post is the message** (`format_project_post`, up to 4000 visible UTF-16 units),
2. **the card rides above it as a large link preview** — `link_preview_options.prefer_large_media = true`, `show_above_text = true`,
3. the card image is hosted on **ImgBB** (the owner's requirement). Measured on the live channel: GitHub Release assets answer `application/octet-stream` with `content-disposition: attachment`, and Telegram silently drops the preview — that is why earlier posts had no picture. `raw.githubusercontent` does serve `image/jpeg`, but using it as the primary host would mean committing every card.
4. the preview upload is a **miniature web JPEG** (~70–90 KB, 1200×630): crawlers time out on heavy PNGs even when the content type is right.

If a caption-only variant is ever needed again, `format_project_caption` still
exists and is the only correct way to publish a photo post.

## What a story block may not do

- invent fieldwork ("I ran this for a week"),
- invent benchmarks, funding or adoption,
- repeat the hook, the pain, or the feature list,
- end a sentence with an ellipsis.
