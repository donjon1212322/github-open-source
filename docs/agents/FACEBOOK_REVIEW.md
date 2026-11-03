# Panel: Facebook post design — audit of the live format

Personas imported from `agency-agents`:
`marketing/marketing-social-media-strategist`, `marketing/marketing-content-creator`,
`paid-media/paid-media-paid-social-strategist` (paid is only consulted for
the "does this look like an ad" question), `design/design-brand-guardian`.

Audited input: 4 real posts from `github-projects-bot/data/facebook_posts.json`
(REEL-GENERATOR, SEEKER, ROXUM-IDE, NOTE-GEN) and the template that produced
them (`data/templates/facebook_template.md`). Distribution is
Google Sheets → make.com → Facebook Page.

## What the current format does well

1. **Scannable skeleton.** Name | one-line purpose, then a divider, then
   labelled blocks. A reader gets the value in one glance.
2. **Emoji bullets as icons.** Cheap, renders everywhere, no upload step.
3. **Stats are concrete.** "⭐ 9 stars", "🍴 1 forks" — the numbers are
   visible instead of hidden behind a link.
4. **Closing CTA is consistent.** "Follow us for daily GitHub projects" plus
   a link to the channel.

## What is broken

| Problem | Evidence | Why it matters |
|---|---|---|
| **Faux-bold via Unicode** | `𝗥𝗘𝗘𝗟-𝗚𝗘𝗡𝗘𝗥𝗔𝗧𝗢𝗥`, `𝗧𝗘𝗖𝗛 𝗦𝗧𝗔𝗖𝗞` | Copies into screen readers and search as garbage; some Android fonts render tofu; Facebook search cannot index it |
| **Divider spam** | 6 dividers per post, some 30 characters long | Turns a post into a form. Facebook's feed already separates paragraphs |
| **Duplicate opener** | Name + purpose, then "USE CASE" repeats the same purpose | The 1 912-char post spends ~200 chars saying the same thing twice |
| **Redundant ABOUT block** | `readme_summary` after features and stats | Four sections where two would do |
| **"KEY FEATURES" 7 bullets** | REEL-GENERATOR lists 7 | Nobody reads the 7th; it pushes the link below the "see more" fold |
| **Generic filler CTA** | "🔥 FOLLOW US for daily GitHub projects!" | Same sentence on every post trains the reader to skip it |
| **No visual identity** | Image is either `opengraph.githubassets.com` (a plain GitHub card) or a README banner | On Facebook the image is the first thing judged; ours looks like an auto-generated link |
| **0 open issues shown** | "🔍 0 open issues" | Reads as a bug report, not a selling point |
| **Length** | 1 548–1 922 chars | Facebook hides everything after ~3 lines; the good part is below the fold |

## Decisions

1. **Drop Unicode faux-bold entirely.** Use plain caps or nothing; the card
   carries the branding, the text carries the information.
2. **One divider maximum**, and only between the promise and the detail.
3. **Cap features at 4**, never repeat the description in two sections.
4. **First 125 characters must sell.** That is roughly what Facebook shows
   before "See more". Structure: `Name — hook` + one proof line.
5. **Attach the branded card** we already render for Telegram (editorial or
   terminal, 1200×630 = Facebook's preferred link size). Same renderer, zero
   new design work, and it makes the Page look like a magazine rather than a
   scraper.
6. **Replace the generic CTA** with the project-specific discussion question
   we already generate, plus the channel link as a quiet last line.
7. **Never print a zero stat.** Drop `open issues` unless it is ≥ 5.

## What the current format keeps

- Emoji as bullet markers (they render on every device, unlike the Unicode
  bold block).
- The labelled-block skeleton — it is genuinely scannable.
- The Telegram return loop, but moved to the last line.
