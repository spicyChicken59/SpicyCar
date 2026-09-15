# Trust the current comparison · 15 September 2026

An acceptance pass, not a redesign. Two browser harnesses had been failing on a page that
worked; the shopping journey had never been walked without its answer pre-seeded; and six
facts a reader has to be able to trust were implemented but not pinned. Nothing about the
tracker, the watchlist, `targets.json`, the ledgers, the ranking, the shipping or financing
arithmetic, the collection allocation or the API budget is touched here. `docs/data.json`,
`docs/index.html` and the vendored `docs/design-system/` are byte-for-byte the snapshot this
started from.

**Baseline.** `1080d8c3e1064931cacbb88a0f5960608e4781b1` — remote `main`, the `snapshot
2026-09-15` commit, which is also where the working branch stood. `docs/data.json` at that
commit: SHA-256 `042936a4e3e13493ad58577ba10ed859208d96a0f10bee40cecfd401f5c642b5`,
`generated` and `data_through` both 2026-09-15. Design snapshot v2.13.0 at `ad5aa0f`, 22/22
assets verified, not re-vendored.

## 1 · The two harnesses were looking at the wrong workspace

**Classification: neither surface is retired; both harnesses started on the wrong route.**
Reproduced once each against the unmodified snapshot before anything was edited.

`tools/fieldwork_smoke.mjs` and `tools/matrix_navigation_smoke.mjs` both opened the default
page and waited for `#decision-matrix tbody tr[data-signal-vin]` **to be visible**. The
timeout log is the finding: *"64 × locator resolved to 2 elements"* — the rows were in the
document the whole time, carrying their exact VINs, and the wait was for visibility they
never get on that route. `shopping-workspace.css` hides `#signal-card`, `#hero-card` and
`#kpis` under `[data-workspace="explore"]`, deliberately: Explore browses, **Compare & save**
decides. Neither harness is in `check.yml`, so nothing said so for two snapshots.

Probed across routes at 390 and 1280, both themes, zero page errors:

| Route | `#signal-card` | `#hero-card` | `#kpis` | matrix rows |
| --- | --- | --- | --- | --- |
| arrival (`explore`) | `display:none` | `display:none` | `display:none` | 2 in the document, 0 drawn |
| press **Compare & save** | shown, 667px | shown, 854px | shown, 438px | 2 drawn, exact VINs |
| `?view=compare` | shown | shown | shown | 2 drawn |
| `?view=report` | shown | shown | shown | 2 drawn |

So the matrix, the photo dossier and the instrument strip are all still supported, on a route
a reader reaches by a control and a link reaches by `?view=`. Both harnesses now arrive where
a reader arrives, wait on Explore's own readiness condition — `.car-place-card`, which the
CI-wired `browse` and `discovery` suites already use — and press **Compare & save** by its
role and accessible name. **No CSS override, no forced click, no hidden element read.**

### Old assertion → replacement

Two assumptions described the matrix-first homepage of #62 and are false of this one. Neither
is deleted; each is replaced by the obligation underneath it.

| Was | Is now | Where |
| --- | --- | --- |
| `the first signal remains on the opening screen` | `Explore arrives on the cars and one press opens the whole matrix` — arrival route is `explore`, the first card is on screen, the matrix is off the route **by CSS while its rows are in the document**, the press is a ≥44px control, and after it every row is drawn with a well-formed VIN | `tools/matrix_navigation_smoke.mjs`, 6 viewport/theme pairs |
| `first signal stays on arrival and instrument values remain legible` | `the dossier opens one press from Explore and its instruments stay legible` — the same arrival contract, plus the photograph on screen after a scroll, and the contrast and tile containment the check already had | `tools/fieldwork_smoke.mjs`, 6 viewport/theme pairs |

Everything else in both harnesses is retained and running: every criterion jump, sticky
candidate identity, keyboard tab stops and focus rings, reduced motion, forced colors, print,
the filter rerender, the native no-enhancement fallback, the image stage geometry, the
photo-unavailable state and the exact-VIN fact comparison.

### One check that could never have failed

