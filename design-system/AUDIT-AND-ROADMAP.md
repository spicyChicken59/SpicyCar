# Audit and roadmap — from the RPS BI template to SpicyChicken59 v1.0

*August 2026. Mohammed Tahir Madni / SpicyChicken59.*

## 1. What the template got right (kept)

The RPS BI system is a well-made corporate standard. Six things in it are worth keeping as the spine
of a personal system, and v1.0 keeps all six:

- **A real token layer.** Colors, type, spacing, radii and motion were already named variables
  with stated uses — most "design systems" people attach to AI tools are mood boards.
- **The eyebrow as a signature.** A mono, uppercase, tracked label above every heading is cheap,
  distinctive, and survives every medium (HTML, Markdown, slides, email).
- **Restraint rules that are checkable.** Flat fills, hairline borders, modest radii, no new hues,
  no decorative gradients, no icon library. Each is a yes/no test at review time.
- **Voice and casing rules.** Sentence case, plain-spoken, one next action. These shape content, not
  just chrome, which is what makes a portfolio feel coherent.
- **A small component vocabulary** — card, callout, chip, eyebrow, data table — rather than a big one.
- **Plain-HTML equivalents.** The note that every component must be reproducible with inline styles
  (for email, for exports) is what makes a system survive outside its own CSS.

## 2. What was template-bound (removed or replaced)

| Template element | Problem for a personal system | v1.0 |
|---|---|---|
| Nucor brand-kit greens, "RPS BI leads / Nucor supports" | Someone else's brand and hierarchy | Cobalt + spice; **project leads / SpicyChicken59 endorses / person authors** |
| Green-tinted neutrals (`#F5F4F0`, `#cdd8d2`, `#5B6770`) | Leaked green into every surface | Re-tinted cool (`#F2F4F8`, `#CFD6DE`, `#5D6671`) |
| Light blue "interactivity only" | Invisible once the palette is blue | Spice (`#FE825C` / `#D24100`) — the one hot accent |
| Provenance chips `[K] [B] [A] [Q] [W]` | Meaningful only inside Nucor course material | Generic chip tones (brand, neutral, spice, good, warn, danger); the letters can still ride on them in documents |
| `.ana` / `.ws` course callouts, Hinglish digest, SQL readout, ASCII blueprint | Content-specific to one course | Dropped. `core`, `ink`, `warning` kept; `spice` added for the one next action |
| React bundle namespace `NuRampDesignSystem_778fb7`, `_ds/…` paths, Design-Component `<x-import>` | Tooling that doesn't exist outside that workspace | One CSS file, zero build. Tokens also exported as `tokens.json` |
| Print-bound "Deliverable" paginator template | Valuable, but a separate artifact with its own script | Deferred to the roadmap (§4) |
| Light-only; dark used only for covers | Owner's preference is dark-first | Dark default, light first-class, both validated |
| Embedded Nucor / RPS BI logos (Appendix A) | Not yours | Removed. Mark slots reserved with construction constraints (§9 of the spec) |
| No chart palette; "no new hues" applied to data too | Every dashboard would have reinvented colors | A validated 5-slot categorical set, sequential ramp, emphasis pair, per mode |
| Two slightly different values for the same token across the spec and the CSS (`#D9E8E2` vs `#e3ede7`, `#5B6770` vs `#6b7d76`, `#A1CEAD` vs `#4a8f63`) | Drift — the spec and the CSS had already diverged | One source: `sc59.css`; `tokens.json` and the style guide are generated or read from it |

## 3. How the colors were derived (so it can be repeated)

Every green was converted to OKLCH, its **lightness kept exactly**, hue rotated to 255° (cobalt), and
converted back with gamut mapping. That preserves every contrast pair the template had. Two
deliberate deviations: chroma raised on the two darkest steps (a low-chroma blue reads as gray where
a low-chroma green still reads green), and neutrals re-tinted to the same hue so no surface leans
green. The chart slots were then chosen inside the `dataviz` lightness band for each mode and
verified with its validator (colorblind separation ≥ 8 ΔE, normal-vision ≥ 15, contrast ≥ 3:1 against
the card surface). The same procedure produces a new accent or mode in minutes: pick L and C, rotate
hue, validate.

