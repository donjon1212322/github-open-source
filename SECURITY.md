# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately.

- **GitHub private vulnerability reporting:** Settings → Security → Private
  vulnerability reporting (enabled for this repository). Use the
  "Report a vulnerability" button in the Security tab.
- **Fallback:** open a private security advisory or contact the maintainer
  through the repository owner. Do not open a public issue for an
  unpatched vulnerability.

Please include:

- affected version or commit,
- reproduction steps or a proof of concept,
- impact you believe it has,
- any logs or screenshots (redact credentials).

We aim to acknowledge a report within 72 hours and to ship a fix or a
mitigation within 14 days for high-severity issues.

## Scope

This repository is an **unofficial** open-source discovery and publishing
system for the Telegram channel `@GitHub_Open_Source`. It is not affiliated
with GitHub, Inc.

In scope:

- the Python package under `src/ghos_v6`,
- the GitHub Actions workflows under `.github/workflows`,
- the state handling and the publishing pipeline.

Out of scope:

- the Telegram channel, its moderation, or accounts operated by third
  parties,
- the content of third-party repositories that the bot reads,
- social accounts (Bluesky, Facebook, dev.to) — report account
  compromise to the respective platform.

## Security properties

- The bot never executes code from a repository it reads. README content is
  treated as untrusted data, is wrapped in a data block for the model, and
  is stripped of wrapper tags before the prompt is built.
- Only PNG, JPEG, GIF and WEBP images are decoded, with a 40 MP pixel cap.
  Pillow is pinned to a supported major version.
- Outbound HTTP is HTTPS-only, redirects are followed manually, and every
  hop is checked against private IP ranges (SSRF protection).
- Secrets are redacted from logs and from the committed event log.
- The publishing workflow runs project code with a read-only token and no
  persisted checkout credentials. A separate job commits the state, has no
  access to publishing secrets, and refuses to commit a state that lost
  published records.
- A queued run syncs `origin/main` before it starts, and the `commit-state`
  job syncs `main` again before comparing/committing, so a stale queued run
  cannot lose the state or republish a post.
- `download_bytes` follows redirects manually and validates every hop, so a
  public URL cannot bounce the bot to a private address.
- Cross-posts are crowned from all of today's posts, not only from pending
  ones; the losers stay `held`. A failed Telegram publish removes the
  candidate and records a dead-letter instead of blocking the queue.
- The deterministic fallback (no Gemini) is `safety="reject"` for every
  project, including dual-use: the bot fails closed rather than publishing
  without a safety verdict. A Gemini outage keeps the candidate in the queue.
- Moderation ignores Telegram service senders (`is_automatic_forward` on the
  message, user 777000), so the channel's own forwarded posts are not deleted.
- A project the model fails on twice is removed from the queue and recorded
  in `dead_letters`; one bad project cannot block the publishing slots.
- Fatal Telegram errors (401/403, chat not found, network/timeout) abort the
  slot and keep the candidate. A timeout also adds the repo to `suppressed`
  so the next collect does not republish it.
- Crossposts wait until 18:00 UTC, so the day's winner is chosen from all of
  the day's posts, not the first one.
- `collect_command` keeps published repos only when `dedup.check` returns
  `revisit`; `record_published` preserves the previous record in `history`.
- `requirements.lock` is runtime-only; `requirements-dev.lock` adds the linters
  and tests. The publishing job installs only the runtime lock.
- `state.json` is compacted on every save. Anti-duplication keys are kept
  forever; heavy generated payloads and stale `seen_repos` entries are not.
- A weekly lifecycle guard watches state size, repository activity and the
  configured Gemini model; it opens an issue instead of publishing anything.

## Known operational risks

- This is the **production** repository (`donjon1212322/github-open-source`).
  The old v5/v6 accounts are disabled. Revoke the setup PATs that were used
  during migration and rotate every key that passed through the chat.
- Live state lives on the public orphan `state` branch, not in `main`. It
  contains no credentials and no private `t.me/c/` ids; keep it that way.
  `main` holds only an empty bootstrap, enforced by a CI guard.
- `commit-state` has `contents: write` but no publishing secrets and runs no
  project code. It writes only `refs/heads/state`; the monthly `squash` is
  the only force-update and only ever targets that branch. `main` is never
  force-pushed.
- `TELEGRAM_OWNER_CHAT_ID` is an Actions Variable; verify it still points at
  the owner's chat after any account change.
- Facebook publishing goes through make.com + a Google Sheet. No Page token
  exists in this repository by design.
- Dependabot **version updates** are deliberately off (`.github/dependabot.yml`
  removed 2026-11-03): all Actions are pinned to reviewed SHAs and both lock
  files require `--require-hashes`, so automated version PRs could not be
  merged safely. Dependabot security alerts remain a Settings-level feature
  and do not create these PRs. CodeQL, gitleaks, secret scanning and push
  protection stay enabled.

## Disclosure

We will publish a short note in the release notes for any security fix and
credit the reporter unless they prefer to remain anonymous.

## Manual repository settings

The following still require a human with access to the production repository.
Values are never stored in this repository.

- Secret `GOOGLE_SERVICE_ACCOUNT_JSON` and Variables `FACEBOOK_SHEETS_ID`,
  `FACEBOOK_SHEETS_RANGE`, `FEATURE_SHEETS_HANDOFF` — the only things needed
  for Facebook. The service-account e-mail must have Editor access to the
  sheet, and the make.com scenario must be switched on.
- No `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_PAGE_ID` or `FEATURE_FACEBOOK`:
  the direct Graph API adapter is not part of this repository
  v6 never holds a Page token.
- Production repository: re-issue every key. Keys for the brand accounts
- Confirm the `Lifecycle guard` issues are reviewed; confirm `Budgets and
  alerts` is set on the billing account.
- Revoke the temporary setup PAT after bootstrap.

**Launch policy:** The target lock remains a security control and must not be
removed: with test variables all posts go to the private test channel, and
`main` needs all three production variables at once. A staged acceptance run may use the test target.

`APP_ENV`, `PUBLISH_TARGET`, `TELEGRAM_TEST_CHANNEL_ID`,
`TELEGRAM_OWNER_CHAT_ID` and `TELEGRAM_MAIN_CHANNEL_ID` are configured. The
legacy `MAIN_TELEGRAM_CHANNEL` remains as a compatibility alias and both
workflows pass it explicitly.

The private test-channel id is intentionally not stored in `state.json` or in
current configuration files; it remains in older git history.
