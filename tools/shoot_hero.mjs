// The two PNGs this repo ships, and the only way they are made.
//
//   node tools/shoot_hero.mjs <design-system-checkout> [--only hero|card]
//                             [--hero docs/screenshot.png] [--card docs/og.png]
//                             [--fonts <dir>] [--offline]
//
// WHY THIS FILE EXISTS. Nothing in the daily run writes a PNG. The tracker
// writes docs/data.json, REPORT.md and data/snapshots.csv; there is no build
// step, and GitHub Pages serves what is committed. So every image in this repo
// is a hand-made artifact, and a hand-made artifact with no committed recipe is
// one that will not be remade — which is exactly how docs/og.png came to spend
// five days unfurling "Every model", ten models, a Chevrolet chip and a Kia EV6
// at $13,901 onto Slack, iMessage, X and LinkedIn while data.json said "The
// watchlist", seven models and five brands. The card was cured by taking the
// numbers out of it (tools/og_card.html). The hero could not be: a hero IS the
// dashboard, so it carries today's numbers and will go stale. This script is
// the answer to that — the recipe, so retaking it is a one-liner instead of an
// archaeology project.
//
//   docs/screenshot.png   the README hero. A real capture of docs/index.html at
//                         1440x900, deviceScaleFactor 2, colorScheme dark,
//                         cropped 12px above #takeaway. IT DATES. Retake it
//                         when the page changes shape, and say in the README
//                         alt only what is structurally there (a chip row, a
//                         tile row) — never a number, because the alt goes
//                         stale on a different clock than the image.
//   docs/og.png           the og:image, rendered from tools/og_card.html at
//                         1200x630 x2 = 2400x1260, the frame Facebook and
//                         LinkedIn scale from. Data-free by design; re-render
//                         it after ANY edit to og_card.html, or the shipped
//                         card stops being what the reviewable source says.
//
// WHY THE CROP STOPS AT #takeaway. Everything below that node is a dealer photo
// on a third-party host. In CI, in a sandbox, and on any offline run those
// fetches fail and the page falls back to the placeholder chick — a hero full
// of chicks is a photo-less-looking dashboard, which is precisely the kind of
// misrepresentation this whole workstream is about. Above the cut there is no
// remote image: masthead, tiles and filters only. So the crop is not framing,
// it is honesty, and it holds whether or not the machine has a network.
//
// OFFLINE. Same serving technique as tools/dashboard_smoke.mjs: docs/ over a
// local http server (the page fetches data.json, which file:// forbids) and
// every cdn.jsdelivr.net request answered from the design-system checkout CI
// already clones for the linter. --offline additionally 404s everything else
// (dealer photos, the us-atlas topology, which lives below the cut anyway), and
// --fonts <dir> answers fonts.googleapis.com from a local mirror — a directory
// holding fonts.css plus its .woff2 files, served at /f/. Without --fonts the
// three type families come off the network, and WITHOUT THEM THE CAPTURE IS
// WRONG: it renders in a fallback face at different metrics.
//
// The exact invocation that produced the committed pair (offline sandbox):
//
//   node tools/shoot_hero.mjs ../design-system --offline --fonts ./fontmirror
//
// Needs playwright's chromium, which is not a repo dependency — run it with
// `npx playwright@1.56 …` or against a preinstalled browser.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { extname, join, resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..');
const ROOT = join(REPO, 'docs');

// The frames. tests/test_tracking.py reads CARD_FRAME and CARD_SCALE out of
// this file and asserts the committed docs/og.png is exactly that size, so a
// frame change here without a re-render fails the suite instead of shipping.
const CARD_FRAME = { width: 1200, height: 630 };
const CARD_SCALE = 2;
const HERO_FRAME = { width: 1440, height: 900 };
const HERO_SCALE = 2;
const HERO_CUT_ABOVE = '#takeaway';   // the picks card: every photo starts here
const HERO_CUT_PAD = 12;

const TAKES_VALUE = new Set(['--only', '--hero', '--card', '--fonts']);
const opt = {};
const positional = [];
for (let i = 0, a = process.argv.slice(2); i < a.length; i++) {
  if (TAKES_VALUE.has(a[i])) opt[a[i]] = a[++i];
  else if (a[i].startsWith('--')) opt[a[i]] = true;
  else positional.push(a[i]);
}
const DS = positional[0];
const ONLY = opt['--only'] || null;
const HERO_OUT = resolve(REPO, opt['--hero'] || 'docs/screenshot.png');
const CARD_OUT = resolve(REPO, opt['--card'] || 'docs/og.png');
const FONTS = opt['--fonts'] ? resolve(opt['--fonts']) : null;
const OFFLINE = opt['--offline'] === true;