## 4. Roadmap — in order

1. **Marks.** ✅ Done. The chick (v1.1.0) and the first project mark, the Auto Market Tracker car
   (v1.1.1), both on the 24-unit grid. Future projects repeat the recipe: cobalt body, file-fold,
   cream details, one spice element.
2. **Own repository.** Move this folder to `spicyChicken59/design-system`, enable Pages, and link
   `https://spicychicken59.github.io/design-system/sc59.css` from projects instead of copying. Tag
   releases (`v1.0.0`); projects pin a tag. Keep a `CHANGELOG` section below.
3. **Type decision.** ✅ Done in v1.2: Bricolage Grotesque (display) / Instrument Sans (body) /
   IBM Plex Mono (labels), chosen to break the visual tie to the workplace template along with the
   lowercase `//` eyebrow, folded callouts, squared chips, and open table headers.
4. **Figma — when the marks start.** Figma (free tier) is the right tool for drawing the marks and
   for exploring layouts; it is not needed to *hold* the system. Import `tokens.json` with a variables
   plugin (Tokens Studio or "Variables Import/Export") so Figma colors match the CSS exactly, and
   treat the CSS as the source of truth: a change goes CSS → `tokens.json` → Figma, never the reverse.
5. **README / GitHub profile treatment.** A profile README and a repo README template in the same
   voice (eyebrow line, one-sentence dek, a "what you get" table, one next action). Uniformity starts
   on the profile page, before anyone clicks through.
6. **Document template.** Bring back the print-bound "Deliverable" template on SC59 tokens — US Letter,
   running header, numbered section rules — as `deliverable.html`, for reports and one-pagers.
7. **Slide template.** Six-slide arc (cover on `--sc-ink`, problem, summary, architecture, roadmap,
   ask) as standalone 1280×720 HTML pages.
8. **Component growth, only on demand.** Likely next: a timeline/strip for built-vs-planned, a
   dual-audience pair (exec + engineer), a meter. Add each to the style guide the day it ships.
9. **Keep the pre-ship check honest.** `CHECKLIST.md` ships with v1.0; re-read it every release and
   add a line whenever a real mistake slips through.

## 5. Changelog

- **1.2.0 (2026-08-23)** — De-templated from the RPS BI source and simplified to one mark. New type
  stack: Bricolage Grotesque / Instrument Sans / IBM Plex Mono (retiring the template's trio). The
  signature changes from ALL-CAPS tracked eyebrows to lowercase mono with a `//` prefix; callouts
  trade the left border for the mark's file-fold corner; chips and tabs square off; table headers
  open up (lowercase mono over a cobalt rule, no filled bar). The chick becomes the mark for every
  project — the car mark is retired, its generator archived in the tracker's `docs/brand/`.
- **1.1.1 (2026-08-23)** — First project mark: the Auto Market Tracker car, generated on the family
  grid (cobalt body, ping comb, file-fold), four forms + tile in the tracker's `docs/brand/`; swapped
  into that dashboard's masthead and favicon; family figure added to the style guide.
- **1.1.0 (2026-08-23)** — The SpicyChicken59 mark locked and shipped: `assets/` with four SVG forms
  (traced from the finalized Claude Design renders), avatar tile, lockup PNGs, favicon set; wine
  primitives (`--sc-wine-300/500/700/900`); `img.sc-mark` classes; the mark wired into the style
  guide masthead and every watermark; spec §9 rewritten from "reserved" to shipped.

- **1.0.0 (2026-08-23)** — First SpicyChicken59 release. Cobalt remap of the RPS BI greens at identical
  lightness; spice accent; dark default + light mode; cool neutrals; status set; validated chart
  palette; 18 components; `tokens.json`; `starter.html`; `styleguide.html`; `CHECKLIST.md`. Logos and marks reserved.
