**Choose, then compare — SpicyCar product-quality milestone, 19 September 2026**

This pass improves the existing Explore → Choose cars → Compare & save journey. It adds no tracked
brands, models, evidence, providers or recommendation rules. The reader can keep a brand interesting,
choose no models, compare several directions, or return to saved vehicles without being steered toward
one universal winner.

**Starting point and ownership**

`origin` was fetched before implementation. The verified main and exact branch base were
`7db6ad53e70e97ad994fd07aff1e065704e62349` (PR #83), not an assumed old checkout.
Branch: `astra/decision-journey-quality`.

Fable's [PR #84](https://github.com/spicyChicken59/SpicyCar/pull/84) was open but clearly stopped:
ready for merge audit, unchanged since 03:49 UTC, no running workflow, and its only changed file was
`tools/dashboard_smoke.mjs`. Its head was `bc9ec707c7f9f8845e0c8e3f60e3162d03149afb` and
[check run 35419173448](https://github.com/spicyChicken59/SpicyCar/actions/runs/35419173448)
had succeeded. There was no overlapping application work. That file remains byte-identical to this
branch's base. Main's check run 35417098087 failed; Pages run 35417097564 succeeded.

**What the actual browser showed**

The checked-in application was served locally and walked in Chromium through Playwright 1.56.1.
The audit covered 1440×1000, 1280×900, 390×844 and 320×844, each in light and dark: 104 journey states
before and 104 after, with zero uncaught page errors or page-level horizontal overflow. Captures were
opened and inspected, including chooser, model comparison, empty scope, vehicle detail, notes, garage,
saved comparison, reload and filter recovery. Initial external-font failures were resolved by serving
the exact downloaded font assets and available listing photographs requested by the application.

Three issues earned the implementation time:

1. The phone chooser's apply button was below the dialog's visible edge. Long explanatory copy
   competed with the list, and an interest-only save still said “Shop these models.”
2. Compare & save put an empty saved-car panel and finance setup before the models the reader had
   just chosen. Repeated spacing and headings also delayed the first car on arrival.
3. The phone model table compressed two columns and put the third outside the visible reading area.
   A long explanation preceded the evidence, while the existing saved-car pair layout already solved
   a similar reading problem.

**What changed**

The native chooser now has a shorter explanation, a scrolling list, and visible close/apply controls.
An interest-only change says **Save brand interest**. Selected models, interested brands and the
explicit zero-model state retain their separate meanings. On a narrow phone the controls form complete
rows. Model photographs are contained rather than cropped.

Comparison leads with selected models until there are saved cars to compare. A returning shortlist
still leads. The full explanation, including financing and cost assumptions, remains in a native
disclosure; the visible summary says each column has its own market and there is no overall winner.
Desktop model names have a stronger hierarchy, and long notes wrap so three compared models fit at 1280px.

Phones reuse the shared two-record comparison pattern. The reader can view any two of N models or
trims; all N remain in the comparison. Sticky identities label the values below them. Every displayed
cell is copied from the existing table, including missing values, estimate marks, cohort basis,
sparklines and existing value labels. Choosing a pair never rescores it, changes shopping preferences,
changes the URL, or changes recommendations. Opening a model focuses the visible model heading.
Desktop, Full report and print retain the complete native table.

Spacing is tighter around the existing workspace and filter bar. The masthead reserves the same
two-line phone shell before and after data loads, including the Full report route. Narrow filter
controls fit their container.

**Before → after, measured with the same fonts, data and states**

Numbers below are document-space top positions in CSS pixels; lower means reached sooner. Both themes
gave the same positions. The comparison state has BMW i5, BMW i7 and Porsche Taycan selected, no saved
VINs, and BMW/Porsche interest. Nothing about their prices or desirability is inferred from this test.

| Viewport | First car, before → after | Model comparison, before → after | Chooser apply button |
| --- | ---: | ---: | --- |
| 1440×1000 | 631 → 532 | 774 → 437 | Visible in both |
| 1280×900 | 627 → 528 | 770 → 434 | Visible in both |
| 390×844 | 586 → 503 | 1075 → 442 | Bottom 909 → 823; now inside viewport |
| 320×844 | 663 → 553 | 1231 → 512 | Bottom 1010 → 823; now inside viewport |

Before: choose brands/models, scroll to recover apply, enter comparison through an empty garage and a
finance form, then read a narrow table with another model offscreen. After: keep brand interest or
apply models from visible controls, arrive at the comparison, choose any pair and read the same
evidence. Saving vehicles still returns the reader to the existing shortlist and notes workflow.

**Inspected captures**

These are actual local-browser captures of the committed 18 September data snapshot, not mockups or
fresh inventory claims. The phone comparison captures align to the same section start. Six images are
kept here; the broader light/dark, desktop/phone audit is summarized above.

| State | Before | After |
| --- | --- | --- |
| Chooser, 390×844 light | [Before](decision-chooser-before-390.png) | [After](decision-chooser-after-390.png) |
| Three selected models, 390×844 light | [Before](decision-compare-before-390.png) | [After](decision-compare-after-390.png) |
| Initial decision screen, 1440×1000 dark | — | [After](decision-compare-after-1440-dark.png) |
| Arrival, 320×844 dark | — | [After](decision-arrival-after-320-dark.png) |

**Verification and backstops**

| Check | Baseline | Final |
| --- | --- | --- |
| `python -m unittest discover -s tests -t . -v` | 570 run: 569 pass, 1 skip | 570 run: 569 pass, 1 skip |
| JSON validation | 2/2 | 2/2 |
| Call plan | 29 targets; 21 today; 21/40 worst; 640/month, 64% | Unchanged |
| Design snapshot | 22 assets, v2.13.0, `ad5aa0f82af7aa98def5807ae33b01bdb0a1e868` | Same 22 assets verified |
| `consumer_lint_ci` | Clean | Clean |
| `studio_smoke` | Integrated journey passed | Integrated journey passed |
| `workspace_smoke` | 3 journey groups passed | 3 journey groups passed |
| `discovery_smoke` | 10/10 | 10/10 |
| `browse_smoke` | 51/51 named checks | 63/63 named checks |
| `fieldwork_smoke` | 26/26 | 26/26 |
| `matrix_navigation_smoke` | 26/26 | 26/26 |
| `dashboard_smoke`, unchanged main harness | 307/309, 14 skipped; 323 declared/recorded | 307/309, 14 skipped; 323 declared/recorded |
| Exact PR #84 harness against this application and unchanged report | Separate pending repair | 310/310, 14 skipped; 324 declared/recorded |

The twelve new browser cases fail against the detached starting checkout and pass against this one.
They cover reachable dialog actions, cancel/focus return, honest interest-only labeling, any two of
three models, exact evidence/estimate/value-label retention, stable shopping/recommendations, pair
swap/focus, model navigation/back, closed-disclosure print content and all native print columns. The desktop cases also require all three identities and every figure to fit.
The existing saved-car test now scopes its selectors to the saved-car panel. Fieldwork measures
settled layout with reduced motion for each viewport; all 26 assertions and contrast thresholds remain.

The two known baseline failures are **“and a confirmed departure of the floor car is named as one”**
and **“and an unconfirmed one is not.”** Fable repairs their contradictory planted history. This branch
does not absorb that repair. A separate scratch harness uses his exact file, this application's docs,
the unchanged `REPORT.md`, and the pinned design snapshot to check compatibility.

Implementation verification caught and fixed an unstyled phone button, an unscoped test selector,
animation-sensitive fieldwork sampling, and a masthead loading jump. The focused masthead check now
measures 76.19px before and after data arrives at 320×568. No assertion threshold or count backstop was
lowered. No uncaught application page errors were observed. Deliberately aborted photo requests in
the fieldwork fallback test are expected network-console messages, not application exceptions.

**Transfer size**

| Asset | Base gzip bytes | Final gzip bytes | Delta |
| --- | ---: | ---: | ---: |
| `docs/index.html` | 158,559 | 160,155 | +1,596 |
| `docs/data.json` | 406,721 | 406,721 | +0 |
| `docs/shopping-workspace.css` | 2,924 | 3,664 | +740 |
| `docs/shopping-workspace.js` | 4,193 | 4,189 | -4 |

Total UI delta: **2,332 bytes (2.28 KiB)** compressed. HTML plus data: **566,876 bytes**. HTML is
494,813 bytes raw; data is 4,907,992 bytes raw and byte-identical to base. Measurement uses Node 24 `gzipSync(bytes, {level: 9})` for both base and head.
The existing 200 KiB HTML / 400 KiB data budgets are unchanged. No data bytes were added.

**Material diff and preserved boundaries**

| File | Material change |
| --- | --- |
| `docs/index.html` | Presentation-only comparison pair, shorter visible explanation, section priority, heading focus and asset cache keys |
| `docs/shopping-workspace.css` | Chooser fit, workspace rhythm, reserved masthead, phone comparison composition, narrow filter fit and print/forced-color rules |
| `docs/shopping-workspace.js` | Chooser composition, description and action label; existing apply paths unchanged |
| `tools/browse_smoke.mjs` | Twelve consequential browser cases; saved-panel selector scope; updated honest-action expectation |
| `tools/workspace_smoke.mjs` | Interest-only button-label expectation |
| `tools/fieldwork_smoke.mjs` | Set reduced motion before each layout measurement |
| `docs/design-review/` | This record, six screenshots and the review index link |

**KEEP:** brand interest as device-local intuition; explicit shopping-model scope; zero chosen models;
separate saved VINs, notes and comparison membership; recommendation versus market context; fact versus
fit versus subjective interest; every missing/unknown value and existing estimate label.

**FIX NOW, completed:** chooser reachability, model comparison readability and decision-screen priority;
regressions discovered while verifying those changes.

**DEFER:** broader navigation changes, a deeper finance-form pass, and unrelated detail/photo polish.
The detail and saved-car journeys already worked, and changing them would make this a second campaign.
Missing third-party thumbnails were left as missing; no new listing evidence was collected.

**OMIT:** a brand dashboard, new filters or product features, rankings or universal winners, new providers,
framework/state/routing changes, and shared-design-system edits. `Tracking.py`, `targets.json`,
`docs/data.json`, historical evidence, pricing/shipping/finance mathematics, departure semantics,
workflow/deployment/settings/secrets and sibling repositories are untouched.

**NOT RUN / BLOCKED:** WebKit, Firefox, physical phones and live external map-tile availability were not
verified. Browser regressions use their declared offline fixtures; visual captures use the available
original assets. Main CI remains blocked on the two known fixture failures until PR #84 lands.
No merge or deployment was requested or performed.

**Settled next action:** merge-audit this single UX PR together with its baseline evidence; land Fable's
separate repair through its own review, then refresh this branch against main and require green CI
before any merge. Do not begin another product campaign as part of this milestone.
