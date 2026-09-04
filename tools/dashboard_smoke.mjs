// Does the dashboard still work? The one check that opens it.
//
//   node tools/dashboard_smoke.mjs <design-system-checkout> [--shots <dir>]
//
// docs/index.html is thousands of lines of markup, CSS and one IIFE, and until this
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

// --- one check may fail; the run may not -------------------------------------
// This file used to be one straight line of awaits, and a thrown locator was
// the end of it: `#f-trim button` .nth(1) on a model the sheet had left with
// one trim raised TimeoutError 30000ms out of the top level, and node printed
// a Playwright stack trace where the check names should have been. Not one
// result reached the operator either — every row was buffered to the end — so
// a run that had already proved forty claims reported none of them, and there
// was no way to tell a broken dashboard from a thinner watchlist.
//
// The irony is what makes it worth fixing: half the checks below carry a
// `skip` path so the suite survives a market that moves. Those paths could not
// fire. On the very morning the data shrank the way they were written for, the
// crash came a hundred lines earlier than the remedy.
//
// So the run is a list of STEPS. A step is a stretch of page-driving that
// shares one setup, it names the checks it is going to report, and it is run
// inside a try. If it throws, every check it promised and had not yet reported
// is written down BY NAME as a failure that never ran, the page is put back the
// way the next step expects to find it, and the run carries on. A step that
// finds no subject says so under those same names with skipRest(), which is the
// remedy the market-movement paths always meant.
//
// Three flags per skipped row is deliberate. Five workstreams wrote this helper
// independently in three different row shapes; a row missing `pass` was counted
// FAILED by one tally and a row missing `skipped` vanished from another.
// Carrying all three makes a skip render and count correctly under any of them.
//
// A check whose SUBJECT is not in today's data has neither passed nor failed —
// it had nothing to look at. Saying so out loud keeps the count honest about
// what was actually covered: scoring it a pass would quietly retire the check
// on the day the tracker stops watching the car it needed, and scoring it a
// failure would blame the dashboard for the market.
let live = null;                                   // the step running right now
const results = [];
const RESULT_LINE = (r) => `  ${r.skip || r.skipped ? 'skip' : r.pass ? 'ok  ' : 'FAIL'}  ${r.name}`
  + `${r.detail ? '  — ' + r.detail : ''}`;
// Streamed, not buffered: whatever else goes wrong, the operator keeps the
// record of everything that had already been decided.
const record = (r) => { results.push(r); console.log(RESULT_LINE(r)); if (live) live.said.add(r.name); return r; };
const ok = (name, pass, detail = '') => record({ name, pass: !!pass, detail });
// A skip names itself in the output and in the tally, so it counts as planned:
// only an `ok` that no step declared is a hole in the isolation.
const skip = (name, detail = '') => {
  if (live) live.planned.add(name);
  return record({ name, pass: true, skip: true, skipped: true, detail });
};

// What this step is about to report. Declared inside the body so a step whose
// check names come out of the sheet can name them once it knows them, and
// enforced both ways: a name reported without being planned is itself a
// failure, so a check added here later cannot quietly escape the isolation.
const plan = (...names) => { for (const n of names) live.planned.add(n); };
const unsaid = () => [...live.planned].filter((n) => !live.said.has(n));
// The subject is not in today's sheet: every check still owed is a skip, by name.
const skipRest = (why) => { for (const n of unsaid()) skip(n, why); };

// Put the page back the way an untouched step expects it. A step that threw may
// have left a route installed (the two CLS checks hold data.json on a latch), a
// phone-sized viewport, or a written profile behind it, and none of those are
// the next step's fault.
async function recover() {
  try { await ctx.unroute('**/data.json'); } catch { /* none installed */ }
  try { await page.setViewportSize({ width: 1280, height: 1000 }); } catch { /* page is gone */ }
  try { await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } }); }
  catch { /* nothing loaded */ }
}

