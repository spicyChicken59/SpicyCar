# SpicyChicken59 Design System — v1.2

**The visual standard for everything Mohammed Tahir Madni ships under SpicyChicken59.**

Attach this file (and `sc59.css`) to any build — a dashboard, a README, a deck, a one-pager — and
follow it exactly. When something seems to need a new color, a new font, or a new component, it
needs a new *use* of an existing one. Uniformity across projects is the whole point.

Files in this folder:

| File | What it is |
|---|---|
| `sc59.css` | The system. Tokens + base + components, dark default, light mode. One file, no build. |
| `tokens.json` | The same tokens in W3C Design Tokens format, resolved per mode — for Figma variables / Tokens Studio. Generated from the CSS; edit the CSS. |
| `starter.html` | The page skeleton every project starts from: masthead, theme toggle, footer with watermark. |
| `styleguide.html` | Living reference — every token and component rendered in both modes. |
| `DESIGN_SYSTEM.md` | This document. |
| `AUDIT-AND-ROADMAP.md` | Where this came from, what was changed and why, what's next. |
| `CHECKLIST.md` | The pre-ship check. |
| `assets/` | The brand mark: four SVG forms, avatar tile, lockup PNGs, favicon set. |

---

## 1. Brand hierarchy

- **One mark: the chick.** Every project wears it — masthead, favicon, avatar — with the project's
  name set beside it in the display face. The name is the project's identity; the mark is the maker's.
- **The watermark repeats it.** Mono form + wordmark, footer-right on every page, same size and
  opacity everywhere, linking to the profile.
- **The person authors.** "Mohammed Tahir Madni" appears in READMEs, document covers, and `rel="author"`
  — never in UI chrome.

- **Voice:** plain-spoken and specific. State the outcome and the reason; no hype, no buzzword stacks.
  *"Landed price = what it costs to put it in your driveway."*
- **Casing:** sentence case for headlines and body. ALL-CAPS belongs to mono eyebrows and short labels only.
- **One next action.** A page, document, or deck ends with exactly one clear next step — never a menu.

---

## 2. Color

Cobalt-anchored, dark-first, with one hot accent. **Hard rules: no new hues, no decorative gradients,
flat fills only.** Every value below is validated (WCAG text contrast; the chart slots pass the
colorblind and normal-vision separation checks in `dataviz`).

### Provenance
The cobalt ramp carries the **exact OKLCH lightness** of the template's greens, so every contrast
pairing the old system relied on still holds. Chroma was raised on the two darkest steps only —
blue needs more saturation than green to read as a color at low lightness. Neutrals were re-tinted
cool so nothing on the page leans green anymore.

### Primitives (never used directly in a page)

| Ramp | Steps | Notes |
|---|---|---|
| **Cobalt** | 50 `#F3F7FC` · 100 `#E5EEF9` · 200 `#C6D9F2` · 300 `#A3C4EE` · 400 `#71A1DF` · 500 `#4682CC` · 600 `#2766B1` · **700 `#165194`** · 800 `#1B3E69` · **900 `#182E4B`** · **950 `#111F31`** | 700 = the old medium green's role · 900 = the old dark green · 950 = covers, code, mastheads |
| **Spice** | 300 `#FFAC92` · **400 `#FE825C`** · 500 `#EE5D2B` · **600 `#D24100`** · 700 `#AC3400` · 800 `#852B09` | 400 on dark, 600 on light. Links, primary actions, focus. |
| **Night** | 950 `#09111B` · 900 `#121C2A` · 850 `#162232` · 800 `#1B2737` · 700 `#2A394D` · 600 `#3C4E66` | Dark-mode surfaces and hairlines. Cool, never pure black. |
| **Gray** | 50 `#F2F4F8` · 100 `#EDF2FA` · 200 `#E6E8EB` · 300 `#CFD6DE` · 400 `#A6AFBB` · 500 `#737B86` · 600 `#5D6671` · 800 `#2F3741` | Light-mode surfaces, borders, text. |
| **Status** | good `#4EA954` / `#1B7E2A` · warn `#D59800` / `#906400` · danger `#E94E5A` / `#BE2132` · info = cobalt | dark / light. Reserved: never a chart series. Always paired with a word or icon. |
| **Wine** | 900 `#580819` · **700 `#82182B`** · **500 `#A12E3E`** · 300 `#E2AFB0` | The brand mark's family (Zenith-style maroon). 700 on light, 500 on dark. Reserved for the mark and brand moments — never a UI state, never a chart series. |