`phone evidence remains a native keyboard disclosure with visible focus` called `.focus()` and
then read `outlineStyle`. A **programmatic** focus does not raise `:focus-visible`, and
Chromium duly reports `outline: none` over a perfectly good ring — measured: `outlineStyle
"none"`, `:focus-visible` false, and yet Enter opened the disclosure. Reached by keyboard
instead (land, step off, Tab back) the same element reports `:focus-visible` true and a solid
2px outline. The application was right; the assertion was unsound, and it is keyboard-reached
now.

### And a gate, so it cannot happen again

`check.yml` runs both harnesses on every push and pull request. They cost about 15 seconds
each measured here, on a job that already takes minutes, and their screenshots ride the
existing artifact. Being optional is why they rotted.

## 2 · The journey, walked with nothing pre-saved

Every step in `tools/browse_smoke.mjs` seeded a finished garage through an `addInitScript`
that runs again on **every navigation**. That proves what the page does *with* a save; it
cannot prove saving works, and it cannot prove anything survives a reload, because the reload
re-seeds it. One step now starts with empty storage and makes every write through a control:

choose the two models in the picker → cards, map and *Price & miles* → **Quick look** on the
first card → status to *Saved* → type a note → read both back out of `localStorage` → compare
→ drop from the comparison and find the save, the note and the status still there → put it
back → open the garage → **reload, re-seeding nothing** → reopen the same car to the same
status and the same note.

## 3 · Six facts, pinned on named subjects

Each is a copy of the record shaped in memory and served to one browser context.
`docs/data.json` is never written. The subjects are chosen by position in the committed
record, so a check cannot pass because tonight's snapshot happens to hold no car of that
shape — which is exactly how two checks here were passing: `if (!noMiles.length) return` and
`if (!vin) return` are a silent green, and both are asserts now.

| Fact | What is pinned |
| --- | --- |
| **Freshness** | A car whose last observation predates a newer `generated`/`data_through` keeps its own date in the summary *and* in the dated list, and gains no row for a day nothing was observed. A reload is not an observation and a first visit claims no change (both already covered, retained). |
| **History uncertainty** | Null mileage reads *Mileage unreported*, never `0 mi`; null accidents and owners produce no *no accidents* and no *1-owner* on the card, in the sheet, or in the comparison's **Reported history** cell — which is where `flagsCell()` actually renders — and the absence is marked rather than left to read as a value. |
| **Cost basis** | A recorded asking price is never marked an estimate; the computed out-the-door and monthly figures are; shipping rides beside the price rather than inside it; the *What these estimates assume* disclosure names its shipping and tax bases; no unavailable figure is written as `$0`. |
| **Dated price evidence** | Every row of the recorded-prices disclosure checked against the record's own series, date and price, row for row, so a drawn segment cannot become an observation. |
| **Disappearance** | A saved car that stops printing keeps its last recorded price *dated as last seen*, stays in the comparison it was saved into, and the record keeps offering the cause that is not a sale. Covered both for a car already in `gone` and for one a later fixture moves there. |
| **Geography** | A car with no coordinates gets no marker, the map names the omission and why, and the omission link reaches the car itself in the cards — paged the way a reader pages them. |

A seventh shapes an 88,000-mile car so the filter-exclusion case has a subject whatever the
tracker publishes next: the exclusion is explained, the filter is **not** widened behind the
reader, and only their own press brings the car back.

**Flat premium window.** #79's regression is reused unchanged and not reopened — its guard and
its logic in `dashboard_smoke.mjs` are untouched. On this snapshot it does not skip: *the
premium over the shared fetch days is the one the ledgers make*, *the drivable premium over the
record is the one the ledgers make* and *one drivable car a day is not a premium* all report
`ok`, and the page renders.

## 4 · Both themes, both widths

Every session in the browse suite ran dark at 1280. Four steps now measure the journey's own
controls at 1280 and 390 in **both** themes: *Choose cars*, *Compare & save*, *Garage*, *Quick
look*, *Save car*, the status control, the notes field and the assumptions disclosure are each
on the page, visible, a real press target, unclipped at the viewport edge, and ≥4.5:1 against
whatever is actually painted behind them — walked up the tree, because a control's own
background is usually transparent. The sheet is measured against the screen and the page is
checked for sideways scroll at both widths. The existing 320px and 820px checks are untouched,
and the touch targets stay where they were: the phone step owns the 44px and 36px floors, and
28px is what the design system's `--sm` button is on a desktop, not a defect.

