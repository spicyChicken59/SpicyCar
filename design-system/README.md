# SpicyChicken59 Design System

The visual standard for everything Mohammed Tahir Madni ships under **SpicyChicken59** —
dashboards, documents, READMEs, decks. Cobalt structure, one spice accent, dark by default.

**Start here:** open `styleguide.html` (every token and component, both modes), then read
`DESIGN_SYSTEM.md` (the rules). New project: copy `sc59.css` + `starter.html` and build.

| File | Purpose |
|---|---|
| `sc59.css` | The system — tokens, base styles, components. One file, no build step. |
| `starter.html` | Page skeleton: masthead with theme toggle, footer with watermark. |
| `styleguide.html` | Living reference. Open it next to any page you're building. |
| `tokens.json` | Tokens in W3C format for Figma / Tokens Studio. Generated from the CSS. |
| `DESIGN_SYSTEM.md` | The standard. Attach it to Claude or any AI tool before generating material. |
| `AUDIT-AND-ROADMAP.md` | Where it came from, what changed and why, what's next, changelog. |
| `CHECKLIST.md` | Ten lines to tick before any page ships. |
| `assets/` | The SpicyChicken59 mark: SVG forms, avatar tile, lockups, favicons. |

This folder is built to become its own repository (`spicyChicken59/design-system`) with GitHub
Pages enabled, so projects can link `sc59.css` instead of copying it. Until then, copy the file and
note the version from its header.
