# GitHub Open Source

Autonomous, zero-cost discovery and publishing system for the
[GitHub Open Source](https://t.me/GitHub_Open_Source) Telegram channel.

> **Unofficial project.** This is an independent project. It is not
> affiliated with, endorsed by, or sponsored by GitHub, Inc. The channel
> name and account handles belong to their owner; no GitHub brand rights
> are claimed. Add the same disclaimer to every public account profile.

> **Security posture:** project code runs in a read-only job with no
> persisted checkout credentials; a separate job with no publishing
> secrets validates and commits the state. See [`SECURITY.md`](SECURITY.md).

## New here? Read these in order

| # | Document | Why |
|---|---|---|
| 1 | [`docs/HANDOFF.md`](docs/HANDOFF.md) | **Start here.** How the project is built and how to run it |
| 2 | [`docs/OWNER_RULES.md`](docs/OWNER_RULES.md) | Decisions the owner made that are not up for redesign |
| 3 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the modules fit together and why |
| 4 | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Daily running, incidents, rollback |
| 5 | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Setup order, secrets, rollout operations |
| 6 | [`docs/SECRETS_CHECKLIST.md`](docs/SECRETS_CHECKLIST.md) | Which secrets/variables are needed and where to get them |
| 7 | [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phases from here onward |
| 8 | [`SECURITY.md`](SECURITY.md) | Vulnerability reporting, security properties, operational risks |

## What it does

- Collects a daily pool of repositories from GitHub Search, Hacker News
  and optional public Telegram source channels.
- Enriches candidates with GitHub metadata, contributors and README.
- Applies a conservative autonomous quality/safety gate: license,
  activity, community signal, README, permanent dedup and a malicious-
  topic blocklist. Legitimate dual-use security tools are labelled.
- Uses two structured Gemini calls per project: an editorial pass and a
  per-surface copy pass. A deterministic fallback is used for surface copy
  when Gemini is unavailable; publishing a project requires a model verdict,
  so the slot is skipped (fail-closed) without Gemini.
- Publishes long Telegram text posts (up to the 4,096-character Bot API
  budget) with a large preview above the text and inline buttons.
- Creates a Telegraph full breakdown for every published project.
- Uses the adopted card system: Editorial Daily by default, Neon
  Terminal for CLI/TUI projects, Glass Aurora only for AI Brief assets,
  and Editorial Sunday Edition for the digest.
- Queues Bluesky/Facebook crossposts behind feature flags with
  retries and dead-letter records.
- Can publish the best project of the day to dev.to.
- Includes a verified WorldofAI AI Brief track: the YouTube channel is a
  discovery radar only; a primary source is preferred but not required
  (owner decision), and the brief never carries a "no confirmation" label.
- Publishes a Sunday Top 5 digest + poll and a weekly owner report.

## Architecture

```text
GitHub Search ─┐
Hacker News ───┼─> enrich -> gate -> rank -> queue
Telegram web ──┘                              |
                                              v
                           Gemini / deterministic fallback
                                              |
                         visual cascade -> Telegraph first
                                              |
                                      Telegram test/main
                                              |
                    dev.to article + delayed social crossposts

WorldofAI RSS -> strict filters -> primary source -> Gemini verify
              -> AI Brief (separate feature flag)
```

State is deliberately boring: an append-only `state/events.jsonl`
review log plus a deterministic `state/state.json` projection committed
by GitHub Actions. All scheduled state writers share one concurrency
group.

## Repository layout

```text
src/ghos_v6/
  config.py      environment + target safety lock
  sources.py     GitHub/HN/Telegram-web collection and enrichment
  gate.py        autonomous quality/safety gate
  rank.py        deterministic ranking and diversity selection
  Gemini.py      structured generation + fallback
  formatter.py   long Telegram project posts
  cards.py       Editorial/Terminal/Aurora/Sunday renderers
  media.py       README -> card -> OpenGraph visual cascade
  telegraph.py   Telegraph pages and image upload
  telegram.py    Bot API client and ID discovery
  platforms.py   dev.to, Bluesky, Facebook adapters
  content_adapters.py  platform-native copy, limits, tags and alt text
  news.py        WorldofAI RSS and verified AI Brief synthesis
  pipeline.py    collect/publish/crosspost/article/news/digest/report
  doctor.py      non-destructive integration checks
  cli.py         GitHub Actions entry point
state/
  state.json
  events.jsonl
```

## Local development

```bash
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
ruff check .
mypy src
pytest
python tools/secret_scan.py
ghos-v6 doctor
```

Dependencies are locked with hashes: `requirements.lock` is runtime-only
(installed by the publishing job), `requirements-dev.lock` adds the linters and
tests. Regenerate with `pip-compile --generate-hashes requirements.in` and
`pip-compile --generate-hashes requirements-dev.in`. CI runs the same install,
plus gitleaks and CodeQL.

Real credentials belong only in GitHub Secrets/Variables. See
`.env.example` for names, `docs/RUNBOOK.md` for rollout operations
and `docs/CONTENT_FORMATS.md` for per-platform structure, size and tone
(`docs/FORMAT_AUDIT_V2.md` records the review findings behind format v2).

## Safety principles

- No personal Telegram user session is used for publishing.
- The publication target cannot fall back from test to main.
- README and video text are untrusted LLM input, never instructions.
- A failed crosspost cannot roll back the Telegram post.
- Sponsored and rumor-only news is rejected.
- Secrets are redacted from errors and are never committed.

## Hardening

- Dependencies are pinned with hashes; Pillow is restricted to an explicit
  image format allowlist with a decompression cap.
- CI splits a read-only `run` job from a state-writing job that receives no
  publishing secrets; actions are pinned to SHAs.
- Untrusted README and video text can never become instructions; generated
  links are allowlisted and the model verdict is fail-closed.
- `state.json` corruption raises `StateCorruptError` instead of silently
  resetting; a queued run syncs `main` before it starts.
- A failed Telegram publish records a dead-letter instead of blocking the
  queue; news fetching is SSRF-checked and bounded.

## License

MIT. The channel name/branding remains the property of its owner.