**Observed, not a defect.** At 390px `.shop-navigation` is a real `overflow-x: auto` scroller
(`nowrap`, content 469px in a 350px box). At rest *Explore cars*, *Garage* and *Compare &
save* are fully visible and *Market history* is reachable by scrolling; pressing a control
scrolls it into view, which is what put a part-scrolled nav in one capture. The page itself
never scrolls sideways.

## Negative controls

Fourteen, each the smallest reversible mutation to one Car source file, run against the
repaired check, then restored from git and the restore proved by SHA-256. All fourteen turn
their check red.

| # | Mutation | Check that went red |
| --- | --- | --- |
| 1 | "Compare & save" lands back on Explore | both harnesses time out |
| 2 | `#signal-card` hidden on the compare route too | matrix nav, 1/2 |
| 3 | the matrix leaks onto the Explore arrival route | all 6 arrival checks, on `matrixOffRoute` |
| 4 | `#hero-card` hidden on the compare route | fieldwork image stage |
| 5 | the disclosure loses its `:focus-visible` ring | the keyboard disclosure check |
| 6 | `savePrefs()` suppressed | *the page wrote the save itself* |
| 7 | the note write suppressed | *and the note beside it* |
| 8 | one observation dropped from the dated list | *18 observations were recorded and 17 rows are printed* |
| 9 | an unknown accident count read as zero | *an unknown accident count is never a clean record* |
| 10 | the price journey quotes the build date | *the car's own last observation is …* |
| 11 | the all-in figure loses its estimate mark | *otd is marked as this page's estimate* |
| 12 | a car with no coordinates given a position | *the map names the omission* |
| 13 | a departure drops the cause that is not a sale | *an absence from a sampled fetch is not the end of the listing* |
| 14 | the filter widens itself | *the filter … was not quietly widened* |

