# Truthful omission-group captions

Starting main: `70236f4cc18f5d793a3122d8dc975712eb82faf0`.
Starting tree: `101c87a5f1d06c159d90503f71e78318cf8a2390`.
Remote main matched the supplied baseline and there were no open PRs. No AGENTS.md exists in the repository or applicable parent directories. Work uses one fresh branch, `fix/browse-omission-captions`.

## Reproduction and scope

Before application edits, the actual versioned committed snapshot was served locally, with all external map/image/vehicle requests intercepted. Explore → Price & miles → Show them in the cards displayed **11 cars at this location**. The same controls with a labeled missing-coordinate fixture displayed **1 cars at this location**. Both were captured at 320 and 1280 pixels, in light and dark themes. These were browser reproductions, not source-only conclusions.

Only `docs/car-discovery.js` changes application behavior: one transient `groupReason`, set on omission/cluster selection and cleared alongside membership in every existing clear path. Captions distinguish normal results, map area, map group, map omissions and plot omissions. The plot omission sentence now says price or mileage is unavailable. Nothing changes eligibility, candidates, filtering, sorting, persistence, coordinates, transport or data.

## Evidence labels

- `before/before-snapshot-chart-*`: pre-edit real snapshot, 11 plot omissions, all four size/theme combinations.
- `before/caption-fixture-*`: unchanged-main detached worktree, same synthetic fixture as the corrected tests; eight expected FAIL results in `baseline-regressions.log`.
- `after/caption-fixture-*`: corrected application, identical labeled fixture; all eight size/theme cases PASS.
- `after/caption-snapshot-chart-1280-dark.png`: corrected real versioned snapshot.
- The chosen real snapshot scope contains 319 BMW i5/i7 cars, 11 plot omissions and **zero map omissions**. Real map omissions in this scope are NOT RUN for lack of subjects; fixtures cover them explicitly.
- Synthetic fixture: ten real VIN identities from the chosen models, with copied records reduced to those subjects, and explicitly changed fields. Three plot subjects: missing mileage, missing price, both. Three map subjects: missing coordinates, out-of-range coordinates, and the overlapping both-fields subject. These are not new observations and never overwrite data.json.
- Before/after fixture screenshots at both widths and both themes were visually inspected. Counts and relevant cards are visible, long plot captions wrap at 320px, focus is visible, and there is no horizontal page overflow. Blank imagery and empty map tiles are intentional external-request isolation, not a live provider presentation test.

## Exact acceptance journeys

1. Map/plot → omission action activated with Enter → count receives focus → complete omitted VIN membership and ordering checked through real pagination. Plot dots equal exactly the plottable VINs; map marker totals match usable coordinates and omitted cards have no Show on map action.
2. Switch Cars/Map/Price while a group is active: caption reason persists. Space on Show all matching cars restores the current ordered set.
3. Replace plot omissions with their map-omitted overlap; Fit all cars clears the group. Search the fitted map area replaces omissions with the exact located VIN set. Selecting a real popup VIN outside omissions clears the subset and caption.
4. Real snapshot map group: activate each popup row to resolve its exact VIN, then keyboard-activate Browse these cars. Cards equal those VINs in existing order and say **in this map group**, without a verified-address claim. Clear restores the full ordered set.
5. Global accident requirement and price sort survive Show all matching cars. Explicitly change shopping models: transient context clears and only the chosen model is included. Workspace tabs use replaceState, so Explore/Compare route switches retain the group. Deal radar creates a history entry: scope changes clear the group, and Back restores scope without reviving stale captions. Reload clears transient group/area state. Existing accident/sort reset-on-reload behavior remains unchanged; chosen models persist.
6. From a named omission VIN, keyboard-open Quick look, save, write a note, Escape to the same control, inspect comparison, reload without reseeding, and assert exact saved VIN, note and comparison membership.

## Test accounting and limitations

`tools/browse_smoke.mjs` remains wired into the unchanged check workflow. It now declares **76** checks: all **63** existing checks plus **13** caption checks. A declared-count guard prevents silent removal; every synthetic case asserts its subjects exist. One existing assertion is updated solely to require the corrected price-or-mileage wording.

Local focused caption acceptance: **PASS**, 13/13, zero page errors. Uncorrected-main negative controls: **FAIL**, 8/8 expected caption regressions. Python: **PASS**, 604 declared, 603 passed, 0 failed, 1 NOT RUN (existing skip: no national_only target configured). Matrix: **PASS**, 74/74. Photo dossier: **PASS**, 26/26. Studio, workspace, discovery and pinned-design consumer lint: **PASS**. JSON and call-plan validation: **PASS**.

Initial local full dashboard: **FAIL**, 337 declared, 322 passed, 1 failed, 14 NOT RUN subject skips. Its existing decision-panel navigation check missed opening the model during concurrent browser runs. The unchanged-main control and an isolated corrected-source rerun both passed all five decision-panel assertions. No unrelated code or backstop was changed. Initial browse: 62/63 existing checks passed; the old wording assertion failed and was corrected, then passed in isolation. Final-head full CI accounting is recorded in the PR, rather than representing these preliminary runs as complete PASS results.

No live provider/dealer calls, real device Safari acceptance, merge, publication, settings, schedules or sibling/design-system writes. Native device Safari: NOT RUN. Design remains v2.13.0 at ad5aa0f82af7aa98def5807ae33b01bdb0a1e868. #88 transport and preservation tests and #87 matrix coverage are unchanged.

KEEP: candidate membership/order, temporary navigation semantics, saved state, transport, pinned design.
FIX NOW: omission reason and map-group captions, with CI-wired assertions (implemented).
DEFER: Honda external link, dense plot usability, unrelated explanation cleanup, long-term capacity architecture.
OMIT: new persistence, synthesized coordinates, provider calls, filter expansion and unrelated changes.

Next action: independent review of the unmerged PR and its exact-head check results. Merge requires separate guidance approval.
