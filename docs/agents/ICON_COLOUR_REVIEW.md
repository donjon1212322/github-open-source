# Panel: colour + layout review of the v6 editorial card

Personas imported from `agency-agents` (design/, marketing/):
`design-brand-guardian`, `design-ui-designer`, `design-visual-storyteller`,
`gis-cartography-designer` (label placement), `marketing-carousel-growth-engine` (card as a feed unit).

## Q1 — Should brand icons keep their own colours?

Brand Guardian: yes, with a guardrail. The card is a *newspaper spread*, not a
dashboard: one ink colour carries the masthead, the rules, the numerals and the
accent. A full-colour icon grid turns the right column into a sticker sheet and
kills the typographic hierarchy the paper relies on.

UI Designer: the reader's job here is *recognition*, not decoration. A Rust
gear in `#b3342a` is recognisable because of the *shape*; the colour adds
almost nothing at 190 px on cream paper, and at 34 px in the stack row it adds
nothing at all. Real brand colour buys one thing: instant parsing of the stack
row when there are four marks in a line.

Cartography Designer: same rule as a map legend — a symbol gets colour only
when colour encodes meaning or disambiguates near-identical shapes. C / C++ /
C# are three near-identical marks; Docker and PostgreSQL are not.

Visual Storyteller: the single-ink look is what makes the card read as *print*.
Full colour turns it into a "trending repo" tile.

**Verdict:**

1. **Language mark (hero, 190 px): single ink.** It sits next to the headline and
   is the loudest graphic element on the card; two colours fight each other there.
2. **Stack row: brand colour.** At 34 px the marks are purely informational; colour
   is the fastest way to read "Python · Docker · Postgres" without reading.
3. Never recolour the marks themselves beyond one flat ink — no gradients, no
   brand-colour pairs; a flat `#b3342a` or the true flat brand colour only.

## Q2 — Long stacks / long names

- Never shrink below 34 px to fit more marks. Cap the row at 4 and, when it
  overflows, drop the *least distinctive* tool (the one every project has) and
  keep the rare one.
- When a label would push the row past the right rule, switch from
  `icon + label` to `icon + tooltip-free label` truncation at 10 chars with an
  ellipsis-free cut, or drop to icons-only. Icons-only is allowed: the row is
  decorative-informative, not the only source of the fact.
- Long project names must never eat the copy band: title auto-fit down to 44 px,
  then wrap-free truncation is forbidden — instead the hook loses a line, never
  the name.

## Q3 — Accessibility / legibility

- Minimum 2 px optical gap between ink and paper; brand marks on cream need a
  contrast check — yellow/green brands (Node, JS, Dart, Lua) fail on
  `#f7efde` and must be darkened for the row.
- System icons stay ink-coloured: they are punctuation, not brands.

## Q4 — What the card must prove in the feed

Carousel Growth Engine: the first 0.3 s decides the swipe. The hero mark is a
*thumbnail*, so it must be big and flat, not detailed. That again argues for the
single-ink mark next to the headline and colour only in the small row.