### Semantic roles — what a color *does*

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `--sc-bg` | night-950 | gray-50 | page |
| `--sc-surface` | night-900 | white | cards, tables, inputs |
| `--sc-raised` / `--sc-hover` | night-800 / 850 | cobalt-50 / gray-100 | code, chips, row hover |
| `--sc-border` / `--sc-border-strong` | night-700 / 600 | gray-300 / 400 | hairlines, input borders |
| `--sc-ink` · `--sc-on-ink` · `--sc-on-ink-2` | cobalt-950 · `#F4F7FB` · cobalt-300 | same | masthead, footer, covers, tooltips — **dark in both modes** |
| `--sc-heading` / `--sc-text` / `--sc-text-2` / `--sc-text-3` | `#F4F7FB` / `#D8DFE8` / `#92A0B1` / `#6D7C8F` | cobalt-900 / gray-800 / gray-600 / gray-500 | headings / body / muted / faint (labels only) |
| `--sc-brand` / `--sc-brand-strong` | cobalt-300 / 400 | cobalt-700 / 600 | eyebrows, labels, data emphasis |
| `--sc-brand-fill` / `--sc-brand-line` | `#19273A` / cobalt-700 | cobalt-100 / cobalt-700 | tinted fills, left borders, table headers |
| `--sc-accent` / `--sc-accent-hover` / `--sc-on-accent` | spice-400 / 300 / night-950 | spice-600 / 700 / white | links, primary button, focus ring |
| `--sc-good` `--sc-warn` `--sc-danger` `--sc-info` (+ `-fill`) | see status | see status | state, never decoration |
| `--sc-chart-1…5`, `--sc-chart-other`, `--sc-chart-emphasis`, `--sc-chart-context`, `--sc-chart-seq-1…5`, `--sc-chart-grid` | §8 | §8 | data only |

### The two color jobs
- **Cobalt is structure.** Surfaces, headings, eyebrows, rules, table headers, the emphasized series.
- **Spice is heat.** Links, the primary button, focus rings, the one "look here" moment. Used sparingly:
  if everything is spicy, nothing is. Spice never colors body text, headings, or large fills.

---

## 3. Typography

Three families, loaded from Google Fonts by `sc59.css`. Do not substitute.

```
https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=Instrument+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap
```

- **Bricolage Grotesque 700** — display and headlines (h1–h3). Modest negative tracking
  (−.2px to −1px); its ink-trap personality carries the identity, not the tracking.
- **Instrument Sans 400–700** — body and UI at 15px; 13px captions; 18px deks. **Numbers in tiles
  and hero figures are Instrument too** — never the display face, never tabular figures at display sizes.
- **IBM Plex Mono 400–600** — eyebrows, labels, chips, table headers, code, VINs, dates.

**The signature tell:** a **lowercase** mono eyebrow opened by a dimmed `//`, modest tracking
(≤.5px). Use it on every title block, section opener, and callout label. Never ALL-CAPS with wide
tracking — that is the previous system's signature, deliberately retired.

Scale: eyebrow 12 · caption 13 · body 15 · lead 18 · h3 20 · h2 28 · h1 40 · display 56.
Line height 1.1 tight / 1.2 heading / 1.6 body. `tabular-nums` only in columns that must align.

## 4. Spacing, shape, motion

- **8px rhythm:** 4, 8, 16, 24, 32, 40, 48, 64, 80, 96 (`--sc-s0`…`--sc-s12`).
- **Radii:** 6 (inputs, small) · 10 (callouts, buttons) · 14 (cards) · 999 (pills). Never bubbly.
- **Borders:** 1px hairlines in `--sc-border`. Separate with color, not heavy shadow.
- **Shadow:** `--sc-shadow` only — soft and rare.
- **Motion:** fades and short slides, 240ms, ease-out. Base state = finished state. No bounce, no loops.
  Hover signals with color (fill toward `--sc-hover`, border toward `--sc-brand-strong`), never scale.
  `prefers-reduced-motion` zeroes it.
- **Widths:** prose 800 · content 1120 · wide (dashboards, tables) 1280.

---

## 5. Themes

**Dark is the default.** The page follows the OS setting; the masthead toggle (Dark / Light / Auto)
overrides it and is remembered per browser. Mechanics: `:root` carries the dark tokens;
`@media (prefers-color-scheme: light)` and `:root[data-theme="light"]` carry the light set;
`data-theme="dark"` pins dark. The script in `starter.html` runs before paint so there is no flash.

