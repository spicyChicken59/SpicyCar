# Decision polish · 13 September 2026

One bounded pass over the shopping journey — choose a model, inspect an exact
vehicle, compare evidence and costs, save or open the source — rendered in
Chromium before anything was edited, with the same records and the same three
saved cars held constant. Three weaknesses were measured, not argued.

## 1 · "Side by side" showed one car on a phone

`#finalists-table` kept `.sc-signal-matrix`'s 680px floor. Three saved cars
rendered a **693px table inside a 348px region** — columns 136/176/176/185, the
measure column and **one** car on screen, **373px off it**. The model comparison
beside it already had a narrow band, written for `#compare-table` alone.

That band is upstream now as **`.sc-signal-matrix--fit`**, for the transposed
shape the criterion navigator excludes by design. Both tables wear it; the local
fork is gone. After: **96/112/112/112**, the measure column and **two** cars on
screen, 132px off it. The identity column stays sticky, nothing is hidden, and
the desktop table is unchanged.

| Before | After |
| --- | --- |
| ![One car column beside the measure labels](shortlist-phone-before.png) | ![The measure column and two car columns at 390px](shortlist-phone-after.png) |

## 2 · A recorded price and two computed ones wore one ink

Asking, out-the-door and per-month were one ink in the dossier and one size,
weight and colour in the shortlist; in the signal matrix the **largest number on
the card** was the estimated all-in, its basis carried by a 10px note. An
unreported fact wore a measured fact's 16px heading ink.

Upstream: **`.sc-estimate`** and **`.sc-unreported`**. Recorded stays the default
and wears no class. A derived figure steps one shade off heading ink and takes
the approximation mark — drawn in CSS with empty alt text, so the word beside it
still carries the meaning. An absence becomes a mono lowercase status word at
its own size and never lines up with a number. No figure changed.

![Mileage and dealer in heading ink beside "no condition details reported" in small mono](unreported-fact.png)

## 3 · The photograph had no shape of its own

The hero stretched to whatever height the fact column needed, cropping the same
photo **1.26:1 at 1280 and 1.60:1 at 390**, and three actions wrapped into two
ragged rows (129/153/166px) with the primary one last. The stage is
`.sc-dossier--studio` now — 16:10 at both widths, contained, cornered, with the
shared no-photo band — and the dialog ends in one `.sc-actionbar`.

| Before | After |
| --- | --- |
| ![A near-square cropped photo and a ragged row of controls](dossier-before.png) | ![A contained photograph, marked estimates and one action bar](dossier-after.png) |

## Provenance and verification

Before: `0a427ca9e8d1a37a38251d1b4c77beae2512a57d` at design-system `08cd626f`
(v2.10.0). Upstream: `design/car-decision-polish`, proposed **v2.12.0** — no tag
cut; the version is settled at integration. Captures come from the Chromium
harness over the checked-in `docs/data.json` with the documented offline font
and photo fallbacks; they are evidence of layout, not listing or financing
claims.

Offline: 565 Python tests, two failures pre-existing on `main` and unrelated
(the committed record's key order; the README sheet row). Consumer lint clean.
`studio`, `workspace`, `discovery` green. `dashboard_smoke` 302/306, zero page
errors — the identical four pre-existing failures `main` reports. Print
pagination on both tables, 320px, keyboard reach, reduced motion, forced colors
and the missing-photograph state all pass. Upstream `npm run check` green (109
component blocks, 360 classes findable); `visual-check --browser` 6/6.

## Keep / fix / defer / omit

**Keep:** `--fit` on both comparison tables; basis marks applied from what the
record says, never from a computation; the photo stage and the one action bar.
**Fix if it bites:** the action bar is 293px tall on a phone (98px as a ragged
row) — the height buys a bounded, ordered block, but the listing link is now
below a sentence; the no-photo band is a 144px slab in a 480px stage.
**Defer:** the discovery card's four-action footer (Home owns list composition);
`--sc-matrix-record` as a calc() over the region rather than a tuned pixel.
**Omit:** any change to the tracker, watchlist, `targets.json`, ledgers, ranking
or financing arithmetic. Nothing here touches a number.

## Next builder prompt

> Continue SpicyCar. This is the **integration** step for one upstream
> contribution, not a new design pass.
>
> State: SpicyCar branch `claude/spicycar-shopping-polish-0ebw1k` (PR open) pins
> design-system `design/car-decision-polish`, which proposes **v2.12.0** adding
> `.sc-signal-matrix--fit`, `.sc-estimate` and `.sc-unreported`. **No tag is
> cut.** SpicyHome ran a parallel upstream branch; its work is not in this one.
> SpicyCar PR #67 (`design/print-table-flow`) is **superseded** — its two print
> rules are byte-identical in this snapshot and its v2.10.0 pin is older — but it
> is still OPEN and must not be closed, merged, or merged on top of the newer
> pin without approval.
>
> Do, in order:
> 1. Fetch both repos. If Home's upstream branch is approved or merged first,
>    reconcile this branch's **source** onto that result — never resolve a
>    generated file (`styleguide.html`, `tokens.json`, `sc-*.js`,
>    `.claude/skills/**`) by choosing a side: take both sides' source edits, then
>    regenerate with `node build/brand-assets.mjs && node build/templates.mjs &&
>    node build/gen-tokens.mjs && node build/assemble.mjs`. Keep both
>    contributions; resolve duplicate primitives by name and behaviour, and
>    settle one correct next version across `sc.css`, both `package.json`s,
>    `react/package-lock.json`, the style guide and AUDIT-AND-ROADMAP §5.
> 2. `npm run check` (the cover's component-block count moves with the classes)
>    and `node build/visual-check.mjs --browser --shots /tmp/sc`.
> 3. Commit upstream, then from the clean committed checkout:
>    `node build/vendor.mjs <SpicyCar>/docs/design-system`, and check
>    `node tools/design_snapshot.mjs` reports the new commit.
> 4. Sweep the consumer: `docs/index.html` and `docs/how.html` cite the sheet
>    version in prose, and a test fails if they name one they do not load.
> 5. Re-run, matching the linter's checkout to the new pin:
>    `AUTODEV_API_KEY=test-key-not-used python -m unittest discover -s tests -t . -v`
>    (two failures are pre-existing on `main`: the committed record's key order
>    and the README sheet row — reproduce them there before blaming this branch),
>    `node tools/consumer_lint_ci.mjs <upstream> docs/index.html docs/how.html tools/og_card.html`,
>    `node tools/studio_smoke.mjs`, `node tools/workspace_smoke.mjs`,
>    `node tools/discovery_smoke.mjs`,
>    `node tools/dashboard_smoke.mjs <upstream> --shots /tmp/car-review`
>    (302/306 with four pre-existing failures is the baseline; anything else is
>    yours), and look at the shots at 390 and 1280 in both themes.
> 6. Only then does the tag question arise, and it is the owner's.
>
> Do not touch `Tracking.py`, `targets.json`, `docs/data.json`, `REPORT.md`, the
> ledgers, ranking or financing arithmetic. Do not weaken a lint rule or mask an
> overflow. A basis mark is applied from what the record already says about a
> figure, never from a computation the page just performed.