// Playwright's call log is coloured and several lines long; the first line of
// it says which locator sat there, which is the whole of what an operator
// needs to go and look.
const oneLine = (e) => String((e && e.message) || e).replace(/\u001b\[[0-9;]*m/g, '')
  .split('\n').slice(0, 3).join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
async function step(label, body) {
  live = { label, planned: new Set(), said: new Set() };
  let threw = false;
  try {
    await body();
  } catch (e) {
    threw = true;
    const lost = unsaid();
    // Named where the step said what it owed; named after the step where it
    // threw before it could say, or after everything it owed was already in.
    if (lost.length) for (const n of lost) record({ name: n, pass: false, detail: `never ran — ${label} threw: ${oneLine(e)}` });
    else record({ name: label, pass: false, detail: `threw: ${oneLine(e)}` });
    await recover();
  } finally {
    const stray = [...live.said].filter((n) => live.planned.size && !live.planned.has(n));
    if (stray.length) record({ name: `${label} declares every check it reports`, pass: false,
                               detail: `unplanned: ${stray.join(' | ')}` });
    // The other half of the same promise, and the one that was missing. A step
    // that RETURNS NORMALLY having declared a check and never reported it used
    // to lose that check without a word: it is not in the failures, not in the
    // skips, not in the tally — and the EXPECTED backstop below only fires when
    // nothing skipped, so one skip anywhere disarmed the only thing that could
    // have noticed. An early `return` past half a step's checks is exactly how
    // this file has lost coverage before. A dropped check is a failure of the
    // suite, not a quiet absence: skipRest() is how a step says "no subject".
    if (!threw) {
      for (const n of unsaid()) record({ name: n, pass: false,
        detail: `declared by "${label}" and never reported — say skip()/skipRest() if it had no subject` });
    }
    live = null;
  }
}

// The watchlist is whatever the tracker fetched this morning, so a step that
// needs a car finds one here rather than naming one: a check that writes down
// ?brand=…&m=… asserts today's config beside the page's behaviour and goes red
// on a night that only changed the watchlist.
const SHEET = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const WATCHED = Object.entries(SHEET.brands || {}).flatMap(([bk, b]) =>
  Object.entries((b || {}).models || {}).map(([mk, m]) => ({
    bk, mk, id: `${bk} ${mk}`, slug: `${bk}-${mk}`, q: `?brand=${bk}&m=${mk}`, label: (m || {}).label || mk,
    trims: Object.keys((m || {}).trims || {}), cars: ((m || {}).listings || []).length })));
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
await step('the watchlist', async () => {
  plan('the watchlist opens', 'four tiles', 'a row per model', 'the chart draws', 'the map draws',
       'three chip groups');
  await open('');
  ok('the watchlist opens', (await page.textContent('#h1')) === 'The watchlist', await page.textContent('#h1'));
  ok('four tiles', (await page.locator('#kpis .sc-tile').count()) === 4);
  // Two of these six are claims about a watchlist with more than one model on
  // it — the index draws a row EACH, and the model chips are a group only where
  // there is something to tell apart (index.html hides #f-model-field below two
  // models, :3818) — and two are claims about a watchlist with a car on it. The
  // rest hold on any sheet. Below either line the check has nothing to look at,
  // which is a thinner watchlist and not a broken page.
  const several = WATCHED.length > 1;
  const thin = `the watchlist holds ${WATCHED.length === 1 ? 'one model' : 'no models'} today`;
  if (several) ok('a row per model', (await page.locator('#overview-table tbody tr').count()) > 1);
  else skip('a row per model', thin);
  if (WATCHED.some((w) => w.cars)) {
    ok('the chart draws', (await page.locator('#chart svg').count()) > 0);
    ok('the map draws', (await page.locator('#map svg').count()) > 0);
  } else {
    skip('the chart draws', 'no model on the watchlist holds a car today');
    skip('the map draws', 'no model on the watchlist holds a car today');
  }
  if (several) ok('three chip groups', (await page.locator('#f-where button').count()) > 0
    && (await page.locator('#f-model button').count()) > 1);
  else skip('three chip groups', `${thin} — no model chips are drawn`);
  await shot('watchlist');
});

// --- a model page ----------------------------------------------------------
// WHICH model belongs to the sheet, not to this file. The first one the
// watchlist holds cars for: a model with none renders no list and no scatter,
// so it is not a subject for these four, and a sheet holding none at all is a
// thinner watchlist rather than a broken page.
const carried = WATCHED.find((w) => w.cars);
await step('a model page', async () => {
  plan('a model page opens', 'it lists cars', 'it plots price against miles', 'no compare card at rest');
  if (!carried) return skipRest('no model on the watchlist holds a car today');
  await open(carried.q);
  ok('a model page opens', (await page.textContent('#h1')).includes(carried.label), await page.textContent('#h1'));
  ok('it lists cars', (await page.locator('#list-table tbody tr').count()) > 0);
  ok('it plots price against miles', (await page.locator('#scatter svg').count()) > 0);
  ok('no compare card at rest', await page.locator('#compare-card').isHidden());
});

// --- comparing trims -------------------------------------------------------
// This presses the SECOND chip and then the THIRD, so it wants a model the
// sheet gives at least three trims — and index.html renders no chip at all
// below two (:3833), which is how `#f-trim button` .nth(1) used to end the
// whole run on the morning a model dropped to one. The first such model, so a
// reordered watchlist does not move the subject.
const trio = WATCHED.find((w) => w.trims.length >= 3);
await step('comparing trims', async () => {
  plan('the trim control is chips', 'one trim is a scope, not a comparison', 'one trim reaches the title',
       'two trims compare', 'the title says vs', 'a column per trim', 'a line per trim',
       'the chart says which question it answered', 'the comparison is in the address bar',
       'a shared trim comparison reopens', 'with both chips pressed');
  if (!trio) return skipRest('no watched model has three trims today — nothing to compare');
  await open(trio.q);
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
});

// --- comparing models ------------------------------------------------------
// The other crash site, and the same shape: index.html hides #f-model-field
// below two models (:3818), so on a single-model watchlist the chip is in the
// DOM but never visible and `.first().click()` sat there for 30s and then took
// the process down with it.
await step('comparing models', async () => {
  plan('one model narrows the index', 'two models compare', 'the index narrows to both',
       'their cars pool into one table', 'the pooled table names the car',
       'every pooled row says which model', 'and it can be sorted',
       'the comparison is in the address bar', 'a shared model comparison reopens');
  if (WATCHED.length < 2) return skipRest('the watchlist holds fewer than two models today — nothing to compare');
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
});

// A link naming a car the watchlist no longer has opens the rest and says so,
// rather than dropping the reader on a page that is silently missing one. The
// live half is a real slug out of the sheet; the dead half is built from it, so
// the URL cannot go stale and cannot accidentally name a car that comes back.
await step('a half-dead link', async () => {
  plan('a half-dead link still opens', 'and names what went missing');
  if (!carried) return skipRest('no model on the watchlist holds a car today');
  const ghost = `${carried.bk}-not-a-car`;
  await open(`?models=${carried.slug},${ghost}`);
  ok('a half-dead link still opens', (await page.locator('#overview-table tbody tr').count()) === 1);
  ok('and names what went missing', new RegExp(ghost).test(await page.textContent('#notice')));
});

// ---- ns/NS-03 ----
await step('prototype-key links', async () => {
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
plan(...protoUrls.flatMap((q) => [`${q} lands on the watchlist, not on a car`,
                                  `${q} says the link missed and stops re-sharing itself`]));
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
});
// --- the things two audits found, so they cannot come back -----------------
// A URL is a subject only while the sheet still holds the car it names. The
// three comparisons below were written against particular cohorts — the trims
// whose years proved the winner rule, the model with one snapshot day, the two
// models whose pooled count was measured against itself — and which cars those
// are belongs to the watchlist, not to this file. A morning that retires one is
// a morning with nothing to look at, not a failing dashboard, and it used to be
// a page of dashes read as a regression.
const inSheet = (q) => {
  const p = new URLSearchParams(q.slice(1));
  const want = (p.get('models') || '').split(',').filter(Boolean);
  if (want.length) return want.every((sl) => WATCHED.some((w) => w.slug === sl));
  const w = WATCHED.find((x) => x.bk === p.get('brand') && x.mk === p.get('m'));
  return !!w && (p.get('trims') || '').split(',').filter(Boolean).every((t) => w.trims.includes(t));
};

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
await step('the marked winner', async () => {
const MARKED = 'a winner is marked only where every column was judged on its own trim and year';
plan(MARKED);
const cohorts = ['?brand=bmw&m=ix&trims=bmw-ix-xdrive,bmw-ix-m',
                 '?brand=bmw&m=i5&trims=bmw-i5-edrive40,bmw-i5-m60',
                 '?brand=bmw&m=i7&trims=bmw-i7-edrive50,bmw-i7-xdrive60',
                 '?brand=bmw&m=i7&trims=bmw-i7-edrive50,bmw-i7-m70',
                 '?models=bmw-i5,bmw-i7'].filter(inSheet);
if (!cohorts.length) return skipRest('this snapshot holds none of the comparisons the rule was written against');
for (const q of cohorts) {
  await open(q);
  const r = await page.evaluate(() => {
    const row = [...document.querySelectorAll('#compare-table tbody tr')]
      .find((tr) => /Best value vs typical/.test(tr.querySelector('th').textContent));
    const cells = [...row.querySelectorAll('td')];
    return { bases: cells.map((td) => td.getAttribute('data-basis')),
             marked: cells.filter((td) => td.classList.contains('is-best')).length };
  });
  const comparable = r.bases.every((b) => b === 'trim');
  ok(MARKED, comparable ? r.marked <= 1 : r.marked === 0,
    `${q} → ${r.marked} marked, bases ${r.bases.join('/')}`);
}
});

// One shared record handed every Audi row a "new" chip while the compare card
// beside it said the Audi had no previous snapshot to be new against.
await step('the "new" chip', async () => {
const FRESH = 'a model with one snapshot day has no "new" cars';
plan(FRESH);
const q = '?models=audi-a6-etron,bmw-i5';
if (!inSheet(q)) return skipRest(`${q} names a car the watchlist no longer holds`);
await open(q);
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
ok(FRESH, Object.values(newByModel).every((v) => v.fresh < v.rows), JSON.stringify(newByModel));
});

// The line whose job is to say what the filters are doing measured the
// selection against itself and printed "showing all 169 cars" over 503, and the
// count on the chip beside it turned muted-grey-on-cobalt the moment it was
// pressed. One selection, so one step: the second reads the chip the first
// pressed.
await step('the filter line and the chip beside it', async () => {
plan('the count measures against the whole watchlist', "a pressed chip's count is still readable");
const q = '?models=bmw-i5,kia-ev9';
if (!inSheet(q)) return skipRest(`${q} names a car the watchlist no longer holds`);
await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
await open(q);
const count = await page.textContent('#filter-count');
ok('the count measures against the whole watchlist', / of \d+ cars/.test(count) && /\+/.test(count), count);

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
});

// ---- ns/NS-02 ----
await step('chip counts in both themes', async () => {
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
plan(...chipStates.flatMap(([where]) => ['dark', 'light']
  .map((theme) => `every chip count is readable on ${where}, in ${theme}`)));
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
});
// ---- ns/NS-08 ----
await step('what a note may say', async () => {
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
plan('no note on the watchlist carries a dated commitment', 'a described trim reaches the dek',
     'and the chip it sits beside says the same', 'and neither reads as a deadline');
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
});
// ---- ns/NS-09 ----
await step('the filter count announces itself', async () => {
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
plan('the filter count is a status message', 'and a re-sort changes what it says');
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
});
// "Only this →" takes its own card off the page; the keyboard must land
// somewhere, not on <body>.
await step('narrowing to one column', async () => {
plan('narrowing to one column keeps the keyboard somewhere');
const q = '?brand=bmw&m=i5&trims=bmw-i5-edrive40,bmw-i5-xdrive40';
if (!inSheet(q)) return skipRest(`${q} names a trim the watchlist no longer holds`);
await open(q);
await page.evaluate(() => document.querySelector('[data-fkey^="cmp:"]').focus());
await page.keyboard.press('Enter');
await page.waitForTimeout(400);
ok('narrowing to one column keeps the keyboard somewhere',
  (await page.evaluate(() => document.activeElement.tagName)) !== 'BODY');
});

// ---- ns/NS-01 ----
await step('the tiles and the chart chip agree', async () => {
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
plan('at rest the tiles claim the nation and the chart chip agrees',
     'a trim chip makes the price tiles say filtered, like the chart chip',
     'and leaves the movement tile, which counts every trim, saying so');
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
  skipRest('no watched model has two trims today — the tiles-vs-chip scope check has no subject');
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
});
// ---- ns/NS-04 ----
await step('an empty trim', async () => {
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
plan('a zero-car trim says why the page is empty',
     'and its way out drops the trim and brings the sections back',
     'a stale link onto an empty trim is not a dead end either',
     'the empty-filters notice counts what its own link restores');
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
// When the market hands us no empty trim, SYNTHESIZE one rather than skip.
// These three checks were carried for a year by bmw-i7-cpo, a watch that
// returned zero cars because its query could not reach one; standing it down
// retired the empty-state coverage with it, silently, and a permanently
// skipped check is a check that no longer exists. The rule under test — a
// trim with no cars must SAY so and offer a way back — does not depend on
// which trim is empty or on why, so an emptied one proves it exactly as well.
let stubbedEmpty = false;
async function synthesizeEmptyTrim() {
  for (const [bk, b] of Object.entries(DATA.brands || {}))
    for (const [mk, m] of Object.entries(b.models || {})) {
      const c = perTrim(m);
      const ids = Object.keys(m.trims || {});
      // Needs a model that still has cars once one trim is emptied, or the
      // page takes the different `!total` path and this proves nothing.
      const victim = ids.find((tid) => c[tid] && (m.listings || []).length > c[tid]);
      if (!victim) continue;
      await ctx.route('**/data.json', async (route) => {
        const r = await route.fetch();
        const sheet = JSON.parse(await r.text());
        const mm = ((sheet.brands || {})[bk] || {}).models[mk];
        mm.listings = (mm.listings || []).filter((x) => x.trim_id !== victim);
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      stubbedEmpty = true;
      return { bk, mk, tid: victim, label: (m.trims[victim] || {}).label || victim };
    }
  return null;
}
const zt = emptyTrim() || await synthesizeEmptyTrim();
if (!zt) {
  // vanished from the tally would be indistinguishable from one deleted.
  for (const name of ['a zero-car trim says why the page is empty',
                      'and its way out drops the trim and brings the sections back',
                      'a stale link onto an empty trim is not a dead end either'])
    skip(name, 'no watched trim holds zero cars and none could be emptied');
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
// The emptied-trim sheet belongs to the three checks above and to nothing else.
// The check below needs a trim that holds SOME cars, which is exactly what the
// stub took away — it selects from the real sheet on disk and would then drive
// a page serving the stubbed one, and read "no cars in this trim" where it
// expects "all N are filtered out".
if (stubbedEmpty) { await ctx.unroute('**/data.json'); stubbedEmpty = false; }
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
});
// ---- ns/NS-05 ----
await step('a legend chip is not a filter', async () => {
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
plan('a legend chip still hides its own line', 'but it takes no dot off the map',
     'and the toggle does not outlive the visit',
     'and a hidden set left by an older profile is ignored');
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
  skipRest('no legend chip has a line drawn in this data — the legend and map checks had nothing to press');
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
});
// --- the phone -------------------------------------------------------------
await step('a comparison on a phone', async () => {
plan('the comparison survives a phone', 'and does not scroll the page sideways',
     'and every figure in it is on screen');
await page.setViewportSize({ width: 390, height: 844 });
const q = '?models=bmw-i5,bmw-ix';
if (!inSheet(q)) return skipRest(`${q} names a car the watchlist no longer holds`);
await open(q);
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
});

// The scope chip names every selection, and sc-chip does not wrap: seven models
// pushed the document 289px wider than the phone. Selected by pressing, not by
// a hand-written URL — the watchlist changes, and a test that names its models
// tests the config instead of the page.
await step('every model on a phone', async () => {
plan('nor does selecting every model');
await page.setViewportSize({ width: 390, height: 844 });
if (WATCHED.length < 2) return skipRest('the watchlist holds fewer than two models today — no chips to press');
await open('');
await page.locator('#filter-toggle').click();
await page.waitForTimeout(200);
const chips = await page.locator('#f-model button').count();
for (let i = 0; i < chips; i++) { await page.locator('#f-model button').nth(i).click(); await page.waitForTimeout(60); }
await page.waitForTimeout(400);
const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok('nor does selecting every model', wide <= 1, `${chips} models, ${wide}px of overflow`);
});

// ---- ns/NS-06 ----
await step('the footer while the data loads', async () => {
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
plan('the footer waits below the fold while the data loads');
// Set here rather than inherited from the phone block above: a step that has
// to be isolated has to carry its own viewport, or the size it measures at is
// whatever the step before it happened to survive on.
await page.setViewportSize({ width: 390, height: 844 });
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
});
// ---- ns/NS-10 ----
await step('a phone in landscape', async () => {
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
plan('a phone in landscape still sees the results under the filter bar',
     'and can still reach every filter in it', 'and the cap lifts again on a tall screen');
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
});

// ---- ns/7B-A ----
await step('the masthead while the data loads', async () => {
// The masthead's right-hand slot was the last box on this page nobody had
// reserved, and the last layout-shift entry with it. It ships "Loading…" and
// render() replaces it with "Data through " + an ISO date — 56px of text
// becoming 161px — and at 320x568 that no longer fits beside the nav on the
// masthead's wrapped second row, so the slot took a row of its OWN, grew the
// masthead from 100.5px to 127.2px and pushed all of #main down with it: one
// entry, sources #mast-right and #main, and the whole 0.194 that NS-06's
// #main{min-height:100vh} could not reach at that width. 320 is where this has
// to be measured: from ~334px up the same growth costs no row at all and the
// page is already at 0.001.
//
// data.json is held on the same latch NS-06 uses rather than a sleep, so no
// machine is slow enough to render the page out from under the shell reading.
//
// The ORACLE names the mechanism beside the outcome. An equal masthead height
// on its own only measures how long today's date string happens to be — a
// shorter one would stop wrapping by itself and let a reverted stylesheet go
// green — so the reservation is asserted too, and against the WIDEST line the
// slot can hold rather than against today's: the live label with its value
// swapped for a full-width ISO stand-in, measured in the slot's own font. That
// keeps the check off the market entirely. It reads no car, price, count or
// trim id, and if data.json does not render there is no swap to watch and it
// says so by name instead of passing quietly.
const mastName = 'the masthead holds its height when the data line lands';
plan(mastName);
let releaseMast;
const mastHeld = new Promise((r) => { releaseMast = r; });
await ctx.route('**/data.json', async (r) => { await mastHeld; r.continue(); });
await page.setViewportSize({ width: 320, height: 568 });
await page.goto(BASE + '/index.html', { waitUntil: 'load' });
const mastShell = await page.evaluate(() => {
  const el = document.getElementById('mast-right');
  return { text: (el.textContent || '').trim(),
           reserved: parseFloat(getComputedStyle(el).minWidth) || 0,
           mastH: +document.querySelector('header.sc-masthead').getBoundingClientRect().height.toFixed(2) };
});
releaseMast();
await page.waitForFunction(() => {
  const h = document.getElementById('h1');
  return h && h.textContent.trim() && h.textContent !== 'Loading snapshot…';
}, null, { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(250);
const mastLive = await page.evaluate(() => {
  const el = document.getElementById('mast-right'), cs = getComputedStyle(el);
  const probe = document.createElement('span');
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};`
    + `letter-spacing:${cs.letterSpacing};text-transform:${cs.textTransform}`;
  el.parentElement.appendChild(probe);
  probe.textContent = (el.textContent || '').trim().replace(/\S+$/, '0000-00-00');
  const ink = +probe.getBoundingClientRect().width.toFixed(2), wide = probe.textContent;
  probe.remove();
  return { text: (el.textContent || '').trim(), wide, ink,
           h1: (document.getElementById('h1').textContent || '').trim(),
           mastH: +document.querySelector('header.sc-masthead').getBoundingClientRect().height.toFixed(2) };
});
await ctx.unroute('**/data.json');
if (mastLive.h1 === 'Snapshot unavailable' || mastLive.text === mastShell.text)
  skip(mastName, `#mast-right never swapped — it still reads "${mastLive.text}"`);
else
  ok(mastName, mastLive.mastH === mastShell.mastH && mastShell.reserved >= mastLive.ink,
    `320x568: masthead ${mastShell.mastH}px "${mastShell.text}" -> ${mastLive.mastH}px "${mastLive.text}",`
    + ` ${mastShell.reserved}px reserved for a ${mastLive.ink}px "${mastLive.wide}"`);
});


// ---- ns/7B-B ----
await step('the narrowest phone', async () => {
// ---- ns/7B-B ----
// 320x568 — an iPhone SE / small Android, the narrowest phone still in use, and
// the width below every band this file measured. A model page scrolled 12px
// sideways there: docH scrollWidth 332 in a 320 viewport.
//
// The cause is structural, not a width. sc.css lays .sc-media--card out as
// `auto 1fr auto` — frame | the three text lines | aside — and the frame,
// spanning three rows in column 1, is what parks title/sub/code in column 2.
// The departed-vehicle card is the one card on the page with no photo, so
// auto-placement put its title in column 1, the price in column 2 and the
// LOCATION in a third column of its own; three max-content columns plus two
// gaps came to 286px inside a 228px card and hung 12px off the document.
//
// Both halves are asserted, and neither is a width or a pixel budget:
//   1. the document does not scroll sideways at all, and
//   2. every departed card keeps its own content inside its own border box —
//      the mechanism, so this cannot go green on a day the strings happen to
//      be short enough to fit a still-scrambled grid.
// The subject is whichever model has the most departures in today's file, and
// a file with no departure anywhere renders no card to look at: that is a skip,
// not a pass, because the check would have had nothing to measure.
plan('a model page does not scroll sideways on the narrowest phone');
{
  const departed = (() => {
    const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
    const all = [];
    for (const [bk, b] of Object.entries(site.brands || {}))
      for (const [mk, m] of Object.entries((b || {}).models || {})) {
        const vins = new Set(((m || {}).gone || []).map((g) => String(g.vin || '').toUpperCase()).filter(Boolean));
        all.push({ q: `?brand=${bk}&m=${mk}`, id: `${bk} ${mk}`, gone: vins.size });
      }
    all.sort((a, b) => b.gone - a.gone || a.id.localeCompare(b.id));
    return all[0] && all[0].gone ? all[0] : null;
  })();
  const NAME = 'a model page does not scroll sideways on the narrowest phone';
  if (!departed) {
    skip(NAME, 'no model in data.json has a departed vehicle — no card to lay out');
  } else {
    await page.setViewportSize({ width: 320, height: 568 });
    await open(departed.q);
    await page.waitForTimeout(250);
    const narrow320 = await page.evaluate(() => {
      const de = document.documentElement, cards = document.getElementById('gone-cards');
      const shown = cards && !cards.hidden ? [...cards.children] : [];
      return {
        over: de.scrollWidth - de.clientWidth,
        cards: shown.length,
        worstCard: shown.reduce((w, c) => Math.max(w, c.scrollWidth - c.clientWidth), 0),
      };
    });
    if (!narrow320.cards) {
      skip(NAME, `${departed.id}: the departed-vehicle cards did not render at 320px`);
    } else {
      ok(NAME, narrow320.over <= 1 && narrow320.worstCard <= 1,
        `${departed.id}: ${narrow320.over}px past the 320px viewport, `
        + `worst of ${narrow320.cards} departed cards ${narrow320.worstCard}px past its own box`);
    }
  }
}

});

// ---- ns/7B-C ----
await step('the side-by-side table header', async () => {
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
plan(NAME);
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

});

// ---- the monthly payment, which is the number this buyer decides on -------
// A certified car on a promotional APR can cost less per month than a cheaper
// car that is not, and that is the ONLY reason this ranking exists — so the
// checks below hold the two things that make it true rather than decorative:
// the promo reaches certified cars and nothing else, and a promo capped at 60
// months is not quoted at 72. Both are asserted against whatever the sheet says
// today: the subject is discovered, never named, because a config that retires
// the i5 must not read as a broken dashboard.
//
// Inside step(), like everything else. These four lived in a bare block for
// want of a wrapper, which meant one thrown locator here ended the whole run
// with a Playwright stack trace where the results should be — the precise
// failure the step() harness exists to end, sitting in the same file as the
// comment describing it.
await step('the monthly payment', async () => {
  plan('a promo reaches certified cars and no others',
       'a promo capped at 60 months is not quoted at 72');
  const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
  const fin = (site.buyer || {}).finance;
  const livePromo = ((fin || {}).promos || []).find((p) => p.active && p.apr != null);
  // A model that has both a certified car and a non-certified one, under a live
  // promo — the only shape where the boundary is observable at all.
  const subject = !livePromo ? null : (() => {
    const [bk, mk] = String(livePromo.model).split('/');
    const m = ((site.brands || {})[bk] || {}).models?.[mk];
    const rows = (m || {}).listings || [];
    return (rows.some((x) => x.cpo) && rows.some((x) => !x.cpo))
      ? { q: `?brand=${bk}&m=${mk}`, who: `${bk} ${mk}`, apr: livePromo.apr, cap: livePromo.max_term } : null;
  })();
  if (!subject) {
    skip('a promo reaches certified cars and no others',
         'no watched model has a live promo with both certified and non-certified cars today');
    skip('a promo capped at 60 months is not quoted at 72', 'same');
  } else {
    await open(subject.q);
    await page.selectOption('#f-sort', 'payment');
    await page.waitForTimeout(350);
    const seen = await page.$$eval('#list-table tbody tr', (rows) => rows.map((r) => {
      const code = r.querySelector('.sc-media__code');
      const note = [...r.querySelectorAll('.sc-note')].map((n) => n.textContent).find((t) => /\/mo at /.test(t)) || '';
      const m = note.match(/at ([\d.]+)%/);
      return { vin: code ? code.textContent.trim() : '', apr: m ? Number(m[1]) : null };
    }));
    const cpoVins = await page.evaluate(() => window.__cpoVins || null);
    const promoRows = seen.filter((r) => r.apr === subject.apr);
    const otherRows = seen.filter((r) => r.apr != null && r.apr !== subject.apr);
    ok('a promo reaches certified cars and no others',
       promoRows.length > 0 && otherRows.length > 0 && new Set(seen.map((r) => r.apr).filter((v) => v != null)).size === 2,
       `${subject.who}: ${promoRows.length} rows at ${subject.apr}%, ${otherRows.length} at the ordinary rate`);
    // The cap: ask for the longest term the sheet offers and the promo car must
    // still quote its own maximum, not the longer one.
    const longest = Math.max(...((fin.terms || [60])));
    if (subject.cap && longest > subject.cap) {
      await page.selectOption('#f-term', String(longest));
      await page.waitForTimeout(350);
      const title = await page.$$eval('#list-table tbody .sc-note', (ns, apr) => {
        const t = ns.map((n) => n.getAttribute('title') || '').find((t) => t.includes(apr + '%'));
        return t || '';
      }, String(subject.apr));
      ok('a promo capped at 60 months is not quoted at 72',
         title.includes(`${subject.cap} months`),
         `term set to ${longest}; the promo car says "${title.slice(0, 70)}"`);
      await page.selectOption('#f-term', String(fin.default_term || 60));
    } else {
      skip('a promo capped at 60 months is not quoted at 72',
           'no live promo has a term cap shorter than the longest offered term');
    }
  }
});

// ---- out-the-door ----
// Tax is the largest number this page has never shown — roughly seven times the
// median shipping estimate it always has — and it lands on EVERY car, because
// Illinois charges the buyer's home rate wherever the car was bought. Checked
// through the rendered page rather than the internals, and against the sheet's
// own fee block, so it follows the config instead of a number written here.
await step('out the door', async () => {
  plan('every car carries tax, local and shipped alike',
       'the out-the-door total shows its own working');
  const fees = await page.evaluate(async () => {
    const r = await fetch('data.json');
    return ((await r.json()).buyer || {}).fees || null;
  });
  if (!fees) {
    skip('every car carries tax, local and shipped alike', 'this sheet has no fees block');
    skip('the out-the-door total shows its own working', 'this sheet has no fees block');
  } else {
    await open('?brand=bmw&m=i5');
    await page.selectOption('#f-sort', 'otd');
    await page.waitForTimeout(350);
    const rows = await page.evaluate(() => {
      // Row shape, verified against the rendered DOM rather than assumed: the
      // price cell carries the first .sc-figure, and the shipping cell is the
      // one whose text says "landed", "drivable" or "n/a" — it carries a
      // .sc-figure only when there is a charge. The total column is dropped at
      // some widths, so shipping is taken from its own cell, never from the
      // last figure in the row and never from the out-the-door note itself.
      const num = (el) => (el ? Number((el.textContent.match(/\$([\d,]+)/) || [0, 0])[1].replace(/,/g, '')) : null);
      return [...document.querySelectorAll('#list-scroll tbody tr')].slice(0, 12).map((tr) => {
        const tds = [...tr.querySelectorAll('td')];
        const shipCell = tds.find((td) => /landed|drivable|n\/a/.test(td.textContent));
        const otdEl = [...tr.querySelectorAll('.sc-note')].find((n) => /out the door/.test(n.textContent));
        const shipFig = shipCell ? shipCell.querySelector('.sc-figure') : null;
        return {
          price: num(tr.querySelector('.sc-figure')),
          ship: shipFig ? num(shipFig) : 0,
          drivable: !!(shipCell && /drivable/.test(shipCell.textContent)),
          otd: num(otdEl),
          title: otdEl ? otdEl.getAttribute('title') || '' : '',
        };
      }).filter((r) => r.price && r.otd);
    });
    const fixed = (fees.doc_fee || 0) + (fees.title || 0) + (fees.registration || 0) + (fees.ev_surcharge || 0);
    const want = (r) => Math.round(r.price * (1 + fees.tax_rate) + fixed + (r.drivable ? 0 : r.ship));
    const bad = rows.filter((r) => Math.abs(r.otd - want(r)) > 1);
    ok('every car carries tax, local and shipped alike',
      rows.length > 0 && bad.length === 0,
      rows.length ? `${rows.length} rows, ${rows.filter((r) => r.drivable).length} drivable · ${bad.length ? `worst $${bad[0].otd} vs $${want(bad[0])}` : 'every total exact'}`
                  : 'no out-the-door rows rendered under the otd sort');
    // A total nobody has verified must say so, or it reads as researched.
    const verified = !!fees.checked;
    ok('the out-the-door total shows its own working',
      rows.length > 0 && rows.every((r) => /asking/.test(r.title) && /tax/.test(r.title)
        && (verified ? /checked/.test(r.title) : /ESTIMATE/.test(r.title))),
      rows.length ? rows[0].title : 'no rows');
  }
});

// ---- what the page refuses to offer -----------------------------------------
// A sort the sheet cannot compute is the quietest kind of wrong: every row's key
// falls back to the same sentinel, the list does not move, and the reader reads
// the unchanged order as the answer to the question they just asked. So the
// option has to be GONE, not inert. Proved against a sheet with the fees block
// cut out — the shape a page has before a buyer has ever filled one in — rather
// than by reading the guard's source.
await step('a sort the sheet cannot compute is not offered', async () => {
  plan('the out-the-door sort is withheld without a fee block',
       'and offered again when the sheet has one');
  // Only buyer.fees is cut. Whether this sheet HAS a finance block decides what
  // the payment option proves below, so it is read before the cut, not assumed.
  const hasFinance = await page.evaluate(async () => !!((await (await fetch('data.json')).json()).buyer || {}).finance);
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    if (sheet.buyer) delete sheet.buyer.fees;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open('?brand=bmw&m=i5');
    const state = await page.evaluate(() => ({
      hidden: !!document.querySelector('#f-sort option[value="otd"]')?.hidden,
      // Two controls against a false pass. "landed" must survive, or the select
      // emptied and every option is "hidden" for the wrong reason. And where the
      // sheet has finance, "payment" must survive too — it is guarded off a
      // DIFFERENT config block, so cutting fees must not take it down with it.
      payShown: !document.querySelector('#f-sort option[value="payment"]')?.hidden,
      landedShown: !document.querySelector('#f-sort option[value="landed"]')?.hidden,
    }));
    ok('the out-the-door sort is withheld without a fee block',
       state.hidden && state.landedShown && (!hasFinance || state.payShown),
       `otd hidden=${state.hidden} · landed still offered=${state.landedShown}`
       + ` · payment ${hasFinance ? `still offered=${state.payShown}` : 'not applicable, this sheet has no finance block'}`);
  } finally {
    await ctx.unroute('**/data.json');
  }
  // And the other direction, which is the half that keeps the guard honest.
  // Testing only the absent case passes an unconditional `hidden = true`: the
  // whole out-the-door ranking vanishes from the real sheet and CI stays green.
  // Playwright will select a hidden option without complaint, so the existing
  // otd checks cannot notice either.
  await open('?brand=bmw&m=i5');
  const shownAgain = await page.evaluate(() => !!document.querySelector('#f-sort option[value="otd"]')?.hidden);
  const sheetHasFees = await page.evaluate(async () => !!((await (await fetch('data.json')).json()).buyer || {}).fees);
  ok('and offered again when the sheet has one',
     shownAgain === !sheetHasFees,
     sheetHasFees ? `this sheet has fees, otd hidden=${shownAgain}`
                  : `this sheet has no fees, otd correctly hidden=${shownAgain}`);
});

// ---- the term the note claims is the term the rows are quoted at ------------
await step('the finance note owns the promo term cap', async () => {
  plan('a note that claims 72 months says which promos are capped shorter',
       'and claims no cap when the term already fits inside one');
  const fin = await page.evaluate(async () => ((await (await fetch('data.json')).json()).buyer || {}).finance || null);
  const capped = fin && (fin.promos || []).find((p) => p.active && p.max_term);
  const longest = fin && Math.max(...(fin.terms || [60]));
  if (!capped || !(longest > capped.max_term)) {
    return skipRest('no live promo caps the term below the longest one offered');
  }
  await open('?brand=bmw&m=i5');
  // #compare-hint carries the note, and the card only exists once two trims are
  // picked ON PURPOSE — a model that happens to have four is not comparing them.
  const chips = await page.$$('#f-trim button');
  if (chips.length < 2) return skipRest('this model has fewer than two trims to compare');
  await chips[0].click(); await chips[1].click();
  await page.waitForTimeout(300);
  await page.selectOption('#f-term', String(longest));
  await page.waitForTimeout(350);
  const hint = await page.$eval('#compare-hint', (n) => n.textContent);
  ok('a note that claims 72 months says which promos are capped shorter',
     hint.includes(`${longest} months`) && hint.includes(`capped at ${capped.max_term} months`),
     hint.slice(hint.indexOf('Payments assume')) || '(no finance note rendered)');
  // The negative case, without which the check passes on an UNCONDITIONAL cap
  // clause — a cap that does not bite, phrased as though it forces 60. Asked at
  // a term the promo already accommodates, the note must not mention a cap.
  const fits = Math.min(...(fin.terms || [60]).filter((t) => t <= capped.max_term));
  await page.selectOption('#f-term', String(fits));
  await page.waitForTimeout(350);
  const short = await page.$eval('#compare-hint', (n) => n.textContent);
  ok('and claims no cap when the term already fits inside one',
     short.includes(`${fits} months`) && !short.includes('capped at'),
     short.slice(short.indexOf('Payments assume')) || '(no finance note rendered)');
  await page.selectOption('#f-term', String(fin.default_term || 60));
});

// ---- a down payment that covers the whole car -------------------------------
// payFor clamps the payment to $0; the tooltip used to subtract straight through
// and print "60 months on -$4,200 financed" underneath it. Two numbers from one
// principal must not disagree about its sign.
await step('a down payment larger than the car', async () => {
  plan('a covered car is not quoted a negative balance');
  const fin = await page.evaluate(async () => ((await (await fetch('data.json')).json()).buyer || {}).finance || null);
  if (!fin) return skipRest('this sheet has no finance block');
  await open('?brand=bmw&m=i5');
  // The sort must be SELECTED, not asked for in the query string: the page
  // parses brand/m/model/models/trims and nothing else, so `?sort=payment` left
  // S.sort at 'local' — where payNote renders only for PROMO cars. This check
  // was reading 4 rows of 30, and a clamp applied to the promo branch alone
  // passed it green while 25 rows printed "60 months on -$359,373 financed".
  await page.selectOption('#f-sort', 'payment');
  await page.waitForTimeout(300);
  await page.fill('#f-down', '400000');
  // fill() raises `input`; the control listens for `change`, which a real reader
  // fires by leaving the field. Without this the page never sees the number and
  // the check passes on rows that were never given a down payment at all.
  await page.dispatchEvent('#f-down', 'change');
  await page.waitForTimeout(400);
  const notes = await page.$$eval('#list-scroll tbody .sc-note',
    (ns) => ns.map((n) => ({ text: n.textContent, title: n.getAttribute('title') || '' }))
              .filter((n) => /\/mo|month/.test(n.text + n.title)));
  const negative = notes.filter((n) => /-\s*\$|\$-/.test(n.title));
  // Every priced row must carry one, or the check is reading a subset again.
  const rowCount = await page.$$eval('#list-scroll tbody tr', (rs) => rs.length);
  ok('a covered car is not quoted a negative balance',
     notes.length >= rowCount && rowCount > 4 && negative.length === 0
       && notes.every((n) => /paid outright/.test(n.title)),
     notes.length ? `${notes.length} payment notes over ${rowCount} rows, ${negative.length} negative,`
                    + ` ${notes.filter((n) => /paid outright/.test(n.title)).length} say paid outright`
                    + ` · "${(notes[0].title || '').slice(0, 56)}"`
                  : 'no payment notes rendered at a $400,000 down payment');
  await page.fill('#f-down', '0');
  await page.dispatchEvent('#f-down', 'change');
});

// ---- the two ways of building a day row agree -------------------------------
// The chart draws a precomputed series (Tracking.py's daily_stats) until a
// filter is on, and then rebuilds the same days from every car's own price
// history. Those are two implementations of one definition, in two languages,
// and the file has always claimed they reconcile — so this presses filters that
// exclude NOTHING and asserts the numbers do not move.
//
// It is the check that catches a fix applied to one side only. Both sides had
// counted the cars FETCHED on a day rather than the cars known on it, so a
// model whose trims run on different cadences halved its own row every other
// day and swung its median by thousands; repairing Python alone would have left
// the page telling one story unfiltered and another with a no-op filter on.
await step('the rebuilt day rows match the precomputed ones', async () => {
  plan('a filter that excludes nothing does not move a single day row');
  const subject = WATCHED.find((w) => w.cars > 1);
  if (!subject) return skipRest('no model on the watchlist holds cars today');
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
  await open(subject.q);
  const readRows = () => page.$$eval('#chart-table tbody tr',
    (rs) => rs.map((r) => [...r.querySelectorAll('td')].map((td) => td.textContent.trim()).join('|')));
  const before = await readRows();
  // every Where chip pressed: each car sits in one of the buyer's states or
  // outside them, so the selection is the whole market by construction
  const chips = await page.$$('#f-where button');
  for (const c of chips) { await c.click(); await page.waitForTimeout(60); }
  await page.waitForTimeout(400);
  const after = await readRows();
  const rebuilt = await page.textContent('#chart-scope');
  const same = before.length === after.length && before.every((r, i) => r === after[i]);
  ok('a filter that excludes nothing does not move a single day row', same && before.length > 0,
     before.length
       ? (same ? `${before.length} day rows identical, chip now reads "${rebuilt.trim()}"`
               : `first difference: precomputed "${before.find((r, i) => r !== after[i])}" vs rebuilt "${after[before.findIndex((r, i) => r !== after[i])]}"`)
       : 'the chart table drew no rows');
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
});

// ---- an estimate says so where it is read -----------------------------------
// "ESTIMATE" appeared exactly once in 4,482 lines: a title= attribute on the
// out-the-door note, which renders only under the out-the-door sort. Hover-only,
// sort-gated, invisible on a phone. Meanwhile every row printed
// "+ $1,336 shipping = $66,224" as flat fact from band rates nobody has quoted,
// and every monthly payment silently contained an unverified 9.25% tax.
//
// Asserted in BOTH directions against a stubbed sheet, because a check that
// only looks for the word passes on an unconditional one — a page that cries
// "estimate" over calibrated numbers is the same failure wearing the other
// coat, and it is the direction a later edit takes.
await step('an estimate says so where it is read', async () => {
  plan('shipping is called an estimate on the row itself',
       'the listings caption says which numbers nobody has checked',
       'and a calibrated sheet drops the word');
  const buyer = await page.evaluate(async () => ((await (await fetch('data.json')).json()).buyer || {}));
  if (!((buyer.ship_bands || []).length)) return skipRest('this sheet prices no shipping through bands');
  if (buyer.ship_calibrated || (buyer.fees || {}).checked)
    return skipRest('this sheet is already calibrated — the uncalibrated wording has no subject');
  const shopped = WATCHED.find((w) => w.cars);
  if (!shopped) return skipRest('no model on the watchlist holds a car today');
  await open(shopped.q);
  // the shipping CELL, not the whole row: a row also carries a payment note
  // and a location, and matching those would pass on a page that says nothing
  // about shipping at all
  const shipTexts = await page.$$eval('#list-scroll tbody tr',
    (rows) => rows.map((r) => {
      const cell = [...r.querySelectorAll('td')].find((td) => /landed|drivable|n\/a/.test(td.textContent));
      return cell ? cell.textContent : '';
    }).filter((t) => /landed/.test(t)).slice(0, 8));
  ok('shipping is called an estimate on the row itself',
     shipTexts.length > 0 && shipTexts.every((t) => /est\. shipping/.test(t)),
     shipTexts.length ? `${shipTexts.length} rows priced for shipping, first says "${shipTexts[0].replace(/\s+/g, ' ').trim()}"`
                      : 'no shipped car on this page');
  const hint = await page.textContent('#list-hint');
  ok('the listings caption says which numbers nobody has checked',
     /estimate/i.test(hint) && /not verified|unverified/i.test(hint),
     hint.slice(-190));

  // …and with the two dates filled in, the hedge is gone.
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    if (sheet.buyer) {
      sheet.buyer.ship_calibrated = '2026-09-01';
      if (sheet.buyer.fees) sheet.buyer.fees.checked = '2026-09-01';
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(shopped.q);
    const after = await page.evaluate(() => ({
      rows: [...document.querySelectorAll('#list-scroll tbody tr')].map((r) => {
        const cell = [...r.querySelectorAll('td')].find((td) => /landed|drivable|n\/a/.test(td.textContent));
        return cell ? cell.textContent : '';
      }).filter((t) => /landed/.test(t)).slice(0, 8),
      hint: document.getElementById('list-hint').textContent,
    }));
    ok('and a calibrated sheet drops the word',
       after.rows.length > 0 && after.rows.every((t) => !/est\. shipping/.test(t))
         && /calibrated/i.test(after.hint) && /checked/i.test(after.hint)
         && !/not verified|unverified/i.test(after.hint),
       after.hint.slice(-170));
  } finally {
    await ctx.unroute('**/data.json');
  }
});

// ---- the table is actually in the order it says it is -----------------------
// sortRows could ignore S.sort entirely and the suite still reported green: the
// select was driven, the re-render was asserted, the ORDER never was. A sort
// that does not sort is the quietest kind of wrong — the list does not move and
// the reader takes the unchanged order for the answer to the question they just
// asked.
//
// The oracle is monotonicity of the column the reader is looking at, read out of
// the rendered rows, so it needs no fixture and cannot rot with the market.
await step('the listings table honours the order it advertises', async () => {
  const ORDERS = [['price', 'Asking', 'asc'], ['miles', 'Miles', 'asc'],
                  ['days_listed', 'Longest on market', 'desc']];
  plan(...ORDERS.map(([k]) => `sorting by ${k} really orders the rows by ${k}`));
  const subject = WATCHED.find((w) => w.cars > 3);
  if (!subject) return skipRest('no model on the watchlist holds enough cars to order');
  await open(subject.q);
  for (const [key, , dir] of ORDERS) {
    await page.selectOption('#f-sort', key);
    await page.waitForTimeout(350);
    // read the VALUES from the rows, by the same key the sort claims to use
    const vals = await page.evaluate((k) => {
      const cells = (tr) => [...tr.querySelectorAll('td')];
      return [...document.querySelectorAll('#list-scroll tbody tr')].map((tr) => {
        const tds = cells(tr);
        const num = (el) => { const m = (el ? el.textContent : '').match(/-?[\d,]+/); return m ? Number(m[0].replace(/,/g, '')) : null; };
        if (k === 'price') return num(tds[1]);
        if (k === 'miles') return num(tds[2]);
        const loc = tds.find((td) => /d listed|since /.test(td.textContent));
        const m = loc && loc.textContent.match(/(\d+)d listed/);
        return m ? Number(m[1]) : null;
      }).filter((v) => v !== null);
    }, key);
    const ordered = vals.every((v, i) => i === 0 || (dir === 'asc' ? v >= vals[i - 1] : v <= vals[i - 1]));
    ok(`sorting by ${key} really orders the rows by ${key}`,
       vals.length > 2 && ordered,
       vals.length > 2 ? `${vals.length} rows: ${vals.slice(0, 6).join(' ')}${ordered ? '' : '  <- out of order'}`
                       : `only ${vals.length} readable values`);
  }
  await page.selectOption('#f-sort', 'local');
});

// ---- the two numbers the page leads with ------------------------------------
// The tiles are the first thing read and the last thing checked: tile 2 could
// name a Florida car as "Lowest drivable asking · no shipping" and the suite
// reported 92/92. Both tiles are asserted against the sheet itself — the
// cheapest car, and the cheapest car in a state the buyer actually drives to.
await step('the headline tiles name the right car', async () => {
  plan('the lowest-asking tile is the cheapest car on the page',
       'the lowest-drivable tile is the cheapest car the buyer can drive to');
  const subject = WATCHED.find((w) => w.cars > 3);
  if (!subject) return skipRest('no model on the watchlist holds enough cars');
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
  await open(subject.q);
  const truth = await page.evaluate(async (mk) => {
    const sheet = await (await fetch('data.json')).json();
    const m = sheet.brands[mk.bk].models[mk.mk];
    const states = (sheet.buyer || {}).states || [];
    const priced = (m.listings || []).filter((x) => x.price != null);
    const local = priced.filter((x) => states.includes(String(x.state || '').toUpperCase()));
    const low = (a) => a.reduce((b, x) => (!b || x.price < b.price ? x : b), null);
    return { all: low(priced), local: low(local), states };
  }, { bk: subject.bk, mk: subject.mk });
  const tiles = await page.$$eval('#kpis .sc-tile', (ns) => ns.map((n) => ({
    label: n.querySelector('.sc-tile__label').textContent,
    value: n.querySelector('.sc-tile__value').textContent,
    sub: (n.querySelector('.sc-tile__sub') || {}).textContent || '',
  })));
  const money = (n) => '$' + Number(n).toLocaleString('en-US');
  const t1 = tiles.find((t) => /lowest asking/i.test(t.label));
  const t2 = tiles.find((t) => /drivable/i.test(t.label));
  ok('the lowest-asking tile is the cheapest car on the page',
     !!(t1 && truth.all && t1.value.trim() === money(truth.all.price)),
     t1 ? `tile "${t1.label}" says ${t1.value.trim()}, cheapest in the sheet is ${truth.all ? money(truth.all.price) : 'none'}` : 'no such tile');
  ok('the lowest-drivable tile is the cheapest car the buyer can drive to',
     !!(t2 && truth.local && t2.value.trim() === money(truth.local.price)
        && truth.states.some((st) => t2.sub.includes(st))),
     t2 ? `tile "${t2.label}" says ${t2.value.trim()} — "${t2.sub.trim()}"; cheapest drivable in the sheet is ${truth.local ? money(truth.local.price) + ' in ' + truth.local.state : 'none'}` : 'no such tile');
});

// ---- the promo, priced ------------------------------------------------------
// financeNote() has always said "60 days left"; nothing said what those days
// were worth. The strip turns the offer into money on a real car, so the thing
// that has to hold is that the heading, the rate and the car are the SAME
// offer: the first build picked the promo before the car and put a 2.49% iX
// under a heading that read 2.99%.
await step('the promo strip prices one real offer', async () => {
  plan('the rate in the heading is the rate the car is financed at',
       'and a certified car at a seller not named BMW is kept out of the figure',
       'and the strip disappears when no promo is live');
  const fin = await page.evaluate(async () => ((await (await fetch('data.json')).json()).buyer || {}).finance || null);
  const live = ((fin || {}).promos || []).filter((p) => p.active && p.apr != null);
  if (!live.length) return skipRest('this sheet has no live promo');
  await open('');
  const shown = await page.evaluate(() => {
    const c = document.getElementById('promo-card');
    if (!c || c.hidden) return null;
    return { title: document.getElementById('promo-title').textContent,
             hint: document.getElementById('promo-hint').textContent,
             tiles: [...document.querySelectorAll('#promo-tiles .sc-tile')].map((t) => t.textContent),
             foot: document.getElementById('promo-foot').textContent };
  });
  if (!shown) return skipRest('no car in view is reachable by a live promo');
  // the heading names a promo; the hint quotes its rate; both must be one offer
  const promo = live.find((p) => (p.label || '') === shown.title.trim());
  ok('the rate in the heading is the rate the car is financed at',
     !!promo && shown.hint.includes(`What ${promo.apr}% is worth`)
       && shown.tiles.some((t) => /\/mo →/.test(t)),
     promo ? `"${shown.title}" quotes ${promo.apr}% — "${shown.hint.slice(0, 70)}…"`
           : `heading "${shown.title}" matches no live promo (${live.map((p) => p.label).join(', ')})`);

  // The feed's `cpo` is a generic certified flag; the captive lender's rate is
  // written at its own franchise. Where the two differ the page must say so.
  const unnamed = await page.evaluate(async () => {
    const sheet = await (await fetch('data.json')).json();
    let flagged = 0, unnamed = 0;
    for (const b of Object.values(sheet.brands || {}))
      for (const m of Object.values(b.models || {}))
        for (const x of (m.listings || []))
          if (x.cpo) { flagged++; if (!/\bbmw\b/i.test(x.dealer || '')) unnamed++; }
    return { flagged, unnamed };
  });
  if (!unnamed.unnamed) skip('and a certified car at a seller not named BMW is kept out of the figure',
                             'every certified car on this sheet is listed by a seller named BMW');
  else ok('and a certified car at a seller not named BMW is kept out of the figure',
          /does not say BMW/.test(shown.foot) && /excluded/.test(shown.foot),
          `${unnamed.unnamed} of ${unnamed.flagged} certified cars — foot says "${shown.foot.slice(0, 110)}…"`);

  // …and it is a promo card, not furniture: with every promo expired it goes.
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    for (const q of (((sheet.buyer || {}).finance || {}).promos || [])) { q.active = false; q.days_left = -1; }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open('');
    ok('and the strip disappears when no promo is live',
       await page.locator('#promo-card').isHidden(), 'every promo expired');
  } finally { await ctx.unroute('**/data.json'); }
});

// ---- a percentage says what it is a percentage OF ---------------------------
// "21% under typical" was printed bare on the rows, the phone cards and the
// scatter tooltip. The compare card has carried the cohort in a title= since
// the winner rule was written — because the cohort is exactly what decides
// whether the comparison means anything — and every other surface dropped it.
// It is visible now, with the cohort's size, because a title= is not readable
// on a phone and not reachable from a keyboard.
await step('a value percentage names its cohort', async () => {
  plan('every "under typical" note says which cohort and how many cars',
       'and a fallback cohort says it is a fallback');
  const subject = WATCHED.find((w) => w.cars > 3);
  if (!subject) return skipRest('no model on the watchlist holds enough cars');
  await open(subject.q);
  await page.selectOption('#f-sort', 'value');
  await page.waitForTimeout(350);
  const notes = await page.$$eval('#list-scroll tbody .sc-note',
    (ns) => ns.map((n) => ({ text: n.textContent.replace(/\s+/g, ' ').trim(),
                             basis: n.getAttribute('data-basis'),
                             title: n.getAttribute('title') || '' }))
             .filter((n) => /under typical/.test(n.text)));
  if (!notes.length) return skipRest('no car on this page sits under its typical price');
  const named = notes.filter((n) => /n=\d+/.test(n.text) && n.basis);
  ok('every "under typical" note says which cohort and how many cars',
     named.length === notes.length,
     `${named.length} of ${notes.length} — first: "${notes[0].text}"`);
  // …and where the cohort is NOT the car's own trim and year, the note must
  // say so rather than let a blended median pass as a like-for-like median.
  const fallback = notes.filter((n) => n.basis !== 'trim');
  if (!fallback.length) skip('and a fallback cohort says it is a fallback',
                             'every scored car on this page had its own trim and year to be judged against');
  else ok('and a fallback cohort says it is a fallback',
          fallback.every((n) => /too few/.test(n.title)),
          `${fallback.length} fallback note(s), first title: "${fallback[0].title.slice(0, 90)}"`);
  await page.selectOption('#f-sort', 'local');
});

// ---- the two surfaces agree about the picks ---------------------------------
// The dashboard held four drivable seats and reserved two of them for the
// models being shopped; REPORT.md ranked by margin alone and reserved nothing.
// Nothing published the rule — targets.json never mentioned it and the export
// never carried it — so the page carried a hard-coded 2 and the two lists
// disagreed about half the front page: the report's drivable picks were an
// Ioniq 5, an Ioniq 5, an iX and an EV9, with neither car being decided on
// among them. One rule now, buyer.picks.reserve_shopping, read by both.
//
// The SETS are asserted, not the order: the page leads with the shopped cars
// on purpose, while the report keeps every list in margin order. Set equality
// is the claim that both applied the same rule to the same market.
await step('the picks agree across both surfaces', async () => {
  plan('the drivable picks are the same cars in the report and on the page',
       'and so are the worth-the-ship picks');
  const reportPath = resolve(HERE, '..', 'REPORT.md');
  if (!existsSync(reportPath)) return skipRest('no REPORT.md beside the dashboard');
  const md = readFileSync(reportPath, 'utf8');
  const whole = md.split('## Spicy picks across the watchlist')[1];
  if (!whole) return skipRest('this report has no watchlist-wide picks section');
  const vinsIn = (text) => [...new Set((text.match(/`[A-HJ-NPR-Z0-9]{17}`/g) || [])
    .map((v) => v.slice(1, -1)))].sort();
  const section = (head) => {
    const cut = whole.split(head)[1];
    return cut === undefined ? null : vinsIn(cut.split('###')[0]);
  };
  const wantLocal = section('### Drivable');
  const wantShip = section('### Worth the ship');
  await open('');
  const groups = await page.evaluate(() => [...document.querySelectorAll('#takeaway .picks-group')]
    .map((g) => ({
      label: ((g.querySelector('.sc-eyebrow') || g.querySelector('summary') || {}).textContent || ''),
      vins: [...new Set([...g.querySelectorAll('[data-fkey^="pick:"]')]
        .map((a) => a.getAttribute('data-fkey').split(':')[1]))].sort(),
    })));
  const got = (needle) => (groups.find((g) => g.label.toLowerCase().includes(needle)) || {}).vins || null;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => v === b[i]);
  if (wantLocal === null) skip('the drivable picks are the same cars in the report and on the page',
                              'the report names no drivable picks today');
  else ok('the drivable picks are the same cars in the report and on the page',
          same(wantLocal, got('drivable')),
          `report ${JSON.stringify(wantLocal)} vs page ${JSON.stringify(got('drivable'))}`);
  if (wantShip === null) skip('and so are the worth-the-ship picks',
                              'the report names no worth-the-ship picks today');
  else ok('and so are the worth-the-ship picks',
          same(wantShip, got('worth the ship')),
          `report ${JSON.stringify(wantShip)} vs page ${JSON.stringify(got('worth the ship'))}`);
});

// --- the shortlist you build yourself --------------------------------------
// Everything else on this page is the market's opinion: what is cheapest, what
// is under typical, what the tracker thinks is worth a look. The shortlist is
// the one surface that holds YOUR opinion, and it is kept in localStorage,
// which means nothing in CI touches it and nothing in the Python suite can see
// it. If the cycle breaks or the profile stops loading, the page still renders
// perfectly and the buyer quietly loses the four cars they were deciding
// between. So the membership and the round trip are asserted here, by driving
// the actual buttons a reader presses.
await step('the shortlist you build yourself', async () => {
  plan('starring two cars puts exactly those two cars in the shortlist table',
       'the star cycles through its four states',
       'a car you rule out leaves the comparison but keeps its mark',
       'and the shortlist survives a reload');
  await open('');
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  await open('');
  const keys = await page.locator('button[data-fkey^="star:"]')
    .evaluateAll((bs) => bs.map((b) => b.getAttribute('data-fkey')));
  if (keys.length < 2) return skipRest('the watchlist offers fewer than two cars to star today');
  // Press by data-fkey, never by position: every press re-renders, and the pick
  // cards re-sort under it. The first version of this check pressed .nth(0)
  // three times and starred three different cars.
  const press = async (k) => {
    await page.locator(`button[data-fkey="${k}"]`).first().click();
    await page.waitForTimeout(120);
  };
  const vinOf = (k) => k.split(':')[1];
  await press(keys[0]);
  await press(keys[1]);
  const inTable = async () => (await page.locator('#finalists-table thead [data-fkey^="fin:"]')
    .evaluateAll((as) => as.map((a) => a.getAttribute('data-fkey').split(':')[1]))).sort();
  const want = [vinOf(keys[0]), vinOf(keys[1])].sort();
  const got = await inTable();
  ok('starring two cars puts exactly those two cars in the shortlist table',
     got.length === 2 && got.every((v, i) => v === want[i]),
     `starred ${JSON.stringify(want)} · table holds ${JSON.stringify(got)}`);

  // none -> shortlisted -> called -> ruled out -> none, and the button says
  // which. A ruled-out car leaves the comparison but keeps its mark, so the
  // table count is part of what the cycle means.
  const label = async (k) => (await page.locator(`button[data-fkey="${k}"]`).first().textContent()).trim();
  const seen = [await label(keys[0])];
  const held = [await inTable()];
  for (let i = 0; i < 3; i++) { await press(keys[0]); seen.push(await label(keys[0])); held.push(await inTable()); }
  const cycled = /shortlisted/.test(seen[0]) && /called/.test(seen[1])
              && /ruled out/.test(seen[2]) && /^☆/.test(seen[3]);
  ok('the star cycles through its four states', cycled, seen.join(' → '));
  // "Ruled out" is the state that has to do two things at once, and they pull
  // against each other: the car must leave the comparison (you are not deciding
  // between it and anything any more) while keeping its mark on its own row (so
  // you do not open it again next week and start over). A plain star would lose
  // the second; a fourth state that stayed in the table would defeat the first.
  const v0 = vinOf(keys[0]);
  const inAt = held.map((h) => h.includes(v0));
  ok('a car you rule out leaves the comparison but keeps its mark',
     inAt[0] && inAt[1] && !inAt[2] && !inAt[3] && /ruled out/.test(seen[2]),
     `in the table at: shortlisted ${inAt[0]} · called ${inAt[1]} · ruled out ${inAt[2]} · cleared ${inAt[3]}`);

  // Back to a two-car shortlist, then reload: the whole point of the feature is
  // that it is still there tomorrow.
  await press(keys[0]);
  const before = await inTable();
  await page.reload({ waitUntil: 'load' });
  await page.waitForTimeout(500);
  const after = await inTable();
  ok('and the shortlist survives a reload',
     after.length === before.length && after.length === 2 && after.every((v, i) => v === before[i]),
     `${JSON.stringify(before)} → ${JSON.stringify(after)}`);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
});

// --- what the shortlist refuses to say -------------------------------------
// The compare card above it marks a winner on exactly one row and says why at
// length: across MODELS a price is not a score, so calling the cheapest one
// "best value" would have the EV9 beat the i5 by being smaller. The shortlist
// is a different question — every car in it was starred by the reader, so
// "which of these is cheapest" is real — but the answer is still only what it
// literally is, and the phrase "best value here" must not follow a price onto
// this table. It did, in the first draft: under Asking, under Out the door,
// under Per month and under Miles.
//
// The other two claims here are absences that were hiding an answer. A car
// sitting ABOVE typical printed a dash, which on your own shortlist is the one
// number you would most want before ringing the dealer; and a car whose price
// was cut and then put back up printed "$0 off", which reads as a discount —
// 22 cars in this sheet were in that state the day it was written.
await step('the shortlist refuses to call a price a score', async () => {
  plan('no row on the shortlist calls a price the best value',
       'a car above typical says so rather than showing a dash',
       'and a cut that was undone is not printed as a discount');
  // A car that was cut and ended no cheaper: a fact about the data, computed
  // here rather than read off the page, so the check cannot agree with a bug.
  const cutback = Object.entries(SHEET.brands || {}).flatMap(([bk, b]) =>
    Object.entries((b || {}).models || {}).flatMap(([mk, m]) =>
      ((m || {}).listings || []).filter((x) => x.cuts && (x.delta || 0) >= 0)
        .map((x) => ({ bk, mk, vin: x.vin, cuts: x.cuts, delta: x.delta || 0 }))))[0] || null;
  // The dearest cars on one model page: whatever cohort scores them, the top of
  // a distribution is above its own median, so this is where "% over" lives.
  const biggest = WATCHED.slice().sort((a, b) => b.cars - a.cars)[0];
  const home = cutback ? WATCHED.find((w) => w.bk === cutback.bk && w.mk === cutback.mk) : biggest;
  if (!home || !home.cars) return skipRest('no model on the watchlist holds a car today');
  const listings = ((SHEET.brands[home.bk] || {}).models[home.mk] || {}).listings || [];
  const dearest = listings.filter((x) => x.price != null)
    .sort((a, b) => b.price - a.price).slice(0, 3).map((x) => x.vin);
  const wanted = [...new Set([...(cutback && cutback.bk === home.bk && cutback.mk === home.mk ? [cutback.vin] : []), ...dearest])];
  if (wanted.length < 2) return skipRest(`${home.id} holds fewer than two priced cars today`);
  await open(home.q);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  await open(home.q);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count()) await more.click();
  await page.waitForTimeout(200);
  const starred = [];
  for (const vin of wanted) {
    const b = page.locator(`button[data-fkey="star:${vin}"]`).first();
    if (!(await b.count())) continue;
    await b.click(); await page.waitForTimeout(120);
    starred.push(vin);
  }
  if (starred.length < 2) return skipRest(`only ${starred.length} of the wanted cars is on ${home.id}'s page today`);

  // Read the table as a reader sees it: the row's own label, then each cell.
  const grid = await page.locator('#finalists-table tbody tr').evaluateAll((trs) => trs.map((tr) => ({
    label: (tr.children[0].textContent || '').trim(),
    cells: [...tr.children].slice(1).map((td) => ({
      figure: ((td.querySelector('.sc-figure') || {}).textContent || '').trim(),
      notes: [...td.querySelectorAll('.sc-note')].map((n) => n.textContent.trim()),
      best: td.classList.contains('is-best'),
    })),
  })));
  const rowFor = (needle) => grid.find((r) => r.label.toLowerCase().includes(needle));
  const allNotes = grid.flatMap((r) => r.cells.flatMap((c) => c.notes));
  const winners = grid.flatMap((r) => r.cells.filter((c) => c.best).flatMap((c) => c.notes.slice(-1)));
  const valueRow = rowFor('value vs typical');
  ok('no row on the shortlist calls a price the best value',
     !allNotes.some((n) => /best value/i.test(n))
       && winners.length > 0
       && winners.every((w) => /^(lowest asking|cheapest all in|lowest payment|fewest miles)$/.test(w))
       && !!valueRow && valueRow.cells.every((c) => !c.best),
     `${winners.length} winners marked: ${[...new Set(winners)].join(', ') || 'none'}`);

  // Two ways the value row can be wrong, and this pins both: a scored car whose
  // figure is a dash (the answer withheld), and an unscored car whose note is a
  // shrug rather than the rule that excluded it.
  if (!valueRow) skip('a car above typical says so rather than showing a dash', 'the shortlist drew no value row');
  else {
    const scored = valueRow.cells.filter((c) => c.notes.some((n) => /\bn=\d+/.test(n)));
    const over = scored.filter((c) => /% over/.test(c.figure));
    const withheld = scored.filter((c) => c.figure === '—');
    const shrug = valueRow.cells.filter((c) => c.notes.some((n) => /^not scored$/i.test(n)));
    if (!over.length && !withheld.length) {
      skip('a car above typical says so rather than showing a dash',
           `every scored car on this shortlist happens to sit under typical (${scored.length} scored)`);
    } else {
      ok('a car above typical says so rather than showing a dash',
         over.length > 0 && withheld.length === 0 && shrug.length === 0,
         `${over.length} say "% over", ${withheld.length} scored car(s) show a dash, ${shrug.length} say only "not scored"`);
    }
  }

  const marketRow = rowFor('on the market');
  if (!cutback || !starred.includes(cutback.vin) || !marketRow) {
    skip('and a cut that was undone is not printed as a discount',
         cutback ? 'the cut-then-restored car is not on this page today' : 'no car in this sheet was cut and then put back up');
  } else {
    const col = starred.indexOf(cutback.vin);
    const heads = await page.locator('#finalists-table thead [data-fkey^="fin:"]')
      .evaluateAll((as) => as.map((a) => a.getAttribute('data-fkey').split(':')[1]));
    const at = heads.indexOf(cutback.vin);
    const cell = at >= 0 ? marketRow.cells[at] : null;
    const said = cell ? cell.notes.join(' ') : '';
    ok('and a cut that was undone is not printed as a discount',
       !!cell && /back up/.test(said) && !/\$0 off/.test(said) && !/-\$/.test(said),
       `${cutback.vin} (${cutback.cuts} cuts, delta ${cutback.delta}) reads "${said}"`);
    void col;
  }
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
});

await browser.close();
server.close();

// Every row was printed as it was decided; what is left is the reckoning.
const failedRows = results.filter((r) => !r.skip && !r.skipped && !r.pass);
// Named again at the bottom, without the detail already printed beside each:
// on a long CI log the roll-call is the thing worth scrolling to.
if (failedRows.length) { console.log('\n  what failed:'); for (const r of failedRows) console.log('      - ' + r.name); }
if (errors.length) { console.log('\n  the page logged errors:'); for (const e of [...new Set(errors)]) console.log('      - ' + e); }
const skipped = results.filter((r) => r.skip || r.skipped).length;
const failed = failedRows.length;
const ran = results.length - skipped;
console.log(`\ndashboard smoke: ${ran - failed}/${ran} checks`
  + `${skipped ? `, ${skipped} skipped for want of a subject` : ''}, ${errors.length} page error${errors.length === 1 ? '' : 's'}`);
// The hardest lesson this suite has learned, encoded where it survives the night.
// It has been assembled three different ways that each reported GREEN while quietly
// covering less: 63/63 with 17 checks missing, 76/76 with 7, and a merge that lost
// four more. A count is the only thing that catches that, and a count written down
// in a markdown file catches nothing.
// Skips are legitimate and vary with the data — a shrunken watchlist genuinely has
// fewer subjects, and some checks collapse into a coarser skip. So the assertion is
// made where it is exact: when nothing skipped, every check had a subject and the
// total must be the declared one. That is the case CI runs.
// If you ADD a check, raise this number in the same commit. That is the point.
const EXPECTED = 115;
if (!skipped && results.length !== EXPECTED) {
  console.log(`\n  !! this suite declares ${EXPECTED} checks and recorded ${results.length},`);
  console.log('     with nothing skipped. A check was lost or added silently.');
}
process.exit(failed || errors.length || (!skipped && results.length !== EXPECTED) ? 1 : 0);
