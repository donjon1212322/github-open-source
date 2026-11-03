# Roadmap — GitHub Open Source

Single source of truth for *what happens next*. Every item has an owner action
or a measurable «done», because a roadmap item without both is a wish.

---

## 0. Decisions locked by the owner

| Decision | Choice | Consequence |
|---|---|---|
| Cadence | **3 posts a day** + weekly digest | Three cron slots; the fourth post is refused in code |
| Polls | **Project of the Week only** | One poll per week, opened Sunday, closed Thursday |
| Novelty | **Re-visit only on a milestone** | ≥ +50 % stars or a major release, never inside a 90-day cooldown |
| Value signal | Views + reactions + votes. **No click tracking** | Links stay `github.com` |
| Publishing gate | Three variables simultaneously | Nothing reaches the main channel by accident |
| Card design | Editorial, brand icons, no ring | Frozen; changes only on owner request |
| Facebook | make.com + Google Sheet | No direct Graph API in the repository |

---

## 1. Phase A — launch (current)

| Item | Done when |
|---|---|
| All 7 Secrets and 16 Variables written | `doctor` reports 13/13 and no missing names |
| CI + CodeQL green on the clean tree | Both checks succeed on the initial commit |
| Doctor + one live post | Post and article render correctly; CTA points at the real channel |
| Publishing workflows enabled | 13 cron routes active, `commit-state` commits state |

## 2. Phase B — acceptance

- Three autonomous days of posts without manual intervention.
- Sunday digest + poll, Thursday `poll-close` and `learn` produce `value_model`.
- Weekly owner report arrives in the owner chat.
- Facebook hand-off: a row reaches the sheet and make.com picks it up.

## 3. Phase C — steady state

- Weekly report reviewed; `value_model` clamped within 0.85–1.15.
- Dead-letter queue stays at zero; moderation removes spam from discussions.
- State growth monitored; compaction keeps the projection reviewable.

## 4. Production hardening — done 2026-11-03 (was a "later phase")

| Item | Done when |
|---|---|
| `state/` moved out of `main` | orphan branch `state` holds the live files; `main` holds only an empty bootstrap, guarded by CI |
| `fetch-depth: 0` removed | both `run` and `commit-state` use `fetch-depth: 1`; no `reset --hard`, no `pull --rebase` |
| Atomic state writes | one CAS commit per run via Git Data API; `published count decreased` guard |
| Bounded state history | monthly `squash` to one parentless commit on `state` |
| Heartbeat | weekly `status`: size, staleness, bootstrap invariant; Doctor runs it too |
| Artifact bloat | state artifact uploaded only when the digest changed |
| dev.to cover | dedicated 1000×420 cover, PUT on the existing article id |

The phrase "фаза B/C" in older reports referred to these deferred items.
The numbered Phase B (three autonomous days) and Phase C (steady state)
above are **acceptance milestones**, not code tasks; they still need calendar
time and are being observed now.

## 5. Later phases (owner decision, not started)

- Key rotation schedule and a short-lived setup token policy.
- Gemini model migration before its deprecation window; Python runtime
  upgrade before EOL.
- Wider source list and additional platform adapters, if the owner asks.

---

See [`RUNBOOK.md`](RUNBOOK.md) for the launch order and
[`SECRETS_CHECKLIST.md`](SECRETS_CHECKLIST.md) for credentials.