**Two of them found real holes in my own checks**, and each is closed with the assertion it
showed was missing. #9 survived because reading the card and the sheet misses the surface that
renders an accident count: the card prints the record's own `flags` list and the sheet never
calls `flagsCell()` at all, so the subject is saved into the comparison now and the
**Reported history** cell read directly. #13 survived because `Stopped being seen | fetch
window | left the tracked` is an OR, so a mutant that dropped the uncertainty and asserted the
listing had ended still matched the first branch; all three clauses are required now.

## Verification

Run at the candidate tree, on this sandbox (Python 3.11.15, Node 22.22.2, Playwright 1.56.1
with the pre-installed Chromium) against an isolated read-only design-system checkout at the
pinned `ad5aa0f`. CI runs Python 3.12 and Node 24; the divergence is the sandbox's, not the
repository's, and is recorded rather than papered over.

| # | Command | Result | Executed |
| --- | --- | --- | --- |
| 1 | `AUTODEV_API_KEY=test-key-not-used python -m unittest discover -s tests -t . -v` | **PASS** | 570 run, 569 passed, 1 skipped, 0 failed |
| 2 | `node tools/design_snapshot.mjs` | **PASS** | 22/22 assets verified from `ad5aa0f` |
| 3 | `node tools/consumer_lint_ci.mjs "$DS" docs/index.html docs/how.html tools/og_card.html` | **PASS** | policy clean; 10 candidates already decided, 0 new |
| 4 | `node tools/studio_smoke.mjs` | **PASS** | all checks, 0 page errors |
| 5 | `node tools/workspace_smoke.mjs` | **PASS** | all checks, 0 page errors |
| 6 | `node tools/discovery_smoke.mjs` | **PASS** | all checks, 0 page errors |
| 7 | `node tools/browse_smoke.mjs --shots "$SHOTS/browse"` | **PASS** | 34/34 steps, 0 page errors (21 pre-existing + 13 added) |
| 8 | `node tools/dashboard_smoke.mjs "$DS" --shots "$SHOTS/dashboard"` | **PASS** | 308/308 checks, 15 skipped for want of a subject, 0 page errors |
| 9 | `node tools/fieldwork_smoke.mjs "$DS" --shots "$SHOTS/fieldwork"` | **PASS** | 26/26 (was: uncaught timeout, 0 checks) |
| 10 | `node tools/matrix_navigation_smoke.mjs "$DS" --shots "$SHOTS/matrix"` | **PASS** | 26/26 (was: 1/2, "expected 26, recorded 2") |

No harness was consolidated or removed, so every command above is the one that was run.

**Why 308 and not #79's 309.** `dashboard_smoke`'s expected total is 323 and it still is: #79's
pull-request run (`34802551292`, at `cd0a82f`) recorded 309/309 with 14 skipped, and this
snapshot gives 308/308 with 15 skipped. One check moved from run to skip across two nights of
new tracker data; this run's skip list is all data-dependent, one of them reading *bmw i5 does
not print a typical-days split today*. #79's skip list was not captured, so which check moved is
not claimed here — only that the total is unchanged, that nothing in this pass touches
`dashboard_smoke`, and that the same 308/15 was measured at the untouched baseline before any
edit.

**Skips are reported as skips.** `dashboard_smoke` reports 15 data-dependent skips, each a
branch with no subject in this snapshot: an unfetched model, a model with fewer than two trims
to compare, a fallback cohort, a car that left one watch while still listed on another, a
first-fetch model, and so on. Each prints its own reason. **None of them is the flat premium
window**, which passes. They are neither passes nor failures and are not counted as coverage.
The same discipline is why the two early-returning steps in `browse_smoke` are asserts now:
this suite has 0 skips.

**Not a pass:** a missing browser, a missing dependency, an unmatched `--only`, zero executed
assertions or a data-dependent early return. Every number above is an executed count read off
the run, and the two steps that used to return early on an absent subject are asserts now.

**Budgets preserved, not adjusted.** `dashboard_smoke`'s transfer guard is unchanged —
`index.html` 200 KB and `data.json` 400 KB gzipped — and reports 151 KB and 337 KB, inside
both. No data was trimmed, no writer repaired and no output regenerated to obtain a green.

### Evidence classes, kept apart

- **Historical CI.** #79's merged-commit run `34807296459` (test, consumer-lint, dashboard) and
  the Pages build/deploy runs at the 15 September snapshot. A deployment success is not an
  application-test result, and neither is quoted as one here.
- **Newly executed local checks.** The table above, plus the fourteen negative controls, run on
  this machine at the candidate tree.
- **Fixture-based screenshots and behaviour.** Everything captured by these harnesses. Dealer
  photographs are the offline stand-in — a labelled geometry fixture in `fieldwork_smoke`, a
  1×1 pixel elsewhere — and map geometry is a synthetic atlas. **These are not real
  dealer-photo or live-map proof.**
- **Live-site observations.** None. The sandbox proxy does not reach
  `spicychicken59.github.io`, so nothing here is a claim about what is served.
- **Unverified seller facts.** Every price, mileage, accident count and availability in the
  record is the feed's, not confirmed with any dealer. No deployment and no seller contact of
  any kind was made.

## Keep / fix / defer / omit

**Keep.** The route split — Explore browses, Compare & save decides — and harnesses that reach
a panel the way a reader does. `visible`-state and role-based locators over CSS overrides and
DOM-attachment waits. Named fixture subjects served to one context, never written to
`docs/data.json`. Comparison membership separate from saving. The record's own change detection
as the garage baseline. Both harnesses in `check.yml`.

**Fix if it bites.** The journey step takes the first card's VIN, so a change to the default
sort changes its subject; the assertions read the record for that VIN, so it stays correct, but
the shots move. `browse_smoke` is now ~4 minutes here and about a minute on a runner.

**Defer.** The <320px page overflow, pre-existing and unchanged — the supported floor is 320px,
where the page is clean. The two harnesses still have no `--only`, so a mutant is judged by a
whole run.

**Omit.** Any change to the tracker, the watchlist, `targets.json`, the ledgers, ranking,
shipping or financing arithmetic, the collection allocation, the API budget or the real garage.
`loop.yml` is untouched: it is a weekly design-system issue digest with `contents:read` and
`issues:write`, not an automatic updater, and a successful digest is not an upgrade. No shared
release was cut, no re-vendoring done, no schedule altered, no tracker run and no `daily.yml`
dispatch.

## Not claimable

A live fetch or any provider call. A tracker run. Resend or any mail. A Pages deployment — the
proxy refuses the served host from here, so distribution rests on the runner-side gate. A real
dealer photograph or a real map tile. Any seller-confirmed fact. And expectancy of any kind:
this pass changes what the checks can see, not what the record says.
