# Value system — how we decide what worked, and what to post next

Three subsystems, deliberately small and auditable. Nothing here calls an
LLM: a ranking weight that changes silently is worse than no ranking.

## 1. Anti-duplication (`src/ghos_v6/dedup.py`)

Runs inside `publish_slot` before the gate, on the enriched candidate.

| Layer | Catches | Cost |
|---|---|---|
| Repo key (`full_name`, else `id:`) | The same repo twice | O(1) |
| Description shingles (word bigrams, containment ≥ 0.55) | A renamed fork or a repost | O(n) |
| README fingerprint (first 400 words, badges stripped) | A copy-paste README under a new name | O(n) |
| Milestone check | Re-visits | O(1) |

A re-visit is allowed **only after 90 days** *and* **only** with a measurable
event: stars grew ≥ 50 %, or a major release tag appeared. The record then
carries `revisit_of` and `revisit_reason`, so the post is an update, not a
repeat.

Owner decision: re-visits are milestone-only.

## 2. Measurement (`src/ghos_v6/insights.py`)

Telegram gives a bot no analytics. There are exactly two free signals:

**Views** come from the public embed widget
`https://t.me/<channel>/<id>?embed=1&mode=tme`. Public post views are read from the channel embed widget.

**Votes** come from `stopPoll` on a poll the bot itself created, before it
expires. Telegram caps polls at 10 days and cannot create them
retroactively — which is why the weekly poll must be opened on schedule.

**Clicks** are not measured — and not because they are hard to get. Measuring
them means routing every GitHub link through a domain we own, so the reader
sees `go.example.dev/r/3ec69c85` instead of `github.com/owner/repo`. The
owner rejected that trade (the current release). The score drops the term entirely
rather than pretending to know it.

### Age normalisation — the part that matters

Views accumulate for about a week. Measured on the live channel:

| Age | Views/hour | Cumulative |
|---|---|---|
| 1 h | ~12 | 17 |
| 4 h | ~6 | 24 |
| 24 h | ~2.6 | 62 |
| 40 h | ~2.2 | 88 |
| 250 h | ~0.7 | 208 (2 weeks) |

Comparing raw counts would call every fresh post a flop. Every score is
therefore rescaled to a **24-hour reference age** (`views_at_reference`),
clamped to 0.3×–6× so a single outlier cannot dominate.

### Score

```
value = 50·(views@24h / median_views) · 1.0
      + min(20, votes_share·20) ├ 6.0
      + min(10, reactions)      ├ 2.0
```

Views carry the baseline because they exist for every post; votes and
reactions are bonuses that only exist when the loop is healthy.

## 3. Learning (`learn` command, Thursdays after the poll closes)

For each language and topic it compares the median value score of posts
having it against the overall median, and writes a multiplier:

```
multiplier = clamp(0.85 … 1.15, 1 + (local_median − overall_median) / overall_median × 0.5)
```

`select_candidate` multiplies the editorial score by the language and topic
multipliers (final clamp 0.8–1.25).

**Sample floors** (see `docs/agents/SAMPLE_SIZE.md` for the arithmetic):
`learn` writes nothing below **20** measured posts; a language or topic gets
a weight only with **10+** posts of its own; a poll share enters the score
only above **100** voters and is discounted until 200. Measured spread on the
live channel is sd≈8 views on a median of 49, so five posts give a ±14%
interval — wider than any effect the channel could act on.

**Review rule:** if a multiplier sits at a clamp for two consecutive weeks,
the model is fighting the editorial score. Read the raw posts before
trusting it.

## 3b. Post format — long text, card on top

Telegram caps a **photo caption at 1024 characters**, and a photo post
deletes the long text. The owner's format is the opposite: the long post
*is* the message, and the card rides above it as a large link preview.

1. text = `format_project_post`, up to 4 000 visible UTF-16 units,
2. preview = the card, hosted on **ImgBB** (`prefer_large_media`,
   `show_above_text`). GitHub release assets do not work: they answer
   `application/octet-stream` with `content-disposition: attachment` and
   Telegram silently drops the preview — that was the "no photo" bug,
3. the preview upload is a ~70–90 KB web JPEG; crawlers time out on the
   full PNG even with the right content type,
4. the judgement block (who / why / verdict / quick start) is **open by
   default** — a collapsed blockquote hides the verdict from most readers.

## 3c. Editorial taste — is this interesting, or a library for ten people?

`src/ghos_v6/audience.py`, wired into the gate and the ranking.

| Layer | Rule |
|---|---|
| Narrow rejection | a repo that calls itself an SDK / bindings / ORM / adapter / boilerplate / plugin marketplace is rejected **unless** it shows at least one "a person can use this" signal |
| Hard rejects | prompt dumps (`prompt-library`), curated lists, datasets, dotfiles, tutorials — regardless of description |
| Appeal signals | desktop/GUI, editor, self-hosted, a named platform, a browser app, a creative medium, media, productivity, game, hardware, network, files, messaging, "replacement for X", installable **agent skills** |
| Ranking | up to **+10** points for appeal, so a usable product outranks a bare library with the same stars |

Owner decision: coding-agent skills *are* publishable — a skill
that does something a person can run (an ffmpeg editor for agents) counts;
a pack of prompts does not, and a plugin marketplace does not.

Measured effect on a live collect: 7 prompt-skill repos and 2 plugin
ecosystems rejected; the queue opens with Concat, m3e-canvas, Peach,
solidcraft, torrentplayer instead of `google/artemis` and three DeepSeek
plugin markets.

## 3d. Where the score stands today

| Part | State | Evidence |
|---|---|---|
| Anti-duplication | **ready** | 4 layers, 90-day milestone-only re-visits |
| Gate (safety, licence, freshness, language, narrowness) | **ready** | 22 rejection reasons, all named in `events.jsonl` |
| Ranking | **ready** | quality + freshness + momentum + content + appeal, 0–100 |
| Measurement | **half** | views are live (public widget); votes need a poll that has actually closed; reactions are collected but not yet in the `value` formula |
| Learning | **not exercised** | needs **20** measured posts (raised from 5 — see `docs/agents/SAMPLE_SIZE.md`); a fresh state dir has none |

So the system is *complete in design and wired end to end*, but only the
first half has been proven on live data. The honest status is "ready to
run a cycle", not "validated by a cycle".

## 4. Weekly cycle

| When (UTC) | What | Where |
|---|---|---|
| Sun 16:10 | Digest of the week's top 5, poll opens (120 h) | `digest` |
| Daily 19:50 | Views snapshot for 12 recent posts | `metrics` |
| Thu 18:10 | Poll closes, tally stored, then `learn` | `poll-close` + `learn` |
| Sun 16:40 | Owner report | `report` |

The close and the learn run on the **same cron, in that order**, so `learn`
never reads a poll that has not been closed yet.

## 5. What is deliberately absent

- No LLM in the ranking loop.
- No retroactive polls (impossible via the Bot API).
- No MTProto session (needs a phone number, risks the account).
- No silent weight changes: every model update lands in `state.json` and in
  `events.jsonl`, both committed by the workflow.
