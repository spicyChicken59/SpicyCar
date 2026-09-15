# Your choices, and the market around them · 15 September 2026

One distinction, made where it was missing: the cars the reader is actually choosing between,
and the cars Car tracks so the market has a shape. No new recommendation model, no new
configuration, no ranking arithmetic touched.

**Verified starting SHA** `a9c42bb66a379690360ca1363aa8c325df445adb` (remote `main`, the merge
of #81). **Snapshot measured** `docs/data.json` SHA-256 `e4ba1add…`, `generated` and
`data_through` 2026-09-15, 1,678 live listings across 21 models. Design snapshot v2.13.0 at
`ad5aa0f`, 22/22 verified, not re-vendored and not drifted.

## The reproduction, before anything was edited

`buyer.shopping` names two trims — `bmw-i5-edrive40` and `bmw-i7-edrive50` — so the shopping
set is BMW i5 and BMW i7. With no local model choice, in **Compare & save**, under a heading
reading **Spicy picks**, eight cards. Five were models the reader never chose:

| group | cards |
| --- | --- |
| drivable — IL/OH/IN/WI | BMW i5 8% · BMW i5 7% · **Hyundai Ioniq 9 29%** · **Volkswagen ID.4 18%** |
| worth the ship — nationwide | BMW i5 25% · **Ioniq 9 31%** · **Ioniq 9 27%** · **Toyota bZ4X 24%** |

A three-row SUV offered as a pick to someone choosing between two saloons, and numerically
ahead of every BMW on the page. Each out-of-set card wore a chip reading **"comparison"**,
which asserts the opposite of what was true: the car was not an alternative under
consideration, it was in the list because the watchlist tracks the wider market.

**Why this is recommendation scope and not broad market context.** The heading is the product's
own recommendation surface, marked with the spice chick. The cards are actionable — a save
star and *Open the listing →*. And they sat in Compare & save, the workspace the product
defines as where deciding happens. Breadth is not the complaint; the claim is.

With **no shopping models at all** it was worse: all eight cards came from the 21-model
watchlist, every one chipped "comparison", and the heading still said *Spicy picks* — the whole
watchlist silently promoted to the reader's picks.

## The two concepts, exactly

**Shopping set** — the models the reader is choosing between. `shoppingSet()`, from explicit
intent only and in the order the product already establishes it: the **local model choices**
when this browser has made any, **otherwise** the **`buyer.shopping` defaults** the record
carries. It is never inferred from price, body style, drivetrain, brand, ranking score or any
automotive judgement of mine. No second configuration was added; the architecture already
represented this.

**Market coverage** — everything else the watchlist tracks. Still collected, still shown,
still counted, never deleted for being outside the set.

## Surfaces whose scope changed

| Surface | Before | After |
| --- | --- | --- |
| **Spicy picks** (`renderPicks`) | ranked the whole watchlist together, shopped models merely sorted first and holding 2 reserved seats | recommends only the shopping set; strong values outside it move to a collapsed **market context** group that says it is not a recommendation |
| **No shopping set** | heading still read *Spicy picks* over the whole watchlist | heading reads *Strong values across the market*, zero recommendations, and the page says how to turn them into picks |
| **Role chip**, three call sites | three copies of `shopping ? 'shopping' : 'comparison'`, read off `buyer.shopping` | one `roleChip()` reading the shopping set: **your choice** / **market context** |

## Surfaces deliberately left broad

Explore cards, the map, Price & miles, the market-history charts, the market counts and the
model index are unchanged and still cover the whole watchlist: their contract is coverage, not
recommendation. `passesShared()` — the one predicate the three Explore views read — was not
touched, so they remain one candidate set. The **model comparison card** was already scoped by
construction (`comparingModels()` requires the reader to have picked two or more on purpose)
and needed no change; it now reads *your choice* on models the reader put there, where it used
to call them *comparison* even as they sat in a comparison the reader had built.

## The ranking mathematics did not change

`annotateValue()` scores every eligible listing once at boot, against its **own model's**
cohort — the comment above it has always said so: "against the FULL market — so the row note,
the sort, and the pick cards all say the same number instead of a median that shifts under
filters." Which models are in view therefore cannot move a score. Only the eligible set
changed. Nothing was touched in typical value, value-vs-typical, the spicy-pick score, the
mileage allowance, shipping, financing or the ranking weights, and no threshold was retuned.
The BMW percentages are identical before and after — 8, 7, 25 — and the seats the Ioniq 9 and
the ID.4 held are filled by the i7s that were always next in line. A fixture asserts it
directly: every car appearing both with and without the market model in the set carries the
same percentage.

## Fixtures and the negative control

Five steps in the CI-wired `tools/browse_smoke.mjs`, on a shopping model and a market model
chosen from the record by position (BMW i5 and Acura ZDX on this snapshot), with the bargain
**constructed rather than observed** — a clean 9,000-mile car at 45% of its cohort's median —
so the case never depends on whichever model happens to score best tomorrow.

They cover: scope and market context; choosing the market model so it competes; nothing chosen;
one chosen model; a chosen model emptied by filters; the choice surviving a reload made through
the picker; scores unmoved by scope; and a saved out-of-set car staying saved, staying in its
comparison and staying reopenable.

**Negative control** — the smallest reversible change, `const chosen = scored`, making a
market-context-only model eligible again:

> FAIL · *a pick is only ever a model you chose* — **every recommendation is the model that was chosen, not Acura ZDX**

Restored from git and the restore proved by SHA-256.

## Two defects of my own, found by the fixtures

The bargain was first planted on the model's median-priced car, which landed in a **six-car
cohort**. The page's rule is that nine are the fewest that can put a car outside its cohort's
95% interval, so the cheapest car in the record scored nothing and the test was measuring the
cohort floor rather than scope. It is planted in the largest nine-plus cohort now.

And two steps wrote the shopping choice into `localStorage` and reloaded — which `session()`'s
`addInitScript` re-seeds on every navigation, so the write was clobbered and the reload proved
nothing. Both make the choice through the picker, the way a reader does.

A third was found by looking rather than by any assertion: the design system lowercases a
`<details>` summary in mono, so "every model Car tracks" rendered as "every model car tracks",
which parses as a kind of car. It reads "every model on the watchlist" now.

## Desktop, mobile and theme inspection

Measured and looked at at 1280 and 390, light and dark, in both the chosen and the
nothing-chosen state — eight captures:

- **zero horizontal overflow** in all eight;
- the heading switches correctly in all eight (*Spicy picks* / *Strong values across the market*);
- the market group is **collapsed** in all eight, so an unchosen car never sits above a chosen one;
- its summary reads at **7.23:1 light / 10.55:1 dark** and fits the viewport at both widths
  (38–352 of 390; 50–1230 of 1280);
- exactly **two** role words on the page, and no badge was added — the chip already sat on every
  pick card; one word changed;
- the primary actions are untouched: *Open the listing →* and the save star are where they were.