if (!DS || !existsSync(join(DS, 'sc.css'))) {
  console.error('usage: node tools/shoot_hero.mjs <design-system-checkout> [--only hero|card]');
  console.error('       [--hero <png>] [--card <png>] [--fonts <dir>] [--offline]');
  console.error('       (the checkout the consumer linter already clones — it must contain sc.css)');
  process.exit(2);
}
if (ONLY && ONLY !== 'hero' && ONLY !== 'card') {
  console.error(`--only takes "hero" or "card", not ${JSON.stringify(ONLY)}`);
  process.exit(2);
}

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { console.log('  skip  playwright is not installed — nothing was captured'); process.exit(0); }

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
                '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };

// docs/ at /, the font mirror at /f/ — one server, so the page and its faces
// share an origin and nothing is fetched off this machine unless we let it.
const server = createServer(async (req, res) => {
  const p = decodeURIComponent(req.url.split('?')[0]);
  const font = FONTS && p.startsWith('/f/');
  const base = font ? FONTS : ROOT;
  const file = resolve(join(base, font ? p.slice(3) : (p === '/' ? '/index.html' : p)));
  if (!file.startsWith(base)) { res.writeHead(403).end(); return; }
  try { res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' }).end(await readFile(file)); }
  catch { res.writeHead(404).end('not found'); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch();

async function context(frame, scale) {
  const ctx = await browser.newContext({ viewport: frame, deviceScaleFactor: scale, colorScheme: 'dark' });
  // Order matters: playwright matches the LAST registered route first, so the
  // catch-all goes down before the specific ones.
  if (OFFLINE) await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.fulfill({ status: 404, body: '' }));
  await ctx.route('**://cdn.jsdelivr.net/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    const file = join(DS, path.replace(/^\/gh\/spicyChicken59\/design-system@[^/]+\//, ''));
    return existsSync(file)
      ? route.fulfill({ path: file, contentType: TYPES[extname(file)] })
      : route.fulfill({ status: 404, body: 'not in the checkout: ' + path });
  });
  if (FONTS) {
    await ctx.route('**://fonts.googleapis.com/**', (r) => r.fulfill({ path: join(FONTS, 'fonts.css'), contentType: 'text/css' }));
    await ctx.route('**/f/*.woff2', (r) => r.fulfill({ path: join(FONTS, basename(new URL(r.request().url()).pathname)), contentType: 'font/woff2' }));
  }
  return ctx;
}

// Every face the page asks for must have arrived before the shutter: a capture
// taken mid-swap is a capture of the fallback stack.
async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);
}

if (ONLY !== 'card') {
  const ctx = await context(HERO_FRAME, HERO_SCALE);
  const page = await ctx.newPage();
  await page.goto(BASE + '/index.html', { waitUntil: 'load' });
  // The page is a shell until data.json lands; shooting before that captures
  // "Loading snapshot…".
  await page.waitForFunction(() => {
    const h = document.getElementById('h1');
    return h && h.textContent.trim() && !/^(Loading|Snapshot unavailable)/.test(h.textContent);
  }, null, { timeout: 25000 });
  await settle(page);
  const cut = await page.evaluate((sel) => {
    const t = document.querySelector(sel);
    if (!t) return null;
    return Math.round(t.getBoundingClientRect().top + window.scrollY);
  }, HERO_CUT_ABOVE);
  if (cut === null) {
    console.error(`  FAIL  ${HERO_CUT_ABOVE} is gone from docs/index.html — the crop this hero depends on`);
    console.error('        no longer exists. Pick the new first-photo node and update HERO_CUT_ABOVE,');
    console.error('        do not just shoot the whole page: below it the dealer photos are chicks.');
    await browser.close(); server.close();
    process.exit(1);
  }
  const height = cut - HERO_CUT_PAD;
  await page.screenshot({ path: HERO_OUT, clip: { x: 0, y: 0, width: HERO_FRAME.width, height } });
  console.log(`hero  ${HERO_OUT}  ${HERO_FRAME.width}x${height} @${HERO_SCALE}x  (cut ${HERO_CUT_PAD}px above ${HERO_CUT_ABOVE} at y=${cut})`);
  await ctx.close();
}

if (ONLY !== 'hero') {
  const ctx = await context(CARD_FRAME, CARD_SCALE);
  const page = await ctx.newPage();
  await page.goto('file://' + join(REPO, 'tools', 'og_card.html'), { waitUntil: 'load' });
  await settle(page);
  // The promise the card makes, checked at the moment it is baked: no number,
  // no price. tests/test_tracking.py holds the same rule on the source; this
  // holds it on the pixels' last chance to be wrong.
  const text = await page.evaluate(() => document.getElementById('og-card').innerText);
  if (/[0-9$]/.test(text)) {
    console.error('  FAIL  the card renders a digit or a price — it will be stale by tomorrow:');
    console.error('        ' + JSON.stringify(text));
    await browser.close(); server.close();
    process.exit(1);
  }
  await page.screenshot({ path: CARD_OUT });
  console.log(`card  ${CARD_OUT}  ${CARD_FRAME.width * CARD_SCALE}x${CARD_FRAME.height * CARD_SCALE}  (data-free: no digit, no $)`);
  await ctx.close();
}

await browser.close();
server.close();
