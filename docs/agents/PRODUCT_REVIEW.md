# Panel: what should this channel build next

Personas imported from `agency-agents`:
`product/product-manager`, `product/product-sprint-prioritizer`,
`marketing/marketing-growth-hacker`, `design/design-ui-designer`
(for card-level questions only).

## Q1 — Is a "value system" premature for a channel with 224 views/post?

**PM:** No, but the scope is wrong if it becomes a recommender. At 224 views
a week you do not have the sample size for ranking *models*; you have enough
for *guardrails*. The three guardrails worth building are exactly the ones we
built: never post the same repo twice, never let a post go unmeasured, never
let a poll expire unread. Anything beyond that is ML theatre.

**Sprint prioritizer:** the cost of not having anti-duplication is
asymmetric — one repeated repo costs credibility with every reader who
notices; a missing recommender costs nothing. So: ship anti-dup first, it is
the only item with a catastrophic downside.

**Growth hacker:** the channel's problem is not precision, it is *reach*.
At 224 views/post, a 5 % ranking improvement is invisible. Cadence and
distribution (crossposts) move the number 10× more. Treat ranking as
maintenance, not as growth.

**Verdict:** build the guardrails, measure honestly, and put the effort into
distribution and cadence. That is what the roadmap does.

## Q2 — Views, reactions, stars or clicks?

**PM:** measure what the reader did, not what the project did. Stars measure
the project's popularity *in the world*, which we already use at selection.
For "did this post work", the only honest signals are views, votes and
clicks.

**Growth hacker:** clicks are the only signal that separates "read" from
"acted". Views count people who opened Telegram and scrolled past. If we
never measure clicks, we will optimise for curiosity, not for usefulness.

**Sprint prioritizer:** but clicks cost an external dependency and a
redirect hop. Ship views + votes now (free, zero infrastructure), and hold
clicks as an opt-in layer with its own phase — which is what Phase D is.

**Verdict:** views + votes + reactions in the core; clicks as an optional
layer, off by default so nothing breaks without it.

## Q3 — Three posts a day, five, or one?

**Growth hacker:** three. Five spreads the same 224 views across more posts
and lowers the median; one loses the morning/evening split that a European
audience actually reads at.

**PM:** cadence is a promise to the reader. Three a day is a promise you can
keep with a queue and a gate. Five is a promise that fails the first day the
runway dips.

**Verdict:** three, with the digest as the weekly anchor. Revisit only after
30 days of normalised data.

## Q4 — What is the one thing most likely to be wrong in six weeks?

**PM:** the value model overfitting ten samples and quietly steering
selection away from good projects. Mitigation, already implemented: clamp to
0.85–1.15, require ≥ 5 samples, and write every update to state so a human
can read it.

**Sprint prioritizer:** the poll quietly failing to close (a missed cron →
no tally → no signal for the week). Mitigation: the poll auto-closes at 96 h
via `open_period`, so a missed run degrades to "tally arrives late", not
"tally never exists".
