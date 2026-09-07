# Fieldwork: the photograph and the facts

This design-only pass gives each decision candidate's existing listing photograph
a full-width stage on larger screens and an enlarged side frame on phones. The
original photo is shown with `object-fit: contain`, so the
car and any information embedded in its photograph remain visible. Fine brackets
sit in reserved side margins, and the existing model/index caption has its own
footer. A failed photograph keeps the original SpicyChicken mark and explicit
“photo unavailable” label.

The exact price, cost qualifications, evidence disclosure and candidate link remain
in their existing reading order. The phone layout keeps the complete decision
within the existing one-screen height budget. The signal matrix still precedes the
decision cards and remains visible on the opening phone screen.

The four existing market metrics now share an ink instrument strip. Labels,
numbers, notes and links retain their exact content. The metrics are independent
facts, so this strip deliberately has no route connectors or implied funnel.
Its CSS adapter consumes the shared design system's `--sc-stat-*` hooks and fixed-ink
semantic aliases. Both themes retain readable figures, status text and keyboard
focus. Print returns the metrics to a paper surface and omits photographs.

## Public page before this pass

![The publicly deployed decision cards before this pass, with the two candidates' actual listing photographs shown as smaller thumbnails](fieldwork-before-live.jpg)

This is a real browser capture of the public SpicyCar page at source commit
`792bd6a`, captured before the fieldwork changes on 7 September 2026. Its listing
figures and photographs are historical review evidence, not current claims.

## Verification

`tools/fieldwork_smoke.mjs` runs the unchanged application with its checked-in data.
Its 26 focused checks cover 390, 820 and 1280 pixels in light and dark themes,
plus a 320-pixel layout, native disclosure keyboard access, visible link focus,
reduced motion, forced colors, print, and unavailable photographs. It compares the
exact rendered candidate text, image sources, links, matrix and metrics with the
composition stylesheet disabled and enabled.

The harness replaces external photos with an explicitly labeled geometry image
and uses the existing atlas/font stand-ins. Any captures from this command are
offline layout evidence, not screenshots of live dealer photographs. Motion is
disabled for screenshot capture so the saved image shows the settled design.

```bash
node tools/fieldwork_smoke.mjs ../design-system --shots /tmp/car-fieldwork
```

The original dashboard suite and its expected count are unchanged. Production
HTML, data and decision logic are unchanged by the composition stylesheet.
Shared snapshot provenance is recorded in
[`../design-system/provenance.json`](../design-system/provenance.json).
