// Does the dashboard still work? The one check that opens it.
//
//   node tools/dashboard_smoke.mjs <design-system-checkout> [--shots <dir>]
//
// docs/index.html is 3,700 lines of markup, CSS and one IIFE, and until this
// existed not one of them could fail a build: the Python suite is a Tracking.py
// suite, and the consumer linter reads the CSS namespace, not the behaviour. A
// typo in the render path shipped green.
//
// It drives the real page in headless Chromium and asserts what the page
// PROMISES, not how it is built — the watchlist draws, a model page draws, and
// the comparison the filter chips assemble says the same thing in the tiles,
// the table, the chart and the address bar. Every assertion below is a claim a
// reader would notice being wrong.
//
// Offline by construction, so CI has nothing new to reach for: docs/ is served
// from a local http server (the page fetches data.json, which file:// blocks),
// every cdn.jsdelivr.net request is answered from the design-system checkout CI
// has already cloned for the linter, the us-atlas topology is answered with a
// stand-in built from the sheet's own state list — the map's geometry is not
// what this is testing — and every dealer photo with a 1×1 pixel.
//
// Needs playwright's chromium. It is not a repo dependency: run it with
// `npx playwright@1.56 …` or against a preinstalled browser. If chromium is
// missing the script says so and exits 0 — a machine without a browser is not
// a failing dashboard.
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { extname, join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', 'docs');
const argv = process.argv.slice(2);
const DS = argv.find((a) => !a.startsWith('--'));
const SHOTS = argv.includes('--shots') ? argv[argv.indexOf('--shots') + 1] : null;
if (!DS || !existsSync(join(DS, 'sc.css'))) {
  console.error('usage: node tools/dashboard_smoke.mjs <design-system-checkout> [--shots <dir>]');
  console.error('       (the checkout the consumer linter already clones — it must contain sc.css)');
  process.exit(2);
}

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { console.log('  skip  playwright is not installed — the dashboard was not opened'); process.exit(0); }

// --- the stand-in atlas ----------------------------------------------------
// One rectangle per state, on a grid, in the same pre-projected frame the real
// us-atlas uses. Deliberately not a map: this checks that the map DRAWS and
// responds to the filters, and a fake Ohio does that as well as a real one
// while costing no network.
function standInAtlas() {
  const src = readFileSync(join(DS, 'sc-map.js'), 'utf8');
  const m = src.match(/STATE_ABBR\s*=\s*(\{[\s\S]*?\});/);
  const names = [...m[1].matchAll(/'?([A-Za-z][A-Za-z .]*?)'?\s*:\s*'[A-Z]{2}'/g)].map((x) => x[1].trim());
  const arcs = [], geometries = [];
  names.forEach((n, i) => {
    const x = 20 + (i % 10) * 92, y = 20 + Math.floor(i / 10) * 95;
    arcs.push([[x, y], [70, 0], [0, 70], [-70, 0], [0, -70]]);
    geometries.push({ type: 'Polygon', arcs: [[arcs.length - 1]], id: String(i + 1).padStart(2, '0'), properties: { name: n } });
  });
  return JSON.stringify({ type: 'Topology', transform: { scale: [1, 1], translate: [0, 0] }, arcs,
                          objects: { states: { type: 'GeometryCollection', geometries } } });
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
                '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
const server = createServer(async (req, res) => {
  const p = decodeURIComponent(req.url.split('?')[0]);
  const file = resolve(join(ROOT, p === '/' ? '/index.html' : p));
  if (!file.startsWith(ROOT)) { res.writeHead(403).end(); return; }
  try { res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' }).end(await readFile(file)); }
  catch { res.writeHead(404).end('not found'); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}`;
if (SHOTS) await mkdir(SHOTS, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=', 'base64');
const ATLAS = standInAtlas();
// Order matters: playwright matches the LAST registered route first, so the
// catch-all goes down before the specific ones.
await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => (/\.(png|jpe?g|webp|gif|svg)/i.test(r.request().url())
  ? r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
  : r.fulfill({ status: 200, contentType: 'text/plain', body: '' })));
await ctx.route('**://cdn.jsdelivr.net/**', (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.includes('us-atlas')) return route.fulfill({ contentType: 'application/json', body: ATLAS });
  const file = join(DS, path.replace(/^\/gh\/spicyChicken59\/design-system@[^/]+\//, ''));
  return existsSync(file)
    ? route.fulfill({ path: file, contentType: TYPES[extname(file)] })
    : route.fulfill({ status: 404, body: 'not in the checkout: ' + path });
});

const errors = [];
const page = await ctx.newPage();
page.on('pageerror', (e) => errors.push('uncaught: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

const results = [];
const ok = (name, pass, detail = '') => results.push({ name, pass: !!pass, detail });

// A check whose SUBJECT is not in today's data has neither passed nor failed —
// it had nothing to look at. Saying so keeps the count honest about what was
// actually covered: scoring it a pass would quietly retire the check on the day
// the tracker stops watching the car it needed, and scoring it a failure would
// blame the dashboard for the market.
// Every flag is set on purpose. Five workstreams wrote this helper independently
// in three different row shapes; a row missing `pass` was counted FAILED by one
// tally and a row missing `skipped` vanished from another. Carrying all three
// makes a skip render and count correctly under any of them.
const skip = (name, detail = '') => results.push({ name, pass: true, skip: true, skipped: true, detail });
// ---- ns/NS-02 ----
{
// failed — it had nothing to look at. Saying so keeps the count honest about
// what was actually covered; scoring it a pass would quietly retire the check
// on the day the tracker stops watching the car it needed.
}
// ---- ns/NS-03 ----
{
// dashboard — it is a check with nothing to point at. Say so out loud rather
// than passing quietly or failing on the config.
}
// ---- ns/NS-04 ----
{
// about the code, and a dashboard with nothing to look at is not a broken one.
// chromium is.
}
// ---- ns/NS-08 ----
{
// the market moves, and an absent fixture is not a broken dashboard. Both
}
// ---- ns/NS-09 ----
{
// about the code, and a dashboard with nothing to look at is not a broken one.
// chromium is.
}
// ---- ns/NS-10 ----
{
// loud, so a green run never quietly stops covering something.
}
const shot = async (n) => { if (SHOTS) await page.screenshot({ path: join(SHOTS, n + '.png') }); };
async function open(query) {
  await page.goto(BASE + '/index.html' + query, { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const h = document.getElementById('h1');
    return h && h.textContent.trim() && h.textContent !== 'Snapshot unavailable';
  }, null, { timeout: 20000 });
  await page.waitForTimeout(350);
}

// --- the watchlist ---------------------------------------------------------
await open('');
ok('the watchlist opens', (await page.textContent('#h1')) === 'The watchlist', await page.textContent('#h1'));
ok('four tiles', (await page.locator('#kpis .sc-tile').count()) === 4);
ok('a row per model', (await page.locator('#overview-table tbody tr').count()) > 1);
ok('the chart draws', (await page.locator('#chart svg').count()) > 0);
ok('the map draws', (await page.locator('#map svg').count()) > 0);
ok('three chip groups', (await page.locator('#f-where button').count()) > 0
  && (await page.locator('#f-model button').count()) > 1);
await shot('watchlist');

// --- a model page ----------------------------------------------------------
await open('?brand=bmw&m=i5');
ok('a model page opens', (await page.textContent('#h1')).includes('i5'), await page.textContent('#h1'));
ok('it lists cars', (await page.locator('#list-table tbody tr').count()) > 0);
ok('it plots price against miles', (await page.locator('#scatter svg').count()) > 0);
ok('no compare card at rest', await page.locator('#compare-card').isHidden());

// --- comparing trims -------------------------------------------------------
const trims = page.locator('#f-trim button');
ok('the trim control is chips', (await trims.count()) > 1, `${await trims.count()} chips`);
await trims.nth(1).click(); await page.waitForTimeout(250);
ok('one trim is a scope, not a comparison', await page.locator('#compare-card').isHidden());
ok('one trim reaches the title', (await page.textContent('#h1')).split(' ').length >= 2);
await trims.nth(2).click(); await page.waitForTimeout(350);
ok('two trims compare', await page.locator('#compare-card').isVisible());
ok('the title says vs', (await page.textContent('#h1')).includes(' vs '), await page.textContent('#h1'));
ok('a column per trim', (await page.locator('#compare-table thead th').count()) === 3);
ok('a line per trim', (await page.locator('#chart svg [data-sid]').count()) >= 2);
ok('the chart says which question it answered', (await page.textContent('#chart-title')).includes('by trim'));
ok('the comparison is in the address bar', page.url().includes('trims='), page.url());
await shot('compare-trims');

const trimUrl = page.url();
await open(trimUrl.slice(trimUrl.indexOf('?')));
ok('a shared trim comparison reopens', await page.locator('#compare-card').isVisible());
ok('with both chips pressed', (await page.locator('#f-trim button[aria-pressed="true"]').count()) === 2);

// --- comparing models ------------------------------------------------------
await open('');
const models = page.locator('#f-model button');
await models.nth(0).click(); await page.waitForTimeout(250);
ok('one model narrows the index', (await page.locator('#overview-table tbody tr').count()) === 1);
await models.nth(1).click(); await page.waitForTimeout(400);
ok('two models compare', await page.locator('#compare-card').isVisible());
ok('the index narrows to both', (await page.locator('#overview-table tbody tr').count()) === 2);
ok('their cars pool into one table', await page.locator('#list-card').isVisible());
ok('the pooled table names the car', (await page.textContent('#list-table thead th:first-child')).trim() === 'Car');
ok('every pooled row says which model', (await page.locator('#list-table tbody .sc-eyebrow').count()) > 0);
ok('and it can be sorted', await page.locator('#f-sort').isVisible());
ok('the comparison is in the address bar', page.url().includes('models='), page.url());
await shot('compare-models');

const modelUrl = page.url();
await open(modelUrl.slice(modelUrl.indexOf('?')));
ok('a shared model comparison reopens', await page.locator('#compare-card').isVisible());

// A link naming a car the watchlist no longer has opens the rest and says so,
// rather than dropping the reader on a page that is silently missing one.
await open('?models=bmw-i5,bmw-not-a-car');
ok('a half-dead link still opens', (await page.locator('#overview-table tbody tr').count()) === 1);
ok('and names what went missing', /bmw-not-a-car/.test(await page.textContent('#notice')));

// ---- ns/NS-03 ----
{
// The same promise, against the keys every plain object already answers to.
// applyUrl used to test membership with `brands[wantBrand]` and
// `(m.trims || {})[wantModel]`, so Object.prototype's own keys all read as
// hits: ?model=constructor matched every trim in the file and the last match
// won, so it opened whichever car the watchlist ends on, with no notice and a
// canonical ?brand=…&m=… in the address bar — the one thing the comment above
// applyUrl swears never happens, a link honored with a different car and then
// made re-shareable. ?brand=__proto__ set a brand that does not exist and the
// page rendered "Snapshot unavailable — could not load data.json" over a
// data.json that had parsed fine, bookmarkable because syncUrl kept the query.
// lc() hid three of these behind lowercasing (tostring, valueof,
// hasownproperty already missed correctly); these four did not.
//
// The fifth URL is the one that pins the fourth call site. Reverting only
// `!own(models, wantM)` leaves the other four URLs green — ?brand=__proto__
// and ?brand=constructor stop at the brand lookup, ?model=constructor and
// ?m=constructor never reach it — while ?<a real brand>&m=constructor still
// renders "constructor" as the <h1> and keeps the dead query. The brand comes
// out of data.json, not out of this file: naming one here would test the
// watchlist's contents instead of the page.
const brands = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8')).brands || {};
const realBrand = Object.keys(brands).find((b) => Object.keys(brands[b].models || {}).length);
// Only two of Object.prototype's keys survive lc(); the probe needs one of them
// that the watchlist itself does not use, or the check would be asserting the
// config rather than the page.
const ghost = realBrand && ['constructor', '__proto__'].find(
  (k) => !Object.prototype.hasOwnProperty.call(brands[realBrand].models, k));
const protoUrls = ['?brand=__proto__', '?brand=constructor', '?model=constructor', '?m=constructor'];
if (ghost) protoUrls.push(`?brand=${encodeURIComponent(realBrand)}&m=${ghost}`);
else skip('a tracked brand plus a prototype-key model lands on the watchlist',
  realBrand ? `${realBrand} really has a model called constructor and one called __proto__`
            : 'data.json has no brand with models — nothing to ask for');
await open('');
const baseRows = await page.locator('#overview-table tbody tr').count();
for (const q of protoUrls) {
  let state = null;
  try {
    await open(q);
    state = await page.evaluate(() => ({
      h1: document.getElementById('h1').textContent.trim(),
      search: location.search,
      notice: document.getElementById('notice').textContent,
    }));
  } catch (e) { state = { h1: 'never rendered', search: '?', notice: String(e.message).slice(0, 60) }; }
  const rows = state.h1 === 'never rendered' ? -1 : await page.locator('#overview-table tbody tr').count();
  ok(`${q} lands on the watchlist, not on a car`,
    state.h1 === 'The watchlist' && rows === baseRows, `${state.h1}, ${rows} rows`);
  ok(`${q} says the link missed and stops re-sharing itself`,
    /not tracked|no longer tracked/.test(state.notice) && state.search === '',
    `search=${JSON.stringify(state.search)} notice=${JSON.stringify(state.notice.replace(/\s+/g, ' ').slice(0, 40))}`);
}
}
// --- the things two audits found, so they cannot come back -----------------
// The marked winner is the one number that survives a comparison, and it is
// only markable when every column's best car was judged against its own trim
// AND year. The iX proved it: its 2024 cohort was six M60s and one xDrive50,
// so the xDrive column's top car scored 24% under a mostly-M60 median while
// sitting 18% ABOVE a typical xDrive.
//
// The RULE is asserted, not that example. Pinning "the iX marks nothing" made
// this a test of the market rather than of the page — two more snapshot days
// gave every iX trim-year three eligible cars, the fallback stopped happening,
// and a correct page failed the check. The oracle is now the page's own stated
// basis: each column says in data-basis whether its percentage was measured
// against the car's trim and year, its year, or the whole model, and a winner
// may be marked only where every column says "trim".
for (const q of ['?brand=bmw&m=ix&trims=bmw-ix-xdrive,bmw-ix-m',
                 '?brand=bmw&m=i5&trims=bmw-i5-edrive40,bmw-i5-m60',
                 '?brand=bmw&m=i7&trims=bmw-i7-edrive50,bmw-i7-xdrive60',
                 '?brand=bmw&m=i7&trims=bmw-i7-edrive50,bmw-i7-m70',
                 '?models=bmw-i5,bmw-i7']) {
  await open(q);
  const r = await page.evaluate(() => {
    const row = [...document.querySelectorAll('#compare-table tbody tr')]
      .find((tr) => /Best value vs typical/.test(tr.querySelector('th').textContent));
    const cells = [...row.querySelectorAll('td')];
    return { bases: cells.map((td) => td.getAttribute('data-basis')),
             marked: cells.filter((td) => td.classList.contains('is-best')).length };
  });
  const comparable = r.bases.every((b) => b === 'trim');
  ok('a winner is marked only where every column was judged on its own trim and year',
    comparable ? r.marked <= 1 : r.marked === 0,
    `${q} → ${r.marked} marked, bases ${r.bases.join('/')}`);
}

// One shared record handed every Audi row a "new" chip while the compare card
// beside it said the Audi had no previous snapshot to be new against.
await open('?models=audi-a6-etron,bmw-i5');
const newByModel = await page.evaluate(() => {
  const out = {};
  for (const tr of document.querySelectorAll('#list-table tbody tr')) {
    const k = ((tr.querySelector('.sc-eyebrow') || {}).textContent || '?').trim();
    out[k] = out[k] || { rows: 0, fresh: 0 };
    out[k].rows++;
    if (tr.querySelector('.sc-chip--spice')) out[k].fresh++;
  }
  return out;
});
ok('a model with one snapshot day has no "new" cars',
  Object.values(newByModel).every((v) => v.fresh < v.rows), JSON.stringify(newByModel));

// The line whose job is to say what the filters are doing measured the
// selection against itself and printed "showing all 169 cars" over 503.
await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
await open('?models=bmw-i5,kia-ev9');
const count = await page.textContent('#filter-count');
ok('the count measures against the whole watchlist', / of \d+ cars/.test(count) && /\+/.test(count), count);

// The count on a chip turned muted-grey-on-cobalt the moment it was pressed.
const chipCR = await page.evaluate(() => {
  const n = document.querySelector('#f-model button[aria-pressed="true"] .chip-n');
  if (!n) return null;
  const lum = (c) => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map(Number)
    .map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const [a, b] = [lum(getComputedStyle(n).color), lum(getComputedStyle(n.closest('button')).backgroundColor)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
});
ok('a pressed chip\'s count is still readable', chipCR >= 4.5, chipCR ? chipCR.toFixed(2) + ':1' : 'no pressed chip');

// ---- ns/NS-02 ----
{
// ...and an UNPRESSED chip's count was the same defect one step quieter, left
// behind when the pressed state was fixed: an opacity: 0.7 over sc-note's
// --sc-text-2 — 6.44:1 dark and 5.83:1 light on its own ground — composited
// down to 3.82:1 (#6c7889 on #121c2a) and 3.05:1 (#8e949c on #ffffff) at
// 12px/400. That was every one of axe's serious nodes on the page: 7 on the
// watchlist, 9 on a model page, 5 on compare, in BOTH themes. Unpressed is the
// resting state of nearly every chip, so it is the ratio that matters most,
// and light — the theme nobody had measured — was the worse of the two, which
// is why this asserts each theme rather than whichever one CI happens to boot
// in.
//
// It reads EVERY count in both groups and reports the WORST, because the one
// declaration governed three sets and a single-node read could only ever see
// one of them: #f-model on the watchlist (pressed and unpressed), #f-model on
// a model page — the "compare with" doors, which carry no aria-pressed at all
// and were the largest group axe found — and #f-trim. Scoping a dim back onto
// any one of the three would otherwise ship green.
//
// Two things it has to get right: opacity never appears in
// getComputedStyle().color, so the ratio is taken on the composite; and sc.css
// transitions .sc-tab's background, so the switch has to settle before
// anything is read or the theme just left is what gets measured. The worst
// node is often the one the mouse happens to be resting on, whose ground is
// --sc-hover rather than --sc-surface — a real state, and one axe never sees,
// so it belongs in the measurement even though it makes the number wobble.
const worstChipCount = () => page.evaluate(() => {
  const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
  const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  let n = 0, worst = Infinity;
  for (const cn of document.querySelectorAll('#f-model .chip-n, #f-trim .chip-n')) {
    if (!cn.getBoundingClientRect().width) continue;  // below 721px the bar folds the counts away
    const btn = cn.closest('button');
    const bg = rgb(getComputedStyle(btn).backgroundColor);
    let alpha = 1;                       // every opacity between the count and its ground
    for (let e = cn; e && e !== btn.parentElement; e = e.parentElement) alpha *= Number(getComputedStyle(e).opacity);
    const fg = rgb(getComputedStyle(cn).color).map((v, i) => v * alpha + bg[i] * (1 - alpha));
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    worst = Math.min(worst, (a + 0.05) / (b + 0.05));
    n++;
  }
  return { n, worst };
});
// Which model page is read out of data.json rather than named here: index.html
// hides "compare with" below two models and the trim field below two trims, so
// the state exists only where the data still has it — and a check that names a
// failure; the watchlist half still runs.
const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const watched = Object.entries(site.brands || {}).flatMap(([bk, b]) =>
  Object.entries(b.models || {}).map(([mk, m]) => ({ bk, mk, nt: Object.keys(m.trims || {}).length })));
const subject = watched.length > 1 ? watched.find((m) => m.nt > 1) : null;
const chipStates = [['the watchlist', null]];      // whatever the check above left — chips pressed and not
if (subject) chipStates.push(['a model page', `?brand=${subject.bk}&m=${subject.mk}`]);
else skip('every chip count is readable on a model page', 'no watched model has two trims today');
for (const [where, query] of chipStates) {
  if (query) await open(query);
  for (const theme of ['dark', 'light']) {
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
    await page.waitForTimeout(400);
    const { n, worst } = await worstChipCount();
    const claim = `every chip count is readable on ${where}, in ${theme}`;
    if (n) ok(claim, worst >= 4.5, `${worst.toFixed(2)}:1, worst of ${n}`);
    else skip(claim, 'no chip counts on screen');
  }
  // Restored, but not waited on: nothing reads the page again before the next
  // full load, which drops the attribute anyway.
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
}
}
// ---- ns/NS-08 ----
{
// The dek prints a trim's note verbatim, and the i5 eDrive40's note was the
// buyer's own project status: "First real buyer, near Chicago. Decide by
// mid-Sept." shipped as the public one-line statement of what that page is
// about, and again inside the eDrive40 chip's title. Its sibling shows what a
// note is for — the i7 eDrive50's reads "The other half of the decision,
// beside the i5 eDrive40." The rule is asserted, not that one string: nothing
// the page prints from a note may carry a dated commitment, because a date in
// the dek rots silently on a page that is published every day.
//
// Asserted over the DATA, because every note reaches a dek (index.html:2392)
// and a chip title (:3586), and a rule enforced on one URL is the gap that let
// rather than named here — naming bmw-i5-edrive40 would test the config, and
// would pass vacuously the day that trim is renamed or the i5 drops to one
// trim (chips render only when tIds.length > 1, :3580).
const DATED = /\b(decide|decision|deadline)\s+by\b|\bby\s+(mid|early|late)[- ]?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i;
const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const noted = [];
for (const [bId, b] of Object.entries(site.brands || {})) {
  for (const [mId, m] of Object.entries(b.models || {})) {
    const tIds = Object.keys(m.trims || {});
    noted.push({ key: `${bId}/${mId}`, note: m.note || '', bId, mId, tId: null, siblings: tIds.length });
    for (const tId of tIds) noted.push({ key: `${bId}/${mId}/${tId}`, note: (m.trims[tId] || {}).note || '', bId, mId, tId, siblings: tIds.length });
  }
}
const datedNotes = noted.filter((n) => DATED.test(n.note));
ok('no note on the watchlist carries a dated commitment', datedNotes.length === 0,
  datedNotes.map((n) => `${n.key}: ${n.note}`).join(' | ') || `${noted.filter((n) => n.note).length} notes read`);

// One rendered proof that a note really is what the dek and the chip say.
const subject = noted.find((n) => n.tId && n.note && n.siblings > 1);
if (!subject) {
  skip('a described trim reaches the dek and its own chip', 'no model today has a described trim beside a sibling');
} else {
  await open(`?brand=${subject.bId}&m=${subject.mId}&trims=${subject.tId}`);
  const dek = (await page.textContent('#dek')).trim();
  ok('a described trim reaches the dek', dek.startsWith(subject.note), `${subject.key} — ${dek}`);
  const titles = await page.$$eval('#f-trim button', (bs) => bs.map((b) => b.title));
  const chip = titles.find((t) => t.includes(subject.note));
  ok('and the chip it sits beside says the same', !!chip, `${titles.length} chips — ${chip || titles.join(' | ')}`);
  ok('and neither reads as a deadline', !DATED.test(dek) && !DATED.test(chip || ''), dek);
}
}
// ---- ns/NS-09 ----
{
// Pressing a chip rewrote the tiles, the map, the chart and the table and
// announced the word "pressed" — the page's only live region was the chart
// tooltip. The count line says the right sentence already; it just has to be
// a status message, and a re-sort has to move it (it changed nothing before,
// so a live region would have had nothing to read out).
//
// Asserted on #filter-count and nothing else. The obvious stronger-looking
// assertion — document.querySelectorAll('[role=status]').length >= 2 — is a
// trap: only ONE of those two nodes is in this repo. The other is built by the
// design system (sc-charts.js, `if (opts.live)` on the chart tooltip), so a
// pin bump, or whoever gives that tooltip role=alert instead, would turn this
// check red for a reason that has nothing to do with this page. The count is
// still reported, so the number stays on the record; it just is not the claim.
// Being outside a [hidden] subtree IS the claim, and is ours: a status message
// in a hidden container announces nothing.
const liveCount = await page.evaluate(() => {
  const n = document.getElementById('filter-count');
  return { role: n.getAttribute('role'), live: n.getAttribute('aria-live'),
           inA11yTree: !n.closest('[hidden]'), regionsInDoc: document.querySelectorAll('[role=status]').length };
});
ok('the filter count is a status message',
  liveCount.role === 'status' && liveCount.live === 'polite' && liveCount.inA11yTree, JSON.stringify(liveCount));

// The sort control only exists where a listings table does, so this needs a
// model page — and WHICH model must not be written down here: the watchlist is
// whatever the tracker fetched this morning, and a check that names a model
// tests the config instead of the page. Open the first "Open →" the overview
// rendered. The order is picked the same way, off the select's own options,
// so nine hard-coded value strings cannot rot either. Empty watchlist means no
await open('');
const sortSubject = await page.evaluate(() =>
  (document.querySelector('#overview-table [data-fkey^="open:"]') || {}).dataset?.fkey?.slice(5) || null);
if (!sortSubject) {
  skip('and a re-sort changes what it says', 'no model on the watchlist to open');
} else {
  const sortSaid = await page.evaluate(async () => {
    document.querySelector('#overview-table [data-fkey^="open:"]').click();
    await new Promise((r) => setTimeout(r, 500));
    const n = document.getElementById('filter-count'), sel = document.getElementById('f-sort');
    const was = n.textContent;
    const other = [...sel.options].find((o) => o.value !== sel.value);
    if (!other) return { was, now: was, why: 'the sort select offers only one order' };
    sel.value = other.value; sel.dispatchEvent(new Event('change'));
    await new Promise((r) => setTimeout(r, 300));
    return { was, now: n.textContent, order: other.value, shown: !sel.parentElement.hidden };
  });
  ok('and a re-sort changes what it says', sortSaid.was !== sortSaid.now,
    `${sortSubject} → ${sortSaid.order || sortSaid.why}: "${sortSaid.was}" → "${sortSaid.now}"`
    + (sortSaid.shown === false ? '  (the sort select was off-screen — nothing to announce an order for)' : ''));
}
}
// "Only this →" takes its own card off the page; the keyboard must land
// somewhere, not on <body>.
await open('?brand=bmw&m=i5&trims=bmw-i5-edrive40,bmw-i5-xdrive40');
await page.evaluate(() => document.querySelector('[data-fkey^="cmp:"]').focus());
await page.keyboard.press('Enter');
await page.waitForTimeout(400);
ok('narrowing to one column keeps the keyboard somewhere',
  (await page.evaluate(() => document.activeElement.tagName)) !== 'BODY');

// ---- ns/NS-01 ----
{
// A trim chip narrows the tiles but could never make them SAY so: renderKpis
// asked "is this filtered?" as rows.length !== trimRows(listings).length, and
// the trim sits on both sides of that, so tile 1 called the i7's M70 floor of
// $93,399 "Lowest asking nationwide" over a real national $43,663 — $49,736
// of overstatement, with the chart chip an inch below already reading
// "filtered — M70". The oracle is that agreement, not those numbers: the two
// elements describe one screen and may not contradict each other. Tile 4 is
// the exception and is checked as one — its counts run over the model's own
// rows before the shared filters, so a trim chip really is not a filter there.
//
// retires models and trims nightly, so a check that names the i7 asserts the
// watchlist's contents alongside the page's behaviour and goes red on a night
// that only changed the watchlist. Take the first model the sheet gives more
// than one trim (buildFilters hides #f-trim-field below two) and press its
// busiest chip. A night with no such model is a thinner watchlist, not a
// already does.
const scopeSubject = (() => {
  const sheet = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
  for (const [bk, b] of Object.entries(sheet.brands || {})) {
    for (const [mk, m] of Object.entries(b.models || {})) {
      const ids = Object.keys(m.trims || {});
      if (ids.length < 2) continue;
      const n = {};
      for (const x of (m.listings || [])) n[x.trim_id] = (n[x.trim_id] || 0) + 1;
      // stable sort: a tie keeps the sheet's own chip order, so the pick is the
      // same on two runs of the same data
      const nth = ids.map((_, i) => i).sort((a, z) => (n[ids[z]] || 0) - (n[ids[a]] || 0))[0];
      if (!n[ids[nth]]) continue;                 // every chip empty — nothing for it to narrow
      return { q: `?brand=${bk}&m=${mk}`, nth, who: `${bk} ${mk} · ${(m.trims[ids[nth]] || {}).label || ids[nth]}` };
    }
  }
  return null;
})();
if (!scopeSubject) {
  console.log('  skip  no watched model has two trims today — the tiles-vs-chip scope check has no subject');
} else {
  // Guarded: three workstreams add a block at this anchor and the order they
  // end up in is an integration decision, so this must not be the one thing
  // that throws if it lands first, on about:blank, where localStorage is a
  // SecurityError rather than an empty store.
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
  await open(scopeSubject.q);
  const scopeSaid = async () => ({ who: scopeSubject.who, ...await page.evaluate(() => ({
    tiles: [...document.querySelectorAll('#kpis .sc-tile__label')].map((n) => n.textContent),
    chip: document.getElementById('chart-scope').textContent,
  })) });
  const rest = await scopeSaid();
  ok('at rest the tiles claim the nation and the chart chip agrees',
    /nationwide/.test(rest.tiles[0]) && /drivable asking/.test(rest.tiles[1]) && !/^filtered/.test(rest.chip),
    JSON.stringify(rest));
  await page.locator('#f-trim button').nth(scopeSubject.nth).click(); await page.waitForTimeout(350);
  const scoped = await scopeSaid();
  ok('a trim chip makes the price tiles say filtered, like the chart chip',
    /^filtered/.test(scoped.chip) && /\(filtered\)/.test(scoped.tiles[0]) && /\(filtered\)/.test(scoped.tiles[1]),
    JSON.stringify(scoped));
  ok('and leaves the movement tile, which counts every trim, saying so',
    !/all cars/.test(scoped.tiles[3]), `${scopeSubject.who} → ${scoped.tiles[3]}`);
}
}
// ---- ns/NS-04 ----
{
// A trim chip with no cars behind it used to empty the page in silence: the
// notice measured its total THROUGH the trim selection under test, so total
// "CPO under 30k mi 0" chip left a whole model's worth of page as dashes with
// no message and no way back.
//
// WHICH trim reads 0 is a tracker accident, not a property of the page, so the
// bmw-i5-cpo: it held 0 cars the morning it was written and 2 the next
// morning, and the check went red on a CORRECT page reporting
// {"hidden":true,"text":""} — byte-identical to what the unfixed code
// produces, so an operator would have read a healthy dashboard as a regression.
// The oracle is the rule: whatever trim holds no cars must say so and offer a
// way out. A market where no trim is empty is not a failing dashboard, it is a
const DATA = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const perTrim = (m) => (m.listings || []).reduce((c, x) => (c[x.trim_id] = (c[x.trim_id] || 0) + 1, c), {});
// A model with no listings at all is the pre-existing `!total` path, not this
function emptyTrim() {
  for (const [bk, b] of Object.entries(DATA.brands || {}))
    for (const [mk, m] of Object.entries(b.models || {})) {
      if (!(m.listings || []).length) continue;
      const c = perTrim(m);
      for (const [tid, t] of Object.entries(m.trims || {})) if (!c[tid]) return { bk, mk, tid, label: t.label || tid };
    }
  return null;
}
const SECTIONS = ['kpis', 'filters-card', 'compare-card', 'takeaway', 'list-card', 'scatter-card',
                  'chart-card', 'map-card', 'notes-card', 'gone-card', 'next-callout'];
const onScreen = () => page.evaluate((ids) => ids.filter((id) => { const n = document.getElementById(id); return n && !n.hidden; }), SECTIONS);
const zt = emptyTrim();
if (!zt) {
  // vanished from the tally would be indistinguishable from one deleted.
  for (const name of ['a zero-car trim says why the page is empty',
                      'and its way out drops the trim and brings the sections back',
                      'a stale link onto an empty trim is not a dead end either'])
    skip(name, 'no watched trim holds zero cars in this snapshot');
} else {
  await open('?brand=' + zt.bk + '&m=' + zt.mk);
  await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
  // The control: the same model with nothing picked. Which sections a model
  // shows depends on what the market handed it — a model with no departures
  // has no gone-card — so the way out is measured against this page, not
  // against a list of ids somebody typed.
  await open('?brand=' + zt.bk + '&m=' + zt.mk);
  const control = { count: (await page.textContent('#filter-count')).trim(), sections: await onScreen() };
  await open('?brand=' + zt.bk + '&m=' + zt.mk + '&trims=' + zt.tid);
  const zeroTrim = await page.evaluate(() => ({
    hidden: document.getElementById('notice').hidden,
    text: document.getElementById('notice').textContent,
  }));
  ok('a zero-car trim says why the page is empty',
    !zeroTrim.hidden && zeroTrim.text.includes(zt.label), zt.tid + ' → ' + JSON.stringify(zeroTrim));
  // Guarded so the regression reports as a failed check rather than a 30s hang
  // on a link that is not there.
  if (await page.locator('#notice a').count()) { await page.click('#notice a'); await page.waitForTimeout(400); }
  const backFromZero = { trims: await page.evaluate(() => new URLSearchParams(location.search).get('trims')),
                         count: (await page.textContent('#filter-count')).trim(), sections: await onScreen() };
  ok('and its way out drops the trim and brings the sections back',
    backFromZero.trims === null && /^showing all [\d,]+ cars$/.test(backFromZero.count)
      && backFromZero.count === control.count
      && backFromZero.sections.join() === control.sections.join(),
    JSON.stringify(backFromZero) + ' vs control ' + JSON.stringify(control));

  // A shared link can be stale AND empty at once, and the stale-link notice
  // returns before every other branch: it printed "showing the rest of it"
  // over a page with nothing on it and offered no link at all, and stayed
  // that way until some other press re-rendered it.
  // A trim id this model certainly does not have — checked against the config,
  // not assumed, so the check cannot be defeated by a watchlist that grows one.
  const ids = new Set(Object.keys(DATA.brands[zt.bk].models[zt.mk].trims || {}));
  let ghost = zt.tid + '-retired'; while (ids.has(ghost)) ghost += '-x';
  await open('?brand=' + zt.bk + '&m=' + zt.mk + '&trims=' + zt.tid + ',' + ghost);
  const stale = await page.evaluate(() => ({ hidden: document.getElementById('notice').hidden,
    text: document.getElementById('notice').textContent, links: document.querySelectorAll('#notice a').length }));
  if (stale.links) { await page.click('#notice a'); await page.waitForTimeout(400); }
  const backFromStale = { trims: await page.evaluate(() => new URLSearchParams(location.search).get('trims')),
                          count: (await page.textContent('#filter-count')).trim() };
  ok('a stale link onto an empty trim is not a dead end either',
    !stale.hidden && stale.links === 1 && stale.text.includes(ghost)
      && backFromStale.trims === null && backFromStale.count === control.count,
    JSON.stringify(stale) + ' → ' + JSON.stringify(backFromStale));
}

// The other notice prints a number, and that number is a promise its own link
// has to keep: "Clear the filters" deliberately leaves the trim selection
// alone, so the sentence has to count what comes back, not what the model
// holds. Making the guard above honest broke this — with one trim picked the
// notice said "All 136 cars are filtered out" over a link that restored 80,
// and never named the trim holding the other 56 back.
//
// Discovered, again: a trim that holds SOME of its model's cars, plus a buyer
// state that trim has none of — press that where-chip and the page is empty
// for a reason the link does not undo. The assertion is the equality of the
// two numbers, so it neither knows nor cares which trim or which state.
function narrowedTrim() {
  for (const [bk, b] of Object.entries(DATA.brands || {}))
    for (const [mk, m] of Object.entries(b.models || {})) {
      const L = m.listings || [];
      const c = perTrim(m);
      for (const tid of Object.keys(m.trims || {})) {
        if (!c[tid] || c[tid] === L.length) continue;       // must be a real narrowing, or the check cannot discriminate
        const held = new Set(L.filter((x) => x.trim_id === tid).map((x) => (x.state || '').toUpperCase()));
        const w = ((DATA.buyer || {}).states || []).find((s) => !held.has(s));
        if (w) return { bk, mk, tid, w };
      }
    }
  return null;
}
const nt = narrowedTrim();
if (!nt) skip('the empty-filters notice counts what its own link restores', 'no trim in this snapshot is empty in a buyer state');
else {
  await open('?brand=' + nt.bk + '&m=' + nt.mk + '&trims=' + nt.tid);
  await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
  await open('?brand=' + nt.bk + '&m=' + nt.mk + '&trims=' + nt.tid);
  await page.click('[data-fkey="where:' + nt.w + '"]');
  await page.waitForTimeout(400);
  const promised = (await page.textContent('#notice')).match(/All ([\d,]+) cars are filtered out/);
  if (await page.locator('#notice a').count()) { await page.click('#notice a'); await page.waitForTimeout(400); }
  const restored = (await page.textContent('#filter-count')).trim().match(/^showing ([\d,]+) of ([\d,]+) cars/);
  const n = (s) => Number(String(s).replace(/,/g, ''));
  ok('the empty-filters notice counts what its own link restores',
    promised && restored && n(promised[1]) === n(restored[1]) && n(restored[1]) < n(restored[2]),
    nt.tid + ' + ' + nt.w + ' → promised ' + (promised ? promised[1] : 'no number')
      + ', restored ' + (restored ? restored[1] + ' of ' + restored[2] : 'nothing'));
  // The where-chip persists; leave the browser as the rest of the run found it.
  await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
}
}
// ---- ns/NS-05 ----
{
// A chart-legend chip is the price chart's control, not a filter. It also cut
// the map's dots, so pressing "Hide BMW i7" — a chip a full screen BELOW the
// map — dropped 137 of the 484 cars off it (479 dots to 342) while
// #filter-count still read "showing all 484 cars" and the map's own caption
// still blamed "the current filters". And it was persisted, so that map met
// the reader again days later.
//
// legend chip has a line drawn for it right now (a model with one snapshot day
// still draws one node, but if the data ever leaves every chip lineless there
// is nothing here to test and this says so instead of going red). Every figure
// is read off the live page before the press and compared against itself.
await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
await open('');
const mapState = () => page.evaluate(() => ({
  dots: document.querySelectorAll('#map .sc-dot:not(.is-off)').length,
  hint: document.getElementById('map-hint').textContent.split(',')[0],
  count: document.getElementById('filter-count').textContent,
  lines: document.querySelectorAll('#chart [data-sid]').length,
}));
const litUp = await mapState();
const legendKey = await page.evaluate(() => {
  const drawn = new Set([...document.querySelectorAll('#chart [data-sid]')].map((n) => n.getAttribute('data-sid')));
  const chip = [...document.querySelectorAll('#legend .sc-legend__chip')]
    .find((c) => drawn.has((c.getAttribute('data-fkey') || '').replace(/^legend:/, '')));
  return chip ? chip.getAttribute('data-fkey') : null;
});
if (!legendKey) {
  console.log('  skip  no legend chip has a line drawn in this data — the legend/map checks did not run');
} else {
  await page.click(`#legend [data-fkey="${legendKey}"]`);
  await page.waitForTimeout(400);
  const hidden = await mapState();
  ok('a legend chip still hides its own line', hidden.lines < litUp.lines,
    `${legendKey}: ${litUp.lines} series nodes → ${hidden.lines}`);
  ok('but it takes no dot off the map',
    hidden.dots === litUp.dots && hidden.hint === litUp.hint && hidden.count === litUp.count,
    `${litUp.dots} dots / "${litUp.hint}" / "${litUp.count}" → ${hidden.dots} dots / "${hidden.hint}" / "${hidden.count}"`);
  // A view toggle on one card must not outlive the visit: the next load is a
  // clean one, map AND chart, so no returning reader meets a page quietly
  // missing a model with only a dimmed swatch to explain it.
  await open('');
  const revisit = await mapState();
  ok('and the toggle does not outlive the visit',
    revisit.dots === litUp.dots && revisit.lines === litUp.lines
      && revisit.hint === litUp.hint && revisit.count === litUp.count,
    `${revisit.dots} dots / ${revisit.lines} series nodes, prefs ${await page.evaluate(() => localStorage.getItem('spicycar.prefs'))}`);
  // The other half of that, from the other side: a profile written by an older
  // build still carries a hidden set. It must be ignored, not honoured, or the
  // one reader this whole entry is about — the one who pressed a chip days ago
  // — gets the missing model anyway.
  await page.evaluate((k) => localStorage.setItem('spicycar.prefs',
    JSON.stringify({ where: [], hidden: [k], range: '90' })), legendKey.replace(/^legend:/, ''));
  await open('');
  const stale = await mapState();
  ok('and a hidden set left by an older profile is ignored',
    stale.dots === litUp.dots && stale.lines === litUp.lines
      && stale.hint === litUp.hint && stale.count === litUp.count,
    `${stale.dots} dots / ${stale.lines} series nodes / "${stale.count}"`);
}
await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
}
// --- the phone -------------------------------------------------------------
await page.setViewportSize({ width: 390, height: 844 });
await open('?models=bmw-i5,bmw-ix');
ok('the comparison survives a phone', await page.locator('#compare-card').isVisible());
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok('and does not scroll the page sideways', overflow <= 1, `${overflow}px of overflow`);
// Every figure on screen at once: capped and wrapped, two columns come to 344px
// inside a 348px window. Uncapped they ran to 687px and the reader met ten
// labels beside blank space.
const offscreen = await page.evaluate(() => {
  const right = document.querySelector('#compare-table').closest('.sc-table-scroll').getBoundingClientRect().right;
  return [...document.querySelectorAll('#compare-table tbody .sc-figure')].filter((f) => f.getBoundingClientRect().left >= right - 2).length;
});
ok('and every figure in it is on screen', offscreen === 0, `${offscreen} off the edge`);
await shot('compare-phone');

// The scope chip names every selection, and sc-chip does not wrap: seven models
// pushed the document 289px wider than the phone. Selected by pressing, not by
// a hand-written URL — the watchlist changes, and a test that names its models
// tests the config instead of the page.
await open('');
await page.locator('#filter-toggle').click();
await page.waitForTimeout(200);
const chips = await page.locator('#f-model button').count();
for (let i = 0; i < chips; i++) { await page.locator('#f-model button').nth(i).click(); await page.waitForTimeout(60); }
await page.waitForTimeout(400);
const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok('nor does selecting every model', wide <= 1, `${chips} models, ${wide}px of overflow`);

// ---- ns/NS-06 ----
{
// The loading shell is SHORT: h1 "Loading snapshot…", an empty #kpis and every
// card still hidden leave #main 314px tall, so footer.sc-foot painted at y=462
// on this 390×844 phone and was evicted to y=5527 the instant data.json
// rendered — one layout-shift entry, the page's entire 0.199 CLS. Measured
// against the SHELL because the shift is over ~160ms after paint and nothing
// after render can see it. #main{min-height:100vh} is what keeps the footer
// off the first screen, so unhiding the cards moves nothing the reader had.
//
// data.json is held on a latch rather than a sleep, so no machine is slow
// enough to render the page out from under the measurement, and the shell is
// re-asserted alongside the geometry: a footer below the fold means nothing if
// what is on screen is the finished page. Both figures are read from the live
// viewport (innerHeight, not the literal 844) so the check follows the phone
// block if its size ever changes. Nothing here touches the data — no car,
// price, count or trim id — so a tracker run cannot move it.
let releaseData;
const dataHeld = new Promise((r) => { releaseData = r; });
await ctx.route('**/data.json', async (r) => { await dataHeld; r.continue(); });
await page.goto(BASE + '/index.html', { waitUntil: 'load' });
const shell = await page.evaluate(() => ({
  h1: (document.getElementById('h1').textContent || '').trim(),
  footTop: Math.round(document.querySelector('footer.sc-foot').getBoundingClientRect().top),
  vh: window.innerHeight,
}));
ok('the footer waits below the fold while the data loads',
  shell.h1 === 'Loading snapshot…' && shell.footTop >= shell.vh,
  `footer top y=${shell.footTop} in a ${shell.vh}px viewport, h1 "${shell.h1}"`);
releaseData();
await page.waitForFunction(() => {
  const h = document.getElementById('h1');
  return h && h.textContent.trim() && h.textContent !== 'Loading snapshot…';
}, null, { timeout: 20000 }).catch(() => {});
await ctx.unroute('**/data.json');
}
// ---- ns/NS-10 ----
{
// Turn the phone sideways. Every rule that folds the filter panel away is
// keyed to WIDTH — sc.css hides .sc-filters below 721px and narrow() gates the
// JS at the same edge — so landscape (844x390 on an iPhone 13/14/15) un-folds
// it while the viewport stays 390px tall. The sticky bar then stood 371px of
// those 390 with its toggle at display:none, leaving a 19px letterbox of
// results at every scroll offset and no control to dismiss it.
//
// Two things here are deliberate, both to stop this becoming a test of the
// market instead of the page.
//
// carrying the most trim chips, and which model that is belongs to the
// watchlist, not to this file — a hand-written ?brand=…&m=… asserts today's
// config. If data.json names no model at all there is nothing to open, and
//
// The ORACLE names the mechanism, not only the outcome. `left >= 180` alone
// measures how tall the bar happens to be, and the bar is made of chips: a
// watchlist that shed enough model or where chips would drop the UNCAPPED bar
// under 210px at 844x390 and let a reverted stylesheet go green. So the cap
// itself is asserted beside the geometry — a real max-height, bounded to half
// the viewport, on a panel that scrolls — and those three do not move when the
// market does.
const subject = (() => {
  const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
  const all = [];
  for (const [bk, b] of Object.entries(site.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {}))
      all.push({ q: `?brand=${bk}&m=${mk}`, id: `${bk} ${mk}`,
                 trims: Object.keys((m || {}).trims || {}).length, cars: ((m || {}).listings || []).length });
  all.sort((a, b) => b.trims - a.trims || b.cars - a.cars || a.id.localeCompare(b.id));
  return all[0] || null;
})();
await page.setViewportSize({ width: 844, height: 390 });
if (!subject) {
  skip('a phone in landscape still sees the results under the filter bar', 'data.json names no model to open');
  skip('and can still reach every filter in it', 'data.json names no model to open');
  skip('and the cap lifts again on a tall screen', 'data.json names no model to open');
} else {
  await open(subject.q);
  await page.evaluate(() => window.scrollTo(0, 4000));
  await page.waitForTimeout(250);
  const land = await page.evaluate(() => {
    const c = document.getElementById('filters-card'), cs = getComputedStyle(c);
    return { left: Math.round(innerHeight - c.getBoundingClientRect().bottom),
             clipped: c.scrollHeight - c.clientHeight, height: Math.round(c.getBoundingClientRect().height),
             cap: cs.maxHeight, capPx: parseFloat(cs.maxHeight), overflowY: cs.overflowY, vh: innerHeight };
  });
  ok('a phone in landscape still sees the results under the filter bar',
    land.left >= 180 && land.capPx > 0 && land.capPx <= land.vh / 2,
    `${subject.id}: ${land.height}px bar, ${land.left}px of 390 left for the page, max-height ${land.cap}`);
  // The cap is only honest if what it hides can still be reached: the panel
  // scrolls inside itself rather than losing its last controls off the bottom.
  // The scroll container is asserted directly, so this cannot pass on a page
  // that simply has no cap and therefore nothing clipped.
  ok('and can still reach every filter in it',
    /^(auto|scroll)$/.test(land.overflowY) && (land.clipped === 0 || (await page.evaluate(() => {
      const c = document.getElementById('filters-card');
      c.scrollTop = c.scrollHeight;
      return c.scrollTop > 0;
    }))), `overflow-y:${land.overflowY}, ${land.clipped}px past the cap`);
  // …and it is keyed to the SHORT viewport, not applied everywhere. This one
  // is a guard, not a reproduction: it holds on the unfixed page too, and its
  // job is to catch a later edit that drops the media query and caps the bar
  // on the desktop the sticky panel was designed for.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  const tall = await page.evaluate(() => getComputedStyle(document.getElementById('filters-card')).maxHeight);
  ok('and the cap lifts again on a tall screen', tall === 'none', `max-height ${tall} at 390x844`);
}
}

// ---- ns/7B-C ----
{
// The corner cell of the side-by-side table — the one above the metric column
// — shipped as a th[scope=col] with nothing in it. That is the whole of axe's
// empty-table-header, and it was the last violation left on the site: it fired
// on all four audited states that open the compare card. What it costs a
// reader is not theoretical. Every row label under that cell ("Lowest drivable
// asking", "Best value vs typical") is a th[scope=row], and in a screen
// reader's table mode a row header is announced under its own column heading —
// which was silence.
//
// The oracle names the mechanism, not just "some text is there": the corner
// heads the column of ROW LABELS, so its name has to be a name for those, and
// the tempting wrong fix is to copy a model or trim name into it (which would
// have a screen reader read "BMW i5: Lowest drivable asking"). So the corner's
// text is asserted non-empty AND distinct from every case column's own label,
// with scope=col still saying which way the cell reads, and the row labels
// beside it asserted non-empty too — the same rule, one axis over, which holds
// on the unfixed page and guards the other half of the header.
//
// Both comparisons are checked because one cell serves both, and both subjects
// come out of data.json rather than being typed here: the two models carrying
// the most cars, and the model with the most trims (its two best-stocked ones).
// Nothing is pinned to a count, a price or a named car, so a tracker run that
// empties a trim or reorders the watchlist cannot move this. A watchlist with
// fewer than two models AND no model with two trims has no comparison to open
// at all — that is a skip by name, not a pass and not a failure.
const NAME = 'the side-by-side table names the column its row labels sit in';
const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const all = [];
for (const [bk, b] of Object.entries(site.brands || {}))
  for (const [mk, m] of Object.entries((b || {}).models || {})) {
    const cars = ((m || {}).listings || []);
    const held = {};
    for (const x of cars) if (x && x.trim_id) held[x.trim_id] = (held[x.trim_id] || 0) + 1;
    const trims = Object.keys((m || {}).trims || {})
      .sort((x, y) => (held[y] || 0) - (held[x] || 0) || x.localeCompare(y));
    all.push({ bk, mk, slug: `${bk}-${mk}`, cars: cars.length, trims });
  }
const pair = [...all].sort((a, b) => b.cars - a.cars || a.slug.localeCompare(b.slug)).slice(0, 2);
const multi = [...all].filter((m) => m.trims.length >= 2)
  .sort((a, b) => b.trims.length - a.trims.length || b.cars - a.cars || a.slug.localeCompare(b.slug))[0];
const cases = [];
if (pair.length === 2) cases.push(['models', `?models=${pair[0].slug},${pair[1].slug}`]);
if (multi) cases.push(['trims', `?brand=${multi.bk}&m=${multi.mk}&trims=${multi.trims[0]},${multi.trims[1]}`]);
await page.setViewportSize({ width: 1280, height: 1000 });
if (!cases.length) {
  skip(NAME, 'this snapshot has neither two models nor a model with two trims to compare');
} else {
  const seen = [];
  for (const [what, q] of cases) {
    await open(q);
    const corner = await page.evaluate(() => {
      const heads = [...document.querySelectorAll('#compare-table thead th')];
      if (!heads.length) return null;
      const txt = (n) => (n.textContent || '').replace(/\s+/g, ' ').trim();
      const labels = [...document.querySelectorAll('#compare-table tbody th.cmp-metric')].map(txt);
      return { name: txt(heads[0]), scope: heads[0].getAttribute('scope'),
               columns: heads.slice(1).map((h) => h.getAttribute('aria-label') || txt(h)),
               rows: labels.length, blankRows: labels.filter((t) => !t).length };
    });
    seen.push({ what, q, corner });
  }
  const bad = seen.filter(({ corner: c }) => !c || !c.name || c.scope !== 'col'
    || c.columns.some((h) => h && h.trim() === c.name) || !c.rows || c.blankRows);
  ok(NAME, bad.length === 0, seen.map(({ what, corner: c }) => c
    ? `${what}: "${c.name}" over ${c.rows} row labels, ${c.blankRows} of them blank, beside ${c.columns.length} columns`
    : `${what}: no compare table drew`).join(' · '));
}
}

await browser.close();
server.close();

for (const r of results) console.log(`  ${r.skip || r.skipped ? 'skip' : r.pass ? 'ok  ' : 'FAIL'}  ${r.name}${r.detail ? '  — ' + r.detail : ''}`);
if (errors.length) { console.log('\n  the page logged errors:'); for (const e of [...new Set(errors)]) console.log('      - ' + e); }
const skipped = results.filter((r) => r.skip || r.skipped).length;
const failed = results.filter((r) => !r.skip && !r.skipped && !r.pass).length;
const ran = results.length - skipped;
console.log(`\ndashboard smoke: ${ran - failed}/${ran} checks`
  + `${skipped ? `, ${skipped} skipped for want of a subject` : ''}, ${errors.length} page error${errors.length === 1 ? '' : 's'}`);
process.exit(failed || errors.length ? 1 : 0);
