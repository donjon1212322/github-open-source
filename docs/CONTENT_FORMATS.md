# Content formats and tone by platform

Decision: **one canonical fact pack, native adaptation per surface**.
Gemini writes the facts once; deterministic adapters in
`src/ghos_v6/content_adapters.py` reshape them for each platform.
That gives consistent truth, different rhythm, no extra LLM calls.

Format-v2 principles from the design/growth review:

- **First five seconds:** every post answers *what is it, why care,
  and what is the proof* in the first screen.
- **Component layout:** no glued-together lines. Posts use a title
  block, divider, icon-led proof lines, labelled sections and short
  paragraphs.
- **Icons are scanning aids:** language/stack icons (for example
  TypeScript, Python, Rust, Go) never replace the written label.
  Numbers and section headings carry the visual emphasis.
- **Telegram is the hub:** external posts always include a return
  loop to the public Telegram post when available, otherwise to the
  public channel. Repositories are evidence, not the growth CTA.
- **Engage with our post:** the flagship rotates *React / Forward /
  Save + comment* asks. We never ask readers to star a third-party
  repository.
- **Truth and trust:** no fake first-person testing, no inherited
  hype, primary sources are linked in the body, and dual-use tools
  carry an authorized-use warning.

## Telegram project post — the flagship

- **Budget:** hard Bot API limit 4,096 visible chars; the formatter
  keeps a safety budget and trims extra features, a visible quick
  start, deep-dive sections and only then summary. Header, proof,
  engagement and links are protected.
- **Layout:**
  1. category icon + **name** + `owner/repo` path;
  2. bold hook on its own line;
  3. divider;
  4. proof line: bold stars and forks;
  5. stack line: language icon + bold language, license, latest
     commit activity; contributors on a third line when meaningful;
  6. dual-use warning and authorized-use boundary when needed;
  7. **What it does** in at most two short paragraphs;
  8. **Key features** with `▸` bullets (real `Term:` lead-ins are
     bolded, arbitrary first words are not);
  9. a short one-command **Quick start** is visible; longer commands
     move into the expandable deep dive;
  10. expandable **Who should try it / Why it matters now / Verdict**;
  11. divider, **Your take?**, a low-friction question and one
      rotating React / Forward / Save-comment CTA;
  12. GitHub, Telegraph and homepage links, each on its own icon-led
      line;
  13. at most four hashtags: language and cleaned broad category
      first, then searchable topics. Evasion terms (`antidetect`,
      `undetected`, `stealth`, `bypass`) are never used as hashtags.
- **Links:** inline body links survive forwards; buttons remain the
  convenient tap targets.

## Telegraph full breakdown — the deep dive

- **Layout:** bold standfirst → **Repository snapshot** bullet list
  → small Telegram follow line → H2 article (*What it is / Why it
  matters / Key features / Quick start / Who should try it /
  Verdict*) → **Continue exploring** → Telegram CTA → transparency
  note.
- **Tone:** calm reference article for Instant View, not a search
  landing page.

## dev.to article of the day — the search surface

- **Title:** a good generated article title is preferred so acronyms
  such as `REA` survive; otherwise `Project: main task/use case`,
  search phrasing, no clickbait, about 125 characters maximum.
- **Layout:** TL;DR + Best for + repository snapshot + a Telegram
  daily-picks line in the first screen → article → Discuss section
  with the Telegram discussion/follow link first, then GitHub and the
  Telegraph breakdown, plus a no-hands-on-test disclosure.
- **Tags:** maximum 4, `opensource` first, then language and cleaned
  broad categories (for example `security`, `ai`, `devtools`).
- **SEO decision:** no `canonical_url` to Telegraph. The dev.to
  version has its own TL;DR/discussion wrapper and remains the
  indexable public article; Telegraph serves Telegram readers.
- **Series:** `Open Source Finds`.

## Bluesky — one idea plus the loop

- **Budget:** 300 characters, enforced in code.
- **Layout:** language icon + `Name — concrete benefit` → one
  complete supporting detail → GitHub URL on its own line →
  `Daily picks:` or `Discuss:` Telegram URL. A single `#OpenSource`
  is added only if it does not crowd out the Telegram CTA.
- **Tone:** native and terse; truncation never chops a word and then
  invents sentence punctuation.
- **Accessibility:** clickable link and hashtag facets with UTF-8
  byte offsets; meaningful image alt text.