Stays dark in both modes: masthead, footer, code blocks, tooltips, covers. That is what keeps a
light page recognisably the same product as its dark sibling.

Light mode is not an inversion. Every light value was chosen and contrast-checked on its own
(body 12:1, muted 5.8:1, links 4.7:1, brand labels 8:1 on white).

---

## 6. Components

All classes are prefixed `sc-`. Modifiers use `--`, states use `is-`/`aria-*`. Reproduce these;
don't invent siblings.

| Component | Class | Rules |
|---|---|---|
| Masthead | `.sc-masthead` · `.sc-brand` (`__mark` `__name` `__sub`) · `.sc-masthead__right` · `.sc-nav` | Project left, endorsement/toggle right. Dark in both modes. Nav underline is spice. |
| Theme toggle | `.sc-theme-toggle` | Three buttons, `aria-pressed` on the active one. Lives in the masthead. |
| Eyebrow | `.sc-eyebrow` (`--accent`, `--muted`) | Always above a heading. Lowercase mono, `//` prefix added by CSS, brand cobalt by default. |
| Dek / meta | `.sc-dek` · `.sc-meta` | One sentence; mono meta row with `·` separators. |
| Card | `.sc-card` (`--raised`) · `.sc-card__head` · `.sc-hint` | One discrete idea per card. Heading + hint, optional action right. |
| Stat tile | `.sc-tile` · `__label` `__value` (`--sm`) `__sub` · `.sc-delta` (`--good` `--bad` `--flat`) | Label · value · context · signed delta vs a named period. Sparkline optional. |
| Hero figure | `.sc-hero` | The one number a page leads with. Exactly one per view. |
| Callout | `.sc-callout` (`--core` `--ink` `--spice` `--warning`) · `__label` | Tinted fill + the file-fold corner (`--sc-fold`). Core = the single takeaway, max one per section. Ink = must-remember. Spice = the one next action. Warning adds the 2px danger border. |
| Chip | `.sc-chip` (`--brand` `--neutral` `--spice` `--good` `--warn` `--danger` `--solid`) | Squared lowercase mono chip (6px radius). Meaning carried by the word, tone reinforces. Danger is the outline form. |
| Button | `.sc-btn` (`--primary` `--secondary` `--ghost` `--sm`) | Primary = spice, one per view. Secondary = outline. |
| Inputs | `.sc-input` · `.sc-select` · `.sc-check` · `.sc-field` · `.sc-filters` | Filters: one row, above everything they scope. |
| Table | `.sc-table` (`--compact`) · `.sc-table-scroll` · `th.is-sortable` `.is-sorted` · `.sc-num` | Open header: lowercase mono over a 2px cobalt rule, no filled bar. Hairline rows, row hover. Numbers right-aligned, tabular. |
| Tooltip / legend | `.sc-tooltip` (`__date` `__row` `__meta`) · `.sc-legend` | Ink surface. Values lead, labels follow; line keys not boxes. |
| Tabs | `.sc-tabs` · `.sc-tab[aria-selected]` (`--case`) | Pill row for switching the subject, not for navigation. `--case` keeps proper-noun casing (BMW i5). |
| Notice | `.sc-notice` | The stop-and-read block: what failed, what to do. |
| Footer + watermark | `.sc-foot` · `.sc-watermark` (`__name`) | Source line left, brand watermark right. Every page. |
| Document | `.sc-doc` | On-screen prose surface, 800px, section rules in brand-line. |
| Details | `.sc-details` | Table-view twin under every chart. |

Plain-HTML equivalents (email, exported HTML) copy the values from `tokens.json`; the chip, eyebrow,
card, and callout are the four worth inlining.

---

## 7. Iconography & imagery

No decorative icon library. The visual language is type, color, chips, and layout; glyphs stay
textual (`→ ↓ ↗ ▲ ▼`). If an app UI truly needs icons, use a restrained stroke set (Lucide, 1.5px)
and note the substitution. Photos are content (a car, a screenshot), never decoration; they sit in
hairline-bordered, 6px-radius frames. No emoji in UI or documents.

---

## 8. Data visualization

Validated with the `dataviz` method; re-run the checks if a slot ever changes.

- **Categorical (fixed order, never cycled):** 1 cobalt · 2 spice · 3 teal · 4 violet · 5 pink ·
  Other = gray. Bars/lines may use all five with a legend; **all-pairs forms** (scatter, bubble,
  maps) use slots 1–3 only.
  Dark: `#4682CC #E75623 #009D82 #8B5DCE #CD4290` · Light: `#2766B1 #D24100 #00856E #7541B8 #BC3181`.
