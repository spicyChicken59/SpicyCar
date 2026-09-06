# SpicyCar design review · 6 September 2026

The desktop images below are real captures of the public SpicyCar page, not mockups. They preserve
the visual review of the matrix-first redesign in [PR #62](https://github.com/spicyChicken59/SpicyCar/pull/62).
The figures and dealer photographs are the site's snapshot on that date; they
are historical evidence, not current listing or financing claims.

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

**The criterion navigator below is a local preview. Its website integration has
not yet been published.** The shared helper is merged in
[design-system PR #13](https://github.com/spicyChicken59/design-system/pull/13).
Automatic approval review blocked replacing the application's HTML file because
it contains existing business logic, despite the verified design-only diff.

| Criteria available without guessing | History reached directly |
| --- | --- |
| ![Value, Reach, CPO and History buttons above the native signal matrix](criteria-phone.png) | ![History column visible with the same car identities and exact prices](criteria-history-phone.png) |

These 390-pixel phone captures come from the Chromium regression harness using
the checked-in data and documented offline font/photo fallbacks. They verify the
new layout; the desktop captures above come from the public site. The optional
controls only appear when the native table overflows. Clicking one brings its
criterion into view while car names and prices remain visible. Swiping, keyboard
scrolling and the native table remain available if the helper cannot load.