## Facebook Page — structured plain text

- **Budget:** editorial target up to about 1,800 characters; the API
  allows far more, but readability wins.
- **Layout:** language icon + title → icon proof line → heavy
  divider → labelled sections (**WHAT IT DOES**, **WHAT STANDS
  OUT**, **WHY IT MATTERS**, **GOOD FIT FOR**, **QUICK START**) →
  divider → question → Telegram return CTA → GitHub/breakdown links
  → at most three hashtags.
- **Fitting rule:** the final question/CTA/link block is protected;
  body copy is shortened first.

## AI Brief (Telegram) — verified news

- **Budget:** about 1,400–2,200 visible characters.
- **Visual:** Glass Aurora is reserved for this track only; if the
  upload fails, the YouTube preview is the fallback.
- **Layout:** headline → divider → **What happened** → **Why
  developers should care** → **Confirmed vs. video-only** → divider
  → linked primary source and WorldofAI credit in the body (so trust
  survives forwards without buttons) → **Your take?** → reaction CTA
  → `#AINews #OpenSource`.
- **Tone:** wire-service calm. Uncertainty is labelled, never
  smoothed over; rumors and sponsored items do not publish.

## Sunday digest + poll

- **Visual:** Editorial Daily “Sunday Edition” card.
- **Layout:** numbered Top 5; title on one line, stars + hook on the
  next, blank line between entries → divider → forward CTA → native
  poll containing **all five** projects as options.
- **Tone:** weekly newspaper recap, celebratory but factual.

## Editorial generation rules

The Gemini prompt now requires the summary to add context beyond the
hook, each key feature to add a distinct capability/evidence point,
the category to be one short broad label, and the discussion
question to be answerable in one line (a concrete use case or an
either/or choice).

## Measurement after launch

Format is a funnel, not decoration. Once public posting begins,
compare per-post reactions, comments, saves/forwards and Telegram
joins by CTA variant and by referring surface. Keep the variant only
if it improves those outcomes without reducing link clicks or trust.

## Card copy rule

The cover card carries three text blocks and none of them may be cut
with "…":

| Block | Source field | Limit | Rule |
|---|---|---|---|
| Project name | `name` | auto-fit | serif, shrinks to fit width |
| Headline | `card_hook` | 60 chars | one complete thought, no trailing punctuation |
| Subline | `card_subline` | 130 chars | who it is for / what it removes |

The renderer shrinks type before it drops words, and if copy is still
too long it cuts on a comma/sentence boundary. The old behaviour —
wrap two lines, glue "…" — produced headlines such as *"that replaces
Chrome for AI agents and…"*, which reads like an unfinished sentence
and is exactly what a reader scrolling a feed skips.

## Post structure rule

After the bold hook the Telegram post answers "why should I care
today?" with one line (`why_now`), then the proof meta line, then
What it does. A reader who stops after three lines still learns what
the project is, why it matters now, and that it is real.

## Media hosting rule

Visual cascade stays: a valid large README demo (at least 800×250)
→ locally rendered branded card → GitHub OpenGraph only as the final
fallback. Rendered cards are hosted first on **ImgBB** (`IMGBB_API_KEY`) because
it serves a direct `image/png` URL that Telegram previews and Telegraph
`<img>` tags accept; the second attempt is a monthly GitHub
**prerelease** in this repository (for example `media-2026-11`) using
the workflow token; the legacy `telegra.ph/upload` endpoint is a last
attempt only because it can return HTTP 400 `Unknown error` for
external clients. If all hosts fail and OpenGraph is used, state
records `visual_kind=opengraph` honestly and a `card_host_failed`
event is written. The winning host is stored as `visual_host`.

Hosting lifetime matters less than it looks: Telegram and Bluesky
copy the image into their own storage at post time, and dev.to
downloads the cover. Only the Telegraph article hot-links the URL, so a
later ImgBB deletion/expiry would break the image in that article
alone — never the Telegram or social posts.

## Alt text rule

Alt text is generated from facts, never from guessing pixels:
image kind (README demo / branded card / GitHub preview) + repository
+ one-line purpose + language/license/stars when known.

## Private-link rule

Public social copy never includes private test-channel `t.me/c/...`
links. If a public Telegram post link exists, adapters use it for
discussion; otherwise they use `https://t.me/GitHub_Open_Source`.