- **Emphasis** (the usual right answer): the series that matters in `--sc-chart-emphasis`, the rest
  in `--sc-chart-context` gray.
- **Sequential:** the cobalt ramp, light→dark (`--sc-chart-seq-1…5`). **Diverging:** cobalt ↔ spice
  around a neutral gray midpoint.
- **Status** colors never appear as a series.
- **Marks:** 2px lines, ≥8px markers with a 2px surface ring, bars ≤24px with 4px rounded data-ends,
  gridlines solid hairlines in `--sc-chart-grid`. One y-axis, always. Legend for ≥2 series; direct-label
  selectively (the end, the extreme). Text wears text tokens, never the series color.
- **Interaction:** crosshair + one tooltip listing every series on line charts; per-mark tooltip on
  bars; keyboard arrows step through; a `.sc-details` table twin under every chart.

---

## 9. Marks & watermark

**The SpicyChicken59 mark — the chick — locked 2026-08-23.** Wine head with a file-fold corner,
teardrop flame comb in spice, cream eyes and diamond beak, on a 24-unit grid. The head is 9 units
tall; the date it carries is private.

Files in `assets/`:

| File | Use |
|---|---|
| `sc59-mark-color-light.svg` | wine-700 body — white and cream surfaces |
| `sc59-mark-color-dark.svg` | wine-500 body — ink and dark surfaces (mastheads) |
| `sc59-mark-mono-cream.svg` / `sc59-mark-mono-ink.svg` | watermarks and one-color uses |
| `sc59-avatar-tile.svg` · `favicon.ico` · `apple-touch-icon.png` · `favicon-512.png` | avatar and favicon set (mark on the cream tile) |
| `sc59-lockup-cream.png` / `sc59-lockup-ink.png` | horizontal lockups for external surfaces |

Rules: never recolor, outline, rotate, stretch, or add effects; clear space ≥ the flame's height on
all sides; the mark never merges with a project mark or sits inside another shape (the tile is the
one exception). **Watermark:** mono form, 20px + wordmark in Space Grotesk 700 12px, footer-right,
70% opacity, links to the profile — identical on every page.

**There are no separate project marks.** The chick is the mark for every project — masthead,
favicon, avatar — with the project named in type beside it. (An experimental car mark for the
tracker was built and retired the same day; its generator survives in that repo's `docs/brand/` as
an archive.)

## 10. Accessibility

- Body text ≥ 4.5:1 in both modes; muted ≥ 4.5:1; faint is labels-only at 11–12px bold.
- Focus is always visible: 2px spice ring, 2px offset.
- Color never carries meaning alone — chips have words, deltas have ▲▼, status has a label.
- Every chart has a table twin; every tooltip value is reachable without hovering.
- `prefers-reduced-motion` and `prefers-color-scheme` are honored; the toggle can override either direction.

---

## 11. Using it in a new project

1. Copy `sc59.css` and `starter.html` into the project (or link `sc59.css` from the published
   design-system URL once it lives in its own repo).
2. Rename the project in the masthead and `<title>`; keep the theme script and the footer watermark.
3. Build with `.sc-*` components. Page-specific CSS goes in the page and **only references
   semantic tokens** (`var(--sc-surface)`), never a primitive or a hex.
4. Charts use `--sc-chart-*`. Run the validator if you add a slot.
5. Before shipping, open `styleguide.html` beside the page in both modes: if something on the page
   has no equivalent in the style guide, it's probably a new component — fold it back into the system
   or remove it.
6. Favicon = the project mark (16px form). Social preview = cover on `--sc-ink`, project name in
   Space Grotesk, watermark bottom-right.

Versioning: `sc59.css` carries a version in its header. Bump the minor for new tokens/components,
the major for renamed or removed ones, and note it in `AUDIT-AND-ROADMAP.md`'s changelog.

---

## 12. Do / don't

**Do:** lead with an eyebrow; one core takeaway per section; one spice action per view; dark masthead
and footer in both modes; watermark on every page; table twin under every chart.

**Don't:** add a hue or a gradient; color text with a chart or accent color; use spice for large fills;
put the brand mark in the masthead of a project (it belongs in the watermark); use a display face for
numbers; end with a list of next steps instead of one; use an icon set without noting the substitution.
