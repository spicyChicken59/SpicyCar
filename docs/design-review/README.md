# SpicyCar design review · 6 September 2026

The most recent pass is [Narrowing by drivetrain](narrowing-by-drivetrain.md), 15 September 2026:
the two columns the record kept and nothing read were measured against the committed snapshot, and
one of them earned a filter. Drivetrain covers 1,535 of 1,678 live listings and splits 18 of the 21
models, so it narrows the market rather than restating the model already chosen; seats is deferred,
because 17 of 21 models record exactly one value and one model is 21% covered. Before it,
[Trust the current comparison](comparison-acceptance.md), 15 September
2026: the two browser harnesses nothing ran were classified and repaired, the shopping journey is
walked once with nothing pre-saved, and six evidence invariants — freshness, unknown history, cost
basis, dated prices, disappearance, geography — are pinned on named subjects. It supersedes the
verification numbers and the sheet budget quoted in the pass below; that page's measurements stay as
what was true on 13 September. Before it,
[Discover → Compare → Decide](discover-compare-decide.md): the cards, the
map and price-against-miles made three views of one candidate set with their omissions named and
reachable, the comparison given a phone arrangement and a membership of its own, and the garage
told what the record moved under since the reader last saw data. Before it,
[Decision polish](decision-polish.md): the shortlist comparison made
readable on a phone, asking prices told apart from computed estimates, and the exact car's
photograph given a stage of its own. Before that, [Fieldwork: the photograph and the facts](fieldwork.md):
larger uncropped candidate photographs, an ink instrument strip and preserved phone decision access.

The desktop images below are real captures of the public SpicyCar page, not mockups. They preserve
the visual review of the matrix-first redesign in [PR #62](https://github.com/spicyChicken59/SpicyCar/pull/62).
The figures and dealer photographs are the site's snapshot on that date; they
are historical evidence, not current listing or financing claims.

> **Superseded as a description of the arrival, 15 September 2026.** The captures and the reading
> below are what the page was on 6 September, and they stay. Since the shopping workspace (#77) the
> watchlist opens in **Explore**, which browses; the signal matrix, the photo dossier and the
> instrument strip belong to **Compare & save**, which decides, and `shopping-workspace.css` hides
> them on the Explore route deliberately. "Opening view" and "opening screen" below mean the page of
> 6 September, not today's arrival. See
> [Trust the current comparison](comparison-acceptance.md).

| Before | After |
| --- | --- |
| ![Previous opening view: cover followed by two text-heavy decision tiles](before-desktop.jpg) | ![Matrix-first opening view: compact cover, section links and two candidate signal rows](after-desktop.jpg) |

The previous homepage starts with decision details. The new opening screen
compares the same candidates across value context, reach, certification and
accident record. Exact prices stay visible; unknown evidence stays neutral.

## Decision cards

![The same two candidates with their actual listing photographs, prices and preserved supporting evidence](decision-cards.jpg)

The loaded photographs belong to the exact candidate VINs. An unavailable-photo
fallback remains available; no alternate car's photograph is substituted.

## Dark theme

![The same cover and signal matrix rendered in the dark theme](dark-desktop.jpg)

## Provenance and verification

- Page: <https://spicychicken59.github.io/SpicyCar/>
- Before: `6f6dba99a13d69042482bf84cf5e30535ebc969e`.
- After: `3b95d65b788cf845ea316a271a4daf16ccee917c`.
- Browser screenshot dimensions: 1348 × 926 pixels, unchanged between captures.
- Public HTML and composition stylesheet matched the reviewed Git blob hashes.
- The deployed candidate link opened the exact listing row, in view and focused.
- Both source photographs loaded; Decision and Map links reached their sections.
- PR and main checks passed: 271 browser assertions, three data-dependent skips,
  zero page errors, 342 Python tests, and consumer/snapshot validation.
- Automated layout coverage additionally included 390, 820 and 1280 pixels,
  light/dark, keyboard scrolling/focus, reduced motion, forced colors and print.

## Criterion navigation on a phone

The checked-in chart and table presentation bundle adds navigation using the
matrix's existing column headings. The application's HTML and data stay
byte-for-byte unchanged. The transposed shortlist table remains separate: its
columns name cars, so it does not receive criterion buttons.

| Criteria available without guessing | History reached directly |
| --- | --- |
| ![Value context, Reach, Certification and Accident record controls above the signal matrix](matrix-nav-bundle-phone.png) | ![Accident record visible beside the same car identities and exact prices](matrix-nav-bundle-history.png) |

These 390-pixel phone captures come from the Chromium regression harness using
the checked-in data and documented offline font/photo fallbacks. They verify the
new layout; the desktop captures above come from the public site. The optional
controls only appear when the native table overflows. Clicking one brings its
criterion into view while car names and prices remain visible. Swiping, keyboard
scrolling and the native table remain available without the navigation helper.

The original dashboard verification suite remains unchanged. Run the additional
design check with Playwright 1.56.1 and Chromium. The `../design-system` checkout
must be at the exact commit recorded in the snapshot provenance:

```bash
node tools/matrix_navigation_smoke.mjs ../design-system --shots /tmp/car-matrix-proof
```

Its 26 checks cover the six viewport/theme combinations, every criterion jump,
sticky identities, keyboard focus, reduced motion, forced colors, print,
rerendering and the native fallback. Since 15 September it reaches the matrix by
pressing **Compare & save** rather than expecting it on arrival, and `check.yml`
runs it — and `tools/fieldwork_smoke.mjs` — on every push and pull request, so
neither can rot unnoticed again. The exact upstream commit and asset hashes
are recorded in [`../design-system/provenance.json`](../design-system/provenance.json).
