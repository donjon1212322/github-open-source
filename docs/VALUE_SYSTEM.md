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
| Ranking | **ready** | 12 сигналов, перцентили внутри пула, 0–100 (см. §3e) |
| Measurement | **half** | views are live (public widget); votes need a poll that has actually closed; reactions are collected but not yet in the `value` formula |
| Learning | **not exercised** | needs **20** measured posts (raised from 5 — see `docs/agents/SAMPLE_SIZE.md`); a fresh state dir has none |

So the system is *complete in design and wired end to end*, but only the
first half has been proven on live data. The honest status is "ready to
run a cycle", not "validated by a cycle".

## 3e. The editorial score (rewritten 23.11, owner rule «звёзды — не интерес»)

The old formula was additive with caps — `min(30, 6.2·log10(stars))` for
quality, `min(20, …)` for momentum, `+10` neutral baseline. Measured on the
live queue of 27 candidates it spanned **80.28 – 87.40**: seven points for
every possible project, and a two-day-old repository with 1 123 stars per day
competed on equal terms with a three-year-old shelf item. Ten of the 27
candidates sat on the same freshness cap.

The replacement has three properties.

**1. Twelve named signals, weighted out of 100** (`rank.WEIGHTS`):

| Signal | Weight | Where it comes from |
|---|---|---|
| growth | 24 | stars per day: **our own daily snapshots** when we have two, otherwise stars ÷ repo age |
| acceleration | 12 | is it speeding up: rate of the newer half of the snapshots ÷ rate of the older half |
| forks_rate | 8 | forks per day from the same snapshots (people actually reuse it, not just star it) |
| burst | 6 | a brand-new repo taking off now (`stars / (age+2) / 50`, age ≤ 30 d) |
| freshness | 8 | `pushed_at` — is anybody still working on it |
| content | 8 | README length, topics, homepage, media (can we write about it) |
| community | 5 | forks/stars reuse, contributors, open issues |
| external | 7 | Hacker News points — validation outside GitHub |
| appeal | 7 | `audience.appeal_signals` — a product, not a building block |
| popularity | 5 | `log10(stars)` — the smallest weight on purpose (owner: "по звёздам судить не стоит") |
| reliability | 5 | licence present, not a repo farm (`owner_repo_count`) |
| corroboration | 5 | how many independent sources found it |

Momentum (growth + acceleration + forks_rate + burst) is **50** of 100;
absolute popularity is **5**. The ratio is exactly 10:1, so no shelf item can
outrank a young project that is visibly speeding up.

**2. Pool percentiles.** `rank_candidates()` converts each signal into a
percentile inside the current pool, so the score uses the real 0..100 range
and "who is best today" is answered by comparison instead of caps. Signals
where the whole pool is identical map to 0.5 (no comparison, no claim).
`score_candidate()` alone — tests, one-off lookups — falls back to raw values.

**3. Measured growth, not guessed growth.** `StateStore.mark_seen()` now
writes `star_history` (one snapshot per day, `[day, stars, forks]`, bounded
to 8) for every repository it meets. From the second day on, growth is a
measured fact: 200 → 800 stars in three days is 200/day, whatever the repo's
age says. When only one snapshot exists the model falls back to lifetime
velocity and marks `growth_measured: 0` in `score_parts`, so the difference is
visible in state.

**4. Speed, and reuse, not just size.** Two signals read the snapshot history:

* `acceleration` — snapshots are split in half by time; the rate of the newer
  half ÷ the rate of the older half. `1.0` is a plateau, `3.0` is a take-off.
  Needs ≥ 3 snapshots spanning ≥ 4 days; otherwise it is **unknown**, and an
  unknown signal maps to the neutral percentile `0.5` — it is never punished
  for being young (`rank.UNKNOWN_FLAGS`).
* `forks_rate` — forks per day from the same snapshots (or forks ÷ age when
  there is no history). Stars are a bookmark; a fork is someone building on
  it. Log-scaled (`log1p(forks) / log1p(60)`).

Every candidate carries `score_parts` (all twelve percentiles plus
`stars_per_day`, `acceleration_ratio`, `forks_per_day`, `age_days`,
`growth_measured`, `acceleration_known`, `forks_measured`), so any pick can be
explained after the fact. The two flags are `0` when the signal could not be
measured, so "scored low" and "not enough history yet" never look the same.

Measured effect on the live queue (23.11, same 23 candidates):

| | old score | new score |
|---|---|---|
| spread across the pool | 7.1 points | **37.5 points** |
| top pick | `XinaTian/MROffice` (2 112 ★, 12 d) | **`EL4CTEO/fast-anything` (2 d, ≈1 124 ★/day)** |
| old top under the new model | — | 60.7 (5th) |

Under the twelve-signal model the same queue re-ranks again: a repository that
grew 40 → 90 → 210 stars over three days now outranks a three-year-old project
with 20 000 stars and flat growth, because the first one answers all four
momentum signals and the second answers one.

The gate is unchanged and still rejects manufactured popularity
(> 2 500 stars/day in the first two weeks answers `velocity_too_high`), so
"fast" is rewarded inside the range a real launch produces.

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
