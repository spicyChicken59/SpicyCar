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
// --only <text> runs just the steps whose label contains it. For mutation
// runs: reverting one fix and proving the one step that pins it goes red is a
// three-minute suite per mutant otherwise, and the tally backstop below is
// waived for a partial run since a partial run cannot add up to the whole.
const ONLY = argv.includes('--only') ? argv[argv.indexOf('--only') + 1] : null;
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
  if (ONLY && !label.includes(ONLY)) return;
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

// How the page spells an age, taken FROM the page rather than retyped. There
// are already two implementations of these four lines — Tracking.py's
// days_ago() and index.html's daysAgo — and the Python suite pins them to each
// other by running this same extracted function through node. A third copy
// typed here would be the only one nothing holds to the others, and it is the
// one that decides whether this file's checks pass.
const daysAgo = (() => {
  const m = readFileSync(join(ROOT, 'index.html'), 'utf8')
    .match(/const daysAgo = (\(n\) => [^;]+);/);
  if (!m) throw new Error('the page no longer defines daysAgo — the age checks have lost their subject');
  return (0, eval)(m[1]);
})();
// …and the schedule half, the same way. The check for an overdue model used
// to assume the `cadence > 1` wording, so a shopped DAILY model one day
// behind — which fetch_overdue calls overdue by its own max(1, c) — rendered
// correctly and turned this suite red. Reading the page's own function means
// the expectation follows whatever the page says, and the Python suite is
// what holds that to the report's wording.
const schedulePhrase = (() => {
  const m = readFileSync(join(ROOT, 'index.html'), 'utf8')
    .match(/const schedulePhrase = (\([^)]*\) => \{[\s\S]*?\n  \});/);
  if (!m) throw new Error('the page no longer defines schedulePhrase — the age checks have lost their subject');
  return (0, eval)(m[1]);
})();
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
       'three chip groups', 'the footer wears the watermark, not the cover lockup');
  await open('');
  ok('the watchlist opens', (await page.textContent('#h1')) === 'The watchlist', await page.textContent('#h1'));
  // DESIGN_SYSTEM.md §9 fixes the footer's form for every page: the mono mark
  // at 20px beside the wordmark in the display face. The horizontal lockup is
  // the cover asset, and this footer wore it for its first month.
  const foot = await page.locator('footer.sc-foot').evaluate((f) => ({
    mark: !!f.querySelector('.sc-watermark img.sc-mark'), name: (f.querySelector('.sc-watermark .sc-watermark__name') || {}).textContent || '',
    lockup: f.querySelectorAll('.sc-lockup').length,
    h: (f.querySelector('.sc-watermark img.sc-mark') || { getBoundingClientRect: () => ({ height: 0 }) }).getBoundingClientRect().height }));
  ok('the footer wears the watermark, not the cover lockup',
     foot.mark && foot.name === 'SpicyChicken' && foot.lockup === 0 && Math.round(foot.h) === 20,
     `mark ${foot.mark} at ${Math.round(foot.h)}px · name "${foot.name}" · ${foot.lockup} lockup(s)`);
  ok('four tiles', (await page.locator('#kpis .sc-tile').count()) === 4);
  // Two of these six are claims about a watchlist with more than one model on
  // it — the index draws a row EACH, and the model chips are a group only where
  // there is something to tell apart (buildFilters() hides #f-model-field below
  // two models) — and two are claims about a watchlist with a car on it. The
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
// The model these steps use. Discovered, never named: five blocks below opened
// '?brand=bmw&m=i5' literally, so retiring the i5 — the thing this whole tool
// exists to help decide — would have reported the dashboard as BROKEN rather
// than as a changed watchlist. Every one of those blocks already carries its
// own guard for a thin sheet; they just needed a subject that follows the
// config instead of a URL written down in a test file.
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
// sheet gives at least three trims — and buildFilters() renders no trim chip
// at all below two, which is how `#f-trim button` .nth(1) used to end the
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
// The other crash site, and the same shape: buildFilters() hides
// #f-model-field below two models, so on a single-model watchlist the chip is in the
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
  plan('a half-dead link still opens', 'and names what went missing', 'and a dead link keeps the notice furniture');
  if (!carried) return skipRest('no model on the watchlist holds a car today');
  const ghost = `${carried.bk}-not-a-car`;
  await open(`?models=${carried.slug},${ghost}`);
  ok('a half-dead link still opens', (await page.locator('#overview-table tbody tr').count()) === 1);
  ok('and names what went missing', new RegExp(ghost).test(await page.textContent('#notice')));
  // A dead link is the one real failure the notice slot reports, and it keeps
  // the stop-and-read furniture — the empty selections below it do not.
  ok('and a dead link keeps the notice furniture', (await page.locator('#notice .sc-notice').count()) === 1,
     `${await page.locator('#notice .sc-notice').count()} .sc-notice in #notice`);
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
// The ghost URL carries TWO checks like every other one, so its absence has to
// skip two — the tally below is exact only if every branch declares the same
// number of checks whichever way it goes. Skipping one where the other branch
// plans two is how a data-dependent total creeps in, and a total that moves
// with the market is a total no constant can be compared against.
const GHOST = ['a tracked brand plus a prototype-key model lands on the watchlist',
               'and says the link missed and stops re-sharing itself'];
if (ghost) protoUrls.push(`?brand=${encodeURIComponent(realBrand)}&m=${ghost}`);
else for (const n of GHOST) skip(n,
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

// --- the compare card counts its dated cars ----------------------------------
// The list sentence prints a typical-days figure only over twelve dated cars,
// truncated like the builder, with its denominator, and split where dealer
// stock sits beside used cars; the compare card's row for the same cars read
// "18d" above a sentence reading "17d" (a .5 median, rounded here, truncated
// there) and "9d" for a column with seven dated cars of twenty-two. The row
// now takes the sentence's own figures. Recomputed here from data.json; then
// a served sheet strips one column down to five dated cars, so the floor has
// something to refuse on a day when every model carries twelve.
await step('the compare card counts its dated cars', async () => {
  plan('a column prints a typical days figure only over twelve dated cars, truncated, with its denominator',
       'and a column of dealer stock beside used cars gives each market its own figure',
       'and a column served with five dated cars prints none');
  const STOCK = 100;
  const daysOf = (m) => {
    const rows = m.listings || [];
    const dl = rows.map((x) => x.days_listed).filter((v) => v != null);
    const med = (a) => { const b = a.slice().sort((p, q) => p - q), k = b.length >> 1; return Math.trunc(b.length % 2 ? b[k] : (b[k - 1] + b[k]) / 2); };
    const stock = rows.filter((x) => x.days_listed != null && x.miles != null && x.miles < STOCK).map((x) => x.days_listed);
    const used = rows.filter((x) => x.days_listed != null && x.miles != null && x.miles >= STOCK).map((x) => x.days_listed);
    const split = stock.length >= 12 && used.length >= 12
      // …and the dated cars in neither market, which the cell names because
      // it has just named the total they are counted in.
      ? { stock: { n: stock.length, days: med(stock) }, used: { n: used.length, days: med(used) },
          none: dl.length - stock.length - used.length } : null;
    const figure = dl.length >= 12 ? `${med(dl)}d` : '—';
    const note = !dl.length ? 'no listing dates' : dl.length < 12 ? `${dl.length} of ${rows.length} dated — too few for a median`
      : `${dl.length} of ${rows.length} dated` + (split ? ` · ${split.stock.n} dealer stock at ${split.stock.days}d, ${split.used.n} used at ${split.used.days}d`
        + (split.none ? `, ${split.none} with no mileage` : '') : '');
    return { figure, note, dated: dl.length, n: rows.length, split };
  };
  const models = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] })).map((o) => ({ ...o, days: daysOf(o.m) }));
  const readRow = () => page.evaluate(() => {
    const tbl = document.getElementById('compare-table');
    const heads = [...tbl.querySelectorAll('thead th')].slice(1).map((th) => th.getAttribute('aria-label') || th.textContent.trim());
    const row = [...tbl.querySelectorAll('tbody tr')].find((tr) => /typical days on market/i.test(tr.querySelector('th').textContent));
    if (!row) return null;
    return [...row.querySelectorAll('td')].map((td, i) => ({ label: heads[i], figure: ((td.querySelector('.sc-figure') || {}).textContent || '').trim(),
      note: [...td.querySelectorAll('.sc-note')].map((n) => n.textContent.trim()).join(' ') }));
  });
  const cellFor = (cells, w) => (cells || []).find((c) => c.label.split(',')[0].trim() === w.label);
  const thin = models.find((o) => o.days.dated && o.days.dated < 12);
  const full = models.filter((o) => o.days.dated >= 12).sort((a, b) => b.days.dated - a.days.dated);
  const split = models.find((o) => o.days.split);
  if (full.length < 1) return skipRest('no watched model carries twelve dated cars today');
  const pair = [thin || full[1] || full[0], full[0]].filter((o, i, a) => a.indexOf(o) === i);
  if (pair.length < 2) return skipRest('only one model on the watchlist has any listing dates');
  await open(`?models=${pair.map((o) => o.w.slug).join(',')}`);
  const cells = await readRow();
  const wrong = pair.map((o) => ({ o, c: cellFor(cells, o.w) })).filter(({ o, c }) => !c || c.figure !== o.days.figure || c.note !== o.days.note);
  ok('a column prints a typical days figure only over twelve dated cars, truncated, with its denominator', !!cells && wrong.length === 0,
     wrong.length ? wrong.map(({ o, c }) => `${o.w.label}: cell ${c ? `"${c.figure}" / "${c.note}"` : 'missing'} · sheet "${o.days.figure}" / "${o.days.note}"`).join(' | ')
                  : pair.map((o) => `${o.w.label}: ${o.days.figure} (${o.days.note})`).join(' · '));
  if (!split) skip('and a column of dealer stock beside used cars gives each market its own figure', 'no watched model has twelve dated cars on each side of the stock line');
  else {
    const mate = full.find((o) => o !== split) || thin;
    await open(`?models=${[split, mate].filter(Boolean).map((o) => o.w.slug).join(',')}`);
    const c = cellFor(await readRow(), split.w);
    ok('and a column of dealer stock beside used cars gives each market its own figure', !!c && c.figure === split.days.figure && c.note === split.days.note,
       c ? `${split.w.label}: "${c.figure}" / "${c.note}"` : `${split.w.label}: no cell`);
  }
  // Served: the fullest column keeps five listing dates and loses the rest.
  const victim = full[0], mate = pair.find((o) => o !== victim) || full[1];
  if (!mate) skip('and a column served with five dated cars prints none', 'no second model to compare against');
  else {
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      let kept = 0;
      for (const x of sheet.brands[victim.w.bk].models[victim.w.mk].listings) if (x.days_listed != null) { if (kept < 5) kept++; else x.days_listed = null; }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(`?models=${[victim, mate].map((o) => o.w.slug).join(',')}`);
      const c = cellFor(await readRow(), victim.w);
      ok('and a column served with five dated cars prints none', !!c && c.figure === '—' && c.note === `5 of ${victim.days.n} dated — too few for a median`,
         c ? `${victim.w.label} with five dates: "${c.figure}" / "${c.note}"` : `${victim.w.label}: no cell`);
    } finally { await ctx.unroute('**/data.json'); }
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
plan('the count measures against the whole watchlist',
     'and it still reads it once the watchlist passes a thousand cars',
     "a pressed chip's count is still readable");
const q = '?models=bmw-i5,kia-ev9';
if (!inSheet(q)) return skipRest(`${q} names a car the watchlist no longer holds`);
await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
await open(q);
const count = await page.textContent('#filter-count');
// The total is written with num(), which is toLocaleString — so it grows a
// comma at a thousand cars and `\d+` stops matching. The watchlist holds 444
// today and about 1,148 once all thirty-six models carry cars, so this check
// was a dated time bomb: CI would go red on an unreviewed bot commit, for a
// separator. Four other reads of this same element in this file already allow
// it; this was the one that did not. Read the number and compare it to the
// sheet, so the check is about the VALUE and not about the shape of it.
const totalCars = Object.values(SHEET.brands || {})
  .flatMap((b) => Object.values(b.models || {}))
  .reduce((n, m) => n + (m.listings || []).length, 0);
// ONE parser, used by both checks below. The first draft gave each its own
// copy, and mutating the first left the second passing — two copies of a rule
// is two places for it to be wrong, and the mutant found it immediately.
const totalIn = (txt) => Number(((txt.match(/ of ([\d,]+) cars/) || [])[1] || '').replace(/,/g, ''));
const seenTotal = totalIn(count);
ok('the count measures against the whole watchlist',
   seenTotal === totalCars && /\+/.test(count),
   `${count} · sheet holds ${totalCars}`);
// Served, because 444 cars cannot exercise the separator and the projection
// says the full watchlist is about 1,148. Every listing of one model is
// cloned until the sheet crosses a thousand, with fresh VINs so the page's
// own de-duplication does not undo it.
await ctx.route('**/data.json', async (route) => {
  const r = await route.fetch(); const sheet = JSON.parse(await r.text());
  const m = Object.values(sheet.brands).flatMap((b) => Object.values(b.models))
    .sort((a, b2) => (b2.listings || []).length - (a.listings || []).length)[0];
  const seed = [...(m.listings || [])];
  let i = 0;
  while (Object.values(sheet.brands).flatMap((b) => Object.values(b.models))
    .reduce((n, mm) => n + (mm.listings || []).length, 0) <= 1100)
    m.listings.push({ ...seed[i % seed.length], vin: `CLONE${String(i++).padStart(12, '0')}` });
  return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
});
let big;
try { await open(q); big = await page.textContent('#filter-count'); }
finally { await ctx.unroute('**/data.json'); }
const bigTotal = totalIn(big);
ok('and it still reads it once the watchlist passes a thousand cars',
   bigTotal > 1000 && /,/.test(big),
   `"${big.trim()}" · parsed ${bigTotal}`);

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
// Two themes per place, so a missing model page is two skips, not one — see
// GHOST above for why the count has to be the same either way.
if (subject) chipStates.push(['a model page', `?brand=${subject.bk}&m=${subject.mk}`]);
else for (const theme of ['dark', 'light'])
  skip(`every chip count is readable on a model page, in ${theme}`,
       'no watched model has two trims today');
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
// Asserted over the DATA, because every note reaches both a dek and a chip
// title, and a rule enforced on one URL is the gap that let
// rather than named here — naming bmw-i5-edrive40 would test the config, and
// would pass vacuously the day that trim is renamed or the i5 drops to one
// trim (buildFilters() renders trim chips only when tIds.length > 1).
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
// --- the one-sentence purpose line counts what it names ----------------------
// `rows` in overviewTitles() is the MODEL list — the meta row one line below
// counts the same array as "N models" — and the sentence called them cars.
// "7 electric cars" sat over a sheet holding 502 of them for as long as the
// watchlist was small enough for nobody to look; one EV per brand made it
// "35 electric cars" over 444. Pinned against both populations, because a
// check that only knew the model count would pass on either noun.
//
// The adjective went with the watchlist: the sentence said "electric models"
// about whatever the buyer had chosen, and the buyer chooses the cars. What
// it counts is what this check is about, so the noun is pinned and the word
// in front of it is not.
await step('the watchlist dek counts what it names', async () => {
plan('the watchlist dek counts models, not cars',
     'and the meta row beside it counts the same models');
const sheet = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const models = Object.values(sheet.brands || {}).flatMap((b) => Object.keys(b.models || {})).length;
const cars = Object.values(sheet.brands || {}).flatMap((b) => Object.values(b.models || {}))
  .reduce((n, m) => n + (m.listings || []).length, 0);
if (!models || models === cars) return skipRest(`the sheet holds ${models} models and ${cars} cars — nothing to tell apart`);
await open('');
const dek = (await page.textContent('#dek')).replace(/\s+/g, ' ').trim();
ok('the watchlist dek counts models, not cars',
   new RegExp(`^${models} models\\b`).test(dek) && !/\\bcars\\b/.test(dek),
   `${models} models, ${cars} cars on the sheet — the page says "${dek.slice(0, 90)}"`);
const meta = (await page.textContent('#meta') || '').replace(/\s+/g, ' ');
const shop = Object.values(sheet.brands || {}).flatMap((b) => Object.values(b.models || {}))
  .filter((m) => m.shopping).length;
ok('and the meta row beside it counts the same models',
   meta.includes(`${shop} shopping · ${models - shop} comparison`),
   `"${meta.slice(0, 120)}" · ${shop} of ${models}`);
});

// --- naming nothing is a state, not an absence -------------------------------
// `buyer.shopping` names the models the decision card lays out. Empty is what
// a person browsing thirty-six models has before they have chosen anything —
// and the front page answered it by removing its most important block without
// a word. It is a different state from "the reader's own chips left nothing
// shopped in view", which is deliberate and stays silent, so both are served
// here and the checks are what tell them apart.
await step('naming nothing is a state, not an absence', async () => {
plan('with no models named the decision card says so instead of vanishing',
     'and it lays out no cars, because there are none to lay out',
     'while the reader\'s own chips emptying it stays silent, as before',
     'and the meta row never counts the same models twice');
const serveUnshopped = async () => {
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    sheet.buyer.shopping = [];
    for (const b of Object.values(sheet.brands || {}))
      for (const m of Object.values(b.models || {})) {
        m.shopping = false;
        for (const t of Object.values(m.trims || {})) t.shopping = false;
        for (const x of (m.listings || [])) delete x.shopping;
      }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
};
const readHero = () => page.evaluate(() => ({
  hidden: document.getElementById('hero-card').hidden,
  hint: (document.getElementById('hero-hint').textContent || '').replace(/\s+/g, ' ').trim(),
  cars: document.getElementById('hero-cars').children.length,
  gapHidden: document.getElementById('hero-gap').hidden,
  meta: (document.getElementById('meta').innerText || '').replace(/\s+/g, ' ').trim(),
}));
await serveUnshopped();
let none;
try { await open(''); none = await readHero(); } finally { await ctx.unroute('**/data.json'); }
ok('with no models named the decision card says so instead of vanishing',
   !none.hidden && /No models are named/.test(none.hint) && /model chips|buyer\.shopping/.test(none.hint),
   `hidden=${none.hidden} · "${none.hint.slice(0, 150)}"`);
ok('and it lays out no cars, because there are none to lay out',
   none.cars === 0 && none.gapHidden === true,
   `${none.cars} tiles, gap hidden=${none.gapHidden}`);

// The control: models ARE named, and the reader presses a chip for one that is
// not among them. Same empty hero, different cause, and the page has always
// been right to stay quiet — the reader knows what they just pressed.
const shopped = new Set((SHEET.buyer || {}).shopping || []);
const other = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] }))
  .find((o) => !Object.keys(o.m.trims || {}).some((t) => shopped.has(t)));
if (!shopped.size || !other) skip('while the reader\'s own chips emptying it stays silent, as before',
                                  'this sheet names no shopped models, or every model is shopped');
else {
  await open(`?models=${other.w.bk}-${other.w.mk}`);
  const chip = await readHero();
  ok('while the reader\'s own chips emptying it stays silent, as before',
     chip.hidden === true && !/No models are named/.test(chip.hint),
     `pressing ${other.w.bk}/${other.w.mk}: hidden=${chip.hidden} · "${chip.hint.slice(0, 90)}"`);
}

// "1 of 36 models · 1 models" — the count twice, and un-pluralised. Always
// reachable; the ordinary case now that thirty-four of the thirty-six models
// are not shopped, so almost any chip a reader presses lands on it.
// The state that PRODUCES it is a chip on a model nobody is shopping — with
// nShop 0 the last clause falls through to its own count beside the
// selection's. A first version of this check pressed WATCHED[0], which is a
// shopped model, so it never visited the bug at all and stayed green with the
// duplicate restored: a check that cannot fail, caught by mutating the fix.
const metaQueries = ['', `?models=${WATCHED[0].bk}-${WATCHED[0].mk}`];
if (other) metaQueries.push(`?models=${other.w.bk}-${other.w.mk}`);
if (WATCHED[1]) metaQueries.push(`?models=${WATCHED[0].bk}-${WATCHED[0].mk},${WATCHED[1].bk}-${WATCHED[1].mk}`);
const metas = [none.meta];
for (const q of metaQueries) { await open(q); metas.push((await readHero()).meta); }
const dup = metas.find((m) => /\b1 models\b/.test(m) || /of \d+ models · \d+ models/.test(m));
ok('and the meta row never counts the same models twice', !dup,
   dup ? `"${dup}"` : metas.map((m) => '"' + m.replace(/^Data through [^·]+· /, '') + '"').join(' · '));
});

// --- a query that ran and found nothing is not a query that never ran --------
// A query that comes back empty writes no row, so `as_of` stays null and the
// model meta read "Not fetched yet · first run <ten days out>" for ever, since
// next_due always rolls forward. Thirty of the thirty-six models have never
// run and their model strings are unverified guesses, so the state this is
// about — a string that names something the API does not know, billing a call
// every cadence — is the likeliest thing to happen next on this watchlist.
await step('a query that found nothing is not a query that never ran', async () => {
plan('a model no query has reached says it has not been fetched',
     'and one whose query ran and found nothing says THAT instead');
const site = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
const victim = (() => {
  for (const [bk, b] of Object.entries(site.brands || {}))
    for (const [mk, m] of Object.entries(b.models || {}))
      if (!(m.listings || []).length && !m.as_of && !m.last_asked) return { bk, mk, m };
  return null;
})();
if (!victim) return skipRest('every model on this sheet has been fetched');
const meta = () => page.evaluate(() => (document.getElementById('meta').innerText || '').replace(/\s+/g, ' ').trim());
await open(`?brand=${victim.bk}&m=${victim.mk}`);
const never = await meta();
ok('a model no query has reached says it has not been fetched',
   /Not fetched yet/.test(never) && /first run/.test(never), `"${never}"`);
// Served: the same model, with the day its query ASKED. Same empty listings,
// same null as_of — one field different, and the sentence has to change.
await ctx.route('**/data.json', async (route) => {
  const r = await route.fetch(); const sheet = JSON.parse(await r.text());
  sheet.brands[victim.bk].models[victim.mk].last_asked = '2026-09-01';
  return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
});
let asked;
try { await open(`?brand=${victim.bk}&m=${victim.mk}`); asked = await meta(); }
finally { await ctx.unroute('**/data.json'); }
ok('and one whose query ran and found nothing says THAT instead',
   !/Not fetched yet/.test(asked) && /nothing found/i.test(asked) && /Sep 1, 2026|September 1, 2026/.test(asked)
     // …and no other clause of the same line may still say it has not run.
     // Both were true at once on the first pass: "Asked Tue, September 1,
     // 2026, nothing found · Fetched every 4 days · first fetch pending for
     // this view".
     && !/first fetch pending/.test(asked),
   `"${asked}"`);
});

// --- the brand row is navigation, not the page ------------------------------
// It was six pills and is thirty-five. At 390x844 it stood 382px tall — 45% of
// the first screen — and pushed "The decision", the card this product exists
// for, to y=782 of 844: its heading on screen and not one number in it. The
// oracle is what a reader can SEE, not the row's height: a height is a fact
// about the market (more brands, taller row) and the decision card's first
// figure being on the first screen is the claim.
await step('the brand row is navigation, not the page', async () => {
plan('a phone reaches the leading card without scrolling',
     'and the row costs a constant, whatever the watchlist holds',
     'and every brand is still reachable, pressed one scrolled into view');
await page.setViewportSize({ width: 390, height: 844 });
await open('');
// The subject used to be the DECISION card, because it led the page. It does
// not any more — the Signal Matrix does, on purpose — so naming a card here
// would assert a layout decision rather than the rule. The rule is that
// NAVIGATION does not eat the first screen, and the oracle is whichever
// content card comes first: its first figure has to be on it.
const seen = await page.evaluate(() => {
  const t = document.getElementById('tabs-brand');
  if (!t || t.hidden) return null;
  // Not the COVER. It sits above everything and is always on the first
  // screen, so leading with it makes this unfailable — the first draft did
  // exactly that and reported the dek as the figure it had found. The
  // subject is the first card BELOW the cover, which is what the brand row
  // pushes down.
  const cover = document.querySelector('.sc-cover, #car-heading');
  const coverBottom = cover ? cover.getBoundingClientRect().bottom : 0;
  const cards = [...document.querySelectorAll('main section, main > .sc-card, #signal-card, #hero-card')]
    .filter((c) => !c.hidden && c.getBoundingClientRect().height > 0
                   && !c.contains(cover) && c !== cover
                   && c.getBoundingClientRect().top >= coverBottom - 1)
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  const lead = cards[0];
  if (!lead) return null;
  const fig = [...lead.querySelectorAll('*')]
    .find((n) => !n.children.length && /[\d]/.test(n.textContent || '')
                 && n.getBoundingClientRect().height > 0);
  return { tabs: Math.round(t.getBoundingClientRect().height),
           tabsN: t.children.length,
           lead: lead.id || lead.className.split(' ')[0],
           firstFigure: fig ? Math.round(fig.getBoundingClientRect().bottom) : null,
           figText: fig ? (fig.textContent || '').trim().slice(0, 30) : null,
           vh: innerHeight };
});
if (!seen) return skipRest('this sheet has no brand row or no content card');
ok('a phone reaches the leading card without scrolling',
   seen.firstFigure !== null && seen.firstFigure <= seen.vh,
   `${seen.tabsN} brand tabs in ${seen.tabs}px; #${seen.lead} leads and its first figure `
   + `(${seen.figText}) ends at y=${seen.firstFigure} of ${seen.vh}`);
// The cap used to be three wrapped rows, which fixed the symptom and left the
// height a function of the market — nineteen brands filled all three, and the
// twentieth would have started scrolling a row nobody can see is scrollable.
// One line, scrolled sideways, is a constant: this asserts the cost does not
// grow with the watchlist, which is the thing that went wrong at six brands
// becoming thirty-five.
ok('and the row costs a constant, whatever the watchlist holds',
   seen.tabs <= 56,
   `${seen.tabsN} brand tabs stand ${seen.tabs}px tall; one row is about 39`);
// …and the cap must not hide a brand from the reader who asked for it.
// THREE brands, and the middle one is the load-bearing case: at the end of
// the row every arithmetic lands on the same place, because the scroll is
// clamped to its maximum either way. The first draft checked only the last
// brand and stayed green with the buggy offsetTop version restored — the
// version that really did leave the pressed tab out of view, which is how
// this scroll was found in the first place.
const brands = Object.keys(SHEET.brands || {});
const probe = [...new Set([brands[Math.floor(brands.length / 2)],
                           brands[brands.length - 1], brands[0]])].filter(Boolean);
const out = [];
for (const bk of probe) {
  const mk = Object.keys(SHEET.brands[bk].models || {})[0];
  await open(`?brand=${bk}&m=${mk}`);
  out.push(await page.evaluate(() => {
    const t = document.getElementById('tabs-brand');
    const on = t.querySelector('[aria-pressed="true"]');
    if (!on) return null;
    const hb = t.getBoundingClientRect(), ob = on.getBoundingClientRect();
    return { label: on.textContent, inside: ob.top >= hb.top - 2 && ob.bottom <= hb.bottom + 2,
             scrolls: t.scrollHeight > t.clientHeight + 1, top: Math.round(t.scrollTop) };
  }));
}
ok('and every brand is still reachable, pressed one scrolled into view',
   out.length === probe.length && out.every((p) => p && p.inside),
   out.map((p, i) => p ? `"${p.label}" at ${p.top}px ${p.inside ? 'in view' : 'OUT OF VIEW'}`
                       : `${probe[i]}: no pressed tab`).join(' · '));
await page.setViewportSize({ width: 1280, height: 1000 });
});

// --- five chart colours cannot carry thirty-four brands ----------------------
// brandTone() hands a categorical slot to the first five brands in watchlist
// order and `chart-other` to the rest — which is not what `other` is for: the
// sheet folds a SIXTH category into it, singular, not twenty-nine separate
// ones. Measured on a full-size record, 29 of 36 lines came out byte-identical
// under a hint that said "colored by brand". Nothing in this file read a
// stroke, so the whole channel was unasserted.
await step('the chart draws out what the reader is comparing', async () => {
plan('the lines a reader is not comparing share one context tone',
     'and the ones they are get a slot each, and their name at the end',
     'the legend lists what is drawn and nothing else',
     'and no sentence on the page says colour is the brand',
     'and pressing two models nobody is shopping draws THOSE out');
// Per series, not aggregated: a model with a single day draws as circles and
// no path at all, so counting paths and comparing the total to the number of
// series is off by one and says nothing about which line wears which tone.
const styles = () => page.evaluate(() => {
  // The LINE's stroke, and only that: the end-point marker is a filled dot
  // with a white halo stroke, so reading every element's stroke reports
  // rgb(255,255,255) on every series and a single-day series reports nothing
  // else at all. `.sc-chart__series` is the path.
  const tones = {};
  for (const n of document.querySelectorAll('#chart path.sc-chart__series[data-sid]'))
    (tones[n.getAttribute('data-sid')] = tones[n.getAttribute('data-sid')] || new Set())
      .add(getComputedStyle(n).stroke);
  const by = {};
  for (const n of document.querySelectorAll('#chart .sc-chart__series')) {
    const cs = getComputedStyle(n); const k = `${cs.stroke}|${cs.strokeDasharray}`;
    by[k] = (by[k] || 0) + 1;
  }
  return { by, tones: Object.fromEntries(Object.entries(tones).map(([k, v]) => [k, [...v]])),
           n: document.querySelectorAll('#chart .sc-chart__series').length,
           context: getComputedStyle(document.documentElement).getPropertyValue('--sc-chart-context').trim(),
           labels: [...document.querySelectorAll('#chart text')].map((t) => t.textContent)
             .filter((t) => /[A-Za-z]/.test(t) && !/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/.test(t)),
           chips: [...document.querySelectorAll('#legend .sc-legend__chip')].map((c) => c.textContent.trim()),
           drawn: [...new Set([...document.querySelectorAll('#chart [data-sid]')].map((n) => n.getAttribute('data-sid')))],
           hint: (document.getElementById('chart-hint') || {}).textContent || '' };
});
await open('');
const o = await styles();
if (!o.n) return skipRest('no model on this sheet has price history to draw');
// The shopped models are BMW's, so every non-BMW line must share one tone.
const shoppedBrands = new Set(Object.entries(SHEET.brands)
  .filter(([, b]) => Object.values(b.models || {}).some((m) => m.shopping)).map(([bk]) => bk));
const otherDrawn = o.drawn.filter((id) => !shoppedBrands.has(id.split('/')[0]));
// Each series against the context colour the sheet itself defines, so a
// palette change cannot quietly satisfy this.
const ctx = o.context.toLowerCase();
const asRgb = (hex) => { const h = hex.replace('#', '');
  return `rgb(${parseInt(h.slice(0, 2), 16)}, ${parseInt(h.slice(2, 4), 16)}, ${parseInt(h.slice(4, 6), 16)})`; };
const want = ctx.startsWith('#') ? asRgb(ctx) : ctx;
// A series with no path drew a single point and has no line tone to judge.
const lined = (id) => (o.tones[id] || []).length > 0;
const offFocus = otherDrawn.filter((id) => lined(id) && o.tones[id].some((t) => t !== want));
const onFocus = o.drawn.filter((id) => shoppedBrands.has(id.split('/')[0])
  && lined(id) && o.tones[id].some((t) => t === want));
ok('the lines a reader is not comparing share one context tone',
   offFocus.length === 0 && onFocus.length === 0,
   `context ${want} · ${otherDrawn.filter(lined).length} lined series outside the shopped brands`
   + (offFocus.length ? ` · NOT context: ${offFocus.join(', ')}` : '')
   + (onFocus.length ? ` · shopped but grey: ${onFocus.join(', ')}` : '')
   + ` · ${JSON.stringify(o.tones)}`);
ok('and the ones they are get a slot each, and their name at the end',
   [...shoppedBrands].every((bk) => o.labels.some((l) => o.drawn.some((id) => id.startsWith(bk + '/'))
     && l.includes(SHEET.brands[bk].label || bk))),
   `end labels ${JSON.stringify(o.labels)} · shopped ${[...shoppedBrands].join(',')}`);
ok('the legend lists what is drawn and nothing else',
   o.chips.length === o.drawn.length,
   `${o.chips.length} chips over ${o.drawn.length} drawn series (was 36 chips over 7)`);
const prose = [o.hint, await page.textContent('#map-hint').catch(() => '')].join(' ');
ok('and no sentence on the page says colour is the brand',
   !/colou?red by brand|colou?r is the brand/i.test(prose)
     // …and the sentence that replaced it counts BRANDS, which is the
     // population the hue channel would have to carry. It said "36 models"
     // beside the right number, which is this repo's oldest defect shape.
     && new RegExp(`cannot carry ${Object.keys(SHEET.brands || {}).length} brands`).test(prose),
   `"${prose.replace(/\s+/g, ' ').slice(0, 200)}"`);

// A SECOND state, because with nothing pressed the focus set IS the shopped
// brands and `labeled: focus.has(bk)` cannot be told from `labeled:
// m.shopping` — which is a mutant that survived on the first pass. Pressing
// two models nobody is shopping is the state that separates them.
const unshopped = Object.entries(SHEET.brands)
  .filter(([, b]) => Object.values(b.models || {}).every((m) => !m.shopping))
  .flatMap(([bk, b]) => Object.entries(b.models || {}).map(([mk, m]) => ({ bk, mk, m })))
  .filter(({ m }) => (m.daily || []).length >= 2).slice(0, 2);
if (unshopped.length < 2) skip('and pressing two models nobody is shopping draws THOSE out',
                               'fewer than two unshopped models have history to draw');
else {
  await open('?models=' + unshopped.map(({ bk, mk }) => `${bk}-${mk}`).join(','));
  const p2 = await styles();
  const wanted = unshopped.map(({ bk, mk }) => `${bk}/${mk}`);
  const named = wanted.every(({ }, i) => p2.labels.some((l) => l.includes(unshopped[i].m.label)));
  const coloured = wanted.every((id) => (p2.tones[id] || []).every((t) => t !== want));
  ok('and pressing two models nobody is shopping draws THOSE out', named && coloured,
     `${wanted.join(', ')} · end labels ${JSON.stringify(p2.labels)} · tones ${JSON.stringify(p2.tones)}`);
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
     'and an empty selection is a sentence, not a red box',
     'and its way out drops the trim and brings the sections back',
     'a stale link onto an empty trim is not a dead end either',
     'the empty-filters notice counts what its own link restores');
const DATA = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
// The page's own membership rule: a car two queries both returned is one row
// with the cheaper copy's trim_id and the other under `also`, and it is in both
// trims. Counting only trim_id here would pick a "victim" trim that the page
// still shows cars for, and the emptying below would leave two behind.
const inTrimOf = (x, id) => x.trim_id === id || ((x.also || []).some((a) => a.trim_id === id));
const perTrim = (m) => (m.listings || []).reduce((c, x) => {
  for (const id of new Set([x.trim_id, ...(x.also || []).map((a) => a.trim_id)])) c[id] = (c[id] || 0) + 1;
  return c;
}, {});
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
        mm.listings = (mm.listings || []).filter((x) => !inTrimOf(x, victim));
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
                      'and an empty selection is a sentence, not a red box',
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
  // The reader's own selection returning nothing is not a failure: §6 says one
  // muted sentence, no graphic, no apology — and no 2px danger border, which
  // is the dead link's furniture and was wrapping this too.
  const furniture = await page.locator('#notice').evaluate((n) => ({ empty: n.querySelectorAll('.sc-empty').length, notice: n.querySelectorAll('.sc-notice').length }));
  ok('and an empty selection is a sentence, not a red box', furniture.empty === 1 && furniture.notice === 0,
     `${furniture.empty} .sc-empty, ${furniture.notice} .sc-notice in #notice`);
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
     'and can still reach every filter in it',
     'and the short-viewport cap is the tighter of the two',
     'and the desktop panel is capped without being clipped');
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
  skip('and the short-viewport cap is the tighter of the two', 'data.json names no model to open');
  skip('and the desktop panel is capped without being clipped', 'data.json names no model to open');
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
  // This used to read "and the cap lifts again on a tall screen", asserting
  // max-height:none at 390x844 — which was true while the panel fitted there.
  // One EV per brand put 35 model chips in it and the panel measured 1848px on
  // that 844px screen: a sticky element taller than its viewport, whose lower
  // two-thirds no scroll offset can reach. The cap is unconditional now, so
  // the two things still worth guarding are that the media query is not dead
  // weight (the short viewport must be capped TIGHTER, since it can spare
  // less) and that the desktop the sticky panel was designed for is capped
  // without being clipped — which is what the old assertion was really
  // protecting, and it is now checked where a desktop actually is.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  const tall = await page.evaluate(() => parseFloat(getComputedStyle(document.getElementById('filters-card')).maxHeight));
  ok('and the short-viewport cap is the tighter of the two',
     tall > 0 && land.capPx > 0 && land.capPx / land.vh < tall / 844,
     `${Math.round(land.capPx)}px of 390 in landscape, ${Math.round(tall)}px of 844 in portrait`);
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.waitForTimeout(250);
  const desk = await page.evaluate(() => {
    const c = document.getElementById('filters-card');
    return { clipped: c.scrollHeight - c.clientHeight, h: c.scrollHeight,
             cap: parseFloat(getComputedStyle(c).maxHeight) };
  });
  ok('and the desktop panel is capped without being clipped', desk.clipped <= 1,
     `panel ${desk.h}px under a ${Math.round(desk.cap)}px cap at 1280x1000, ${desk.clipped}px clipped`);
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
      // Show all first. The table renders thirty rows, and at the LONGEST term
      // the promo cars are exactly the ones that cannot use it — capped at 60,
      // they quote a higher payment than the uncapped cars around them and sort
      // straight out of the first page. On 2026-09-01 the i5 had enough
      // certified cars that one survived anyway; on 2026-09-04 it had seven and
      // none did, and this check read an empty string and called the dashboard
      // broken. The subject was there the whole time, thirty rows down.
      const all = page.locator('[data-fkey="more:list"]');
      if (await all.count() && await all.isVisible()) { await all.click(); await page.waitForTimeout(500); }
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
// Tax is the largest number this page has never shown — roughly six times the
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
    await open(carried.q);
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
    await open(carried.q);
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
  await open(carried.q);
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
  const fin = (SHEET.buyer || {}).finance || null;   // the sheet in hand — a relative fetch from about:blank (an --only run) has no base URL
  const capped = fin && (fin.promos || []).find((p) => p.active && p.max_term);
  const longest = fin && Math.max(...(fin.terms || [60]));
  if (!capped || !(longest > capped.max_term)) {
    return skipRest('no live promo caps the term below the longest one offered');
  }
  await open(carried.q);
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
  plan('a covered car is not quoted a negative balance', 'and the finance note says what was put down');
  const fin = (SHEET.buyer || {}).finance || null;   // the sheet in hand — a relative fetch from about:blank (an --only run) has no base URL
  if (!fin) return skipRest('this sheet has no finance block');
  await open(carried.q);
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
  // The sentence that claims to state the assumptions has to state this one:
  // a "$0/mo" cell under "assume 60 months at 6.9%" is a table contradicting
  // its own caption when the $400,000 that made it zero goes unsaid.
  const hintDown = (await page.textContent('#list-hint')) || '';
  ok('and the finance note says what was put down', /\$400,000 down/.test(hintDown),
     (hintDown.match(/Payments assume[^.]*\./) || ['no finance sentence in #list-hint'])[0]);
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
  const fin = (SHEET.buyer || {}).finance || null;   // the sheet in hand — a relative fetch from about:blank (an --only run) has no base URL
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
       'and so are the worth-the-ship picks',
       'and the two surfaces state one rule for the list');
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
  // The rule sentence, once: the report prints picks_rule() and the page
  // picksRule(), and the two drifted apart the day the interval clause was
  // added to one of them. Compared word for word after the page's trailing
  // "Asking prices shown." and the report's capitalisation are set aside.
  const norm = (t) => t.replace(/\s+/g, ' ').replace(/\.?\s*Asking prices shown\.?$/i, '').trim().replace(/^./, (c) => c.toLowerCase()).replace(/\.$/, '');
  const reportRule = (md.match(/\*\*Spicy picks\*\* — ([^\n]+)/) || [])[1] || (md.match(/^_(under [^\n]+?)\. Asking prices shown\._/m) || [])[1] || '';
  const pageRule = await page.getAttribute('#hero-hint', 'title');
  if (!reportRule || !pageRule) skip('and the two surfaces state one rule for the list', `${reportRule ? 'the page' : 'the report'} prints no rule sentence`);
  else ok('and the two surfaces state one rule for the list', norm(reportRule) === norm(pageRule),
          norm(reportRule) === norm(pageRule) ? `"${norm(pageRule).slice(0, 110)}…"` : `report: "${norm(reportRule)}" · page: "${norm(pageRule)}"`);
});

// --- the budget ------------------------------------------------------------
// The one filter that is a fact about the buyer rather than a way of reading
// the page, which is why it persists like the term and the down payment. It is
// also the only filter whose predicate is a DERIVED number — an out-the-door
// total or a monthly payment, not a field on the row — so a car the sheet
// cannot price for the chosen unit has no figure to compare and must not
// quietly pass. That is the failure this pins: a budget that lets an unpriced
// car through is a page claiming a car fits a budget it never measured.
//
// The totals are recomputed here from data.json, in the harness's own
// arithmetic, so a check cannot agree with a bug in otd().
await step('the budget', async () => {
  plan('a budget keeps only the cars that fit it, and all of them',
       'and the count line names it',
       'a car the sheet cannot price is not let through a budget',
       'the decision panel says which setting emptied a model',
       'and the budget is still there on the next visit');
  const f = (SHEET.buyer || {}).fees || null;
  const allIn = (x) => {
    if (x.price == null) return null;
    const ship = x.local ? 0 : (x.ship || 0);
    if (!f) return x.price + ship;
    return Math.round(x.price * (1 + (f.tax_rate || 0))
      + (f.doc_fee || 0) + (f.title || 0) + (f.registration || 0) + (f.ev_surcharge || 0) + ship);
  };
  const every = Object.values(SHEET.brands || {}).flatMap((b) =>
    Object.values((b || {}).models || {}).flatMap((m) => (m || {}).listings || []));
  const totals = every.map(allIn).filter((v) => v != null).sort((a, b) => a - b);
  if (totals.length < 8) return skipRest('the sheet holds too few priced cars to set a meaningful budget');
  await open('');
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  // The rows are checked on a MODEL page: the watchlist's list card is the
  // model index, not a car per row, and only a comparison pools listings there.
  const home = WATCHED.slice().sort((a, b) => b.cars - a.cars)[0];
  if (!home || home.cars < 8) return skipRest('no model on this watchlist holds enough cars to budget against');
  const held = ((SHEET.brands[home.bk] || {}).models[home.mk] || {}).listings || [];
  const mine = held.map(allIn).filter((v) => v != null).sort((a, b) => a - b);
  if (mine.length < 8) return skipRest(`${home.id} holds too few priced cars to budget against`);
  // A budget a quarter of the way up THIS model's market: enough cars in,
  // enough out, and both sides of the line are visible in one table.
  const budget = mine[Math.floor(mine.length / 4)];
  const want = new Set(held.filter((x) => allIn(x) != null && allIn(x) <= budget).map((x) => x.vin));
  await open(home.q);
  const setBudget = async (v, kind) => {
    await page.fill('#f-budget', String(v));
    await page.locator('#f-budget').press('Tab');
    if (kind) await page.selectOption('#f-budget-kind', kind);
    await page.waitForTimeout(400);
  };
  await setBudget(budget, 'otd');
  // The listings table is the page's own answer to "which cars are left".
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(400); }
  const shown = await page.locator('#list-table tbody .sc-media__code')
    .evaluateAll((ns) => ns.map((n) => n.textContent.trim()));
  // Both directions. A budget that lets an expensive car through is the
  // obvious failure; a budget that drops a car it should have kept is the
  // quiet one, and on a page whose whole job is "what can I buy" it is the
  // worse of the two.
  const over = shown.filter((v) => !want.has(v));
  const missing = [...want].filter((v) => !shown.includes(v));
  ok('a budget keeps only the cars that fit it, and all of them',
     shown.length > 0 && over.length === 0 && missing.length === 0,
     `budget ${budget} all in on ${home.id} · ${shown.length} rows shown, ${want.size} of ${mine.length} priced cars fit`
     + (over.length ? ` · over budget: ${over.slice(0, 3).join(', ')}` : '')
     + (missing.length ? ` · dropped: ${missing.slice(0, 3).join(', ')}` : ''));

  // #filter-count is the page's one status line: it exists to say what the
  // filters are doing, and a filter it does not name is one the reader cannot
  // see the effect of.
  const line = (await page.textContent('#filter-count')) || '';
  const pretty = String(budget).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  ok('and the count line names it', line.includes(pretty) && /all in|landed|\/mo/.test(line), line.slice(0, 160));

  // Every car in today's sheet has a price and an APR, so the branch that
  // matters most here — a car with no figure to compare against the budget —
  // has no subject in the live data. It is given one: data.json is served with
  // one car's price removed. That car renders normally with no budget set (a
  // dash where the price goes), and must not survive a budget it was never
  // measured against. "Nothing is said about a car we cannot price" is the
  // rule; letting it through is that rule broken by omission.
  const guinea = held.find((x) => allIn(x) != null && allIn(x) <= budget);
  if (!guinea) skip('a car the sheet cannot price is not let through a budget', 'no car under the test budget to blank');
  else {
    const raw = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
    for (const x of raw.brands[home.bk].models[home.mk].listings) {
      if (x.vin === guinea.vin) { delete x.price; delete x.last_price; }
    }
    await ctx.route('**/data.json', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify(raw) }));
    try {
      // Counted, not looked for in the table: a car with no price never
      // reaches a row (the list is built from priced cars), but it is very
      // much inside the set the filters are counting — #filter-count prints
      // filtered().length, which is applyShared() and therefore the budget
      // predicate itself. So the observable claim is the count: with the
      // budget on, this car must be one fewer, not one more.
      await open(home.q);
      const n = async () => {
        const t = (await page.textContent('#filter-count')) || '';
        const m = t.match(/showing (?:all )?([\d,]+)/);
        return m ? Number(m[1].replace(/,/g, '')) : null;
      };
      // Cleared first: the budget persists, so a reload arrives with the last
      // one still set and "before" would not be a before.
      await setBudget(0, 'otd');
      const loose = await n();
      await setBudget(budget, 'otd');
      const tight = await n();
      ok('a car the sheet cannot price is not let through a budget',
         loose === held.length && tight === want.size - 1,
         `${guinea.vin} blanked · ${loose} cars counted with no budget (${held.length} on this model),`
         + ` ${tight} under ${budget} all in · ${want.size} would fit if it still had its price`);
    } finally {
      await ctx.unroute('**/data.json');
    }
  }

  // A budget low enough to empty a shopped model: the panel must say the budget
  // did it, not shrug about "your filters".
  const shopped = new Set((SHEET.buyer || {}).shopping || []);
  if (!shopped.size) skip('the decision panel says which setting emptied a model', 'this sheet names no shopped trims');
  else {
    await open('');
    await setBudget(totals[1], 'otd');
    const subs = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) =>
      [...t.querySelectorAll('.sc-tile__sub')].map((n) => n.textContent.trim()).join(' | ')));
    const empties = subs.filter((t) => /would fit your rules|matches your filters|no car on its watched/.test(t));
    ok('the decision panel says which setting emptied a model',
       empties.length > 0 && empties.every((t) => /none under \$/.test(t)),
       empties.join(' // ') || 'no tile came up empty at the sheet\'s second-cheapest total');
  }

  await setBudget(budget, 'otd');
  await page.reload({ waitUntil: 'load' });
  await page.waitForTimeout(200);
  await page.waitForTimeout(600);
  const back = await page.locator('#f-budget').inputValue();
  ok('and the budget is still there on the next visit', Number(back) === budget, `typed ${budget}, came back "${back}"`);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
});

// --- the phone filter panel stays put --------------------------------------
// The panel closes itself when the reader scrolls away to read the results —
// open, it covers about a third of a phone screen. That rule watched
// window.scrollY, which is not the same thing as the reader scrolling: the
// browser's own scroll anchoring moves scrollY to hold the visible content
// still whenever content ABOVE the viewport changes height, with no JS
// involved. Nothing above the filter bar used to change height with the
// filters. The decision panel does — it gains a tile per shopped model — and
// the second model chip moved scrollY 370px with nothing on screen having
// moved at all, closing the panel under the finger that was still selecting.
// Both halves are pinned here, because the fix is only right if the original
// behaviour survives it.
await step('the phone filter panel', async () => {
  plan('pressing model chips does not close the filter panel',
       'and scrolling away from it still does');
  await page.setViewportSize({ width: 390, height: 844 });
  if (WATCHED.length < 2) return skipRest('the watchlist holds fewer than two models today — no chips to press');
  await open('');
  await page.locator('#filter-toggle').click();
  await page.waitForTimeout(200);
  const isOpen = () => page.locator('#filters-card').evaluate((n) => n.classList.contains('is-open'));
  if (!(await isOpen())) return skipRest('the filter panel did not open on this viewport');
  const chips = Math.min(3, await page.locator('#f-model button').count());
  const states = [];
  for (let i = 0; i < chips; i++) {
    await page.locator('#f-model button').nth(i).click();
    await page.waitForTimeout(200);
    states.push(await isOpen());
  }
  ok('pressing model chips does not close the filter panel',
     chips > 0 && states.every(Boolean), `${chips} chips pressed, open after each: ${states.join(', ')}`);
  // 500 is past the 360px the rule allows; the panel must give way to the
  // results the reader has gone looking at.
  await page.evaluate(() => window.scrollBy(0, 500));
  await page.waitForTimeout(300);
  ok('and scrolling away from it still does', !(await isOpen()));
  await page.setViewportSize({ width: 1280, height: 1000 });
});

// --- the decision ----------------------------------------------------------
// The first screen of this page was the masthead, a three-line dek, six brand
// chips and two tiles that both read "$19,980 · Hyundai Ioniq 5" — the same
// number twice, for a car this buyer is not buying. targets.json has always
// known which cars the decision is actually between (buyer.shopping names three
// trim ids across two models), and the panel puts them there.
//
// It publishes four things a reader would act on — an all-in total, a monthly
// payment, a named car, and a gap between two models — so all four are
// recomputed here from data.json rather than read back off the page. The check
// that matters most is the third one: the first version of this panel led with
// a $36,479 eDrive40 that was an ex-rental with an accident on it, which
// buyer.picks excludes from every other surface on the page. A decision panel
// that leads with a car the reader has ruled out in writing is worse than no
// panel, and nothing on the page would have said so.
await step('the decision panel', async () => {
  plan('the decision panel names the models the config says you are shopping',
       'the car it leads with passes the buyer\'s own pick rules',
       'and it is the cheapest such car, all in',
       'the gap it states is the difference between the two figures it shows',
       'and it is not on a model page');
  const buyer = SHEET.buyer || {};
  const want = new Set(buyer.shopping || []);
  if (!want.size) return skipRest('this sheet names no shopped trims (buyer.shopping is empty)');
  const f = buyer.fees || null, P = buyer.picks || {};
  const cap = P.max_miles || 50000;
  const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
  const eligible = (x) => x.price != null && x.miles != null && x.miles <= cap
    && !(P.exclude_accidents && x.accidents > 0)
    && !(P.exclude_rental && RENTAL.test(x.usage || ''));
  // heroTotal, recomputed: out the door where the sheet has a fee block, landed
  // where it does not.
  const total = (x) => {
    if (x.price == null) return null;
    const ship = x.local ? 0 : (x.ship || 0);
    if (!f) return x.price + ship;
    return Math.round(x.price * (1 + (f.tax_rate || 0))
      + (f.doc_fee || 0) + (f.title || 0) + (f.registration || 0) + (f.ev_surcharge || 0) + ship);
  };
  const want_models = [];
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {})) {
      const held = ((m || {}).listings || []).filter((x) => want.has(x.trim_id));
      if (!held.length && !Object.keys((m || {}).trims || {}).some((id) => want.has(id))) continue;
      want_models.push({ bk, mk, label: (m || {}).label || mk, held });
    }
  if (!want_models.length) return skipRest('no model on this sheet carries a shopped trim');
  await open('');
  const tiles = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
    label: (t.querySelector('.sc-tile__label') || {}).textContent || '',
    value: (t.querySelector('.sc-tile__value') || {}).textContent || '',
    subs: [...t.querySelectorAll('.sc-tile__sub')].map((n) => n.textContent.trim()),
    vin: ((t.querySelector('[data-fkey^="hero:"]') || {}).getAttribute
      ? t.querySelector('[data-fkey^="hero:"]').getAttribute('data-fkey').split(':')[1] : ''),
  })));
  const labels = tiles.map((t) => t.label.split(' — ')[0].trim());
  ok('the decision panel names the models the config says you are shopping',
     labels.length === want_models.length && want_models.every((w) => labels.includes(w.label)),
     `config says ${JSON.stringify(want_models.map((w) => w.label))} · panel shows ${JSON.stringify(labels)}`);

  const byLabel = new Map(want_models.map((w) => [w.label, w]));
  const led = tiles.map((t) => {
    const w = byLabel.get(t.label.split(' — ')[0].trim());
    const x = w ? w.held.find((y) => y.vin === t.vin) : null;
    return { t, w, x };
  }).filter((r) => r.x);
  if (!led.length) return skipRest('the panel led with no car today — every shopped model is filtered out');
  const bad = led.filter((r) => !eligible(r.x));
  ok('the car it leads with passes the buyer\'s own pick rules',
     bad.length === 0,
     bad.length ? bad.map((r) => `${r.x.vin}: ${r.x.miles} mi, ${r.x.accidents} accidents, usage "${r.x.usage || ''}"`).join(' | ')
                : led.map((r) => `${r.w.label} ${r.x.vin} (${r.x.miles} mi, ${r.x.accidents || 0} acc)`).join(' · '));

  const num = (t) => Number(String(t).replace(/[^0-9]/g, '')) || null;
  const wrong = led.filter((r) => {
    const fit = r.w.held.filter(eligible).map(total).filter((v) => v != null);
    return !fit.length || Math.min(...fit) !== num(r.t.value) || total(r.x) !== num(r.t.value);
  });
  ok('and it is the cheapest such car, all in', wrong.length === 0,
     wrong.length ? wrong.map((r) => `${r.w.label}: panel ${r.t.value}, cheapest eligible ${Math.min(...r.w.held.filter(eligible).map(total))}`).join(' | ')
                  : led.map((r) => `${r.w.label} ${r.t.value}`).join(' · '));

  const gapTxt = (await page.textContent('#hero-gap')) || '';
  if (led.length !== 2) skip('the gap it states is the difference between the two figures it shows',
                             `the panel led with ${led.length} car${led.length === 1 ? '' : 's'} today, so there is no gap to state`);
  else {
    // Both numbers in the sentence, because the panel states two gaps and the
    // monthly one is the number a buyer actually decides on. Checked against
    // the tiles rather than recomputed: the claim is that the sentence and the
    // figures above it agree, and a second implementation of payFor() here
    // would only prove the harness can amortise.
    const vals = led.map((r) => num(r.t.value)).sort((a, b) => a - b);
    const pays = led.map((r) => num((r.t.subs.find((n) => /\/mo\b/.test(n)) || '').split('/mo')[0]))
      .filter((v) => v != null).sort((a, b) => a - b);
    const saidTotal = num((gapTxt.match(/costs \$[\d,]+ more/) || [''])[0]);
    const saidPay = num((gapTxt.match(/, \$[\d,]+ a month/) || [''])[0]);
    const wantPay = pays.length === 2 ? pays[1] - pays[0] : null;
    ok('the gap it states is the difference between the two figures it shows',
       saidTotal === vals[1] - vals[0]
         && (wantPay == null ? saidPay == null : saidPay === wantPay),
       `panel says ${saidTotal} total / ${saidPay} a month; tiles differ by ${vals[1] - vals[0]} (${vals.join(' vs ')})`
       + ` and ${wantPay} a month (${pays.join(' vs ')})`);
  }

  // The panel answers a watchlist-wide question and orderSections('model')
  // never lists it, so nothing but an explicit hide keeps it off a model page.
  // Reached by NAVIGATING from the watchlist, not by loading the model's URL:
  // a fresh load starts with the card hidden in the markup and would pass
  // whatever the code did. The leak this guards against is the one that only
  // exists in a session that has already drawn the panel once.
  const jump = page.locator('#hero-cars [data-fkey^="hero:"]').first();
  if (!(await jump.count())) skip('and it is not on a model page', 'the panel led with no car to open today');
  else {
    const shown = await page.locator('#hero-card').evaluate((n) => !n.hidden);
    await jump.click();
    await page.waitForTimeout(400);
    ok('and it is not on a model page',
       shown && await page.locator('#hero-card').evaluate((n) => n.hidden),
       `visible on the watchlist: ${shown}; then opened ${await page.textContent('#h1')}`);
  }
});

// --- the drivable car, financed --------------------------------------------
// The decision panel's drivable line said "$1,163 more" — the all-in gap —
// under a winner whose own line quotes a monthly payment, and on the day it
// was caught the gap had the wrong sign for the decision: the i5's cheapest
// car all in was a non-certified Houston car at 6.9%, the cheapest drivable a
// certified Cincinnati car on the 2.99% promo, and over the promo's 60 months
// the drivable one costs thousands LESS, hauler included. The line now prices
// the drivable car the way the winner's line prices the winner and states the
// gap in cash by the end of each loan. Checked against the figures ON THE
// TILE, not recomputed: the claim is that the sentence and the numbers above
// it agree, and every number it needs is printed — the winner's payment and
// term, its shipping, the drivable car's payment and term.
await step('the drivable car, financed', async () => {
  plan('the drivable car on the decision panel carries its own payment',
       'and the cash gap it states is the one the figures above it make');
  await open('');
  const tiles = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) =>
    [...t.querySelectorAll('.sc-tile__sub')].map((n) => n.textContent.replace(/\s+/g, ' ').trim())));
  const num = (t) => Number(String(t || '').replace(/[^0-9.]/g, '')) || 0;
  const subjects = tiles.map((subs) => {
    const drive = subs.find((t) => /^cheapest you can drive to:/.test(t));
    if (!drive) return null;
    const winner = subs.map((t) => t.match(/^\$([\d,]+)\/mo at [\d.]+%(?: promo)? over (\d+) months/)).find(Boolean);
    const ship = num((subs.find((t) => /est\. shipping/.test(t)) || '').match(/\+ \$([\d,]+) est\. shipping/) ? RegExp.$1 : 0);
    // the financed line: "$818/mo at 2.99% promo · $3,344 less in cash over 60 months, shipping counted"
    // or, at differing terms, "$818/mo at 2.99% promo over 60 mo, against $857/mo over 72 mo · $X less in cash by the end of each loan, shipping counted"
    const fin = subs.map((t) => t.match(/^\$([\d,]+)\/mo at [\d.]+%(?: promo)?(?: over (\d+) mo, against \$([\d,]+)\/mo over (\d+) mo)? · (?:(the same) in cash|\$([\d,]+) (less|more) in cash)(?: over (\d+) months| by the end of each loan), shipping counted$/)).find(Boolean);
    return { drive, winner, ship, fin, subs };
  }).filter(Boolean);
  if (!subjects.length) return skipRest('every shopped model\'s cheapest car is drivable today — no drivable line to price');
  const unpriced = subjects.filter((s) => !s.fin);
  ok('the drivable car on the decision panel carries its own payment',
     unpriced.length === 0,
     unpriced.length ? unpriced.map((s) => s.drive + ' // ' + s.subs.join(' | ')).join(' ## ')
                     : subjects.map((s) => s.fin[0]).join(' · '));
  const wrong = subjects.filter((s) => s.fin && s.winner).filter((s) => {
    const [, payL, termL, payX2, termX2, same, gapTxt, word, termBoth] = s.fin;
    const payX = num(s.winner[1]), termX = termX2 ? num(termX2) : num(s.winner[2]);
    const tL = termL ? num(termL) : num(termBoth);
    const want = num(payL) * tL - (payX * termX + s.ship);
    const said = same ? 0 : (word === 'less' ? -1 : 1) * num(gapTxt);
    return want !== said || (payX2 && num(payX2) !== payX);
  });
  ok('and the cash gap it states is the one the figures above it make',
     subjects.every((s) => s.winner) && wrong.length === 0,
     wrong.length ? wrong.map((s) => `${s.fin[0]} — winner ${s.winner[0]}, ship $${s.ship}`).join(' ## ')
                  : subjects.map((s) => `${s.fin[0]} against ${s.winner[0]} + $${s.ship} ship`).join(' · '));
});

// --- the market sentence describes the trim in view ------------------------
// The list's "Market:" clause read the MODEL's block — computed in Python over
// every trim — under a table trimRows() had already narrowed to one trim. On
// the i7 that put "listings ran at least ~6d (29 gone)" under the eDrive50
// while every one of those 29 departures was an xDrive60 or an M70. The
// figures are recomputed here from data.json by the same rules Tracking.py's
// market_stats() and sale_stats() apply — days listed over the rows that carry
// one, cuts over rows tracked two days or more, spans over `exact` delistings
// with a listing date, VIN-unique — for a trim whose own sentence differs from
// its model's, so a page still reading the model block cannot pass.
await step('the market sentence describes the trim in view', async () => {
  plan('the market sentence under a trim is that trim\'s own',
       'and a trim with too few departures to speak for does not borrow its siblings\'',
       'and the floors hold on a sheet that would breach them',
       'and the typical-days split keeps its own twelve-car floor');
  const med = (a) => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const bitsOf = (rows, gone) => {
    const dl = rows.map((x) => x.days_listed).filter((v) => v != null);
    // seen at two prices is not cut — two distinct prices, each seen twice, a step each way — and such cars are set aside from every cut figure
    const swing = (x) => { const ps = (x.series || []).map((pt) => pt[1]).filter(Boolean), d = [...new Set(ps)]; if (d.length !== 2 || ps.length < 4 || d.some((p) => ps.filter((q) => q === p).length < 2)) return false; let dn = false, up = false; for (let i = 1; i < ps.length; i++) { if (ps[i] < ps[i - 1]) dn = true; else if (ps[i] > ps[i - 1]) up = true; } return dn && up; };
    const tracked = rows.filter((x) => (x.days_tracked || 0) >= 2), swings = tracked.filter(swing), counted = tracked.filter((x) => !swing(x)), cut = counted.filter((x) => x.cuts);
    const drops = [];
    for (const x of rows) { if (swing(x)) continue; const s = x.series || []; for (let i = 1; i < s.length; i++) if (s[i][1] != null && s[i - 1][1] != null && s[i][1] < s[i - 1][1]) drops.push(s[i - 1][1] - s[i][1]); }
    // `departures` before the listing-date filter, because the median's n is
    // not the number of departures and both surfaces printed it as one. The
    // still-listed clause is the page's own stillListed(): a VIN that left one
    // watch and is listed under another trim did not leave the market.
    const stillL = (g) => !!(g && g.still_listed && (g.still_listed.trim || g.still_listed.trim_id));
    const spans = [], seen = new Set();
    let departures = 0;
    for (const g of gone) {
      if (g.likely !== 'delisted' || g.exact !== true || stillL(g)) continue;
      const k = String(g.vin || '').toUpperCase(); if (seen.has(k)) continue; seen.add(k);
      departures++;
      if (!g.listed_since) continue;
      spans.push(Math.max(0, Math.round((Date.parse(String(g.last_seen).slice(0, 10) + 'T00:00:00Z') - Date.parse(String(g.listed_since).slice(0, 10) + 'T00:00:00Z')) / 86400000)));
    }
    const out = [];
    const stock = rows.filter((x) => x.days_listed != null && x.miles != null && x.miles < 100).map((x) => x.days_listed);
    const used = rows.filter((x) => x.days_listed != null && x.miles != null && x.miles >= 100).map((x) => x.days_listed);
    if (dl.length >= 12) out.push(`typical car ${Math.trunc(med(dl))}d on market (${dl.length} of ${rows.length} dated)`
      + (stock.length >= 12 && used.length >= 12 ? ` — ${stock.length} dealer stock at ${Math.trunc(med(stock))}d, ${used.length} used at ${Math.trunc(med(used))}d`
        + (dl.length - stock.length - used.length ? `, ${dl.length - stock.length - used.length} with no mileage` : '') : ''));
    if (tracked.length >= 5) {
      const netDown = counted.filter((x) => (x.delta || 0) < 0), restored = counted.filter((x) => x.cuts && (x.delta || 0) >= 0);
      const medNet = netDown.length ? Math.trunc(med(netDown.map((x) => -x.delta))) : null;
      // `counted`, not `tracked` — netDown already excludes the sawtooth
      // cars, and printing the wider denominator beside it was the drift
      // this mirror exists to catch.
      out.push(`${netDown.length} of ${counted.length} ask less than when first seen` + (medNet ? `, median $${medNet.toLocaleString('en-US')} less` : '') + (restored.length ? ` · ${restored.length} cut and put back` : ''));
      out.push(`${counted.length ? Math.round(cut.length / counted.length * 100) : 0}% of ${counted.length} cut while tracked` + (drops.length ? `, median $${Math.trunc(med(drops)).toLocaleString('en-US')} of ${drops.length} cut${drops.length === 1 ? '' : 's'}` : '') + (swings.length ? ` · ${swings.length} seen at two prices, not counted` : ''));
    }
    if (spans.length >= 12) out.push(`listings ran at least ~${Math.trunc(med(spans))}d (${spans.length} of ${departures} dated)`);
    return { out, spans: spans.length };
  };
  const dedupe = (rows) => { const seen = new Set(); return rows.filter((g) => { const k = String(g.vin || '').toUpperCase(); if (seen.has(k)) return false; seen.add(k); return true; }); };
  let subject = null;
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {})) {
      const whole = bitsOf(m.listings || [], dedupe(m.gone || []));
      for (const tid of Object.keys(m.trims || {})) {
        const rows = (m.listings || []).filter((x) => x.trim_id === tid);
        if (!rows.length) continue;
        const mine = bitsOf(rows, dedupe((m.gone || []).filter((g) => g.trim_id === tid)));
        if (mine.out.join(' · ') === whole.out.join(' · ')) continue;
        // …and it must be able to SPEAK. The served check below asserts that
        // two floors stay shut while the cut clause still prints, so a trim
        // whose own sentence has no cut clause proves nothing there — it is
        // silent for a reason the check is not about. bitsOf() needs five
        // tracked cars for that clause, and when one EV per brand made
        // bmw-i5-cpo (four listings) the first differing trim on the sheet,
        // the check failed on a page that was behaving correctly.
        if (!mine.out.some((t) => /cut while tracked/.test(t))) continue;
        // prefer the case the bug was found on: a trim with too few departures
        // of its own under a model whose sentence carries the days-to-go clause
        const borrowed = whole.out.some((t) => /listings ran/.test(t)) && mine.spans < 12;
        if (!subject || (borrowed && !subject.borrowed)) subject = { bk, mk, tid, mine, whole, borrowed };
      }
    }
  if (!subject) return skipRest('no trim on this sheet has a market sentence different from its model\'s');
  await open(`?brand=${subject.bk}&m=${subject.mk}&trims=${subject.tid}`);
  const hint = (await page.textContent('#list-hint')).replace(/\s+/g, ' ');
  const said = (hint.match(/Market: (.*?)\.(?= [A-Z]|$)/) || [])[1] || '';
  const want = subject.mine.out.join(' · ');
  ok('the market sentence under a trim is that trim\'s own', said === want,
     `${subject.tid}: page says "${said}" · the trim's own rows say "${want}" · the model's block says "${subject.whole.out.join(' · ')}"`);
  if (!subject.borrowed) skip('and a trim with too few departures to speak for does not borrow its siblings\'',
                              'no trim on this sheet sits under a model whose days-to-go figure it did not earn');
  else ok('and a trim with too few departures to speak for does not borrow its siblings\'',
          !/listings ran/.test(said),
          `${subject.tid} has ${subject.mine.spans} exact delistings of its own; the page says "${said}"`);

  // The two gates the live sheet cannot exercise: on the day this was written
  // every dated delisting in the record was stamped `exact`, and the only trim
  // with fewer than twelve dated cars was a single-trim model. So the same
  // trim is served a sheet that breaches both — five dated cars, and twelve
  // departures that are delisted, dated and NOT exact — and the sentence must
  // print neither the typical-days figure nor the days-to-go one, while still
  // printing the cut clause, so that silence is the gates and not an empty
  // sentence.
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    const mm = sheet.brands[subject.bk].models[subject.mk];
    let kept = 0;
    for (const x of mm.listings) if (x.trim_id === subject.tid && x.days_listed != null && ++kept > 5) x.days_listed = null;
    const donor = (mm.gone || []).find((g) => g.trim_id === subject.tid) || (mm.gone || [])[0] || mm.listings.find((x) => x.trim_id === subject.tid);
    mm.gone = (mm.gone || []).concat(Array.from({ length: 12 }, (_, i) => ({ ...donor, vin: `PLANT${String(i).padStart(12, '0')}`,
      trim_id: subject.tid, likely: 'delisted', exact: false, listed_since: '2026-08-01', last_seen: '2026-08-20', series: [] })));
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(`?brand=${subject.bk}&m=${subject.mk}&trims=${subject.tid}`);
    const planted = ((await page.textContent('#list-hint')).replace(/\s+/g, ' ').match(/Market: (.*?)\.(?= [A-Z]|$)/) || [])[1] || '';
    ok('and the floors hold on a sheet that would breach them',
       !/typical car/.test(planted) && !/listings ran/.test(planted) && /cut while tracked/.test(planted),
       `${subject.tid} with 5 dated cars and 12 unconfirmed departures: the page says "${planted || '(no market sentence)'}"`);
  } finally {
    await ctx.unroute('**/data.json');
  }
  // The split inside the typical-days figure has its own floor: twelve dated
  // cars on EACH side. Served: twenty dated cars of which three are dealer
  // stock, so the figure prints and the split must not — compared against
  // the harness's own sentence for the same served rows, which applies the
  // rule, so a page that prints "3 dealer stock at …" cannot pass.
  const plant2 = JSON.parse(JSON.stringify(SHEET.brands[subject.bk].models[subject.mk]));
  let stockKept = 0, usedKept = 0;
  for (const x of plant2.listings) {
    if (x.trim_id !== subject.tid || x.days_listed == null) continue;
    const stock = x.miles != null && x.miles < 100;
    if (stock ? ++stockKept > 3 : ++usedKept > 17) x.days_listed = null;
  }
  const want2 = bitsOf(plant2.listings.filter((x) => x.trim_id === subject.tid), dedupe((plant2.gone || []).filter((g) => g.trim_id === subject.tid))).out.join(' · ');
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    sheet.brands[subject.bk].models[subject.mk] = plant2;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(`?brand=${subject.bk}&m=${subject.mk}&trims=${subject.tid}`);
    const said2 = ((await page.textContent('#list-hint')).replace(/\s+/g, ' ').match(/Market: (.*?)\.(?= [A-Z]|$)/) || [])[1] || '';
    ok('and the typical-days split keeps its own twelve-car floor', said2 === want2 && /typical car/.test(want2) && !/dealer stock/.test(want2),
       `${subject.tid} with ${stockKept > 3 ? 3 : stockKept} dated stock cars and ${usedKept > 17 ? 17 : usedKept} dated used: page "${said2}" · rules "${want2}"`);
  } finally {
    await ctx.unroute('**/data.json');
  }
});

// --- seen at two prices is not cut ------------------------------------------
// A VIN surfacing through a group's storefronts at two fixed prices on
// alternate days read as four cuts and four restorations, and every downward
// step counted as a cut a dealer took. Two distinct prices, each seen at least
// twice, a step each way: that car was seen at two prices, and the page says
// exactly that — on the row, in the market sentence's set-aside count, in the
// movement tile's cut count and on the shortlist table. Recomputed from the
// sheet; then a served sheet gives one car a single blip up and back, which
// must stay a cut that did not stick, never a second price.
await step('seen at two prices is not cut', async () => {
  plan('a car seen at two prices wears those prices, not a cut count',
       'and the movement tile does not count its down day as a cut',
       'and on the shortlist table it reads the same',
       'and a single blip up and back is still a cut that did not stick',
       'and the cut share is read over the cars that were counted');
  const swingOf = (x) => { const ps = (x.series || []).map((pt) => pt[1]).filter(Boolean), d = [...new Set(ps)].sort((a, b) => a - b); if (d.length !== 2 || ps.length < 4 || d.some((p) => ps.filter((q) => q === p).length < 2)) return null; let dn = false, up = false; for (let i = 1; i < ps.length; i++) { if (ps[i] < ps[i - 1]) dn = true; else if (ps[i] > ps[i - 1]) up = true; } return dn && up ? d : null; };
  const money = (v) => '$' + Math.round(v).toLocaleString('en-US');
  const found = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] })).map((o) => ({ ...o, cars: (o.m.listings || []).filter((x) => swingOf(x)) })).filter((o) => o.cars.length).sort((a, b) => b.cars.length - a.cars.length)[0];
  if (!found) return skipRest('no car on the sheet has been seen at exactly two prices in turn');
  const car = found.cars[0], pair = swingOf(car);
  await open(found.w.q);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  await open(found.w.q);
  if (await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); }
  const rowText = await page.evaluate((vin) => { const b = document.querySelector(`#list-table [data-fkey="star:${vin}"]`); const tr = b && b.closest('tr'); return tr ? tr.textContent.replace(/\s+/g, ' ') : ''; }, car.vin);
  const wantChip = `seen at ${money(pair[0])} and ${money(pair[1])}`;
  ok('a car seen at two prices wears those prices, not a cut count', rowText.includes(wantChip) && !/\d+ cuts?\b/.test(rowText.split(wantChip)[0].slice(-40)),
     `${car.vin.slice(-6)} (${(car.series || []).map((pt) => pt[1]).slice(-6).join('/')}): row ${rowText.includes(wantChip) ? `says "${wantChip}"` : `lacks "${wantChip}"`}`);
  // the movement tile: cars whose last step is down, minus those seen at two prices
  const lastStep = (x) => (x.series && x.series.length >= 2) ? x.series[x.series.length - 1][1] - x.series[x.series.length - 2][1] : 0;
  const listings = found.m.listings || [];
  const wantCuts = listings.filter((x) => lastStep(x) < 0 && !swingOf(x)).length, rawCuts = listings.filter((x) => lastStep(x) < 0).length;
  const tileTxt = await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent.replace(/\s+/g, ' ')).find((t) => /since the previous/i.test(t)) || '');
  const mCut = tileTxt.match(/(\d+) (?:price )?cuts?/);   // no \b: the tile's text runs "7 price cuts2 new" together
  if (!(found.m.daily || []).length || (found.m.daily || []).length < 2) skip('and the movement tile does not count its down day as a cut', 'no previous snapshot to move from');
  else ok('and the movement tile does not count its down day as a cut', !!mCut && Number(mCut[1]) === wantCuts,
          `tile "${(tileTxt.match(/\d+ (?:price )?cuts?[^·]*/) || [tileTxt.slice(0, 60)])[0].trim()}" · sheet: ${wantCuts} cut (${rawCuts} with the sawtooth's down days counted)`);
  // the shortlist table: star the car and one more
  const other = listings.find((x) => x.vin !== car.vin && x.price != null);
  for (const v of [car.vin, other && other.vin].filter(Boolean)) { await page.click(`button[data-fkey="star:${v}"]`); await page.waitForTimeout(150); }
  const cell = await page.evaluate((vin) => {
    const tbl = document.getElementById('finalists-table'); if (!tbl) return null;
    const col = [...tbl.querySelectorAll('thead th')].findIndex((th) => th.querySelector(`[data-fkey="fin:${vin}"]`));
    const row = [...tbl.querySelectorAll('tbody tr')].find((tr) => /on the market/i.test(tr.children[0].textContent));   // days listed, then the cut note
    if (col < 0 || !row) return { col, rows: [...tbl.querySelectorAll('tbody tr')].map((tr) => tr.children[0].textContent.trim()) };
    return { text: row.children[col].textContent.replace(/\s+/g, ' ').trim() };
  }, car.vin);
  ok('and on the shortlist table it reads the same', !!cell && typeof cell.text === 'string' && cell.text.includes(wantChip),
     cell && cell.text ? `"${cell.text}"` : `no movement row found: ${JSON.stringify(cell)}`);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  // Served: the same car with its record rewritten to one blip — the high
  // price seen once — so it is a cut that was put back, not two prices.
  const planted = JSON.parse(JSON.stringify(found.m));
  const px = planted.listings.find((x) => x.vin === car.vin);
  px.series = px.series.map((pt, i) => [pt[0], i === 1 ? pair[1] : pair[0]]);
  px.cuts = 1; px.delta = 0; px.price = pair[0];
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    sheet.brands[found.w.bk].models[found.w.mk] = planted;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(found.w.q);
    if (await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); }
    const rowText2 = await page.evaluate((vin) => { const b = document.querySelector(`#list-table [data-fkey="star:${vin}"]`); const tr = b && b.closest('tr'); return tr ? tr.textContent.replace(/\s+/g, ' ') : ''; }, car.vin);
    ok('and a single blip up and back is still a cut that did not stick', /cut, then back up/.test(rowText2) && !rowText2.includes('seen at'),
       `${car.vin.slice(-6)} with one sighting of ${money(pair[1])}: ${/cut, then back up/.test(rowText2) ? 'reads "cut, then back up"' : rowText2.includes('seen at') ? 'still reads "seen at"' : `reads "${rowText2.slice(0, 80)}"`}`);
  } finally { await ctx.unroute('**/data.json'); }
  // Served: twenty tracked cars that were not sawtooths rewritten into
  // sawtooths, so the set-aside count is large and a share read over every
  // tracked car cannot round to the share read over the cars that were
  // counted — on the live sheet the two can coincide to the percent.
  const planted2 = JSON.parse(JSON.stringify(found.m));
  const rewrite = planted2.listings.filter((x) => (x.days_tracked || 0) >= 4 && !swingOf(x)).slice(0, 20);
  for (const x of rewrite) { const lo = x.price, hi = x.price + 500; x.series = x.series.map((pt, i) => [pt[0], i % 2 ? hi : lo]); x.cuts = Math.floor((x.series.length - 1) / 2); x.delta = x.series[x.series.length - 1][1] - lo; x.price = x.series[x.series.length - 1][1]; }
  const tracked2 = planted2.listings.filter((x) => (x.days_tracked || 0) >= 2), swings2 = tracked2.filter(swingOf), counted2 = tracked2.filter((x) => !swingOf(x)), cut2 = counted2.filter((x) => x.cuts);
  const wantShare = `${counted2.length ? Math.round(cut2.length / counted2.length * 100) : 0}% of ${counted2.length} cut while tracked`;
  const naive = `${Math.round(cut2.length / tracked2.length * 100)}%`;
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    sheet.brands[found.w.bk].models[found.w.mk] = planted2;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(found.w.q);
    const line = ((await page.textContent('#list-hint')) || '') + ' ' + ((await page.locator('#list-card .sc-hint, #list-card p').allTextContents()).join(' '));
    const said = (line.match(/\d+% of \d+ cut while tracked[^·]*·? ?(?:\d+ seen at two prices, not counted)?/) || [''])[0].replace(/\s+/g, ' ').trim();
    ok('and the cut share is read over the cars that were counted', said.startsWith(wantShare) && said.includes(`${swings2.length} seen at two prices, not counted`),
       `${rewrite.length} cars rewritten (${swings2.length} sawtooths of ${tracked2.length} tracked): page "${said || '(no cut clause)'}" · sheet "${wantShare} · ${swings2.length} seen at two prices, not counted" (${naive} if read over every tracked car)`);
  } finally { await ctx.unroute('**/data.json'); }
});

// --- dealer stock is a market of its own ------------------------------------
// The i7 eDrive50 holds two markets under one median: 41 delivery-mileage cars
// — under 100 miles, never registered, priced near sticker — and 49 used cars
// at half the price. The pooled median described neither, and nothing on the
// page said so. Now the market tile names the split with each side's count and
// median, and one filter takes the stock out of every number on the page. Both
// halves are checked on a scope that actually holds both kinds, found in the
// sheet rather than named: a sheet with no dealer stock has nothing to say and
// must say nothing.
await step('dealer stock is a market of its own', async () => {
  plan('a scope holding dealer stock says so beside its median',
       'hiding the stock takes exactly those cars out of the count',
       'and the median that remains is the used cars\' own');
  const NEW = 100;
  const med = (a) => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  let subject = null;
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {}))
      for (const tid of Object.keys(m.trims || {})) {
        const rows = (m.listings || []).filter((x) => x.trim_id === tid);
        const priced = rows.filter((x) => x.price != null);
        const fresh = priced.filter((x) => x.miles != null && x.miles < NEW), used = priced.filter((x) => x.miles != null && x.miles >= NEW);
        if (fresh.length >= 5 && used.length >= 5 && (!subject || fresh.length > subject.fresh.length))
          subject = { bk, mk, tid, rows, priced, fresh, used, all: (m.listings || []).length };
      }
  if (!subject) return skipRest('no trim on this sheet holds five cars under 100 miles beside five used ones');
  const digits = (t) => String(t || '').replace(/[^0-9]/g, '');
  await open(`?brand=${subject.bk}&m=${subject.mk}&trims=${subject.tid}`);
  const tile = () => page.locator('#kpis .sc-tile').nth(2).evaluate((n) =>
    [...n.querySelectorAll('.sc-tile__sub')].map((s) => s.textContent.replace(/\s+/g, ' ').trim()));
  const before = await tile();
  const split = before.find((t) => /delivery-mileage stock/.test(t)) || '';
  // …with the optional tail naming the priced cars that publish no mileage,
  // since the sentence opens by naming the total they are inside.
  const m = split.match(/^(\d+) of (\d+) are delivery-mileage stock — under (\d+) mi, median \$([\d,]+) — and the (\d+) used cars sit at \$([\d,]+)(?:; (\d+) publish no mileage and are in neither)?$/);
  ok('a scope holding dealer stock says so beside its median',
     !!m && +m[1] === subject.fresh.length && +m[2] === subject.priced.length && +m[3] === NEW
       && digits(m[4]) === String(Math.trunc(med(subject.fresh.map((x) => x.price)))) && +m[5] === subject.used.length
       && digits(m[6]) === String(Math.trunc(med(subject.used.map((x) => x.price))))
       && (+m[7] || 0) === subject.priced.length - subject.fresh.length - subject.used.length,
     split ? `${subject.tid}: "${split}" — sheet says ${subject.fresh.length} of ${subject.priced.length} under ${NEW} mi at ${med(subject.fresh.map((x) => x.price))}, ${subject.used.length} used at ${med(subject.used.map((x) => x.price))}`
           : `${subject.tid}: no split line on the tile — ${before.join(' | ')}`);
  await page.check('#f-hidenew');
  await page.waitForTimeout(400);
  const count = (await page.textContent('#filter-count')).trim();
  const shown = +(count.match(/^showing ([\d,]+) of/) || [])[1]?.replace(/,/g, '');
  const wantShown = subject.rows.filter((x) => !(x.miles != null && x.miles < NEW)).length;
  ok('hiding the stock takes exactly those cars out of the count',
     shown === wantShown && /no delivery-mileage stock/.test(count),
     `${count} — expected ${wantShown} of ${subject.all} (${subject.rows.length} on the trim, ${subject.fresh.length} under ${NEW} mi)`);
  const after = await tile();
  const medLine = after.find((t) => /median \$/.test(t)) || '';
  const wantMed = med(subject.priced.filter((x) => !(x.miles != null && x.miles < NEW)).map((x) => x.price));
  ok('and the median that remains is the used cars\' own',
     !after.some((t) => /delivery-mileage stock/.test(t)) && digits((medLine.match(/median \$([\d,]+)/) || [])[1]) === String(Math.trunc(wantMed)),
     `${after.join(' | ')} — expected median ${wantMed} and no split line`);
  await page.uncheck('#f-hidenew');
});

// --- one car, one number ----------------------------------------------------
// Five small things a reader can see, from the audit's remaining list, pinned
// together. A flat delta names the day it is level with (trims run on
// cadences, so "previous day" was two to four snapshot days back as often as
// one). A printed median truncates like the builder's, so the tile and the
// chart's table row agree. The year select lists only years with a car in
// view. "tracked 6d" became "seen 6 of 12 days", because it counts sightings.
// And the map's hint counts the cars the drawing cannot place instead of
// dropping them — it read two short of the count line above it.
await step('one car, one number', async () => {
  plan('a flat delta names the day it is level with',
       'the tile median and the chart table\'s newest median agree',
       'every year in the select has a car in view',
       'a car\'s sightings are counted, not aged',
       'the map hint adds up to the count line');
  const mm = SHEET.brands[carried.bk].models[carried.mk];
  const digits = (t) => String(t || '').replace(/[^0-9]/g, '');
  const prices = (mm.listings || []).filter((x) => x.price != null).map((x) => x.price);
  // Planted, not found. A day whose floor equals its cheapest car and did not
  // move is a coincidence on a live sheet; a median that lands on .5 needs an
  // even count and an odd gap between the middle two; and a watched year with
  // no car in view only exists on a sheet whose targets outrun the market. So
  // one served sheet carries all three: the last two day rows level with the
  // cheapest car, the two middle prices set a dollar apart with the day row's
  // median truncated to match, and a year no car carries added to the watch.
  if ((mm.daily || []).length < 2 || prices.length < 4) skipRest(`${carried.label} has fewer than two day rows or four priced cars to plant on`);
  else {
    const cheapest = Math.min(...prices);
    let plantedMedian = null;
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch();
      const sheet = JSON.parse(await r.text());
      const m = sheet.brands[carried.bk].models[carried.mk];
      const d = m.daily;
      d[d.length - 1].min_price = cheapest; d[d.length - 2].min_price = cheapest;
      const priced = m.listings.filter((x) => x.price != null).sort((a, b) => a.price - b.price);
      if (priced.length % 2) { const dearest = priced.pop(); m.listings = m.listings.filter((x) => x !== dearest); }   // an even count, the dearest car dropped
      // every price re-laid around the middle pair, so the median is exactly
      // the pair's mean — the floor stays the floor, the order stays the order
      const mid = priced.length / 2;
      priced.forEach((x, i) => { x.price = i < mid - 1 ? cheapest + i : i === mid - 1 ? cheapest + 10000 : i === mid ? cheapest + 10001 : cheapest + 10001 + i; });
      plantedMedian = cheapest + 10000;
      d[d.length - 1].median_price = plantedMedian;
      m.years = [...(m.years || []), '2019'];
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(carried.q);
      const delta = ((await page.locator('#kpis .sc-tile').first().locator('.sc-delta').first().textContent().catch(() => '')) || '').trim();
      ok('a flat delta names the day it is level with',
         /^= vs [A-Z][a-z]{2} \d/.test(delta) && !/previous day/.test(delta), `tile 1 says "${delta || '(no delta)'}"`);
      const tileMed = digits(((await page.locator('#kpis .sc-tile').nth(2).textContent()).match(/median \$([\d,]+)/) || [])[1]);
      const rowMed = digits(await page.locator('#chart-table tbody tr').first().locator('td').last().textContent().catch(() => ''));
      ok('the tile median and the chart table\'s newest median agree', tileMed && tileMed === rowMed && tileMed === String(plantedMedian),
         `planted a median of ${plantedMedian}.5: tile says ${tileMed || '(none)'}, the newest table row says ${rowMed || '(none)'}`);
      const opts = await page.$$eval('#f-year option', (os) => os.map((o) => o.value).filter((v) => v !== 'all'));
      const years = new Set((mm.listings || []).map((x) => x.year).filter((y) => y != null).map(String));
      ok('every year in the select has a car in view', opts.length > 0 && opts.every((y) => years.has(y)) && !opts.includes('2019'),
         `watched 2019 with no car; select offers ${JSON.stringify(opts)}; cars carry ${JSON.stringify([...years].sort())}`);
    } finally {
      await ctx.unroute('**/data.json');
    }
  }
  // Sightings over the FETCHES they span, car by car: the row's star button
  // carries the VIN, so each label is checked against the car's own series.
  // The denominator is how many times the car's target was fetched while the
  // car was listed, not how many days passed — at the ten-day cadence
  // twenty-eight models now run on, a car with a perfect record read "seen 4
  // of 31 days" and a buyer reads that as a car that keeps disappearing.
  const fetchDaysOf = (tid) => {
    for (const b of Object.values(SHEET.brands || {}))
      for (const m of Object.values(b.models || {}))
        if ((m.fetch_days || {})[tid]) return m.fetch_days[tid];
    return [];
  };
  const seenOf = (x) => {
    const n = x.days_tracked || 0;
    if (!n) return null;
    if (n === 1) return 'seen once';
    const s = x.series || [];
    if (s.length < 2) return `seen ${n} times`;
    const first = String(s[0][0]).slice(0, 10), last = String(s[s.length - 1][0]).slice(0, 10);
    const looks = fetchDaysOf(x.trim_id).filter((d) => d >= first && d <= last).length;
    return looks >= n ? `seen ${n} of ${looks} fetches` : `seen ${n} times`;
  };
  const byVin = new Map((mm.listings || []).map((x) => [x.vin, x]));
  await open(carried.q);
  const rows = await page.$$eval('#list-table tbody tr', (trs) => trs.map((tr) => {
    const b = tr.querySelector('[data-fkey^="star:"]');
    const m = tr.innerText.match(/seen (?:once|\d+ times|\d+ of \d+ fetches)|tracked \d+d/);
    return { vin: b ? b.getAttribute('data-fkey').slice(5) : '', label: m ? m[0] : '' };
  }));
  const wrongRows = rows.filter((r) => byVin.has(r.vin) && (byVin.get(r.vin).days_tracked > 1) && r.label !== seenOf(byVin.get(r.vin)));
  await page.setViewportSize({ width: 390, height: 844 });
  await open(carried.q);
  const phoneText = await page.locator('#list-card').innerText();
  const phoneLabels = [...phoneText.matchAll(/seen (?:once|\d+ times|\d+ of \d+ fetches)/g)].map((m) => m[0]);
  const expected = new Set((mm.listings || []).map(seenOf).filter(Boolean));
  await page.setViewportSize({ width: 1280, height: 1000 });
  ok('a car\'s sightings are counted, not aged',
     rows.length > 0 && wrongRows.length === 0 && phoneLabels.length > 0 && !/tracked \d+d/.test(phoneText) && phoneLabels.every((l) => expected.has(l)),
     wrongRows.length ? wrongRows.slice(0, 3).map((r) => `${r.vin}: row says "${r.label}", series says "${seenOf(byVin.get(r.vin))}"`).join(' | ')
       : `${rows.length} desktop rows and ${phoneLabels.length} phone labels agree with their series, e.g. "${(rows.find((r) => r.label) || {}).label}"`);
  await open('');
  const hint = (await page.textContent('#map-hint')) || '';
  const on = +(hint.match(/^([\d,]+) cars/) || [, '0'])[1].replace(/,/g, '');
  const beyond = +(hint.match(/([\d,]+) more beyond/) || [, '0'])[1].replace(/,/g, '');
  const noLoc = +(hint.match(/([\d,]+) with no location/) || [, '0'])[1].replace(/,/g, '');
  const count = (await page.textContent('#filter-count')) || '';
  const shown = +((count.match(/showing (?:all )?([\d,]+)/) || [, '0'])[1].replace(/,/g, ''));
  // …and the unplaced are the cars with no coordinates, named as such — not
  // folded into "beyond the lower 48", which is a geographic claim the sheet
  // does not make for them.
  const coordless = Object.values(SHEET.brands || {}).flatMap((b) => Object.values((b || {}).models || {}))
    .flatMap((m) => (m || {}).listings || []).filter((x) => x.lat == null || x.lon == null).length;
  ok('the map hint adds up to the count line', shown > 0 && on + beyond + noLoc === shown && noLoc === coordless,
     `map: ${on} on + ${beyond} beyond + ${noLoc} unplaced = ${on + beyond + noLoc}; count line: "${count.trim()}"; ${coordless} cars carry no coordinates`);
});

// --- in print the mark keeps its surface ------------------------------------
// sc.css prints in the light tokens whatever the theme, but the two chick
// marks are <img src> chosen by the theme at draw time, and in "auto" on a
// dark OS nothing flipped them: the dark-surface forms went onto white paper.
// The print events are dispatched by hand — headless Chromium has no print
// dialog to fire them — so what is pinned is the wiring: before the print the
// marks wear the light-surface files, after it they wear the theme's own again.
await step('in print the mark keeps its surface', async () => {
  plan('the marks flip to the light-surface files for print, and back after');
  await page.emulateMedia({ colorScheme: 'dark' });
  await open('');
  const srcs = () => page.$$eval('img[data-mark]', (is) => is.map((i) => i.getAttribute('src').split('/').pop()));
  const before = await srcs();
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
  const during = await srcs();
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
  const after = await srcs();
  ok('the marks flip to the light-surface files for print, and back after',
     before.length > 0 && before.every((f) => /-dark\.svg$|-cream\.svg$/.test(f)) && during.every((f) => /-light\.svg$|-ink\.svg$/.test(f))
       && after.join() === before.join(),
     `${before.length} marks: ${[...new Set(before)].join('+')} → ${[...new Set(during)].join('+')} → ${[...new Set(after)].join('+')}`);
  await page.emulateMedia({ colorScheme: null });
});

// --- the decision, day by day ----------------------------------------------
// The hero tile's own number on each day the shopped trims were fetched — the
// all-in floor of the cars that meet the buyer's rules — with the floor car's
// identity, and the premium between the two models over the days both were
// fetched. Recomputed here from data.json by the same rules the page states:
// fetch days only (from m.fetch_days), each car counted on a day when its
// trim's latest fetch on or before that day is one it was seen on, priced at
// that day's series entry, placed by local_hist, no point under five cars,
// nothing under three points. Then a SERVED sheet breaches what the live one
// cannot: a fetch day taken away from one trim (so its cars have to be carried
// forward, or the pool collapses) and an early day on which one car was seen
// (so the five-car gate has something to refuse).
await step('the decision, day by day', async () => {
  plan('each shopped tile says how many cars held its floor, and since when',
       'and what stands behind the headline car today',
       'the premium over the shared fetch days is the one the ledgers make',
       'the drivable premium over the record is the one the ledgers make',
       'and six fetch days are too few to say it',
       'and one drivable car a day is not a premium',
       'and today\'s figure is withheld when only one car could be driven to today',
       'and on a sheet that breaches them, the fetch-day rule and the five-car gate hold');
  const buyer = SHEET.buyer || {}, f = buyer.fees || null, P = buyer.picks || {};
  const want = new Set(buyer.shopping || []);
  if (!want.size || !f) return skipRest('this sheet names no shopped trims or has no fee block');
  const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
  const rules = (x) => x.miles != null && x.miles <= (P.max_miles || 50000)
    && !(P.exclude_accidents !== false && x.accidents > 0) && !(P.exclude_rental !== false && RENTAL.test(x.usage || ''));
  const totalAt = (x, price, local) => Math.round(price * (1 + (f.tax_rate || 0)) + (f.doc_fee || 0) + (f.title || 0) + (f.registration || 0) + (f.ev_surcharge || 0) + (local ? 0 : (x.ship || 0)));
  const localOn = (x, day) => { const h = x.local_hist; if (!h || !h.length) return !!x.local; let v = h[0][1]; for (const st of h) { if (st[0] <= day) v = st[1]; else break; } return !!v; };
  const ledgerOf = (m) => {
    const tids = Object.keys(m.trims || {}).filter((id) => want.has(id));
    const fd = m.fetch_days || {};
    const days = [...new Set(tids.flatMap((t) => fd[t] || []))].sort();
    const cars = new Map();
    for (const x of (m.listings || []).concat(m.gone || [])) {
      const k = String(x.vin || '').toUpperCase();
      if (!k || cars.has(k) || !tids.includes(x.trim_id) || !rules(x)) continue;
      const seen = new Map((x.series || []).filter((pt) => pt[1]).map((pt) => [pt[0], pt[1]]));
      if (seen.size) cars.set(k, { x, seen });
    }
    const latest = (tid, day) => { let b = null; for (const d of (fd[tid] || [])) { if (d <= day) b = d; else break; } return b; };
    const drawn = [];
    for (const day of days) {
      let n = 0, nl = 0, floor = null, drive = null;
      for (const { x, seen } of cars.values()) {
        const lf = latest(x.trim_id, day);
        if (!lf || !seen.has(lf)) continue;
        n++;
        const local = localOn(x, day);
        const v = totalAt(x, seen.get(lf), local);
        if (!floor || v < floor.v) floor = { v, vin: x.vin };
        if (local) { nl++; if (!drive || v < drive.v) drive = { v, vin: x.vin }; }
      }
      if (n >= 5 && floor) drawn.push({ day, v: floor.v, vin: floor.vin, nl, drive });
    }
    if (drawn.length < 3) return null;
    const last = drawn[drawn.length - 1];
    let tenure = 0; for (let i = drawn.length - 1; i >= 0 && drawn[i].vin === last.vin; i--) tenure++;
    return { drawn, identities: new Set(drawn.map((d) => d.vin)).size, tenure, first: drawn[0], last };
  };
  const models = [];
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {}))
      if (Object.keys((m || {}).trims || {}).some((id) => want.has(id))) models.push({ bk, mk, m, label: m.label || mk, ledger: ledgerOf(m) });
  if (!models.some((o) => o.ledger)) return skipRest('no shopped model has three fetch days with five fitting cars yet');
  const num = (t) => Number(String(t || '').replace(/[^0-9]/g, '')) || 0;
  const readTiles = () => page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
    label: ((t.querySelector('.sc-tile__label') || {}).textContent || '').split(' — ')[0].trim(),
    spark: !!t.querySelector('.sc-tile__spark svg'),
    cap: ([...t.querySelectorAll('.sc-tile__sub')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()).find((s) => /fetch days/.test(s)) || ''),
    depth: ([...t.querySelectorAll('.sc-tile__sub')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()).find((s) => /^next car /.test(s)) || ''),
    vin: (t.querySelector('[data-fkey^="hero:"]') || { getAttribute: () => '' }).getAttribute('data-fkey').split(':')[1] || '',
  })));
  const capOf = (o) => (!o.ledger ? null : (o.ledger.identities === 1
    ? `the same car all ${o.ledger.drawn.length} fetch days`
    : `${o.ledger.identities} different cars held this floor in ${o.ledger.drawn.length} fetch days, this one for ${o.ledger.tenure}`)
    + ` · $${o.ledger.first.v.toLocaleString('en-US')} all in on`);
  await open('');
  const tiles = await readTiles();
  const wrong = models.map((o) => ({ o, t: tiles.find((t) => t.label === o.label) })).filter(({ o, t }) => t && (o.ledger ? !(t.spark && t.cap.startsWith(capOf(o))) : (t.spark || t.cap)));
  ok('each shopped tile says how many cars held its floor, and since when', wrong.length === 0 && tiles.some((t) => t.spark),
     wrong.length ? wrong.map(({ o, t }) => `${o.label}: tile says "${t.cap}" (spark ${t.spark}); the sheet says "${capOf(o)}"`).join(' | ')
                  : tiles.filter((t) => t.spark).map((t) => `${t.label}: ${t.cap}`).join(' · '));
  // The runner-up's distance and the count of OTHER cars within $1,000, over
  // today's fit pool of the shopped trims, priced the way the tile is.
  const depthWrong = models.map((o) => {
    const t = tiles.find((t) => t.label === o.label);
    if (!t || !t.vin) return null;
    const pool = (o.m.listings || []).filter((x) => want.has(x.trim_id) && x.price != null && rules(x))
      .map((x) => ({ x, v: totalAt(x, x.price, !!x.local) })).sort((p, q) => p.v - q.v);
    if (pool.length < 2) return t.depth ? { o, t, wantDepth: '(nothing: one car)' } : null;
    const floor = pool[0].v, within = pool.filter((r) => r.x.vin !== t.vin && r.v - floor <= 1000).length;
    const wantDepth = `next car $${(pool[1].v - floor).toLocaleString('en-US')} behind · ${within ? `${within} other car${within === 1 ? '' : 's'}` : 'no other car'} within $1,000`;
    return t.depth === wantDepth ? null : { o, t, wantDepth };
  }).filter(Boolean);
  ok('and what stands behind the headline car today', depthWrong.length === 0 && tiles.some((t) => t.depth),
     depthWrong.length ? depthWrong.map(({ o, t, wantDepth }) => `${o.label}: tile "${t.depth}" · sheet "${wantDepth}"`).join(' | ') : tiles.filter((t) => t.depth).map((t) => `${t.label}: ${t.depth}`).join(' · '));
  const two = models.filter((o) => o.ledger);
  const gapTxt = ((await page.textContent('#hero-gap')) || '').replace(/\s+/g, ' ');
  if (two.length !== 2) skip('the premium over the shared fetch days is the one the ledgers make', `${two.length} shopped model(s) carry a ledger today`);
  else {
    const byDay = new Map(two[0].ledger.drawn.map((d) => [d.day, d.v]));
    const shared = two[1].ledger.drawn.filter((d) => byDay.has(d.day)).map((d) => ({ day: d.day, gap: d.v - byDay.get(d.day) }));
    const dearer = shared.length && shared[shared.length - 1].gap >= 0;   // two[1] dearer today, else the sign flips with the sentence's subject
    const gaps = shared.map((d) => Math.abs(d.gap));
    const m = gapTxt.match(/Over the (\d+) fetch days both were fetched, .*? has cost \$([\d,]+)(?:–\$([\d,]+))? more: \$([\d,]+) on [A-Z][a-z]{2} \d+, \$([\d,]+) (?:today|on [A-Z][a-z]{2} \d+)\./);
    // …and the split of the change into each side's own floor movement, by
    // label and by direction: the cheaper model today is the sentence's `a`.
    const [lo0, hi0] = dearer ? [two[0], two[1]] : [two[1], two[0]];
    const move = (o) => o.ledger.drawn.find((d) => d.day === shared[0].day) && o.ledger.drawn.find((d) => d.day === shared[shared.length - 1].day)
      ? o.ledger.drawn.find((d) => d.day === shared[shared.length - 1].day).v - o.ledger.drawn.find((d) => d.day === shared[0].day).v : 0;
    const da = move(lo0), db = move(hi0);
    const wantParts = [da ? `$${Math.abs(da).toLocaleString('en-US')} ${lo0.label}'s floor ${da > 0 ? 'rising' : 'falling'}` : null,
                       db ? `$${Math.abs(db).toLocaleString('en-US')} ${hi0.label}'s floor ${db > 0 ? 'rising' : 'falling'}` : null].filter(Boolean);
    const change = shared.length ? Math.abs(shared[shared.length - 1].gap) - Math.abs(shared[0].gap) : 0;
    if (wantParts.length) wantParts[0] = wantParts[0].replace(' ', ' is ');
    const wantSplit = change ? `Of that $${Math.abs(change).toLocaleString('en-US')} change, ${wantParts.join(' and ')}.` : null;
    const saidSplit = (gapTxt.match(/Of that \$[\d,]+ change, [^.]*\./) || [null])[0];
    if (shared.length < 5) ok('the premium over the shared fetch days is the one the ledgers make', !m, m ? `only ${shared.length} shared days, yet the page says "${m[0]}"` : `${shared.length} shared days — rightly silent`);
    else ok('the premium over the shared fetch days is the one the ledgers make',
       !!m && +m[1] === shared.length && num(m[2]) === Math.min(...gaps) && (m[3] ? num(m[3]) : num(m[2])) === Math.max(...gaps)
         && num(m[4]) === Math.abs(shared[0].gap) && num(m[5]) === Math.abs(shared[shared.length - 1].gap)
         && (wantSplit ? saidSplit === wantSplit : !saidSplit),
       m ? `page: "${m[0]} ${saidSplit || ''}" · ledgers: ${shared.length} days, ${Math.min(...gaps)}–${Math.max(...gaps)}, first ${Math.abs(shared[0].gap)}, last ${Math.abs(shared[shared.length - 1].gap)}; split "${wantSplit || ''}"`
         : `no premium sentence in "${gapTxt.slice(0, 160)}"`);
  }
  // What driving to one has cost over the record: the drivable floor minus
  // the floor on each ledger day with two or more drivable cars, seven such
  // days at least, the pool size beside it.
  const $n = (v) => '$' + v.toLocaleString('en-US');
  const driveLineOf = (ledger) => {
    const ds = ledger.drawn.filter((d) => d.nl >= 2 && d.drive);
    if (ds.length < 7) return null;
    const gaps = ds.map((d) => d.drive.v - d.v), lo = Math.min(...gaps), hi = Math.max(...gaps);
    const pools = ds.map((d) => d.nl), pMin = Math.min(...pools), pMax = Math.max(...pools);
    const ids = new Set(ds.map((d) => d.drive.vin)).size, last = ledger.drawn[ledger.drawn.length - 1];
    const today = last.nl >= 2 && last.drive ? last.drive.v - last.v : null;
    return (lo === hi ? `driving to one has cost ${$n(lo)} more on each of ${ds.length} fetch days` : `driving to one has cost ${$n(lo)}–${$n(hi)} more over ${ds.length} fetch days`)
      + `, on ${pMin === pMax ? pMin : `${pMin}–${pMax}`} drivable cars` + (today != null ? ` · ${$n(today)} today` : '')
      + (ids === 1 ? ' · the same drivable car throughout' : ` · ${ids} different drivable cars held that floor`);
  };
  const readDrive = () => page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
    label: ((t.querySelector('.sc-tile__label') || {}).textContent || '').split(' — ')[0].trim(),
    line: ((t.querySelector('[data-drive-premium]') || {}).textContent || '').replace(/\s+/g, ' ').trim() })));
  {
    const drives = await readDrive();
    const wrongD = models.filter((o) => o.ledger).map((o) => ({ o, t: drives.find((t) => t.label === o.label), want: driveLineOf(o.ledger) || '' })).filter(({ t, want }) => t && t.line !== want);
    const said = models.filter((o) => o.ledger && driveLineOf(o.ledger)).length;
    if (!said) skip('the drivable premium over the record is the one the ledgers make', 'no shopped model has seven ledger days with two drivable cars');
    else ok('the drivable premium over the record is the one the ledgers make', wrongD.length === 0,
       wrongD.length ? wrongD.map(({ o, t, want }) => `${o.label}: tile "${t.line}" · ledgers "${want}"`).join(' | ') : drives.filter((t) => t.line).map((t) => `${t.label}: ${t.line}`).join(' · '));
  }
  // Served: the subject's shopped trims keep only their last six fetch days
  // (the line needs seven), then the subject with every drivable car but one
  // moved beyond the states (one car a day is a price, not a premium).
  const subjectD = models.find((o) => o.ledger && driveLineOf(o.ledger));
  if (!subjectD) { for (const l of ['and six fetch days are too few to say it', 'and one drivable car a day is not a premium', 'and today\'s figure is withheld when only one car could be driven to today']) skip(l, 'no line to take away'); }
  else {
    const serve = async (edit, label, detail, expectLine) => {
      const planted = JSON.parse(JSON.stringify(subjectD.m)); edit(planted);
      const wantL = (ledgerOf(planted) && driveLineOf(ledgerOf(planted))) || '';
      await ctx.route('**/data.json', async (route) => {
        const r = await route.fetch(); const sheet = JSON.parse(await r.text());
        sheet.brands[subjectD.bk].models[subjectD.mk] = planted;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      try {
        await open('');
        const t = (await readDrive()).find((t) => t.label === subjectD.label) || {};
        if (expectLine) ok(label, !!wantL && t.line === wantL && expectLine(wantL), `${detail}: tile "${t.line || '(nothing)'}" · harness "${wantL || '(nothing)'}"`);
        else ok(label, !wantL && !t.line, `${detail}: tile ${t.line ? `still says "${t.line}"` : 'says nothing'}; harness ${wantL ? `would say "${wantL}"` : 'says nothing'}`);
      } finally { await ctx.unroute('**/data.json'); }
    };
    await serve((pm) => { for (const id of Object.keys(pm.fetch_days || {})) if (want.has(id)) pm.fetch_days[id] = pm.fetch_days[id].slice(-6); },
                'and six fetch days are too few to say it', `${subjectD.label} with six fetch days`);
    await serve((pm) => {
      let kept = false;
      for (const x of (pm.listings || []).concat(pm.gone || [])) {
        if (!x.local && !(x.local_hist || []).some((st) => st[1])) continue;
        if (!kept) { kept = true; continue; }
        x.local = false; delete x.local_hist; x.ship = x.ship || 900;
      }
    }, 'and one drivable car a day is not a premium', `${subjectD.label} with one drivable car`);
    // …and every drivable car but one moved beyond the states on the LAST
    // day only: the record still has its seven days, today does not, so the
    // line prints without a "today".
    const lastDay = subjectD.ledger.drawn[subjectD.ledger.drawn.length - 1].day;
    await serve((pm) => {
      let kept = false;
      for (const x of (pm.listings || [])) {
        if (!x.local) continue;
        if (!kept) { kept = true; continue; }
        x.local = false; x.ship = x.ship || 900;
        x.local_hist = [...(x.local_hist || [[(x.series || [[lastDay]])[0][0], true]]), [lastDay, false]];
      }
    }, 'and today\'s figure is withheld when only one car could be driven to today', `${subjectD.label} with one drivable car on ${lastDay}`, (line) => !/ today/.test(line));
  }
  // The served sheet: the first shopped model's first shopped trim loses its
  // middle fetch day, and gets a day before its record on which one fit car
  // was seen.
  // The trim holding the most cars loses a fetch day that ANOTHER shopped
  // trim still has — so the day stays in the ledger's calendar and the trim's
  // cars have to be carried forward onto it — and its series points for that
  // day go too, so a page that merely reads the sightings finds nothing there.
  const subject = two[0] || models[0];
  const held = (id) => (subject.m.listings || []).filter((x) => x.trim_id === id).length;
  const tid = Object.keys(subject.m.trims || {}).filter((id) => want.has(id)).sort((p, q) => held(q) - held(p))[0];
  const fdS = subject.m.fetch_days || {};
  const others = new Set(Object.keys(subject.m.trims || {}).filter((id) => want.has(id) && id !== tid).flatMap((id) => fdS[id] || []));
  const candidates = (fdS[tid] || []).filter((d) => others.has(d));
  const gone = candidates[Math.floor(candidates.length / 2)];
  if (!tid || !gone) skip('and on a sheet that breaches them, the fetch-day rule and the five-car gate hold', 'no fetch day is shared by two shopped trims of one model');
  else {
    const planted = JSON.parse(JSON.stringify(subject.m));
    planted.fetch_days[tid] = planted.fetch_days[tid].filter((d) => d !== gone);
    for (const x of (planted.listings || []).concat(planted.gone || [])) if (x.trim_id === tid) x.series = (x.series || []).filter((pt) => pt[0] !== gone);
    const early = '2026-01-01';
    planted.fetch_days[tid] = [early, ...planted.fetch_days[tid]];
    const one = planted.listings.find((x) => x.trim_id === tid && rules(x) && (x.series || []).length);
    if (one) one.series = [[early, one.series[0][1]], ...one.series];
    const wantLedger = ledgerOf(planted);
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.bk].models[subject.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open('');
      const t = (await readTiles()).find((t) => t.label === subject.label) || {};
      const want2 = wantLedger ? capOf({ ledger: wantLedger }) : null;
      ok('and on a sheet that breaches them, the fetch-day rule and the five-car gate hold',
         wantLedger ? (t.spark && (t.cap || '').startsWith(want2)) : !(t.spark || t.cap),
         `${subject.label} without ${gone} on ${tid} and with one car seen ${early}: tile says "${t.cap || '(nothing)'}"; the rules say "${want2 || '(nothing)'}"`);
    } finally {
      await ctx.unroute('**/data.json');
    }
  }
});

// --- what the premium buys --------------------------------------------------
// The gap sentence prices the decision; this clause says what the money buys,
// in the sheet's own columns for the two floor cars and nothing else: model
// year, miles, owners, certification, days listed. Recomputed here from the
// listing objects behind the two hero links. Then two served sheets turn every
// clause the live one leaves at rest — the dearer car made older, higher-
// mileage, multi-owner, certified and long-listed — and take the owner count
// and the listing age away, since 0 owners is the API's "not reported" and
// must read as silence, never "0 owners against 1".
await step('what the premium buys', async () => {
  plan('the clause states the dearer floor car against the cheaper in the sheet\'s own columns',
       'and every clause turns with the sheet',
       'and a field the sheet lacks is silence, not a zero',
       'the dearer floor\'s total is counted out in the cheaper model\'s own fit cars',
       'and the asking price that would match the cheaper payment is the loan run backwards',
       'and a car priced at the dearer total is not bought, a drivable car priced over it is not counted, and two terms print no payment');
  const buyer = SHEET.buyer || {};
  const want = new Set(buyer.shopping || []);
  if (!want.size) return skipRest('this sheet names no shopped trims');
  const models = [];
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {}))
      if (Object.keys((m || {}).trims || {}).some((id) => want.has(id))) models.push({ bk, mk, m, label: m.label || mk });
  if (models.length !== 2) return skipRest(`${models.length} shopped model(s); the clause needs two`);
  const readHero = async () => {
    const gap = ((await page.textContent('#hero-gap')) || '').replace(/\s+/g, ' ').trim();
    const heads = gap.match(/^(.+?) costs \$[\d,]+ more than (.+?) on today's cheapest/);
    const vins = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
      label: ((t.querySelector('.sc-tile__label') || {}).textContent || '').split(' — ')[0].trim(),
      vin: ((t.querySelector('[data-fkey^="hero:"]') || { getAttribute: () => '' }).getAttribute('data-fkey') || '').split(':')[1] || '',
    })));
    const said = (gap.match(/For that, .*?\. /) || [''])[0];
    return { gap, heads, vins, said };
  };
  // The sentence the sheet supports, from the two listing objects. Any field
  // absent on either side is left out, and an owner count of 0 is absent.
  const n = (v) => v.toLocaleString('en-US');
  const buysOf = (x, y, B) => {
    const head = [];
    if (x.year && y.year) head.push(y.year === x.year ? 'the same model year' : `${Math.abs(y.year - x.year)} model year${Math.abs(y.year - x.year) === 1 ? '' : 's'} ${y.year > x.year ? 'newer' : 'older'}`);
    if (x.miles != null && y.miles != null) head.push(y.miles === x.miles ? 'the same mileage' : `${n(Math.abs(y.miles - x.miles))} ${y.miles < x.miles ? 'fewer' : 'more'} miles`);
    const parts = [head.join(' with ')];
    if (x.owners > 0 && y.owners > 0) parts.push(x.owners === y.owners ? (x.owners === 1 ? 'both one owner' : `both ${x.owners} owners`) : `${y.owners} owner${y.owners === 1 ? '' : 's'} against ${x.owners}`);
    parts.push(!!x.cpo === !!y.cpo ? (y.cpo ? 'both certified' : 'neither certified') : (y.cpo ? `${B} certified, the other not` : `${B} not certified, the other is`));
    if (x.days_listed != null && y.days_listed != null) parts.push(`listed ${y.days_listed} day${y.days_listed === 1 ? '' : 's'} against ${x.days_listed}`);
    return `For that, ${B} is ${parts.filter(Boolean).join('; ')}. `;
  };
  const carOf = (label, vin) => { const o = models.find((o) => o.label === label); return o && (o.m.listings || []).find((x) => x.vin === vin); };
  await open('');
  const live = await readHero();
  if (!live.heads) return skipRest(`no two-model gap sentence today: "${live.gap.slice(0, 120)}"`);
  const [, B, A] = live.heads;
  const pick = (label) => { const t = live.vins.find((t) => t.label === label); return t && carOf(label, t.vin); };
  const y = pick(B), x = pick(A);
  if (!x || !y) return skipRest(`could not find the hero cars for "${A}" and "${B}" in the sheet`);
  ok('the clause states the dearer floor car against the cheaper in the sheet\'s own columns', live.said === buysOf(x, y, B),
     `page: "${live.said || '(no clause)'}" · sheet: "${buysOf(x, y, B)}"`);
  // What the money buys, counted here from the sheet: the cheaper model's
  // shopped, rule-fit, priced cars whose all-in total is under the dearer
  // floor's; the newest (then fewest miles), the lowest-mileage, drivable,
  // certified, and certified at a seller whose name carries the brand.
  {
    const f = (SHEET.buyer || {}).fees || null, P0 = (SHEET.buyer || {}).picks || {};
    const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
    const fit = (c) => c.price != null && c.miles != null && c.miles <= (P0.max_miles || 50000) && !(P0.exclude_accidents !== false && c.accidents > 0) && !(P0.exclude_rental !== false && RENTAL.test(c.usage || ''));
    const totalOf = (c) => { const ship = c.local ? 0 : (c.ship || 0); return f ? Math.round(c.price * (1 + (f.tax_rate || 0)) + (f.doc_fee || 0) + (f.title || 0) + (f.registration || 0) + (f.ev_surcharge || 0) + ship) : c.price + ship; };
    const cheaperModel = models.find((o) => o.label === A);
    const n = (v) => v.toLocaleString('en-US');
    const dearTotal = totalOf(y);
    // the sentence the sheet supports, from one model's listings and the dearer total
    const buysLine = (listings) => {
      const pool = listings.filter((c) => want.has(c.trim_id) && fit(c));
      const buys = pool.filter((c) => totalOf(c) < dearTotal);
      if (pool.length < 2 || !buys.length) return '';
      const newest = buys.slice().sort((p, q) => (q.year - p.year) || (p.miles - q.miles))[0];
      const lowest = buys.slice().sort((p, q) => p.miles - q.miles)[0];
      const cert = buys.filter((c) => c.cpo), named = cert.filter((c) => new RegExp('\\b' + cheaperModel.bk + '\\b', 'i').test(c.dealer || '')).length;
      return `${B}'s $${n(dearTotal)} buys ${buys.length} of the ${pool.length} ${A}s that fit your rules — the newest a ${newest.year} with ${n(newest.miles)} miles, the lowest-mileage a ${lowest.year} with ${n(lowest.miles)}; ${buys.filter((c) => c.local).length} drivable, ${cert.length} certified${cert.length && named !== cert.length ? ` (${named} at a seller named ${cheaperModel.bk.toUpperCase()})` : ''}. `;
    };
    const buysIn = (gap) => (gap.match(new RegExp(`${B.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}'s \\$[\\d,]+ buys .*?\\. `)) || [''])[0];
    const wantBuys = buysLine(cheaperModel.m.listings || []), saidBuys = buysIn(live.gap);
    ok('the dearer floor\'s total is counted out in the cheaper model\'s own fit cars', saidBuys === wantBuys,
       `page: "${saidBuys || '(no clause)'}" · sheet: "${wantBuys || '(nothing: no fit car under the dearer total)'}"`);
    // …and the loan run backwards: the cheaper tile's payment, the dearer
    // tile's own rate and term, the fee block and the dearer car's shipping.
    const tiles = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
      label: ((t.querySelector('.sc-tile__label') || {}).textContent || '').split(' — ')[0].trim(),
      pay: ([...t.querySelectorAll('.sc-tile__sub')].map((s) => s.textContent).find((s) => /\/mo at /.test(s)) || '') })));
    const tA = tiles.find((t) => t.label === A), tB = tiles.find((t) => t.label === B);
    const mA = tA && tA.pay.match(/\$([\d,]+)\/mo at ([\d.]+)% .*?over (\d+) months/), mB = tB && tB.pay.match(/\$([\d,]+)\/mo at ([\d.]+)% .*?over (\d+) months/);
    const saidAsk = (live.gap.match(/would have to ask \$([\d,]+) — \$([\d,]+) off its \$([\d,]+)\./) || null);
    if (!f || !mA || !mB) skip('and the asking price that would match the cheaper payment is the loan run backwards', 'no financed payment on both tiles today');
    else if (mA[3] !== mB[3]) ok('and the asking price that would match the cheaper payment is the loan run backwards', !saidAsk, `terms differ (${mA[3]} vs ${mB[3]} months) — ${saidAsk ? `yet the page says "${saidAsk[0]}"` : 'rightly silent'}`);
    else {
      const pay = Number(mA[1].replace(/,/g, '')), apr = Number(mB[2]), term = Number(mB[3]), r = apr / 100 / 12;
      const principal = r > 0 ? pay * (1 - Math.pow(1 + r, -term)) / r : pay * term;
      const fixed = (f.doc_fee || 0) + (f.title || 0) + (f.registration || 0) + (f.ev_surcharge || 0);
      const inLoan = f.finance_shipping && !y.local ? (y.ship || 0) : 0;   // the loan carries shipping only when the fee block says so
      const ask = Math.round((principal - fixed - inLoan) / (1 + (f.tax_rate || 0)));   // down payment 0 on a fresh profile
      const wantAsk = ask < y.price ? `would have to ask $${n(ask)} — $${n(y.price - ask)} off its $${n(y.price)}.` : null;
      ok('and the asking price that would match the cheaper payment is the loan run backwards', wantAsk ? (!!saidAsk && saidAsk[0] === wantAsk) : !saidAsk,
         `page: "${saidAsk ? saidAsk[0] : '(no ask)'}" · harness from ${tA.pay.trim()} and ${B}'s ${apr}% over ${term}: "${wantAsk || '(nothing: the dearer car already asks less)'}"`);
    }
    // Served: one non-drivable fit car of the cheaper model priced so its
    // all-in total EQUALS the dearer floor's (bought only under "<="), one
    // drivable fit car priced over it (counted only if drivable were tallied
    // over the pool rather than the bought), and a promo that reaches every
    // car of the DEARER model at a 36-month cap, so the two floors finance
    // over different terms and the payment line must stay silent — on the
    // dearer side, because a shorter term on the cheaper car raises its
    // payment above the dearer's and the line would be silent for the wrong
    // reason.
    const poolLive = (cheaperModel.m.listings || []).filter((c) => want.has(c.trim_id) && fit(c));
    const atTotal = poolLive.find((c) => !c.local && c.vin !== x.vin && totalOf(c) < dearTotal);
    const driven = poolLive.find((c) => c.local && c.vin !== x.vin && totalOf(c) < dearTotal);
    const fees = f || {};
    const priceAt = (c, T) => { const fixed = (fees.doc_fee || 0) + (fees.title || 0) + (fees.registration || 0) + (fees.ev_surcharge || 0), ship = c.local ? 0 : (c.ship || 0);
      let price = Math.round((T - fixed - ship) / (1 + (fees.tax_rate || 0)));
      for (let d = -3; d <= 3; d++) if (totalOf({ ...c, price: price + d }) === T) return price + d;
      return null; };
    const pAt = atTotal && f ? priceAt(atTotal, dearTotal) : null;
    if (!atTotal || !driven || pAt == null || !mA || !mB) skip('and a car priced at the dearer total is not bought, a drivable car priced over it is not counted, and two terms print no payment',
      `no ${!atTotal ? 'shipped' : !driven ? 'drivable' : 'financed'} fit car of ${A} to plant with today`);
    else {
      const planted = JSON.parse(JSON.stringify(cheaperModel.m));
      planted.listings.find((c) => c.vin === atTotal.vin).price = pAt;
      planted.listings.find((c) => c.vin === driven.vin).price = y.price + 1000;
      const want2 = buysLine(planted.listings);
      await ctx.route('**/data.json', async (route) => {
        const r = await route.fetch(); const sheet = JSON.parse(await r.text());
        sheet.brands[cheaperModel.bk].models[cheaperModel.mk] = planted;
        const dearer = models.find((o) => o.label === B);
        const fin = sheet.buyer.finance = { ...(sheet.buyer.finance || {}) };
        fin.promos = [...(fin.promos || []).filter((p) => p.model !== `${dearer.bk}/${dearer.mk}`),
                      { model: `${dearer.bk}/${dearer.mk}`, cpo_only: false, apr: 2.99, max_term: 36, expires: '2099-01-01', label: 'planted 2.99%', active: true, days_left: 999 }];
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      try {
        await open('');
        const h2 = await readHero();
        const said2 = buysIn(h2.gap), ask2 = /would have to ask/.test(h2.gap);
        const terms = await page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => (([...t.querySelectorAll('.sc-tile__sub')].map((s) => s.textContent).find((s) => /\/mo at /.test(s)) || '').match(/over (\d+) months/) || [])[1]));
        ok('and a car priced at the dearer total is not bought, a drivable car priced over it is not counted, and two terms print no payment',
           !!h2.heads && said2 === want2 && !ask2 && new Set(terms.filter(Boolean)).size === 2,
           `${atTotal.vin.slice(-6)} at $${n(pAt)} (= ${B}'s $${n(dearTotal)}), ${driven.vin.slice(-6)} at $${n(y.price + 1000)}, terms ${JSON.stringify(terms)}: page "${said2 || '(no clause)'}"${ask2 ? ' + a payment line' : ''} · sheet "${want2}"`);
      } finally { await ctx.unroute('**/data.json'); }
    }
  }
  // Served: the same two cars with their columns rewritten. Prices are
  // untouched, so both stay the floor and the sentence's subjects.
  const dearer = models.find((o) => o.label === B), cheaper = models.find((o) => o.label === A);
  const serve = (edit) => ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    const mx = sheet.brands[cheaper.bk].models[cheaper.mk], my = sheet.brands[dearer.bk].models[dearer.mk];
    edit(mx.listings.find((c) => c.vin === x.vin), my.listings.find((c) => c.vin === y.vin));
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  const turned = { x: { year: 2025, miles: 5000, owners: 1, cpo: false, days_listed: 10 }, y: { year: 2024, miles: 13000, owners: 3, cpo: true, days_listed: 200 } };
  await serve((cx, cy) => { Object.assign(cx, turned.x); Object.assign(cy, turned.y); });
  try {
    await open('');
    const t = await readHero();
    const wantT = buysOf(turned.x, turned.y, B);
    ok('and every clause turns with the sheet', !!t.heads && t.heads[1] === B && t.said === wantT, `page: "${t.said || '(no clause)'}" · expected: "${wantT}"`);
  } finally { await ctx.unroute('**/data.json'); }
  const bare = { x: { year: 2025, miles: 5000, owners: 1, cpo: false, days_listed: 10 }, y: { year: 2025, miles: 13000, owners: 0, cpo: false, days_listed: null } };
  await serve((cx, cy) => { Object.assign(cx, bare.x); Object.assign(cy, bare.y); });
  try {
    await open('');
    const t = await readHero();
    const wantB = `For that, ${B} is the same model year with 8,000 more miles; neither certified. `;
    ok('and a field the sheet lacks is silence, not a zero', !!t.heads && t.heads[1] === B && t.said === wantB, `page: "${t.said || '(no clause)'}" · expected: "${wantB}"`);
  } finally { await ctx.unroute('**/data.json'); }
});

// --- under typical only outside its own interval -----------------------------
// "5% under typical, n=9" used to print as if the median were a fixed point.
// A median of nine cars has a distribution-free 95% interval of its own, and
// a car inside it cannot be told from typical; the page now says "under"
// only below the interval's low edge, and six cars make a cohort because
// five is the largest sample with no interval at all. Recomputed here from
// data.json with the harness's own interval — Pascal's triangle, where the
// page walks the binomial multiplicatively, so a shared slip cannot agree
// with itself. Then two served sheets: the cohort's best car moved to its
// median (the note and the pick must go), and a cohort cut to five cars
// (the survivors must be judged against the year, and say so).
await step('under typical only outside its own interval', async () => {
  plan('a note prints only for a car below the 95% interval of its cohort\'s median',
       'and names the interval the order statistics give',
       'a car moved to its cohort\'s median loses the note and the pick',
       'and five cars are not a cohort: the survivors stop being judged by their trim',
       'and a floor car in a cohort of eight is about typical on every surface',
       'and a margin that rounds to nothing is not a stand',
       'the compare card\'s winner row prints only a car that stands under',
       'and the pick walk skips a typical car with a big margin rather than stopping at it');
  const P0 = (SHEET.buyer || {}).picks || {};
  const P = { max_miles: P0.max_miles || 50000, cpm: P0.cents_per_mile != null ? P0.cents_per_mile : 0.30, base: P0.mileage_baseline || 20000,
              no_acc: P0.exclude_accidents !== false, no_rental: P0.exclude_rental !== false };
  const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
  const eligible = (x) => x.price != null && x.miles != null && x.miles <= P.max_miles && !(P.no_acc && x.accidents > 0) && !(P.no_rental && RENTAL.test(x.usage || ''));
  const valueOf = (x) => x.price + (x.local ? 0 : (x.ship || 0)) + (x.miles - P.base) * P.cpm;
  const ciOf = (vals) => {
    const xs = vals.slice().sort((a, b) => a - b), n = xs.length;
    if (n < 6) return null;
    let row = [1n];
    for (let i = 0; i < n; i++) { const nx = [1n]; for (let j = 1; j < row.length; j++) nx.push(row[j - 1] + row[j]); nx.push(1n); row = nx; }
    const total = 1n << BigInt(n);
    let k = 0;
    for (let t = 1; t <= n >> 1; t++) { let cov = 0n; for (let i = t; i <= n - t; i++) cov += row[i]; if (cov * 20n >= total * 19n) k = t; else break; }
    return k ? [xs[k - 1], xs[n - k]] : null;
  };
  const med = (vals) => { const a = vals.slice().sort((p, q) => p - q), m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  // every eligible car of one model: its cohort, interval and stand
  const scoreModel = (m) => {
    const pool = (m.listings || []).filter(eligible);
    if (pool.length < 6) return new Map();
    const by = (key) => { const g = new Map(); for (const x of pool) { const k = key(x); if (k == null) continue; (g.get(k) || g.set(k, []).get(k)).push(valueOf(x)); } return g; };
    const byTY = by((x) => (x.trim ? `${String(x.trim).trim().toLowerCase()}|${x.year || ''}` : null)), byY = by((x) => String(x.year || ''));
    const all = pool.map(valueOf);
    // …and the cohort's trims, since a cohort of mixed trims prices the mix
    // and says nothing at all — the same guard the scorer applies.
    const trimsOf = (rows) => new Set(rows.map((c) => String(c.trim || '').trim().toLowerCase()));
    const trimAll = trimsOf(pool), trimYear = new Map();
    for (const x of pool) { const y = String(x.year || ''); if (!trimYear.has(y)) trimYear.set(y, trimsOf(pool.filter((c) => String(c.year || '') === y))); }
    const out = new Map();
    for (const x of pool) {
      const tr = String(x.trim || '').trim().toLowerCase();
      const ty = x.trim ? `${tr}|${x.year || ''}` : null, y = String(x.year || '');
      let vals, basis, cohortTrims;
      if (ty && byTY.get(ty).length >= 6) { vals = byTY.get(ty); basis = 'trim'; cohortTrims = new Set([tr]); }
      else if (byY.get(y).length >= 6) { vals = byY.get(y); basis = 'year'; cohortTrims = trimYear.get(y); }
      else { vals = all; basis = 'model'; cohortTrims = trimAll; }
      const mixed = [...cohortTrims].some((t) => t !== tr);
      const [lo, hi] = ciOf(vals), v = valueOf(x), m0 = med(vals);
      const pct = (m0 - v) / m0;   // a stand needs the edge AND a margin that rounds to a digit
      out.set(x.vin, { v, lo, hi, n: vals.length, basis, pct, mixed,
                       stand: mixed ? 'mixed' : v < lo && pct >= 0.005 ? 'under' : v > hi && -pct >= 0.005 ? 'over' : 'typical' });
    }
    return out;
  };
  const dollars = (v) => '$' + Math.round(v).toLocaleString('en-US');
  const readRows = () => page.$$eval('#list-table tbody tr', (trs) => trs.map((tr) => {
    const b = tr.querySelector('[data-fkey^="star:"]');
    const note = [...tr.querySelectorAll('.sc-note')].find((n) => /under typical/.test(n.textContent));
    return { vin: b ? b.getAttribute('data-fkey').split(':')[1] : '', note: note ? note.textContent.replace(/\s+/g, ' ').trim() : '',
             basis: note ? note.getAttribute('data-basis') : null, title: note ? (note.getAttribute('title') || '') : '' };
  }));
  // the rows, against the rule: a note iff under AND at least 5% (the note's own notability floor)
  const audit = (rows, scores) => {
    const wrong = [];
    for (const r of rows) {
      const sc = scores.get(r.vin);
      const want = !!sc && sc.stand === 'under' && sc.pct >= 0.05;
      if (want !== !!r.note) wrong.push(`${r.vin.slice(-6)}: ${r.note ? `"${r.note}"` : 'no note'} but the sheet says ${sc ? `${sc.stand}, ${(sc.pct * 100).toFixed(1)}% (${sc.basis}, n=${sc.n})` : 'unscored'}`);
      else if (r.note && r.basis !== sc.basis) wrong.push(`${r.vin.slice(-6)}: basis ${r.basis} vs ${sc.basis}`);
    }
    return wrong;
  };
  const subject = WATCHED.slice().sort((a, b) => b.cars - a.cars)[0];
  if (!subject) return skipRest('no model on the watchlist');
  const model0 = SHEET.brands[subject.bk].models[subject.mk];
  const scores = scoreModel(model0);
  const sortByValue = async () => { await page.selectOption('#f-sort', 'value'); await page.waitForTimeout(350); };
  const showAll = async () => { if (await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); } };
  await open(subject.q); await sortByValue();
  const rows = await readRows();
  const noted = rows.filter((r) => r.note);
  // The skip is the harness's call, not the page's: a page that printed no
  // note at all on a sheet where the rule owes eighteen is the failure, not a
  // day without a subject.
  if (!rows.some((r) => { const sc = scores.get(r.vin); return sc && sc.stand === 'under' && sc.pct >= 0.05; })) return skipRest(`no car on ${subject.label}'s first rows sits under its cohort's interval by 5% today`);
  const wrong = audit(rows, scores);
  ok('a note prints only for a car below the 95% interval of its cohort\'s median', wrong.length === 0,
     wrong.length ? wrong.slice(0, 3).join(' | ') : `${noted.length} of ${rows.length} rows noted, every one below its cohort's low edge; ${[...scores.values()].filter((s) => s.stand === 'typical' && s.pct >= 0.05).length} car(s) 5%+ under a median yet inside its interval, rightly silent`);
  const badTitle = noted.filter((r) => { const sc = scores.get(r.vin); return !r.title.includes(`typical value ${dollars(sc.lo)}–${dollars(sc.hi)} at 95%`); });
  ok('and names the interval the order statistics give', badTitle.length === 0,
     badTitle.length ? `${badTitle[0].vin.slice(-6)}: title "${badTitle[0].title.slice(0, 120)}" · harness ${dollars(scores.get(badTitle[0].vin).lo)}–${dollars(scores.get(badTitle[0].vin).hi)}`
                     : noted.length ? `first: "${noted[0].title.slice(noted[0].title.indexOf('typical value'))}"` : 'no note to read');
  // Served: the best-margin car of a trim cohort, its price raised until its
  // value is that cohort's median. Prices untouched elsewhere.
  const target = [...scores.entries()].filter(([, s]) => s.stand === 'under' && s.basis === 'trim').sort((a, b) => b[1].pct - a[1].pct)[0];
  if (!target) skip('a car moved to its cohort\'s median loses the note and the pick', 'no trim-cohort car stands under today');
  else {
    const [vin, sc] = target;
    const x = model0.listings.find((c) => c.vin === vin);
    const medOld = sc.v + (sc.pct * sc.v) / (1 - sc.pct);   // v = med(1 - pct)
    const price = Math.round(medOld - (x.local ? 0 : (x.ship || 0)) - (x.miles - P.base) * P.cpm);
    const planted = JSON.parse(JSON.stringify(model0));
    planted.listings.find((c) => c.vin === vin).price = price;
    const after = scoreModel(planted).get(vin);
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.bk].models[subject.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(subject.q); await sortByValue(); await showAll();
      const row = (await readRows()).find((r) => r.vin === vin);
      const picked = await page.locator(`#takeaway [data-fkey^="pick:${vin}:"]`).count();   // the card's links are pick:VIN:media and pick:VIN:open
      ok('a car moved to its cohort\'s median loses the note and the pick', after && after.stand === 'typical' && !!row && !row.note && picked === 0,
         `${vin.slice(-6)} at ${dollars(price)} (was ${dollars(x.price)}, ${(sc.pct * 100).toFixed(1)}% under): harness says ${after ? after.stand : 'unscored'}; row ${row ? (row.note ? `still says "${row.note}"` : 'says nothing') : 'missing'}; pick cards ${picked}`);
    } finally { await ctx.unroute('**/data.json'); }
  }
  // Served: the smallest trim-and-year cohort of six or more cut to five by
  // dropping its dearest cars, leaving the year with six or more.
  const cohorts = new Map();
  for (const x of model0.listings.filter(eligible)) if (x.trim) { const k = `${String(x.trim).trim().toLowerCase()}|${x.year || ''}`; (cohorts.get(k) || cohorts.set(k, []).get(k)).push(x); }
  const yearCount = (y) => model0.listings.filter((x) => eligible(x) && String(x.year || '') === y).length;
  // Nine, not six: at six, seven and eight the interval is the whole sample,
  // so no car in such a cohort can stand under it and the before-half of the
  // check would have no note to lose. The smallest cohort that CAN speak.
  const small = [...cohorts.entries()].filter(([k, xs]) => xs.length >= 9 && yearCount(k.split('|')[1]) - (xs.length - 5) >= 6).sort((a, b) => a[1].length - b[1].length)[0];
  if (!small) skip('and five cars are not a cohort: the survivors are judged against the year', `${subject.label} has no trim cohort that can be cut to five and leave its year with six`);
  else {
    const [key, xs] = small;
    const drop = new Set(xs.slice().sort((a, b) => b.price - a.price).slice(0, xs.length - 5).map((x) => x.vin));
    const keep = xs.filter((x) => !drop.has(x.vin)).map((x) => x.vin);
    // …and, where another model year can spare it, that year cut to four, so
    // the scatter's dashed "typical value" line for it has to go too: a line
    // labelled typical over four cars is the same unsupported median.
    const yearOf = (x) => String(x.year || '');
    const spare = [...new Set(model0.listings.filter((x) => eligible(x) && !drop.has(x.vin)).map(yearOf))]
      .filter((y) => y !== key.split('|')[1] && model0.listings.filter((x) => eligible(x) && !drop.has(x.vin) && yearOf(x) === y).length >= 5)
      .sort((a, b) => model0.listings.filter((x) => eligible(x) && yearOf(x) === a).length - model0.listings.filter((x) => eligible(x) && yearOf(x) === b).length)[0];
    if (spare) for (const x of model0.listings.filter((x) => eligible(x) && !drop.has(x.vin) && yearOf(x) === spare).sort((a, b) => b.price - a.price).slice(0, -4)) drop.add(x.vin);
    const planted = JSON.parse(JSON.stringify(model0));
    planted.listings = planted.listings.filter((x) => !drop.has(x.vin));
    // …and one survivor priced to stand under the YEAR's interval by a
    // notable margin, so the page has to print a note for it and say which
    // cohort it was judged against. Without this the five survivors could
    // all sit quietly inside the year's interval and a page that fell them
    // through to the whole model instead would pass unnoticed.
    // The witness is one survivor priced low enough to be noted while its
    // trim cohort is whole — and the same car on the same price with the
    // cohort cut to five must go quiet, because it falls to the year, and a
    // year cohort can only be trim-clean when no car of that year carries a
    // trim at all (a clean one of six or more would BE the trim cohort). The
    // check is that before/after on two served sheets, so both halves are
    // the harness's own doing and neither depends on the day's prices.
    const whole = JSON.parse(JSON.stringify(model0));   // cohort intact, witness discounted
    let scores2 = scoreModel(planted), witness = null;
    {
      const cheapest = keep.map((v) => planted.listings.find((c) => c.vin === v)).sort((a, b) => valueOf(a) - valueOf(b))[0];
      for (let i = 0; i < 5 && cheapest; i++) {
        const w = whole.listings.find((c) => c.vin === cheapest.vin);
        if (w) w.price = cheapest.price;
        const b0 = scoreModel(whole).get(cheapest.vin), sc = scores2.get(cheapest.vin);
        if (b0 && b0.basis === 'trim' && b0.stand === 'under' && b0.pct >= 0.05 && sc && sc.stand !== 'under') { witness = cheapest.vin; break; }
        if (!sc) break;
        cheapest.price = Math.round(cheapest.price - Math.max(800, (valueOf(cheapest) - sc.lo) + 0.08 * sc.lo));
        scores2 = scoreModel(planted);
      }
    }
    const before = scoreModel(whole);
    // …and the page must actually print that note on the whole-cohort sheet,
    // or the "and now it does not" half proves nothing.
    let noteBefore = '';
    if (witness) {
      await ctx.route('**/data.json', async (route) => {
        const r = await route.fetch(); const sheet = JSON.parse(await r.text());
        sheet.brands[subject.bk].models[subject.mk] = whole;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      try {
        await open(subject.q); await sortByValue(); await showAll();
        noteBefore = ((await readRows()).find((r) => r.vin === witness) || {}).note || '';
      } finally { await ctx.unroute('**/data.json'); }
    }
    const yearsLeft = new Map();
    for (const x of planted.listings.filter(eligible)) yearsLeft.set(yearOf(x), (yearsLeft.get(yearOf(x)) || 0) + 1);
    const wantChips = [...yearsLeft.entries()].filter(([, n]) => n >= 6).map(([y]) => y).sort();
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.bk].models[subject.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(subject.q); await sortByValue(); await showAll();
      const rows2 = await readRows();
      const wrong2 = audit(rows2, scores2);
      const trimNoted = rows2.filter((r) => keep.includes(r.vin) && r.basis === 'trim');
      const yearNoted = rows2.filter((r) => keep.includes(r.vin) && r.basis === 'year');
      const chips = (await page.$$eval('#scatter-legend .sc-legend__chip--select', (bs) => bs.map((b) => b.dataset.y))).sort();
      const chipsRight = JSON.stringify(chips) === JSON.stringify(wantChips);
      const witnessRow = witness && rows2.find((r) => r.vin === witness);
      const w0 = witness && before.get(witness), w1 = witness && scores2.get(witness);
      if (!witness) skip('and five cars are not a cohort: the survivors stop being judged by their trim', `${key} cut to five, but no survivor carried a note to lose`);
      else ok('and five cars are not a cohort: the survivors stop being judged by their trim',
         wrong2.length === 0 && trimNoted.length === 0 && !!noteBefore && !!witnessRow && !witnessRow.note && w1.basis !== 'trim' && chipsRight,
         wrong2.length ? wrong2.slice(0, 3).join(' | ') : !chipsRight ? `scatter year chips ${JSON.stringify(chips)}; years with six eligible cars ${JSON.stringify(wantChips)}`
           : !witnessRow ? `${witness.slice(-6)}: row missing`
           : witnessRow.note ? `${witness.slice(-6)} still says "${witnessRow.note}" on a ${w1.basis} cohort of ${w1.n}`
           : !noteBefore ? `${witness.slice(-6)} carried no note even with its ${w0.n}-car trim cohort whole`
           : `${key} cut from ${xs.length} to 5: ${trimNoted.length} survivor(s) still judged against the trim; ${witness.slice(-6)} read "${noteBefore}" with its own trim's ${w0.n} whole and now falls to a ${w1.basis} cohort of ${w1.n} (${w1.mixed ? 'mixed trims' : w1.stand}) and says nothing; ${spare ? `${spare} cut to four and its line gone, ` : ''}year lines ${JSON.stringify(chips)}`);
    } finally { await ctx.unroute('**/data.json'); }
  }
  // Served, on the front page: the first shopped tile's floor car with its
  // trim-and-year cohort cut to eight — the largest cohort whose interval is
  // still the whole sample — so nothing in it can stand under. The seven
  // kept are its DEAREST siblings, raised by half again, so the floor car
  // sits a long way under the cohort's median and a page that picked on the
  // margin alone would make it a pick; the rule makes it nothing. The tile's
  // delta must read "about typical … n=8" and go flat, the car must be on no
  // pick card, its shortlist row must say "about typical", and — served onto
  // the config shortlist, whose front-page card is where the chip lives —
  // its card must wear no chip.
  const heroTiles = () => page.locator('#hero-cars .sc-tile').evaluateAll((ts) => ts.map((t) => ({
    label: ((t.querySelector('.sc-tile__label') || {}).textContent || '').split(' — ')[0].trim(),
    vin: ((t.querySelector('[data-fkey^="hero:"]') || { getAttribute: () => '' }).getAttribute('data-fkey') || '').split(':')[1] || '',
    delta: ((t.querySelector('.sc-delta') || {}).textContent || '').replace(/\s+/g, ' ').trim(),
    flat: !!t.querySelector('.sc-delta--flat') })));
  await open('');
  const tile = (await heroTiles()).find((t) => t.vin);
  const heroModel = tile && WATCHED.find((w) => w.label === tile.label);
  const hm = heroModel && SHEET.brands[heroModel.bk].models[heroModel.mk];
  const hx = hm && hm.listings.find((c) => c.vin === tile.vin);
  const sameCohort = (c) => hx && c.trim && String(c.trim).trim().toLowerCase() === String(hx.trim || '').trim().toLowerCase() && String(c.year || '') === String(hx.year || '');
  const kin = hx && hx.trim ? hm.listings.filter((c) => eligible(c) && sameCohort(c)) : [];
  if (!hx || kin.length < 8) skip('and a floor car in a cohort of eight is about typical on every surface', `the leading tile's car has ${kin.length} cars in its trim-and-year cohort; eight are needed`);
  else {
    const dropH = new Set(kin.filter((c) => c.vin !== hx.vin).sort((a, b) => a.price - b.price).slice(0, kin.length - 8).map((c) => c.vin));
    const plantedH = JSON.parse(JSON.stringify(hm));
    plantedH.listings = plantedH.listings.filter((c) => !dropH.has(c.vin));
    for (const c of plantedH.listings) if (c.vin !== hx.vin && sameCohort(c) && eligible(c)) c.price = Math.round(c.price * 1.5);
    const scH = scoreModel(plantedH).get(hx.vin);
    const labelWords = new Set(String(heroModel.label).toLowerCase().split(/\s+/));
    const short = `${hx.year} ${String(hx.trim).split(/\s+/).filter((w) => w && !labelWords.has(w.toLowerCase())).join(' ')} ${heroModel.label}`.replace(/\s+/g, ' ').trim();
    const wantDelta = `about typical for a ${short}, n=8`;
    const clearStars = () => page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[heroModel.bk].models[heroModel.mk] = plantedH;
      sheet.buyer = { ...(sheet.buyer || {}), shortlist: [{ vin: hx.vin, note: '' }] };   // the config shortlist, whose front-page card wears the chip
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(''); await clearStars(); await open('');
      const t2 = (await heroTiles()).find((t) => t.label === tile.label) || {};
      const picked = await page.locator(`#takeaway [data-fkey^="pick:${hx.vin}:"]`).count();
      await open(heroModel.q); await sortByValue(); await showAll();
      const other = (await readRows()).find((r) => r.vin && r.vin !== hx.vin);
      for (const v of [hx.vin, other && other.vin].filter(Boolean)) { await page.click(`button[data-fkey="star:${v}"]`); await page.waitForTimeout(150); }
      const cell = await page.evaluate((vin) => {
        const tbl = document.getElementById('finalists-table'); if (!tbl) return null;
        const col = [...tbl.querySelectorAll('thead th')].findIndex((th) => th.querySelector(`[data-fkey="fin:${vin}"]`));
        const row = [...tbl.querySelectorAll('tbody tr')].find((tr) => /value vs typical/i.test(tr.children[0].textContent));
        if (col < 0 || !row) return null;
        const td = row.children[col];
        return { figure: ((td.querySelector('.sc-figure') || {}).textContent || '').trim(), notes: [...td.querySelectorAll('.sc-note')].map((n) => n.textContent.trim()) };
      }, hx.vin);
      await open('');
      const card = await page.evaluate((vin) => {
        const a = document.querySelector(`[data-fkey="sl:${vin}:open"]`), c = a && a.closest('.sc-photo-card');
        return c ? { found: true, chip: ((c.querySelector('.sc-photo-card__chip') || {}).textContent || '').trim() } : { found: false, chip: '' };
      }, hx.vin);
      ok('and a floor car in a cohort of eight is about typical on every surface',
         !!scH && scH.stand === 'typical' && scH.pct >= 0.15 && t2.flat === true && t2.delta === wantDelta && picked === 0
           && !!cell && cell.figure === 'about typical' && cell.notes.some((n) => /\bn=8\b/.test(n)) && card.found && card.chip === '',
         `${hx.vin.slice(-6)} with ${kin.length} → 8 in ${short}: harness ${scH ? `${scH.stand}, ${(scH.pct * 100).toFixed(0)}% under the median` : 'unscored'} · tile "${t2.delta}" (flat ${t2.flat}) · pick cards ${picked} · shortlist row ${cell ? `"${cell.figure}" ${JSON.stringify(cell.notes)}` : 'missing'} · card ${card.found ? (card.chip ? `chip "${card.chip}"` : 'no chip') : 'missing'}`);
    } finally { await ctx.unroute('**/data.json'); await clearStars(); }
  }
  // Served: the subject's largest trim cohort rewritten so every car's VALUE
  // is the same to the dollar and one car's is $100 less. That car is below
  // the interval's low edge — the word "under" would hold — and 0.1% under
  // the median, which every surface would print as "0% under typical". The
  // rule calls it typical; its shortlist row must say so.
  const big = [...cohorts.entries()].filter(([, xs]) => xs.length >= 9).sort((a, b) => b[1].length - a[1].length)[0];
  if (!big) skip('and a margin that rounds to nothing is not a stand', `${subject.label} has no trim cohort of nine`);
  else {
    const [keyT, xs] = big;
    const V = 100000, targetVin = xs[0].vin, otherVin = xs[1].vin;
    const planted = JSON.parse(JSON.stringify(model0));
    for (const c of planted.listings) if (xs.some((x) => x.vin === c.vin)) c.price = Math.round((c.vin === targetVin ? V - 100 : V) - (c.local ? 0 : (c.ship || 0)) - (c.miles - P.base) * P.cpm);
    const scT = scoreModel(planted).get(targetVin);
    const clearStars2 = () => page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.bk].models[subject.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(subject.q); await clearStars2(); await open(subject.q); await sortByValue(); await showAll();
      for (const v of [targetVin, otherVin]) { await page.click(`button[data-fkey="star:${v}"]`); await page.waitForTimeout(150); }
      const cell = await page.evaluate((vin) => {
        const tbl = document.getElementById('finalists-table'); if (!tbl) return null;
        const col = [...tbl.querySelectorAll('thead th')].findIndex((th) => th.querySelector(`[data-fkey="fin:${vin}"]`));
        const row = [...tbl.querySelectorAll('tbody tr')].find((tr) => /value vs typical/i.test(tr.children[0].textContent));
        if (col < 0 || !row) return null;
        return ((row.children[col].querySelector('.sc-figure') || {}).textContent || '').trim();
      }, targetVin);
      ok('and a margin that rounds to nothing is not a stand', !!scT && scT.v < scT.lo && scT.pct > 0 && scT.pct < 0.005 && scT.stand === 'typical' && cell === 'about typical',
         `${keyT} rewritten to one value, ${targetVin.slice(-6)} $100 under it: harness ${scT ? `${scT.stand}, ${(scT.pct * 100).toFixed(2)}% under, ${scT.v < scT.lo ? 'below' : 'not below'} the edge` : 'unscored'} · shortlist row "${cell || '(missing)'}"`);
    } finally { await ctx.unroute('**/data.json'); await clearStars2(); }
  }
  // The compare card's "Best value vs typical" row, two trims of the subject
  // side by side: each column prints its first car that stands under, by
  // margin, or a dash — never the first car by margin, which on today's M70
  // column is a typical car 8% under its median.
  const trimIds = Object.keys(model0.trims || {}).filter((id) => model0.listings.some((x) => x.trim_id === id));
  const byTrim = (id) => [...scores.entries()].filter(([vin]) => (model0.listings.find((x) => x.vin === vin) || {}).trim_id === id);
  const pairT = trimIds.map((id) => ({ id, label: (model0.trims[id] || {}).label || id, unders: byTrim(id).filter(([, sc]) => sc.stand === 'under').sort((a, b) => b[1].pct - a[1].pct), all: byTrim(id).sort((a, b) => b[1].pct - a[1].pct) }))
    .filter((t) => t.all.length).sort((a, b) => (a.unders.length ? 1 : 0) - (b.unders.length ? 1 : 0) || b.all.length - a.all.length).slice(0, 2);
  if (pairT.length < 2) skip('the compare card\'s winner row prints only a car that stands under', `${subject.label} has fewer than two scored trims`);
  else {
    await open(`${subject.q}&trims=${pairT.map((t) => t.id).join(',')}`);
    const cells = await page.evaluate(() => {
      const tbl = document.getElementById('compare-table'); if (!tbl) return null;
      const heads = [...tbl.querySelectorAll('thead th')].slice(1).map((th) => th.getAttribute('aria-label') || th.textContent.trim());
      const row = [...tbl.querySelectorAll('tbody tr')].find((tr) => /best value vs typical/i.test(tr.querySelector('th').textContent));
      return row ? [...row.querySelectorAll('td')].map((td, i) => ({ head: heads[i], figure: ((td.querySelector('.sc-figure') || {}).textContent || '').trim() })) : null;
    });
    const wrongT = pairT.map((t) => {
      const c = (cells || []).find((c) => c.head.includes(t.label));
      const want = t.unders.length ? `${Math.round(t.unders[0][1].pct * 100)}% under` : '—';
      return c && c.figure === want ? null : `${t.label}: cell ${c ? `"${c.figure}"` : 'missing'} · harness "${want}"${t.unders.length ? '' : ` (first by margin ${t.all[0][0].slice(-6)} is ${t.all[0][1].stand} at ${(t.all[0][1].pct * 100).toFixed(1)}%)`}`;
    }).filter(Boolean);
    ok('the compare card\'s winner row prints only a car that stands under', !!cells && wrongT.length === 0,
       wrongT.length ? wrongT.join(' | ') : pairT.map((t) => `${t.label}: ${t.unders.length ? `${Math.round(t.unders[0][1].pct * 100)}% under` : `— (first by margin is ${t.all[0][1].stand})`}`).join(' · '));
  }
  // Served: the subject's largest trim cohort cut to nine cars, two of them
  // rewritten to sit ON the low edge with a 70% margin — typical by the rule,
  // and first in margin order — one drivable and one not, so both pick lists
  // meet a typical car before any real pick. A walk that stopped there would
  // draw no pick card at all; the rule skips, and the best car that stands
  // under still gets its card.
  if (!big) skip('and the pick walk skips a typical car with a big margin rather than stopping at it', `${subject.label} has no trim cohort of nine`);
  else {
    const [keyT, xs] = big;
    const keep9 = xs.slice(0, 9), dropped = new Set(xs.slice(9).map((x) => x.vin));
    const planted = JSON.parse(JSON.stringify(model0));
    planted.listings = planted.listings.filter((c) => !dropped.has(c.vin));
    const H = 150000, L = 40000;
    // miles pinned to the baseline so each value is its price plus shipping
    // to the dollar — a half-cent of mileage adjustment would put one edge
    // car fifty cents below the other and make it genuinely under
    keep9.forEach((x, i) => { const c = planted.listings.find((c) => c.vin === x.vin); c.local = i === 0; c.state = i === 0 ? ((SHEET.buyer || {}).states || ['IL'])[0] : 'CA'; c.ship = i === 0 ? 0 : 1000; c.miles = P.base; c.price = (i < 2 ? L : H) - (c.local ? 0 : c.ship); });
    const sc2 = scoreModel(planted);
    const edge = keep9.slice(0, 2).map((x) => sc2.get(x.vin));
    const unders = [...sc2.entries()].filter(([, s]) => s.stand === 'under').sort((a, b) => b[1].pct - a[1].pct);
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.bk].models[subject.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(subject.q);
      const picked = await page.locator('#takeaway [data-fkey^="pick:"]').evaluateAll((as) => [...new Set(as.map((a) => a.getAttribute('data-fkey').split(':')[1]))]);
      const edgeVins = keep9.slice(0, 2).map((x) => x.vin);
      const bestUnder = unders[0] ? unders[0][0] : null;
      ok('and the pick walk skips a typical car with a big margin rather than stopping at it',
         edge.every((s) => s && s.stand === 'typical' && s.pct > 0.5) && !!bestUnder && (unders[0][1].pct < edge[0].pct) && picked.includes(bestUnder) && !edgeVins.some((v) => picked.includes(v)),
         `${keyT} cut to nine, ${edgeVins.map((v) => v.slice(-6)).join('/')} on the edge at ${edge[0] ? (edge[0].pct * 100).toFixed(0) : '?'}% (${edge.map((s) => (s ? s.stand : 'unscored')).join('/')}); best under ${bestUnder ? bestUnder.slice(-6) : 'none'} at ${unders[0] ? (unders[0][1].pct * 100).toFixed(0) : '?'}% · pick cards ${JSON.stringify(picked.map((v) => v.slice(-6)))}`);
    } finally { await ctx.unroute('**/data.json'); }
  }
});

// --- since your visit ---------------------------------------------------------
// The page remembers the newest data day the reader has seen and, on the next
// front-page load with newer data, says what changed since — floors from the
// hero's own ledger, cars first seen since, cars asking less than they did
// then, and departures worded by what the sheet knows: "gone" only for exact
// rows, "stopped being seen" otherwise. Recomputed here from data.json for a
// remembered day planted three shared fetch days back. Then the silences: a
// first visit, the same data day, a day before the departures window, and a
// slot already claimed by a dead-link notice.
await step('since your visit', async () => {
  plan('the sentence counts what changed since the remembered day, by the sheet\'s own rules',
       'a reload on the same data day repeats it rather than losing it',
       'a first visit says nothing',
       'and a remembered day before the record',
       'and a slot a dead link already claims',
       'a car the watchlist dropped is not the market losing it');
  const buyer = SHEET.buyer || {}, f = buyer.fees || null, P0 = buyer.picks || {};
  const want = new Set(buyer.shopping || []);
  if (!want.size) return skipRest('this sheet names no shopped trims');
  const through = SHEET.data_through;
  const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
  const rules = (x) => x.miles != null && x.miles <= (P0.max_miles || 50000) && !(P0.exclude_accidents !== false && x.accidents > 0) && !(P0.exclude_rental !== false && RENTAL.test(x.usage || ''));
  const totalAt = (x, price, local) => Math.round(price * (1 + ((f || {}).tax_rate || 0)) + ((f || {}).doc_fee || 0) + ((f || {}).title || 0) + ((f || {}).registration || 0) + ((f || {}).ev_surcharge || 0) + (local ? 0 : (x.ship || 0)));
  const localOn = (x, day) => { const h = x.local_hist; if (!h || !h.length) return !!x.local; let v = h[0][1]; for (const st of h) { if (st[0] <= day) v = st[1]; else break; } return !!v; };
  const money = (v) => '$' + Math.round(v).toLocaleString('en-US');
  const fmtDate = (d) => new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  // the floor on a fetch day: cheapest rule-fit car of the shopped trims, all in, seen on its trim's latest fetch on or before the day; five cars or nothing
  const ledgerOf = (m) => {
    const tids = Object.keys(m.trims || {}).filter((id) => want.has(id)), fd = m.fetch_days || {};
    const days = [...new Set(tids.flatMap((t) => fd[t] || []))].sort();
    const cars = new Map();
    for (const x of (m.listings || []).concat(m.gone || [])) {
      const k = String(x.vin || '').toUpperCase();
      if (!k || cars.has(k) || !tids.includes(x.trim_id) || !rules(x)) continue;
      const seen = new Map((x.series || []).filter((pt) => pt[1]).map((pt) => [pt[0], pt[1]]));
      if (seen.size) cars.set(k, { x, seen });
    }
    const latest = (tid, day) => { let b = null; for (const d of (fd[tid] || [])) { if (d <= day) b = d; else break; } return b; };
    const drawn = [];
    for (const day of days) {
      let n = 0, floor = null;
      for (const { x, seen } of cars.values()) { const lf = latest(x.trim_id, day); if (!lf || !seen.has(lf)) continue; n++; const v = totalAt(x, seen.get(lf), localOn(x, day)); if (!floor || v < floor.v) floor = { v }; }
      if (n >= 5 && floor) drawn.push({ day, v: floor.v });
    }
    return drawn.length < 3 ? null : drawn;
  };
  const models = [];
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {}))
      if (Object.keys((m || {}).trims || {}).some((id) => want.has(id))) models.push({ bk, mk, m, label: m.label || mk });
  const sharedDays = [...new Set(models.flatMap((o) => Object.entries(o.m.fetch_days || {}).filter(([id]) => want.has(id)).flatMap(([, ds]) => ds)))].sort();
  const since = sharedDays.filter((d) => d < through).slice(-3)[0];
  if (!since) return skipRest('the record holds fewer than three fetch days before the newest');
  const sentence = () => {
    const bits = [];
    for (const o of models) {
      const tids = new Set(Object.keys(o.m.trims || {}).filter((id) => want.has(id)));
      const days = [...new Set([...tids].flatMap((t) => (o.m.fetch_days || {})[t] || []))].sort();
      if (!days.length || since < days[0]) continue;
      const live = (o.m.listings || []).filter((x) => tids.has(x.trim_id));
      const priceOn = (x) => { let v = null; for (const pt of (x.series || [])) { if (pt[0] <= since && pt[1]) v = pt[1]; else if (pt[0] > since) break; } return v; };
      const fresh = live.filter((x) => x.first_seen && x.first_seen > since).length;
      const less = live.filter((x) => { const then = priceOn(x); return then != null && x.price != null && x.price < then; }).length;
      const liveVins = new Set((o.m.listings || []).map((x) => x.vin));
      const gone = (o.m.gone || []).filter((g) => tids.has(g.trim_id) && g.last_seen >= since && !liveVins.has(g.vin));
      // Same split as the page: a watchlist edit is not the market moving,
      // and `unseen` is a remainder that would otherwise absorb it.
      const moved = gone.filter((g) => g.likely === 'out of scope').length;
      const real = gone.filter((g) => g.likely !== 'out of scope');
      const exact = real.filter((g) => g.exact && g.likely === 'delisted').length, unseen = real.length - exact;
      const ledger = ledgerOf(o.m);
      const then = ledger && ledger.filter((d) => d.day <= since).pop(), now = ledger && ledger[ledger.length - 1];
      const floor = then && now ? (then.v === now.v ? `floor unchanged at ${money(now.v)}` : `floor ${money(then.v)} → ${money(now.v)}`) : '';
      const left = !real.length ? 'none gone' : exact && unseen ? `${exact} gone, ${unseen} more stopped being seen` : exact ? `${exact} gone` : `${unseen} stopped being seen, none confirmed gone`;
      const dropped = moved ? `${moved} dropped from the watchlist` : '';
      bits.push(`${o.label} — ${[floor, `${fresh} new`, `${less} ask less than then`, left, dropped].filter(Boolean).join(', ')}`);
    }
    return bits.length ? `Since you last saw data through ${fmtDate(since)}: ${bits.join('; ')}.` : '';
  };
  const wantTxt = sentence();
  const plant = (obj) => page.evaluate((o) => { try { if (o === null) localStorage.removeItem('spicycar.seen'); else localStorage.setItem('spicycar.seen', JSON.stringify(o)); } catch { /* private mode */ } }, obj);
  const read = () => page.evaluate(() => { const n = document.getElementById('notice'); const p = n && n.querySelector('p[data-since]'); return { hidden: !n || n.hidden, text: p ? p.textContent.replace(/\s+/g, ' ').trim() : '', since: p ? p.getAttribute('data-since') : null, other: n && !n.hidden && !p ? n.textContent.replace(/\s+/g, ' ').trim().slice(0, 80) : '' }; });
  await open('');
  await plant({ through: since, since: null });   // the reader last saw data through `since`
  await open('');
  const r1 = await read();
  ok('the sentence counts what changed since the remembered day, by the sheet\'s own rules', !!wantTxt && r1.text === wantTxt && r1.since === since,
     r1.text === wantTxt ? `"${r1.text}"` : `page: "${r1.text || (r1.hidden ? '(slot hidden)' : r1.other)}" · sheet: "${wantTxt}"`);
  // The load above advanced the remembered day to today's data; the day it
  // replaced is kept as `since`, so the same sentence comes back on a reload.
  await open('');
  const r3 = await read();
  const stored = await page.evaluate(() => { try { return JSON.parse(localStorage.getItem('spicycar.seen') || 'null'); } catch { return null; } });
  ok('a reload on the same data day repeats it rather than losing it', r3.text === wantTxt && stored && stored.through === through && stored.since === since,
     `remembered ${JSON.stringify(stored)}; reload says ${r3.text === wantTxt ? 'the same sentence' : `"${r3.text || (r3.hidden ? '(slot hidden)' : r3.other)}"`}`);
  await plant(null); await open('');
  const r2 = await read();
  ok('a first visit says nothing', r2.hidden && !r2.text, r2.hidden ? 'slot hidden' : `"${r2.text || r2.other}"`);
  // Two ways a remembered day can predate the record: before any shopped
  // trim's first fetch day, and — served, since on this sheet the departures
  // window opens before the record does — before the day departures are
  // counted from, when "gone since" would count cars the sheet never saw leave.
  const early = '2000-01-01';
  await plant({ through: early, since: null }); await open('');
  const r4 = await read();
  await plant({ through: since, since: null });
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    sheet.departures_from = through;   // departures counted only from today: the remembered day is before that
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  let r4b;
  try { await open(''); r4b = await read(); } finally { await ctx.unroute('**/data.json'); }
  ok('and a remembered day before the record', r4.hidden && !r4.text && r4b.hidden && !r4b.text,
     `${early}: ${r4.hidden ? 'slot hidden' : `"${r4.text || r4.other}"`} · departures counted from ${through} with ${since} remembered: ${r4b.hidden ? 'slot hidden' : `"${r4b.text || r4b.other}"`}`);
  await plant({ through: since, since: null }); await open('?brand=no-such-brand');
  const r5 = await read();
  ok('and a slot a dead link already claims', !r5.hidden && !r5.text && /not tracked|no longer|not on/i.test(r5.other),
     r5.text ? `the sentence won over the notice: "${r5.text.slice(0, 60)}"` : `notice: "${r5.other}"`);
  // Served, both ways round: this sentence's "stopped being seen" count is a
  // REMAINDER, so a car the watchlist dropped would have been reported as the
  // market losing it — the same collapse the departures card had, one screen
  // up. One row, labelled twice, is what tells the two apart; asserting only
  // the 'out of scope' side would pass on a page that printed the watchlist
  // clause for every absence.
  const target = models.map((o) => ({ o, g: (o.m.gone || []).find((g) => want.has(g.trim_id) && g.last_seen >= since
      && !(o.m.listings || []).some((x) => x.vin === g.vin)) })).find((x) => x.g);
  if (!target) skip('a car the watchlist dropped is not the market losing it', 'no shopped model has a departure inside the window');
  else {
    const say = async (word) => {
      await ctx.route('**/data.json', async (route) => {
        const r = await route.fetch(); const sheet = JSON.parse(await r.text());
        const mm = sheet.brands[target.o.bk].models[target.o.mk];
        const row = mm.gone.find((g) => g.vin === target.g.vin);
        row.likely = word; if (word === 'delisted') row.exact = true;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      try { await plant({ through: since, since: null }); await open(''); return (await read()).text; }
      finally { await ctx.unroute('**/data.json'); }
    };
    const asGone = await say('delisted');
    const asMoved = await say('out of scope');
    const nOf = (txt, re) => { const m = txt.match(re); return m ? Number(m[1]) : null; };
    const goneBefore = nOf(asGone, new RegExp(target.o.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '[^;]*?(\\d+) gone'));
    const goneAfter = nOf(asMoved, new RegExp(target.o.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '[^;]*?(\\d+) gone'));
    ok('a car the watchlist dropped is not the market losing it',
       !/dropped from the watchlist/.test(asGone) && /1 dropped from the watchlist/.test(asMoved)
         && goneBefore !== null && goneAfter === goneBefore - 1,
       `as delisted: "${asGone.slice(0, 150)}" · as out of scope: "${asMoved.slice(0, 170)}"`);
  }
  await plant(null);
});

// --- a VIN in hand -----------------------------------------------------------
// At a dealer the buyer has seventeen characters on a windshield and a page
// that could only be walked by model. ?vin= (or the VIN field, which goes
// through the same address bar) resolves a full VIN or its tail of six or
// more over live rows then departed ones, and is honoured only when exactly
// one car answers: its model page opens with the car in the notice slot and
// its row landed on. Two answers say so and stop; none goes through the
// dead-link notice; the address bar is cleaned either way. Every case below
// is picked out of data.json, and __proto__ goes in as a VIN.
await step('a VIN in hand', async () => {
  plan('a full VIN opens its car: the model page, the card, the row, the address bar',
       'and six characters of it do the same',
       'a tail that fits two cars says how many and opens nothing',
       'a VIN nobody has seen goes through the dead-link notice and is not re-shared',
       'a departed car\'s VIN opens the card in its gone form',
       'and the VIN field is the same path as the link',
       'five characters are refused even when they would fit one car',
       'and leaving the model page leaves the car behind');
  const all = [];
  for (const [bk, b] of Object.entries(SHEET.brands || {}))
    for (const [mk, m] of Object.entries((b || {}).models || {})) {
      for (const x of (m.listings || [])) all.push({ gone: false, bk, mk, label: m.label || mk, x });
      for (const g of (m.gone || [])) all.push({ gone: true, bk, mk, label: m.label || mk, x: g });
    }
  const liveVins = new Set(all.filter((o) => !o.gone).map((o) => String(o.x.vin).toUpperCase()));
  const byVin = new Map(); for (const o of all) { const v = String(o.x.vin).toUpperCase(); if (!byVin.has(v)) byVin.set(v, o); }
  const live = all.find((o) => !o.gone && o.x.price != null && o.x.vin);
  if (!live) return skipRest('no live priced car on the sheet');
  const vin = String(live.x.vin).toUpperCase();
  const tailOf = (n) => vin.slice(-n);
  const count = (tail) => [...byVin.keys()].filter((v) => v.endsWith(tail)).length;
  const read = () => page.evaluate(() => {
    const n = document.getElementById('notice');
    const card = n && n.querySelector('[data-vin-in-hand]');
    const focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest('tr') : null;
    const fk = focused && focused.querySelector('[data-fkey^="star:"]');
    return { h1: (document.getElementById('h1') || {}).textContent || '', url: location.search,
             card: card ? card.getAttribute('data-vin-in-hand') : null, cardText: card ? card.textContent.replace(/\s+/g, ' ').trim() : '',
             notice: n && !n.hidden && !card ? n.textContent.replace(/\s+/g, ' ').trim() : '',
             landed: fk ? fk.getAttribute('data-fkey').split(':')[1] : null };
  });
  await open('?vin=' + vin);
  const r1 = await read();
  ok('a full VIN opens its car: the model page, the card, the row, the address bar',
     r1.h1.includes(live.label) && r1.card === vin && r1.cardText.includes(vin) && r1.landed === vin && r1.url.includes('vin=' + vin) && r1.url.includes('m=' + live.mk),
     `${vin}: h1 "${r1.h1}" · card ${r1.card} · landed ${r1.landed} · url "${r1.url}"`);
  // six characters, if they are the car's alone
  const six = tailOf(6);
  if (count(six) !== 1) skip('and six characters of it do the same', `${six} fits ${count(six)} cars`);
  else {
    await open('?vin=' + six);
    const r2 = await read();
    ok('and six characters of it do the same', r2.card === vin && r2.landed === vin && r2.url.includes('vin=' + vin), `${six} → card ${r2.card}, landed ${r2.landed}, url "${r2.url}"`);
  }
  // a tail two cars share: the shortest length at which this VIN is not alone
  let amb = null;
  for (let n = 6; n <= 16; n++) { const t = tailOf(n); if (count(t) > 1) { amb = t; break; } }
  if (!amb) { const c = [...byVin.keys()].map((v) => v.slice(-6)).find((t, i, a) => a.indexOf(t) !== i); amb = c || null; }
  // …served when the sheet has none: another live car's VIN is rewritten to
  // end in this car's six, so the tail fits exactly two.
  const twin = amb ? null : all.find((o) => !o.gone && o.x.vin && String(o.x.vin).toUpperCase() !== vin && (o.bk !== live.bk || o.mk !== live.mk));
  if (!amb && twin) await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    const row = sheet.brands[twin.bk].models[twin.mk].listings.find((x) => x.vin === twin.x.vin);
    row.vin = row.vin.slice(0, 11) + six;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  const tail = amb || (twin ? six : null), fits = amb ? count(amb) : 2;
  if (!tail) skip('a tail that fits two cars says how many and opens nothing', 'no second car to plant');
  else {
    try {
      await open('?vin=' + tail);
      const r3 = await read();
      ok('a tail that fits two cars says how many and opens nothing', !r3.card && r3.h1 === 'The watchlist' && new RegExp(`${fits} cars on the sheet end in`).test(r3.notice) && !r3.url.includes('vin='),
         `${tail} fits ${fits}${amb ? '' : ' (one served)'}: h1 "${r3.h1}" · notice "${r3.notice.slice(0, 90)}" · url "${r3.url}"`);
    } finally { if (!amb && twin) await ctx.unroute('**/data.json'); }
  }
  await open('?vin=__proto__');
  const r4 = await read();
  ok('a VIN nobody has seen goes through the dead-link notice and is not re-shared', !r4.card && r4.h1 === 'The watchlist' && /no car on the sheet has that VIN/i.test(r4.notice) && r4.url === '',
     `__proto__: h1 "${r4.h1}" · notice "${r4.notice.slice(0, 80)}" · url "${r4.url}"`);
  const departed = all.find((o) => o.gone && o.x.vin && !liveVins.has(String(o.x.vin).toUpperCase()) && o.x.last_price != null);
  if (!departed) skip('a departed car\'s VIN opens the card in its gone form', 'no departed car on the sheet is absent from the live rows');
  else {
    const dv = String(departed.x.vin).toUpperCase();
    await open('?vin=' + dv);
    const r5 = await read();
    ok('a departed car\'s VIN opens the card in its gone form', r5.card === dv && r5.h1.includes(departed.label) && /left the model's rows|last seen/i.test(r5.cardText) && !r5.landed,
       `${dv}: h1 "${r5.h1}" · card ${r5.card} · "${r5.cardText.slice(0, 100)}"`);
  }
  // the field: type the tail, press Enter
  await open('');
  await page.fill('#f-vin', six.toLowerCase());
  await page.press('#f-vin', 'Enter');
  await page.waitForTimeout(500);
  const r6 = await read();
  ok('and the VIN field is the same path as the link', r6.card === vin && r6.landed === vin && r6.url.includes('vin=' + vin) && (await page.inputValue('#f-vin')) === '',
     `typed "${six.toLowerCase()}": card ${r6.card} · landed ${r6.landed} · url "${r6.url}"`);
  // the last four are a plate, not a car; five is not looked up either, even
  // when the sheet would answer with one car
  const five = tailOf(5);
  await open('?vin=' + five);
  const r7 = await read();
  ok('five characters are refused even when they would fit one car', !r7.card && r7.h1 === 'The watchlist' && /six characters at least/.test(r7.notice) && r7.url === '',
     `${five} fits ${count(five)}: h1 "${r7.h1}" · notice "${r7.notice.slice(0, 80)}"`);
  // leaving: a model chip on the front page, or here the model tab, opens
  // another model and the car in hand does not follow
  await open('?vin=' + vin);
  // by the model tab, the page's own way out — a fresh link would reset the
  // car by itself and say nothing about the tab's handler
  const other = WATCHED.find((w) => w.cars && w.bk === live.bk && w.mk !== live.mk);
  const tab = other && page.locator(`button[data-fkey="tab-model:${other.mk}"]`);
  if (!other || !(await tab.count())) skip('and leaving the model page leaves the car behind', 'no second model of the brand to tab to');
  else {
    await tab.click(); await page.waitForTimeout(500);
    const r8 = await read();
    ok('and leaving the model page leaves the car behind', !r8.card && r8.h1.includes(other.label) && !r8.url.includes('vin='), `${other.label} by its tab: card ${r8.card} · url "${r8.url}"`);
  }
});

// --- arrivals, not reach --------------------------------------------------------
// "N new" on the movement tile is new TO THE TRACKER. A car carrying a listing
// date a fortnight or more before the day the tracker first saw it was on the
// market all along and only now entered a fetch window — the API serves forty
// cars a query, sorted. The tile now says how many of its new cars are that,
// and the comparison's movement row says the same. Recomputed from the sheet;
// then a served sheet dates every new car today (no clause) and one car at
// exactly thirteen days (not counted: fourteen is the line).
await step('arrivals, not reach', async () => {
  plan('the movement tile says how many of its new cars were listed a fortnight before the tracker saw them',
       'and the comparison\'s movement row says the same',
       'and a sheet whose new cars were all listed today gets no clause',
       'and thirteen days is not a fortnight');
  const dt = SHEET.data_through;
  const dayDiff = (a, b) => (Date.parse(String(b).slice(0, 10) + 'T00:00:00Z') - Date.parse(String(a).slice(0, 10) + 'T00:00:00Z')) / 86400000;
  const isNewOn = (x) => x.first_seen ? String(x.first_seen).slice(0, 10) === dt : x.days_tracked === 1;
  const reach = (x) => !!x.listed_since && !!x.first_seen && dayDiff(x.listed_since, x.first_seen) >= 14;
  const counts = (m) => { const fresh = (m.listings || []).filter(isNewOn); return { fresh: fresh.length, reach: fresh.filter(reach).length }; };
  const subject = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] })).map((o) => ({ ...o, c: counts(o.m) }))
    .filter((o) => (o.m.daily || []).length >= 2 && o.c.reach > 0).sort((a, b) => b.c.reach - a.c.reach)[0];
  if (!subject) return skipRest('no model on the watchlist has a new car today that was listed a fortnight before the tracker saw it');
  const clause = (c) => c.reach ? `(${c.reach} listed 14+ days before the tracker saw ${c.reach === 1 ? 'it' : 'them'})` : '';
  const tileTxt = async () => ((await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent.replace(/\s+/g, ' ').trim()).find((t) => /since the previous/i.test(t)))) || '');
  await open(subject.w.q);
  const t1 = await tileTxt();
  ok('the movement tile says how many of its new cars were listed a fortnight before the tracker saw them', t1.includes(`${subject.c.fresh} new`) && t1.includes(clause(subject.c)),
     `${subject.w.label}: tile "${(t1.match(/\d+ new[^·]*/) || [t1.slice(0, 80)])[0].trim()}" · sheet: ${subject.c.fresh} new, ${subject.c.reach} reach`);
  // the comparison's movement row, over this model and one more
  const other = WATCHED.find((w) => w.cars && !(w.bk === subject.w.bk && w.mk === subject.w.mk));
  if (!other) skip('and the comparison\'s movement row says the same', 'no second model to compare');
  else {
    await open(`?models=${subject.w.slug},${other.slug}`);
    const row = await page.evaluate((label) => {
      const tbl = document.getElementById('compare-table'); if (!tbl) return null;
      const col = [...tbl.querySelectorAll('thead th')].findIndex((th) => th.textContent.includes(label));
      const tr = [...tbl.querySelectorAll('tbody tr')].find((r) => /since the previous snapshot/i.test(r.children[0].textContent));
      return col >= 0 && tr ? tr.children[col].textContent.replace(/\s+/g, ' ').trim() : null;
    }, subject.w.label);
    ok('and the comparison\'s movement row says the same', !!row && row.includes(`${subject.c.fresh} new (${subject.c.reach} reach, not arrival)`), `${subject.w.label} column: "${row}"`);
  }
  // Served: every new car listed today — no clause; then one of them listed
  // exactly thirteen days before it was first seen — still no clause.
  const serve = async (edit, label, detail) => {
    const planted = JSON.parse(JSON.stringify(subject.m)); edit(planted);
    const c = counts(planted);
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[subject.w.bk].models[subject.w.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(subject.w.q);
      const t = await tileTxt();
      ok(label, c.reach === 0 && t.includes(`${c.fresh} new`) && !/listed 14\+ days/.test(t), `${detail}: tile "${(t.match(/\d+ new[^·]*/) || [t.slice(0, 80)])[0].trim()}" · sheet ${c.fresh} new, ${c.reach} reach`);
    } finally { await ctx.unroute('**/data.json'); }
  };
  const thirteen = new Date(Date.parse(dt + 'T00:00:00Z') - 13 * 86400000).toISOString().slice(0, 10);
  await serve((pm) => { for (const x of pm.listings) if (isNewOn(x)) { x.listed_since = dt; x.days_listed = 0; } }, 'and a sheet whose new cars were all listed today gets no clause', `${subject.w.label} with every new car listed ${dt}`);
  await serve((pm) => { let first = true; for (const x of pm.listings) if (isNewOn(x)) { x.listed_since = first ? thirteen : dt; x.days_listed = first ? 13 : 0; first = false; } }, 'and thirteen days is not a fortnight', `${subject.w.label} with one new car listed ${thirteen}`);
});

// --- the decision card folds its evidence on a phone ------------------------
// The card is the first screen on a phone, and each shopped tile grew ten
// lines of evidence under four of headline until the card ran to two screens
// and the gap line sat a screen and a half down. On a phone the evidence
// folds behind one disclosure per tile — closed, counted, the way the
// nationwide picks fold — and the headline, the typical stand and the link
// stay out; the gap keeps its first sentence and folds the record behind it.
// A desktop has the room and folds nothing.
await step('the decision card folds its evidence on a phone', async () => {
  plan('each shopped tile keeps its headline, its stand and its link out of a closed fold that holds the evidence',
       'and the card fits one phone screen',
       'and the fold opens to the evidence',
       'and the gap line keeps the decision out and folds the record',
       'and a desktop folds nothing');
  const want = new Set(((SHEET.buyer || {}).shopping) || []);
  if (!want.size) return skipRest('this sheet names no shopped trims');
  await page.setViewportSize({ width: 390, height: 844 });
  try {
    await open('');
    const read = () => page.evaluate(() => {
      const tiles = [...document.querySelectorAll('#hero-cars .sc-tile')].filter((t) => t.querySelector('[data-fkey^="hero:"]')).map((t) => {
        const d = t.querySelectorAll('details.hero-fold');
        const fold = d[0];
        return { folds: d.length, open: fold ? fold.open : null, summary: fold ? fold.querySelector('summary').textContent.trim() : '',
                 inside: fold ? fold.querySelectorAll('.sc-tile__sub, .sc-tile__spark').length : 0,
                 standOut: !!t.querySelector('.sc-delta') && !t.querySelector('details .sc-delta'),
                 linkOut: !t.querySelector('details [data-fkey^="hero:"]'),
                 headline: [...t.children].filter((c) => c.classList.contains('sc-tile__sub')).length };
      });
      const gap = document.getElementById('hero-gap');
      const gfold = gap && gap.querySelector('details.hero-fold');
      const card = document.getElementById('hero-card');
      return { tiles, cardH: Math.round(card.getBoundingClientRect().height), inner: window.innerHeight,
               gapHead: gap ? (gap.childNodes[0] && gap.childNodes[0].nodeType === 3 ? gap.childNodes[0].textContent : '') : '',
               gapFold: gfold ? { open: gfold.open, summary: gfold.querySelector('summary').textContent.trim(), text: gfold.textContent.replace(/\s+/g, ' ').trim() } : null };
    });
    const r = await read();
    const t = r.tiles;
    if (!t.length) return skipRest('no shopped tile carries a car today');
    const bad = t.filter((x) => x.folds !== 1 || x.open !== false || !/^the evidence · \d+ lines?$/.test(x.summary) || x.inside < 2 || !x.standOut || !x.linkOut || x.headline < 3);
    ok('each shopped tile keeps its headline, its stand and its link out of a closed fold that holds the evidence', bad.length === 0,
       bad.length ? JSON.stringify(bad[0]) : t.map((x) => `"${x.summary}" over ${x.inside} evidence nodes, ${x.headline} headline lines out`).join(' · '));
    ok('and the card fits one phone screen', r.cardH <= r.inner + 60, `card ${r.cardH}px on an ${r.inner}px screen (it ran to 1,754px unfolded)`);
    const before = r.cardH;
    await page.locator('#hero-cars details.hero-fold summary').first().click();
    await page.waitForTimeout(200);
    const r2 = await read();
    ok('and the fold opens to the evidence', r2.tiles[0].open === true && r2.cardH > before + 80, `open: ${r2.tiles[0].open}, card ${before}px → ${r2.cardH}px`);
    const gapOk = /costs \$[\d,]+ more than .* on today's cheapest of each/.test(r.gapHead) && !!r.gapFold && r.gapFold.open === false
      && /^over the record, and what the money buys$/.test(r.gapFold.summary) && /Over the \d+ fetch days|For that, |buys \d+ of|Shipping|Out-the-door/.test(r.gapFold.text);
    ok('and the gap line keeps the decision out and folds the record', gapOk, `head "${r.gapHead.slice(0, 90)}" · fold ${r.gapFold ? `"${r.gapFold.summary}" over ${r.gapFold.text.length} characters` : 'missing'}`);
  } finally {
    await page.setViewportSize({ width: 1280, height: 1000 });
  }
  await open('');
  const desk = await page.evaluate(() => ({ folds: document.querySelectorAll('#hero-card details.hero-fold').length, subs: document.querySelectorAll('#hero-cars .sc-tile .sc-tile__sub').length }));
  ok('and a desktop folds nothing', desk.folds === 0 && desk.subs > 6, `${desk.folds} folds, ${desk.subs} evidence lines in the open`);
});

// --- a departure from one query is not a departure from the market ----------
// A CPO watch's market is "certified cars under the mileage cap", so a car
// that loses its badge leaves that watch for real. The page announced it as
// "GONE — the listing ended" while the same VIN sat live in the listings
// table of the same page, asking $51,476 as an xDrive40. The builder now
// marks such a row with the trim it is listed under today; here the counts
// and the words are recomputed from the sheet, and then a served sheet turns
// a real confirmed departure into a still-listed one, which must leave the
// movement tile's count and say where the car went instead.
await step('a departure from one query is not a departure from the market', async () => {
  plan('the gone card counts a car still listed apart from the departures',
       'and the row says which watch it left and what it asks now',
       'the movement tile counts only cars that left the market',
       'and a served departure that is still listed leaves that count and changes its words');
  const money = (v) => '$' + Math.round(v).toLocaleString('en-US');
  const still = (g) => (g.still_listed && (g.still_listed.trim || g.still_listed.trim_id) ? g.still_listed : null);
  const subject = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] }))
    .map((o) => ({ ...o, gone: (o.m.gone || []), n: (o.m.gone || []).filter(still).length }))
    .filter((o) => o.n > 0).sort((a, b) => b.n - a.n)[0];
  const anyModel = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] })).find((o) => (o.m.gone || []).some((g) => g.likely === 'delisted') && (o.m.daily || []).length >= 2);
  // the page's own de-duplication: one row per VIN, first wins
  const rowsOf = (m) => { const seen = new Set(), out = []; for (const g of (m.gone || [])) { const k = String(g.vin || '').toUpperCase(); if (k && seen.has(k)) continue; if (k) seen.add(k); out.push(g); } return out; };
  const hintOf = (m) => {
    const gone = rowsOf(m);
    const nDel = gone.filter((g) => g.likely === 'delisted' && !still(g)).length;
    const nStill = gone.filter(still).length;
    const nOow = gone.filter((g) => !still(g) && g.likely === 'out of window').length;
    // 'out of scope' is the fourth word — the watchlist moved, not the car.
    // Counted here rather than folded into the remainder for the same reason
    // the page counts it: a remainder silently renames every word it does
    // not know.
    const nOos = gone.filter((g) => !still(g) && g.likely === 'out of scope').length;
    const nNc = gone.length - nDel - nStill - nOow - nOos;
    return [`${nDel} confirmed departure${nDel === 1 ? '' : 's'} (the listing ended — not necessarily a sale)`,
            nStill ? `${nStill} left a watch while the ${nStill === 1 ? 'car is' : 'cars are'} still listed above` : null,
            nOow ? `${nOow} probably still for sale, just outside the fetch window` : null,
            nOos ? `${nOos} dropped by a watchlist change, not by leaving the market` : null,
            nNc ? `${nNc} not checked on the day ${nNc === 1 ? 'it' : 'they'} vanished` : null].filter(Boolean).join(' · ');
  };
  const goneCount = (m) => { const daily = m.daily || []; if (daily.length < 2) return 0; const prev = daily[daily.length - 2].date;
    return rowsOf(m).filter((g) => g.likely === 'delisted' && !still(g) && (g.prev_fetch_day ? g.last_seen === g.prev_fetch_day : g.last_seen === prev)).length; };
  const read = async () => page.evaluate(() => {
    const hint = (document.getElementById('gone-hint') || {}).textContent || '';
    const rows = [...document.querySelectorAll('#gone-table tbody tr')].map((r) => r.textContent.replace(/\s+/g, ' ').trim());
    const tile = [...document.querySelectorAll('#kpis .sc-tile')].map((t) => t.textContent.replace(/\s+/g, ' ')).find((t) => /since the previous/i.test(t)) || '';
    return { hint: hint.replace(/\s+/g, ' ').trim(), rows, tile };
  });
  const showAllGone = async () => { if (await page.locator('#gone-more button').count()) { await page.click('#gone-more button'); await page.waitForTimeout(300); } };
  if (!subject) {
    skip('the gone card counts a car still listed apart from the departures', 'no model on the sheet has a car that left a watch while still listed');
    skip('and the row says which watch it left and what it asks now', 'no such car');
    // A sheet with no forwarded listings still has a movement count to check.
    // Keep this assertion active; only the two forwarding-specific rows skip.
    if (anyModel) {
      await open(anyModel.w.q);
      const r = await read();
      ok('the movement tile counts only cars that left the market', new RegExp(`${goneCount(anyModel.m)} gone`).test(r.tile),
         `tile "${(r.tile.match(/\d+ gone/) || ['(no gone clause)'])[0]}" · sheet ${goneCount(anyModel.m)} (no cars still listed)`);
    } else skip('the movement tile counts only cars that left the market', 'no model holds a departure and two day rows');
  } else {
    await open(subject.w.q); await showAllGone();
    const r = await read();
    ok('the gone card counts a car still listed apart from the departures', r.hint.includes(hintOf(subject.m)),
       `page: "${r.hint.slice(-160)}" · sheet: "${hintOf(subject.m).slice(-160)}"`);
    const g0 = rowsOf(subject.m).filter(still)[0], st = still(g0);
    const wantNote = `left the ${g0.trim_label || 'watch'} — the same VIN is listed as ${st.trim || 'another trim'}` + (st.price != null ? ` at ${money(st.price)}` : '') + (st.cpo ? ', still certified' : ', not certified');
    const row = r.rows.find((t) => t.includes(String(g0.vin)));
    ok('and the row says which watch it left and what it asks now', !!row && row.includes(wantNote) && !/GONE|the listing ended/.test(row),
       row ? `"${row.slice(-150)}" · wanted "${wantNote}"` : `no row for ${String(g0.vin).slice(-6)} among ${r.rows.length}`);
    ok('the movement tile counts only cars that left the market', new RegExp(`${goneCount(subject.m)} gone`).test(r.tile),
       `tile "${(r.tile.match(/\d+ gone/) || ['(no gone clause)'])[0]}" · sheet ${goneCount(subject.m)} (${goneCount(subject.m) + subject.n} counting the cars still listed)`);
  }
  // Served: a real confirmed departure of another model is given a forwarding
  // address, so the count must drop by one and the row must stop saying GONE.
  if (!anyModel) skip('and a served departure that is still listed leaves that count and changes its words', 'no model holds a confirmed departure and two day rows');
  else {
    const planted = JSON.parse(JSON.stringify(anyModel.m));
    const victim = rowsOf(planted).find((g) => g.likely === 'delisted' && !still(g) && (g.prev_fetch_day ? g.last_seen === g.prev_fetch_day : true));
    if (!victim) skip('and a served departure that is still listed leaves that count and changes its words', 'no countable departure to re-label');
    else {
      const before = goneCount(planted);
      planted.gone.find((g) => g.vin === victim.vin).still_listed = { trim_id: 'planted-trim', trim: 'eDrive40', price: 12345, cpo: false };
      const after = goneCount(planted);
      await ctx.route('**/data.json', async (route) => {
        const r2 = await route.fetch(); const sheet = JSON.parse(await r2.text());
        sheet.brands[anyModel.w.bk].models[anyModel.w.mk] = planted;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      try {
        await open(anyModel.w.q); await showAllGone();
        const r2 = await read();
        const row = r2.rows.find((t) => t.includes(String(victim.vin)));
        ok('and a served departure that is still listed leaves that count and changes its words',
           after === before - 1 && new RegExp(`${after} gone`).test(r2.tile) && !!row && row.includes('the same VIN is listed as eDrive40 at $12,345, not certified') && r2.hint.includes('left a watch while'),
           `${String(victim.vin).slice(-6)} given a forwarding address: count ${before} → ${after}, tile "${(r2.tile.match(/\d+ gone/) || ['(none)'])[0]}", row ${row ? `"${row.slice(-110)}"` : 'missing'}`);
      } finally { await ctx.unroute('**/data.json'); }
    }
  }
});

// --- a watchlist edit is not a market event ----------------------------------
// Narrowing `years` makes every stored car outside the new range stop coming
// back, and delisted() used to read that as a query having looked and not
// found the car: on this repo's own record, restricting to 2024+ retires 84
// cars across three BMW watches and would have published every one of them as
// "GONE — the listing ended". Tracking.py calls that "out of scope"; this is
// the half that says the PAGE knows the word. It matters here specifically
// because nNc was a remainder — `gone.length - nDel - nStill - nOow` — so any
// word this file did not name was silently renamed "not checked on the day
// they vanished", which is false about a car no fetch will ask for again.
await step('a watchlist edit reads as a watchlist edit, not a departure', async () => {
  plan('the gone card counts a car the watchlist dropped as its own thing',
       'and the row says the model years moved, not that a fetch is owed',
       'and the same row under a word the page does know reads differently');
  const still = (g) => (g.still_listed && (g.still_listed.trim || g.still_listed.trim_id) ? g.still_listed : null);
  const host = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] }))
    .find((o) => (o.m.gone || []).some((g) => !still(g) && g.likely !== 'out of scope'));
  if (!host) {
    for (const n of ['the gone card counts a car the watchlist dropped as its own thing',
                     'and the row says the model years moved, not that a fetch is owed',
                     'and the same row under a word the page does know reads differently'])
      skip(n, 'no watched model carries a departure to re-label');
    return;
  }
  const victim = (host.m.gone || []).find((g) => !still(g) && g.likely !== 'out of scope');
  const readGone = async () => page.evaluate(() => ({
    hint: ((document.getElementById('gone-hint') || {}).textContent || '').replace(/\s+/g, ' ').trim(),
    rows: [...document.querySelectorAll('#gone-table tbody tr')].map((r) => r.textContent.replace(/\s+/g, ' ').trim()),
  }));
  const serveWith = async (word) => {
    const planted = JSON.parse(JSON.stringify(host.m));
    planted.gone.find((g) => g.vin === victim.vin).likely = word;
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[host.w.bk].models[host.w.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(host.w.q);
      if (await page.locator('#gone-more button').count()) { await page.click('#gone-more button'); await page.waitForTimeout(300); }
      return await readGone();
    } finally { await ctx.unroute('**/data.json'); }
  };
  const moved = await serveWith('out of scope');
  ok('the gone card counts a car the watchlist dropped as its own thing',
     /\b1 dropped by a watchlist change, not by leaving the market\b/.test(moved.hint)
       && !/1 not checked on the day it vanished/.test(moved.hint),
     `"${moved.hint.slice(-190)}"`);
  const movedRow = moved.rows.find((t) => t.includes(String(victim.vin)));
  ok('and the row says the model years moved, not that a fetch is owed',
     !!movedRow && movedRow.includes('outside the model years now watched')
       && !/not checked that day|GONE|the listing ended/.test(movedRow),
     movedRow ? `"${movedRow.slice(-150)}"` : `no row for ${String(victim.vin).slice(-6)} among ${moved.rows.length}`);
  // The control. Without it both assertions above pass on a page that prints
  // the watchlist sentence for EVERY absence — which is the same defect
  // pointing the other way, and the reason the counts are counted rather
  // than subtracted.
  const nc = await serveWith('not checked');
  const ncRow = nc.rows.find((t) => t.includes(String(victim.vin)));
  ok('and the same row under a word the page does know reads differently',
     !/dropped by a watchlist change/.test(nc.hint) && /not checked on the day/.test(nc.hint)
       && !!ncRow && ncRow.includes('not checked that day')
       && !ncRow.includes('outside the model years now watched'),
     `"${nc.hint.slice(-160)}" · row ${ncRow ? `"${ncRow.slice(-110)}"` : 'missing'}`);
});

// --- a cohort of mixed trims prices the mix, not the car ---------------------
// The interval asks whether a median is stable; it cannot ask whether the cars
// behind it are the same kind of car. Four 2022 iX xDrive50s read "26–40%
// under typical for a BMW iX, n=21" against a pool of ten M60s and eleven
// xDrive50s, and a Kia EV9 Light Long Range "15% under" a 2024 pool of four
// trims. A cohort now speaks only when every car in it wears the scored car's
// trim. Recomputed here from data.json; then a served sheet gives one model a
// clean pool of one trim, which must speak, and takes a trim cohort down to
// five so its cars fall to a mixed pool and go quiet.
await step('a cohort of mixed trims prices the mix, not the car', async () => {
  plan('no row, chip or pick claims a percentage against a cohort of mixed trims',
       'and the ones that do say so name their own trim and year',
       'a mixed cohort is not ranked first by a number the page will not print',
       'and a served pool of one trim still speaks');
  const P0 = (SHEET.buyer || {}).picks || {};
  const P = { max_miles: P0.max_miles || 50000, cpm: P0.cents_per_mile != null ? P0.cents_per_mile : 0.30, base: P0.mileage_baseline || 20000,
              no_acc: P0.exclude_accidents !== false, no_rental: P0.exclude_rental !== false };
  const RENTAL = /rental|fleet|corporate|commercial|taxi|livery|government|multiple/i;
  const eligible = (x) => x.price != null && x.miles != null && x.miles <= P.max_miles && !(P.no_acc && x.accidents > 0) && !(P.no_rental && RENTAL.test(x.usage || ''));
  const trimOf = (x) => String(x.trim || '').trim().toLowerCase(), yearOf = (x) => String(x.year || '');
  // the cohort a car would be judged by, and whether it is one trim
  const cohortOf = (m, x) => {
    const pool = (m.listings || []).filter(eligible);
    if (pool.length < 6) return null;
    const ty = pool.filter((c) => trimOf(c) === trimOf(x) && yearOf(c) === yearOf(x));
    const yr = pool.filter((c) => yearOf(c) === yearOf(x));
    const use = ty.length >= 6 ? ty : yr.length >= 6 ? yr : pool;
    const basis = ty.length >= 6 ? 'trim' : yr.length >= 6 ? 'year' : 'model';
    return { basis, n: use.length, mixed: use.some((c) => trimOf(c) !== trimOf(x)) };
  };
  const models = WATCHED.map((w) => ({ w, m: SHEET.brands[w.bk].models[w.mk] }));
  const mixedCars = models.flatMap((o) => (o.m.listings || []).filter(eligible).map((x) => ({ o, x, c: cohortOf(o.m, x) })).filter((r) => r.c && r.c.mixed));
  if (!mixedCars.length) return skipRest('no eligible car on the sheet would be judged by a cohort of mixed trims');
  // the model with the MOST such cars, so the sort check has something to
  // order: on a model with one mixed car it would pass whatever the rule did
  const byModel = new Map();
  for (const r of mixedCars) byModel.set(r.o, (byModel.get(r.o) || 0) + 1);
  const subject = [...byModel.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const readNotes = () => page.evaluate(() => {
    const rows = [...document.querySelectorAll('#list-table tbody tr')].map((tr) => {
      const b = tr.querySelector('[data-fkey^="star:"]');
      const note = [...tr.querySelectorAll('.sc-note')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()).find((t) => /typical/.test(t)) || '';
      return { vin: b ? b.getAttribute('data-fkey').split(':')[1] : '', note };
    });
    const picks = [...document.querySelectorAll('#takeaway .sc-photo-card')].map((c) => ({
      vin: ((c.querySelector('[data-fkey^="pick:"]') || { getAttribute: () => '' }).getAttribute('data-fkey') || '').split(':')[1] || '',
      note: ((c.querySelector('.sc-note') || {}).textContent || '').replace(/\s+/g, ' ').trim() }));
    const chips = [...document.querySelectorAll('.sc-photo-card__chip')].map((c) => c.textContent.trim()).filter((t) => /typical/.test(t));
    return { rows, picks, chips };
  });
  const showAll = async () => { if (await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); } };
  await open(subject.w.q); await showAll();
  const r = await readNotes();
  const byVin = new Map((subject.m.listings || []).map((x) => [x.vin, x]));
  const wrong = r.rows.filter((row) => { const x = byVin.get(row.vin); if (!x || !row.note) return false; const c = cohortOf(subject.m, x); return c && c.mixed; });
  ok('no row, chip or pick claims a percentage against a cohort of mixed trims', wrong.length === 0,
     wrong.length ? `${wrong[0].vin.slice(-6)} says "${wrong[0].note}" against a mixed cohort` : `${r.rows.filter((x) => x.note).length} of ${r.rows.length} rows on ${subject.w.label} carry a percentage, none on a mixed cohort (${mixedCars.filter((m) => m.o === subject).length} eligible cars there would be judged by one)`);
  // every pick on the FRONT page names a trim and a year, because a clean
  // cohort of the fallback kind is rare and a mixed one now says nothing
  await open('');
  const front = await readNotes();
  const named = front.picks.filter((p) => p.note);
  const anyMixed = named.filter((p) => { for (const o of models) { const x = (o.m.listings || []).find((c) => c.vin === p.vin); if (x) { const c = cohortOf(o.m, x); return c && c.mixed; } } return false; });
  ok('and the ones that do say so name their own trim and year', named.length > 0 && anyMixed.length === 0,
     anyMixed.length ? `${anyMixed[0].vin.slice(-6)}: "${anyMixed[0].note}"` : named.map((p) => p.note.replace(/^.*below a typical /, '').slice(0, 42)).join(' · '));
  // the value sort must not put a mixed car first on a number it will not print
  await open(subject.w.q);
  await page.selectOption('#f-sort', 'value'); await page.waitForTimeout(350);
  const sorted = await readNotes();
  // Only the ELIGIBLE cars are scored at all — an ineligible one carries no
  // percentage and sorts with the mixed, which is right and is not what this
  // check is about. Classify the rows the page actually judged.
  const cls = sorted.rows.map((row) => { const x = byVin.get(row.vin); if (!x || !eligible(x)) return null; const c = cohortOf(subject.m, x); return c ? (c.mixed ? 'mixed' : 'judged') : null; });
  const firstMixed = cls.indexOf('mixed'), lastJudged = cls.lastIndexOf('judged');
  const mixedShown = cls.filter((v) => v === 'mixed').length;
  if (!mixedShown || lastJudged === -1) skip('a mixed cohort is not ranked first by a number the page will not print', `${subject.w.label} shows ${mixedShown} mixed and ${cls.filter((v) => v === 'judged').length} judged cars under the value sort — nothing to order`);
  else ok('a mixed cohort is not ranked first by a number the page will not print', firstMixed > lastJudged,
     `${mixedShown} mixed of ${cls.filter(Boolean).length} scored cars on ${subject.w.label}: the first sits at row ${firstMixed}, after the last judged car at row ${lastJudged} of ${sorted.rows.length}`);
  // Served: the subject's pool rewritten to one trim, which must speak again.
  const planted = JSON.parse(JSON.stringify(subject.m));
  for (const x of planted.listings) x.trim = 'OneTrim';
  const wantSpeak = (planted.listings || []).filter(eligible).length >= 9;
  await ctx.route('**/data.json', async (route) => {
    const r2 = await route.fetch(); const sheet = JSON.parse(await r2.text());
    sheet.brands[subject.w.bk].models[subject.w.mk] = planted;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(subject.w.q); await showAll();
    const r3 = await readNotes();
    const noted = r3.rows.filter((x) => x.note).length;
    ok('and a served pool of one trim still speaks', wantSpeak ? noted > 0 : noted === 0,
       `${subject.w.label} with every car one trim: ${noted} of ${r3.rows.length} rows carry a percentage${noted ? ` (e.g. "${(r3.rows.find((x) => x.note) || {}).note}")` : ''}`);
  } finally { await ctx.unroute('**/data.json'); }
});

// --- a headline figure carries its own date --------------------------------
// The masthead says "data through Sep 4"; the watchlist's first tile can lead
// with a car from a model on a slower cadence, last fetched two days before,
// and nothing said so. Now the tile stamps " · as of <date>" only when its
// model's as_of is older than the masthead's — never when they agree. The
// case is planted, not found: the served sheet ages the leading model's as_of
// by two days, so the check holds on a morning when every model was fetched.
await step('a headline figure carries its own date', async () => {
  plan('a tile led by a model fetched on an older day says so',
       'and one further behind than its cadence allows gives the count',
       'and a tile led by a model fetched today does not');
  if (WATCHED.length < 1 || !carried) return skipRest('no model on the watchlist holds a car today');
  const through = SHEET.data_through;
  const lead = () => page.locator('#kpis .sc-tile').first().locator('.sc-tile__sub').first().textContent();
  const aged = new Date(Date.parse(through + 'T00:00:00Z') - 2 * 86400000).toISOString().slice(0, 10);
  // whichever model leads the drivable tile, make its as_of two days old
  await open('');
  const leadLabel = ((await lead()) || '').split(' · ')[0].trim();
  const leader = WATCHED.find((w) => w.label === leadLabel);
  if (!leader) return skipRest(`the drivable tile leads with "${leadLabel}", which no watched model is called`);
  // The plant writes every field the tile reads, not just the one it is
  // about. as_of alone leaves age_days and overdue holding the REAL model's
  // values, so the served sheet contradicts itself and the page is asked to
  // describe a state no writer produces — the same incomplete-plant shape the
  // floor-departure check was fixed for. age_days is the gap Tracking.py's
  // fetch_age computes; overdue is its fetch_overdue, which is exact at the
  // cadence with no tolerance.
  const ageTo = (sheet, day) => {
    const m = sheet.brands[leader.bk].models[leader.mk];
    m.as_of = day;
    m.age_days = Math.max(0, Math.round((Date.parse(sheet.data_through) - Date.parse(day)) / 86400000));
    m.overdue = m.age_days >= Math.max(1, m.cadence || 1);
    return m;
  };
  const serve = (day) => ctx.route('**/data.json', async (route) => {
    const r = await route.fetch(); const sheet = JSON.parse(await r.text());
    ageTo(sheet, day);
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  // Two days is a gap most cadences account for — but not a DAILY one, where
  // fetch_overdue is true at a gap of 1, so the tile correctly adds the count
  // and this check, anchored on "no count" with a trailing $, went red on a
  // page that was right. Which model leads the tile is whatever the market
  // makes cheapest, so that was a latent CI failure waiting on a shopped model
  // to lead. The expectation follows the cadence the way the page does.
  const leadCadence = ((SHEET.brands[leader.bk] || {}).models[leader.mk] || {}).cadence || 1;
  const gap = Date.parse(through + 'T00:00:00Z') - Date.parse(aged + 'T00:00:00Z');
  const agedOverdue = Math.round(gap / 86400000) >= Math.max(1, leadCadence);
  const stamp = agedOverdue ? `· as of [A-Z][a-z]{2} \\d+, \\d+ days? ago$` : `· as of [A-Z][a-z]{2} \\d+$`;
  await serve(aged);
  try {
    await open('');
    const t1 = (await lead()) || '';
    ok('a tile led by a model fetched on an older day says so', new RegExp(stamp).test(t1.trim()) && t1.includes(leadLabel),
       `${leader.label} aged to ${aged} (2d on a ${leadCadence}-day cadence — ${agedOverdue ? 'overdue, so the count belongs' : 'inside its cadence, so the date alone'}) under data through ${through}: "${t1.trim()}"`);
  } finally {
    await ctx.unroute('**/data.json');
  }
  // Two days behind is a gap most cadences account for, so the tile above
  // gives a date and no count. Past the cadence it gives both, because that
  // is the case where the date alone reads as recent: these tiles quote a
  // price as the day's answer, and the count is what says how old the answer
  // is. Planted well past any cadence on the watchlist so the branch is
  // reached whatever the leading model runs at.
  const far = Math.max(2, ...WATCHED.map((w) => ((SHEET.brands[w.bk] || {}).models[w.mk] || {}).cadence || 1)) + 3;
  const farDay = new Date(Date.parse(through + 'T00:00:00Z') - far * 86400000).toISOString().slice(0, 10);
  await serve(farDay);
  try {
    await open('');
    const t3 = (await lead()) || '';
    const lc = ((SHEET.brands[leader.bk] || {}).models[leader.mk] || {}).cadence;
    ok('and one further behind than its cadence allows gives the count',
       new RegExp(`· as of [A-Z][a-z]{2} \\d+, ${far} days ago$`).test(t3.trim()) && t3.includes(leadLabel),
       `${leader.label} aged to ${farDay} — ${far} days on a ${lc}-day cadence, under data through ${through}: "${t3.trim()}"`);
  } finally {
    await ctx.unroute('**/data.json');
  }
  // …and the same model served as fetched on the masthead's own day gets no
  // stamp — planted as well, because on the day this was written it really
  // had been fetched two days before.
  await serve(through);
  try {
    await open('');
    const t2 = (await lead()) || '';
    ok('and a tile led by a model fetched today does not', t2.includes(leadLabel) && !/· as of /.test(t2), `${leader.label} as of ${through}: "${t2.trim()}"`);
  } finally {
    await ctx.unroute('**/data.json');
  }
});

// --- the floor delta names its cause ---------------------------------------
// Tile 1's ▲▼ read as the market rising or falling; every floor move on this
// record was a car arriving or leaving. The tile now says which, from the two
// floor cars. Recomputed here from the sheet by the same rule — the previous
// day row's floor car found through the series with the trim's carry-forward,
// today's from the tile's own VIN — for every watched model page, and then a
// served sheet retires today's floor car into a confirmed departure so the
// "left the market" branch is exercised on a day nothing left.
await step('the floor delta names its cause', async () => {
  plan('the cause on every model page is the one the two floor cars make',
       'and a confirmed departure of the floor car is named as one',
       'and an unconfirmed one is not');
  const money = (n) => '$' + Math.round(n).toLocaleString('en-US');
  const latest = (days, day) => { let b = null; for (const d of (days || [])) { if (d <= day) b = d; else break; } return b; };
  const causeOf = (m, bestVin) => {
    const daily = m.daily || [];
    const today = daily[daily.length - 1], prev = daily.length >= 2 ? daily[daily.length - 2] : null;
    const best = (m.listings || []).find((x) => x.vin === bestVin);
    if (!prev || !today || !best || prev.min_price == null || today.min_price == null || prev.min_price === today.min_price || today.min_price !== best.price) return null;
    const fd = m.fetch_days || {};
    const cars = new Map();
    for (const x of (m.listings || []).concat(m.gone || [])) {
      const k = String(x.vin || '').toUpperCase();
      if (!k || cars.has(k)) continue;
      const seen = new Map((x.series || []).filter((pt) => pt[1]).map((pt) => [pt[0], pt[1]]));
      if (seen.size) cars.set(k, { x, seen });
    }
    const priceOn = (c, day) => { const days = fd[c.x.trim_id]; const lf = days && days.length ? latest(days, day) : day; return lf && c.seen.has(lf) ? c.seen.get(lf) : null; };
    let prevCar = null;
    for (const c of cars.values()) { const v = priceOn(c, prev.date); if (v != null && (!prevCar || v < prevCar.v)) prevCar = { c, v }; }
    if (!prevCar || prevCar.v !== prev.min_price) return null;
    const d = today.min_price - prev.min_price;
    if (prevCar.c.x.vin === best.vin) return `this car was ${d < 0 ? 'cut' : 'raised'} ${money(Math.abs(d))}`;
    if (d < 0) { const bc = cars.get(String(best.vin).toUpperCase()); return bc && priceOn(bc, prev.date) != null ? `another car cut to ${money(best.price)}` : `a car arriving at ${money(best.price)}`; }
    const still = priceOn(prevCar.c, today.date);
    if (still != null) return still > prevCar.v ? `the ${money(prevCar.v)} car was raised to ${money(still)}` : null;
    const confirmed = (m.gone || []).some((g) => g.vin === prevCar.c.x.vin && g.likely === 'delisted' && g.exact === true);
    return `the ${money(prevCar.v)} car ${confirmed ? 'left the market' : 'stopped being seen — not a confirmed departure'}`;
  };
  const readTile = () => page.locator('#kpis .sc-tile').first().evaluate((n) => ({
    vin: n.getAttribute('data-vin') || '', delta: (n.querySelector('.sc-delta') || {}).textContent || '',
    why: [...n.querySelectorAll('.sc-tile__sub')].map((s) => s.textContent.replace(/\s+/g, ' ').trim()).find((s) => /^(this car was|another car cut|a car arriving|the \$[\d,]+ car )/.test(s)) || '' }));
  const seen = [], wrong = [];
  for (const w of WATCHED.filter((w) => w.cars)) {
    await open(w.q);
    const t = await readTile();
    const want = causeOf(SHEET.brands[w.bk].models[w.mk], t.vin) || '';
    if (want) seen.push(`${w.label}: ${t.delta} — ${t.why}`);
    if (t.why !== want) wrong.push(`${w.label}: tile "${t.why}" · sheet "${want}" (${t.delta})`);
  }
  ok('the cause on every model page is the one the two floor cars make', wrong.length === 0 && seen.length > 0,
     wrong.length ? wrong.join(' | ') : (seen.join(' · ') || 'no floor moved on any model today'));
  // The served sheet: the carried model's floor car is retired — its listing
  // removed, its series ending on the previous day row, a gone row stamped
  // exact — so today's floor is the runner-up and the old floor car left.
  const m0 = SHEET.brands[carried.bk].models[carried.mk];
  const daily = m0.daily || [];
  if (daily.length < 2) { skip('and a confirmed departure of the floor car is named as one', `${carried.label} has one day row`); skip('and an unconfirmed one is not', `${carried.label} has one day row`); }
  else for (const exact of [true, false]) {
    const planted = JSON.parse(JSON.stringify(m0));
    const today = planted.daily[planted.daily.length - 1], prev = planted.daily[planted.daily.length - 2];
    const priced = planted.listings.filter((x) => x.price != null).sort((a, b) => a.price - b.price);
    const floor = priced[0], next = priced[1];
    planted.listings = planted.listings.filter((x) => x !== floor);
    floor.series = (floor.series || []).filter((pt) => pt[0] <= prev.date);
    // Make the served history agree with the served daily floor. The real
    // car may have cut its price today: preserving yesterday's older price
    // while changing prev.min_price creates contradictory evidence, which
    // the production renderer correctly refuses to describe as a departure.
    const prevFetch = latest((planted.fetch_days || {})[floor.trim_id], prev.date) || prev.date;
    floor.series = floor.series.filter((pt) => pt[0] < prevFetch).concat([[prevFetch, floor.price]]);
    prev.min_price = floor.price; today.min_price = next.price;
    planted.gone = (planted.gone || []).concat([{ ...floor, last_price: floor.price, last_seen: prev.date, likely: 'delisted', exact }]);
    const wantWhy = `the ${money(floor.price)} car ${exact ? 'left the market' : 'stopped being seen — not a confirmed departure'}`;
    const name = exact ? 'and a confirmed departure of the floor car is named as one' : 'and an unconfirmed one is not';
    await ctx.route('**/data.json', async (route) => {
      const r = await route.fetch(); const sheet = JSON.parse(await r.text());
      sheet.brands[carried.bk].models[carried.mk] = planted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
    });
    try {
      await open(carried.q);
      const t = await readTile();
      ok(name, t.why === wantWhy && /▲/.test(t.delta),
         `${carried.label} with its ${money(floor.price)} floor car retired (exact ${exact}): "${t.delta} — ${t.why}" · expected "${wantWhy}"`);
    } finally {
      await ctx.unroute('**/data.json');
    }
  }
});

// --- one tap lands on the car ----------------------------------------------
// Every tile that names a car offered "Open BMW i5 →", which was the model
// page's top: 133 rows under the drivable-first sort with the tile's own car
// at position 23, the i7's at 31 behind the thirty-row cap. The tap now lands
// on the car's own row — opening the list out first when the row sits past
// the cap — and focuses it, so the keyboard and the eyes arrive together.
// The tile whose car sits deepest in its list is the subject, so the cap
// branch is exercised whenever any shopped car sits past it.
await step('one tap lands on the car', async () => {
  plan('the decision tile\'s link lands on its car\'s row, in view and focused',
       'and the list was opened out when the row sat past the cap');
  await open('');
  const links = await page.$$eval('#hero-cars [data-fkey^="hero:"]', (as) => as.map((a) => a.getAttribute('data-fkey').split(':')[1]));
  if (!links.length) return skipRest('the decision panel led with no car today');
  // the deepest car: its position under the page's default order is not
  // recomputed here — the page is opened on each model and the row's index
  // read off the list with everything shown
  let subject = null;
  for (const vin of links) {
    const model = WATCHED.find((w) => (SHEET.brands[w.bk].models[w.mk].listings || []).some((x) => x.vin === vin));
    if (!model) continue;
    await open(model.q);
    if (await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); }
    const idx = await page.$$eval('#list-table tbody tr', (trs, v) => trs.findIndex((tr) => tr.querySelector(`[data-fkey="star:${v}"]`)), vin);
    if (idx >= 0 && (!subject || idx > subject.idx)) subject = { vin, idx, model };
  }
  if (!subject) return skipRest('no decision-tile car could be found in its model\'s list');
  await open('');
  await page.click(`#hero-cars [data-fkey="hero:${subject.vin}"]`);
  await page.waitForTimeout(600);
  const landed = await page.evaluate((v) => {
    const b = document.querySelector(`#list-card [data-fkey="star:${v}"]`);
    if (!b) return { found: false };
    const row = b.closest('tr') || b.closest('.sc-media') || b;
    const r = row.getBoundingClientRect();
    return { found: true, inView: r.top >= 0 && r.bottom <= window.innerHeight, focused: document.activeElement === row || row.contains(document.activeElement),
             h1: document.getElementById('h1').textContent, moreHidden: (document.getElementById('list-more') || {}).hidden };
  }, subject.vin);
  ok('the decision tile\'s link lands on its car\'s row, in view and focused',
     landed.found && landed.inView && landed.focused && landed.h1.includes(subject.model.label),
     `${subject.model.label} row ${subject.idx + 1}: ${JSON.stringify(landed)}`);
  if (subject.idx < 30) skip('and the list was opened out when the row sat past the cap', `the deepest shopped car sits at row ${subject.idx + 1}, inside the cap`);
  else ok('and the list was opened out when the row sat past the cap', landed.found && landed.moreHidden === true, `row ${subject.idx + 1}: show-all hidden ${landed.moreHidden}`);
});

// --- spice is for events; the key is for models ----------------------------
// Two design-system rules the page had drifted from. A role ("shopping",
// "comparison") is configuration, and it wore the spice chip in three places
// beside the promo clock and the "new" chips — with everything spicy, nothing
// was the look-here moment. And the decision tiles named a model with no line
// key while the chart and the compare header drew one, from two different
// builders; there is one builder now, and the tiles wear it when two or more
// models are in view.
await step('spice is for events, the key is for models', async () => {
  plan('no role chip wears spice', 'and each decision tile carries its model\'s line key');
  await open('');
  const spicy = await page.$$eval('.sc-chip--spice', (cs) => cs.map((c) => c.textContent.trim()).filter((t) => /^(shopping|comparison)$/.test(t)));
  ok('no role chip wears spice', spicy.length === 0, spicy.length ? `${spicy.length} role chip(s) in spice` : 'shopping/comparison chips wear brand or neutral');
  const keys = await page.$$eval('#hero-cars .sc-tile .sc-tile__label .cmp-swatch svg line', (ls) => ls.map((l) => ({ dash: l.getAttribute('stroke-dasharray') || '', stroke: l.getAttribute('style') || '' })));
  const tiles = await page.locator('#hero-cars .sc-tile').count();
  if (tiles < 2) skip('and each decision tile carries its model\'s line key', `${tiles} decision tile(s) — one model needs no key`);
  else ok('and each decision tile carries its model\'s line key', keys.length === tiles && new Set(keys.map((k) => k.stroke + '|' + k.dash)).size === keys.length,
          `${keys.length} keys on ${tiles} tiles: ${keys.map((k) => `${k.stroke.replace('stroke:', '')} ${k.dash || 'solid'}`).join(' · ')}`);
});

// --- the chick keeps watch in the large frame only -------------------------
// Every dealer photo in this harness is a one-pixel stand-in, so every frame
// falls back — which is exactly the day this guards against, a slow photo
// host. The large frame (a photo card) holds the mono chick; a 56×40 table
// frame says "no photo" in the sheet's own mono, because forty chicks beside
// forty prices is the mark worn nowhere.
await step('the chick keeps watch in the large frame only', async () => {
  plan('small empty frames say "no photo"', 'and large empty frames hold the mark');
  await page.setViewportSize({ width: 1280, height: 1000 });
  await open(carried.q);
  // Main's fix, and it explains an intermittent this branch could not
  // reproduce: a full run failed once on "0 of 0 small frames" and passed
  // alone and on three full runs after. The branch's answer was to wait on
  // the condition instead of a timer, which cannot help — if the image was
  // never FETCHED there is no fallback to wait for. The taller cover this
  // merge brings is what pushed the small frames outside Chromium's lazy
  // fetch distance. Scrolling one into view is the fix; waiting was a lead.
  // Dealer images are lazy. A taller cover can leave every small table photo
  // outside Chromium's fetch distance; sleeping does not make those requests
  // happen. Bring a real small frame into view, then wait for its pixel to
  // become the fallback before judging the fallback's contents.
  const smallFrame = page.locator('.sc-frame:not(.sc-frame--lg):visible').first();
  await smallFrame.scrollIntoViewIfNeeded();
  const smallFrameNode = await smallFrame.elementHandle();
  try {
    await page.waitForFunction((frame) => frame.classList.contains('sc-frame--empty'), smallFrameNode, { timeout: 10000 });
  } finally { await smallFrameNode.dispose(); }
  const frames = await page.evaluate(() => {
    const all = [...document.querySelectorAll('.sc-frame--empty')];
    const small = all.filter((f) => !f.classList.contains('sc-frame--lg'));
    const large = all.filter((f) => f.classList.contains('sc-frame--lg'));
    return { small: small.length, smallText: small.filter((f) => f.textContent.trim() === 'no photo' && !f.querySelector('img')).length,
             large: large.length, largeMark: large.filter((f) => f.querySelector('img.sc-frame__mark')).length };
  });
  ok('small empty frames say "no photo"', frames.small > 0 && frames.smallText === frames.small, `${frames.smallText} of ${frames.small} small frames`);
  if (!frames.large) skip('and large empty frames hold the mark', 'no large frame on this page fell back');
  else ok('and large empty frames hold the mark', frames.largeMark === frames.large, `${frames.largeMark} of ${frames.large} large frames`);
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

// --- a car is called what it is --------------------------------------------
// trim_label is the name of the TARGET a car was fetched under, and for a model
// watched whole rather than split by trim that name is the placeholder "all
// trims". Every surface that named a car reached for it first, so an Ioniq 5
// pick card read "Hyundai Ioniq 5 · 2023 · all trims · 17,314 mi" — directly
// under a value note that read "a typical 2023 Hyundai Ioniq 5 SEL", because
// THAT line reads the API's own trim field. The page had the real trim the
// whole time and printed the placeholder over it.
//
// Data-driven, like everything else here: the subject is whichever watched
// model carries such a target today, and the check asserts both halves — the
// placeholder is gone, and the thing that replaced it is the trim the sheet
// actually holds for that car, not a blank.
await step('a car is called what it is', async () => {
  plan('no surface names a car "all trims"',
       'and the trim it prints is the one the sheet holds');
  const placeholder = /^(all|all trims)$/i;
  const subject = (() => {
    for (const [bk, b] of Object.entries(SHEET.brands || {}))
      for (const [mk, m] of Object.entries((b || {}).models || {})) {
        const ids = Object.entries((m || {}).trims || {})
          .filter(([, t]) => placeholder.test(String((t || {}).label || '').trim()))
          .map(([id]) => id);
        if (!ids.length) continue;
        const cars = ((m || {}).listings || []).filter((x) => ids.includes(x.trim_id) && (x.trim || '').trim());
        if (cars.length) return { bk, mk, q: `?brand=${bk}&m=${mk}`, id: `${bk} ${mk}`, cars };
      }
    return null;
  })();
  if (!subject) return skipRest('no watched model is fetched whole rather than by trim today');
  await open(subject.q);
  // The picks are where the placeholder was loudest, and they render a card
  // per car with the model, the year and the trim on one line. But a model
  // whose cars all sit inside their cohort's interval draws no pick card at
  // all — the Ioniq 5's twelve eligible cars are one cohort, and none stands
  // under it — and the rule is about every surface, so the list rows, which
  // name the car the same way, are read instead on such a day.
  const picksText = ((await page.locator('#takeaway').evaluate((h) => (h.hidden ? '' : h.textContent))) || '').trim();
  const usingPicks = picksText.length > 0;
  if (!usingPicks && await page.locator('#list-more button').count()) { await page.click('#list-more button'); await page.waitForTimeout(300); }
  const said = usingPicks ? picksText : ((await page.textContent('#list-table tbody')) || '');
  const surface = usingPicks ? 'pick cards' : 'list rows (this model draws no pick card today)';
  ok('no surface names a car "all trims"', said.length > 0 && !/all trims/i.test(said),
     `${subject.id}: ${said.length} characters of ${surface}, `
     + (/all trims/i.test(said) ? 'one of them still says "all trims"' : 'none of them says "all trims"'));
  // …and it did not simply drop the word and leave a gap. Read the card's own
  // FACT line, per VIN — anywhere-on-the-page would pass on the value note
  // beside it, which reads the API trim by a different route and would have
  // covered for a naming rule that returned nothing at all.
  const byVin = new Map(subject.cars.map((x) => [x.vin, x.trim.trim()]));
  const cards = usingPicks
    ? await page.locator('#takeaway .sc-photo-card').evaluateAll((cs) => cs.map((c) => {
        const a = c.querySelector('[data-fkey^="pick:"]');
        const p = c.querySelector('.sc-photo-card__body p');
        return { vin: a ? a.getAttribute('data-fkey').split(':')[1] : '',
                 facts: p ? p.textContent.trim() : '' };
      }))
    : await page.locator('#list-table tbody tr').evaluateAll((trs) => trs.map((tr) => {
        const b = tr.querySelector('[data-fkey^="star:"]');
        const t = tr.querySelector('.sc-media__title');
        return { vin: b ? b.getAttribute('data-fkey').split(':')[1] : '',
                 facts: t ? t.textContent.trim() : '' };
      }));
  const named = cards.filter((c) => byVin.has(c.vin));
  const naked = named.filter((c) => !c.facts.includes(byVin.get(c.vin)));
  if (!named.length) skip('and the trim it prints is the one the sheet holds',
                          `no ${usingPicks ? 'pick card' : 'list row'} on ${subject.id} is one of the cars the sheet gives a trim`);
  else ok('and the trim it prints is the one the sheet holds', naked.length === 0,
          naked.length
            ? naked.map((c) => `${c.vin} should say "${byVin.get(c.vin)}" — card reads "${c.facts.slice(0, 70)}"`).join(' | ')
            : named.map((c) => `${byVin.get(c.vin)} in "${c.facts.slice(0, 48)}…"`).join(' · '));
});

// --- "new" means new ---------------------------------------------------------
// days_tracked is the length of a car's price series, and a series only grows
// on days its target was fetched. So a car seen once on Wednesday still reads
// days_tracked === 1 on Thursday — and the `new` chip, the "Since the previous
// snapshot" tile and the report all called it new again, every day, until its
// trim next ran. Twenty-two of the twenty-nine once-seen cars on the sheet this
// was written against were in exactly that state.
//
// Data-driven both ways: the subject is any car the sheet says was first seen
// before its model's last fetch, and the check also holds the tile's count to
// the chips underneath it, which is the thing a reader can verify by counting.
//
// The day is the MODEL's own last fetch, not the record's newest snapshot.
// Against the record's, every model on a slower cadence counted 0 new by
// construction — the iX, the EV9 and the A6 e-tron on the sheet this was
// rewritten against, each showing "0 new" beside a gone count measured against
// its own last fetch. Two clocks in one tile. The third check below is the one
// that fails if the anchor goes back.
// --- how old the cars on this card are --------------------------------------
// Both surfaces printed the SCHEDULE beside an absolute date and left the
// subtraction to the reader: "Data through Tue, August 25, 2026 · Fetched
// every 4 days" over cars twelve days old, three due days past that cadence.
// The number a reader would compute from what was on screen was wrong by a
// factor of three, on the page whose job is saying what a price is worth.
//
// Data-driven: the subject is whichever model the sheet says is behind, and
// the words are the sheet's own age field rather than a date this file
// re-derives — deriving it here would let the page and the check drift
// together, which is the shape that hid this for a month.
await step('the card says how old its cars are', async () => {
  plan('a model behind the record says how many days behind',
       'and says so when the cadence cannot account for it',
       'and a model fetched on the record\'s newest day claims no age',
       'and the model index says the same thing about the same model');
  const modelOf = (w) => ((SHEET.brands[w.bk] || {}).models[w.mk] || {});
  const withCars = WATCHED.filter((w) => modelOf(w).as_of);
  const aged = withCars.filter((w) => modelOf(w).age_days > 0);
  const fresh = withCars.filter((w) => modelOf(w).age_days === 0);
  const said = async (w) => {
    await open(w.q);
    return page.locator('#meta').evaluate((n) => n.innerText.replace(/\s+/g, ' ').trim());
  };
  if (!aged.length) skipRest('every model carrying cars was fetched on the record\'s newest day');
  else {
    const rows = [];
    for (const w of aged) rows.push({ w, m: modelOf(w), text: await said(w) });
    const silent = rows.filter((r) => !r.text.includes(daysAgo(r.m.age_days)));
    ok('a model behind the record says how many days behind', silent.length === 0,
       rows.map((r) => `${r.w.id} is ${r.m.age_days}d behind · card reads "${r.text.slice(0, 90)}"`).join(' | '));

    const over = rows.filter((r) => r.m.overdue);
    if (!over.length) skip('and says so when the cadence cannot account for it',
                           'no model on the sheet is further behind than its own cadence allows');
    else {
      // The expected words come from the page's own rule, not from a branch
      // guessed here: a daily model says "Past its daily cadence" and has no
      // "Fetched every 1 days" to be missing.
      const cap = (w) => w[0].toUpperCase() + w.slice(1);
      const quiet = over.filter((r) => {
        const want = cap(schedulePhrase(r.m.cadence, true));
        const keptPromise = schedulePhrase(r.m.cadence, false);
        return !r.text.includes(want)
            || (keptPromise && r.text.includes(`Fetched ${keptPromise}`));
      });
      ok('and says so when the cadence cannot account for it', quiet.length === 0,
         over.map((r) => `${r.w.id}: ${r.m.age_days}d on a ${r.m.cadence}-day cadence, wants "${cap(schedulePhrase(r.m.cadence, true))}" · "${r.text.slice(0, 110)}"`).join(' | '));
    }
  }
  // The other side: an age clause on a model fetched today would be noise on
  // every card the page draws, which is how a warning stops being read.
  if (!fresh.length) skip('and a model fetched on the record\'s newest day claims no age',
                          'no model on the sheet was fetched on its newest day');
  else {
    const rows = [];
    for (const w of fresh) rows.push({ w, text: await said(w) });
    const noisy = rows.filter((r) => /days? ago|Past its/.test(r.text));
    ok('and a model fetched on the record\'s newest day claims no age', noisy.length === 0,
       rows.map((r) => `${r.w.id} · "${r.text.slice(0, 80)}"`).join(' | '));
  }

  // The fourth surface, and the one that was left behind. The round that
  // taught the card and the two price tiles to say the age did not touch the
  // model index, so ONE PAGE said both things about one model: the tile read
  // "Chevrolet Equinox EV · as of Aug 27, 10 days ago" and the index row three
  // inches below read "Aug 27 · every 4 days" — the exact sentence that round
  // existed to delete, on the surface a reader scans all thirty-six models in.
  // Checked here as agreement between two surfaces of the same page rather
  // than against a string this file composes: what matters is that they cannot
  // say different things, and asserting a literal here would just be a fifth
  // place for the wording to live.
  if (!aged.length) skip('and the model index says the same thing about the same model',
                         'every model carrying cars was fetched on the record\'s newest day');
  else {
    await open('');
    const idx = await page.evaluate(() => {
      const out = {};
      document.querySelectorAll('table tbody tr').forEach((tr) => {
        const t = tr.querySelector('.sc-media__title');
        if (!t) return;
        out[t.textContent.trim()] = [...tr.querySelectorAll('td')]
          .map((td) => td.innerText.replace(/\s+/g, ' ').trim())
          .find((x) => /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b|first run/.test(x)) || '';
      });
      return out;
    });
    const bad = aged.filter((w) => {
      const row = idx[modelOf(w).label];
      if (row === undefined) return false;               // not on the index under that name
      const m = modelOf(w);
      return !row.includes(daysAgo(m.age_days))
          || !row.includes(schedulePhrase(m.cadence, !!m.overdue))
          || (m.overdue && schedulePhrase(m.cadence, false) && row.includes(schedulePhrase(m.cadence, false)));
    });
    ok('and the model index says the same thing about the same model', bad.length === 0,
       aged.map((w) => `${modelOf(w).label}: ${modelOf(w).age_days}d${modelOf(w).overdue ? ', overdue' : ''} · index row "${idx[modelOf(w).label] ?? '(not on the index)'}"`).join(' | '));
  }
});

await step('a car is new only on the day it arrives', async () => {
  plan('a car first seen before its model was last fetched does not wear the new chip',
       'and the tile counts what the table shows',
       'a model behind the newest snapshot still counts its own arrivals',
       'and a model on its first fetch reports no movement at all',
       'and the watchlist tile is the sum of the models it pools');
  const modelOf = (w) => ((SHEET.brands[w.bk] || {}).models[w.mk] || {});
  const asOf = (w) => modelOf(w).as_of || SHEET.data_through;
  const listingsOf = (w) => modelOf(w).listings || [];
  const staleIn = (w) => listingsOf(w).filter(
    (x) => x.days_tracked === 1 && String(x.first_seen || '').slice(0, 10) !== asOf(w));
  // The model that HAS the subject, not the biggest one: a once-seen car whose
  // first sighting predates the newest snapshot is what wore the chip wrongly,
  // and the largest model is not reliably the one carrying any today.
  const home = WATCHED.slice()
    .sort((a, b) => (staleIn(b).length - staleIn(a).length) || (b.cars - a.cars))[0];
  if (!home || !home.cars) return skipRest('no model on the watchlist holds cars today');
  const held = listingsOf(home);
  const stale = staleIn(home);
  const dt = asOf(home);
  const today = held.filter((x) => String(x.first_seen || '').slice(0, 10) === dt);
  await open(home.q);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(500); }
  // The chip rides in the vehicle cell beside the title, so read it per row.
  const chipped = await page.locator('#list-table tbody tr').evaluateAll((rows) => rows
    .filter((r) => [...r.querySelectorAll('.sc-chip')].some((c) => c.textContent.trim() === 'new'))
    .map((r) => { const c = r.querySelector('.sc-media__code'); return c ? c.textContent.trim() : ''; }));
  if (!stale.length) skip('a car first seen before its model was last fetched does not wear the new chip',
                          `every once-seen car on ${home.id} really was first seen on ${dt}`);
  else {
    const wrong = stale.filter((x) => chipped.includes(x.vin));
    ok('a car first seen before its model was last fetched does not wear the new chip', wrong.length === 0,
       `${stale.length} cars on ${home.id} were seen once, before ${dt} · ${wrong.length} still wear it`
       + (wrong.length ? ` (e.g. ${wrong[0].vin}, first seen ${wrong[0].first_seen})` : ''));
  }
  // …and the tile above the table counts the same set. A count a reader cannot
  // find in the rows below it is a count they cannot check.
  const tile = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent))
    ).find((t) => /new/.test(t)) || '';
  const said = Number((tile.match(/(\d+)\s*new/) || [])[1]);
  if (!Number.isFinite(said)) skip('and the tile counts what the table shows',
                                   'no tile on this model names a count of new cars today');
  else ok('and the tile counts what the table shows',
          said === chipped.length && said === today.length,
          `tile says ${said} · ${chipped.length} chips in the table · ${today.length} cars in the sheet first seen ${dt}`);

  // The anchor itself. A model whose own last fetch is behind the record's
  // newest snapshot is the only place the two candidate rules differ, so it is
  // the only place that can pin which one is in force: its arrivals are the
  // ones of ITS day. Anchored on the record's newest day this tile reads 0 for
  // such a model however many cars arrived at its last fetch.
  //
  // A model on its FIRST fetch is not a subject here, and excluding it is the
  // page's own rule rather than a convenience: with one day in its series
  // there is no previous snapshot of its own to move against, so the tile
  // reads "movement shows from day 2" and every one of its cars is a car the
  // tracker has just met, not a car that arrived. The record this was written
  // against holds exactly that case — hyundai/ioniq9, one fetch on 2026-08-25,
  // 37 cars all first seen that day — and reading it as "37 new" would be the
  // count claiming a comparison that never happened. Checked against the page
  // before it was written down: 0 rows wear the chip there, which is the same
  // answer the tile gives.
  //
  // Every straggler is checked, not `behind[0]`: which model comes first is an
  // accident of the watchlist's order, and a check that reads one of three
  // subjects is a check that a cadence change can silently point somewhere
  // else.
  const daysOf = (w) => ((modelOf(w).daily) || []).length;
  const behind = WATCHED.filter((w) => asOf(w) !== SHEET.data_through && daysOf(w) >= 2
    && listingsOf(w).some((x) => String(x.first_seen || '').slice(0, 10) === asOf(w)));
  if (!behind.length) return skip('a model behind the newest snapshot still counts its own arrivals',
                                  'no model on the watchlist is behind the newest snapshot with a previous fetch of its own and a car that arrived at its last one');
  const read = [];
  for (const lag of behind) {
    const want = listingsOf(lag).filter((x) => String(x.first_seen || '').slice(0, 10) === asOf(lag)).length;
    await open(lag.q);
    const lagTile = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent))
      ).find((t) => /new/.test(t)) || '';
    const got = Number((lagTile.match(/(\d+)\s*new/) || [])[1]);
    read.push({ lag, want, got });
  }
  const off = read.filter((r) => r.got !== r.want);
  ok('a model behind the newest snapshot still counts its own arrivals', off.length === 0,
     read.map((r) => `${r.lag.id} last fetched ${asOf(r.lag)} (record runs to ${SHEET.data_through})`
       + ` · ${r.want} arrived at its own last fetch · tile says ${Number.isFinite(r.got) ? r.got : 'nothing'}`
     ).join(' | '));

  // The other side of that exclusion, and it has to be asserted or the
  // exclusion above is a hole rather than a rule: a model on its first fetch
  // must report NO movement, not zero movement. "0 new · 0 gone" is a
  // comparison the record cannot have made, and on a thirty-six model
  // watchlist coming online a few at a time this is the state most of the
  // page is in — twenty-nine models have never fetched and one has fetched
  // once, against seven with a series to compare. It was pinned nowhere until
  // the check above started depending on it.
  const first = WATCHED.filter((w) => w.cars && daysOf(w) === 1);
  if (!first.length) skip('and a model on its first fetch reports no movement at all',
                          'every model holding cars has fetched more than once');
  else {
    const seen = [];
    for (const w of first) {
      await open(w.q);
      const more2 = page.locator('[data-fkey="more:list"]');
      if (await more2.count() && await more2.isVisible()) { await more2.click(); await page.waitForTimeout(400); }
      const tile2 = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent.replace(/\s+/g, ' ').trim()))
        ).find((t) => /previous snapshot/i.test(t)) || '';
      const chips2 = await page.locator('#list-table tbody tr').evaluateAll((rows) => rows
        .filter((r) => [...r.querySelectorAll('.sc-chip')].some((c) => c.textContent.trim() === 'new')).length);
      seen.push({ w, tile: tile2, chips: chips2, counted: /\d+\s*new/.test(tile2) });
    }
    const wrong = seen.filter((x) => x.counted || x.chips);
    ok('and a model on its first fetch reports no movement at all', wrong.length === 0,
       seen.map((x) => `${x.w.id} (${x.w.cars} cars, one fetch on ${asOf(x.w)}) · tile "${x.tile.slice(-40)}" · ${x.chips} rows wear the chip`).join(' | '));
  }

  // The front page pools all of it into one number, and until this check the
  // pooled one was the only movement figure on the site nothing opened. It is
  // where a lost per-model gate actually shows: `anyPrev` is a `some`, so one
  // model with a series is enough to print the SUM — and a model on its first
  // fetch would then contribute its whole inventory as arrivals. Measured, not
  // argued: ungating movementFor's `fresh` turns "32 new" into "69 new" here
  // and puts Hyundai Ioniq 9 at the head of "most", while every model page
  // stays correct, because the model tile is gated a second time on its own
  // record. Two gates, one mechanism; this is the surface that reads the
  // weaker one. It matters more each week — twenty-nine of the thirty-six
  // models have never fetched, and each one arrives as a first-fetch model on
  // its own day.
  //
  // Summed from the model tiles rather than from the sheet, because that is
  // the arithmetic a reader can do: the front page's number should be the
  // numbers on the pages it links to.
  const parts = [];
  for (const w of WATCHED.filter((x) => x.cars)) {
    await open(w.q);
    const t = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((x) => x.textContent.replace(/\s+/g, ' ').trim()))
      ).find((x) => /previous snapshot/i.test(x)) || '';
    const n = (t.match(/(\d+)\s*new/) || [])[1];
    parts.push({ id: w.id, n: n == null ? null : Number(n) });
  }
  await open('');
  const roof = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((x) => x.textContent.replace(/\s+/g, ' ').trim()))
    ).find((x) => /previous snapshot/i.test(x)) || '';
  const pooled = Number((roof.match(/(\d+)\s*new/) || [])[1]);
  const summed = parts.reduce((a, x) => a + (x.n || 0), 0);
  const silent = parts.filter((x) => x.n == null);
  // The skip is conditioned on the MODEL PAGES, never on the pooled tile: a
  // tile that has stopped naming a count is the failure this check exists for,
  // and skipping on its silence is how it would report safety instead. Turning
  // the tile's `some` into an `every` does exactly that — one model without a
  // series silences the whole front page while six models still have one — and
  // an earlier draft of this check skipped over it, printing "no model on it
  // has a previous fetch" about a record where six models did.
  const voiced = parts.filter((x) => x.n != null);
  if (!voiced.length) skip('and the watchlist tile is the sum of the models it pools',
                           'no model page names a count of new cars, so there is nothing for the watchlist to pool');
  else ok('and the watchlist tile is the sum of the models it pools',
          Number.isFinite(pooled) && pooled === summed,
          (Number.isFinite(pooled)
            ? `the watchlist says ${pooled} new`
            : `the watchlist names no count at all, over ${voiced.length} model page(s) that do`)
          + ` · the model pages sum to ${summed}`
          + ` (${voiced.map((x) => `${x.id} ${x.n}`).join(', ')})`
          + (silent.length ? ` · ${silent.map((x) => x.id).join(', ')} report no movement and so add nothing` : ''));
});
// --- the window the chart draws is the window it names ----------------------
// The range chips are drawn after the rows, so on the first paint of a visit
// the chart was built against whatever S.range the saved profile held — and
// the chips then quietly settled to something narrower underneath it. A
// remembered "90" on a record that cannot support 90 days drew ninety days of
// history under a control reading 30d, and only a second interaction put the
// two back in agreement.
//
// The committed sheet spans about a fortnight, so the chips do not even render
// against it (a range the record cannot distinguish is not offered). The
// subject is therefore made: data.json is served with one day row planted 40
// days back, which is the shape this fails on and the shape the record will
// have in a month.
await step('the chart draws the window its chips name', async () => {
  plan('a remembered range the record cannot support does not survive the first paint');
  const raw = JSON.parse(readFileSync(join(ROOT, 'data.json'), 'utf8'));
  const dt = raw.data_through;
  const old = new Date(Date.parse(dt + 'T00:00:00Z') - 40 * 86400000).toISOString().slice(0, 10);
  let planted = 0;
  for (const b of Object.values(raw.brands || {}))
    for (const m of Object.values((b || {}).models || {})) {
      const daily = m.daily || [];
      if (!daily.length) continue;
      m.daily = [{ ...daily[0], date: old }, ...daily];
      planted++;
    }
  if (!planted) return skipRest('no model in this sheet carries a day series to plant one in');
  await ctx.route('**/data.json', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify(raw) }));
  try {
    // A profile remembering the wider window, written before the page loads.
    await page.goto(BASE + '/index.html', { waitUntil: 'load' });
    await page.evaluate(() => {
      try { localStorage.setItem('spicycar.prefs', JSON.stringify({ where: [], range: '90' })); } catch { /* private mode */ }
    });
    await open('');
    const chip = ((await page.locator('#chart-range [aria-pressed="true"]').allTextContents())[0] || '').trim();
    const dates = await page.locator('#chart-table tbody tr td:first-child')
      .evaluateAll((ns) => ns.map((n) => n.textContent.trim()).filter((t) => /^\d{4}-\d{2}-\d{2}$/.test(t)));
    if (!chip || !dates.length) return skipRest('the range chips did not render even with a 40-day span');
    const days = chip === 'all' ? Infinity : Number(chip.replace(/\D/g, ''));
    const cut = Date.parse(dt + 'T00:00:00Z') - days * 86400000;
    const outside = dates.filter((d) => Date.parse(d + 'T00:00:00Z') < cut);
    ok('a remembered range the record cannot support does not survive the first paint',
       outside.length === 0,
       `chip reads "${chip}" over ${dates.length} day rows (${dates[dates.length - 1]} … ${dates[0]})`
       + (outside.length ? ` · ${outside.length} of them outside it, oldest ${outside[outside.length - 1]}` : ''));
  } finally {
    await ctx.unroute('**/data.json');
    await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* private mode */ } });
  }
});

// --- what a keyboard gets --------------------------------------------------
// Three failures with one shape: the page moved, and told nobody.
//
// The map and the scatter both take arrow keys and both grow the dot under the
// cursor and raise a tooltip — visual, and only visual. A reader arrowing
// across 495 cars was told nothing at all. Each has its own sr-only status node
// now, written ONLY from the arrow keys: the pointer shares the same tooltip,
// and a live one would announce on every pixel of a mouse move, so the check
// asserts BOTH directions or the fix could be "make the tooltip live" and pass.
//
// The KPI links scrolled the viewport and left focus on the link, so pressing
// "74 new" tabbed you back through the filter bar you had just scrolled past.
// And whatever the browser did scroll to landed under that bar, which is
// sticky: scroll-padding-top now reads the bar's own measured height.
await step('what a keyboard gets', async () => {
  plan('the map says which car its arrow keys are on',
       'and a mouse moving over the same dots stays silent',
       'a jump link leaves focus in the card it jumped to',
       'and nothing it jumps to lands under the sticky filter bar');
  await open('');
  const mapDots = await page.locator('#map .sc-dot').count();
  if (!mapDots) skip('the map says which car its arrow keys are on', 'the map drew no dots today'),
                skip('and a mouse moving over the same dots stays silent', 'the map drew no dots today');
  else {
    await page.locator('#car-atlas-switch').click();
    await page.locator('#map').focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(250);
    const said = (await page.textContent('#map-say') || '').trim();
    ok('the map says which car its arrow keys are on',
       said.length > 0 && /\$[\d,]+/.test(said),
       said ? `"${said.slice(0, 90)}"` : 'the status node stayed empty');
    // …and the pointer must not write it. A mouse over a different dot leaves
    // the announcement exactly where the keyboard left it.
    const dots = page.locator('#map .sc-dot');
    const n = await dots.count();
    await dots.nth(Math.min(n - 1, 5)).dispatchEvent('pointerenter', { pointerType: 'mouse', clientX: 10, clientY: 10 });
    await page.waitForTimeout(200);
    const after = (await page.textContent('#map-say') || '').trim();
    ok('and a mouse moving over the same dots stays silent', after === said,
       after === said ? 'unchanged by the pointer' : `pointer rewrote it to "${after.slice(0, 70)}"`);
    await page.keyboard.press('Escape');
  }

  // The three jump links, by name: the overview's kpi:mv:* links navigate to a
  // model and are a different promise.
  // On a MODEL page: the overview's tiles carry kpi:mv:* links, which navigate
  // to a model and are a different promise.
  const kpiHome = WATCHED.slice().sort((a, b) => b.cars - a.cars)[0];
  if (kpiHome) await open(kpiHome.q);
  const jumps = [['kpi:new', 'list-card'], ['kpi:cuts', 'list-card'], ['kpi:gone', 'gone-card']];
  const landed = [];
  for (const [key, card] of jumps) {
    const link = page.locator(`[data-fkey="${key}"]`).first();
    if (!(await link.count())) continue;
    await link.focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    landed.push({ key, card, inside: await page.evaluate((c) =>
      !!(document.activeElement && document.activeElement.closest('#' + c)), card) });
  }
  if (!landed.length) skip('a jump link leaves focus in the card it jumped to',
                           'this sheet renders none of the three jump links today');
  else ok('a jump link leaves focus in the card it jumped to',
          landed.every((l) => l.inside),
          landed.map((l) => `${l.key} -> ${l.inside ? l.card : 'left behind on the link'}`).join(' · '));

  // …and what it jumped to is not sitting behind the bar when it gets there.
  // The bar is sticky, so scrollIntoView({block:'start'}) parks its target at
  // the very top of the viewport — underneath it — unless scroll-padding-top
  // knows how tall it currently is. Measured on a desktop, where the open bar
  // is at its tallest (206px against a phone's shut 62px), which is exactly
  // why a constant could not have done this job.
  if (!kpiHome) skip('and nothing it jumps to lands under the sticky filter bar', 'no model to jump within');
  else {
    await open(kpiHome.q);
    const link = page.locator('[data-fkey="kpi:new"]').first();
    if (!(await link.count())) skip('and nothing it jumps to lands under the sticky filter bar',
                                    'this model draws no "new" tile link today');
    else {
      await link.click();
      await page.waitForTimeout(700);
      const geom = await page.evaluate(() => {
        const bar = document.getElementById('filters-card');
        const h = document.getElementById('list-title');
        if (!bar || bar.hidden || !h) return null;
        const b = bar.getBoundingClientRect(), t = h.getBoundingClientRect();
        return { barBottom: Math.round(b.bottom), titleTop: Math.round(t.top),
                 pad: getComputedStyle(document.documentElement).scrollPaddingTop };
      });
      if (!geom) skip('and nothing it jumps to lands under the sticky filter bar', 'no sticky bar on this view');
      else ok('and nothing it jumps to lands under the sticky filter bar',
              geom.titleTop >= geom.barBottom,
              `the listings heading lands at y=${geom.titleTop}, the bar ends at y=${geom.barBottom}`
              + ` (scroll-padding-top ${geom.pad})`);
    }
  }
});

// --- when the CDN is down --------------------------------------------------
// This page is one static file and a pinned design system, and the design
// system is the half that comes over the wire from somebody else. Everything
// below the map block reaches for SC.geo, SC.spark, SC.toneRef and SC.tooltip
// at module scope, so a jsDelivr outage did not degrade the page — it threw a
// ReferenceError partway through the IIFE, before a single listener was wired,
// and left a masthead over an empty white column: no data, no chart, and
// nothing in the notice to say why. An unattended failure the reader cannot
// even name is the worst kind this page can have.
//
// Asserted at both ends: the notice appears and names the right cause, and it
// does NOT offer the "serve it over HTTP" advice, which is the answer to a
// different question and was printed unconditionally.
await step('when the design system does not load', async () => {
  plan('missing design assets say so instead of drawing a blank page',
       'and does not blame data.json for it');
  // The console errors this step provokes are the point of it, so they are
  // taken back out of the run's tally afterwards — but only the ones that are
  // about the blocked CDN. A genuine new error raised while the routes are
  // swapped still counts, which is the difference between silencing a step and
  // silencing a page.
  const before = errors.length;
  await ctx.route('**/design-system/**', (r) =>
    r.fulfill({ status: 503, contentType: 'text/plain', body: 'no' }));
  try {
    await page.goto(BASE + '/index.html', { waitUntil: 'load' });
    await page.waitForTimeout(800);
    const notice = await page.locator('#notice').evaluate((n) => ({
      hidden: n.hidden, text: (n.textContent || '').replace(/\s+/g, ' ').trim(),
    }));
    ok('missing design assets say so instead of drawing a blank page',
       !notice.hidden && /design system/i.test(notice.text),
       notice.hidden ? 'the notice stayed hidden — the page is blank and silent'
                     : `"${notice.text.slice(0, 110)}"`);
    ok('and does not blame data.json for it',
       !notice.hidden && !/python -m http\.server/.test(notice.text),
       /python -m http\.server/.test(notice.text)
         ? 'it offered the serve-it-over-HTTP advice, which is the answer to a different failure'
         : 'no data.json advice in the notice');
  } finally {
    const mine = /jsdelivr|design system|SC is not defined|503/i;
    const raised = errors.splice(before);
    for (const e of raised) if (!mine.test(e)) errors.push(e);
    // Restore the real locally served snapshot for subsequent steps.
    await ctx.unroute('**/design-system/**');
  }
});

// --- the rate the page is ranking on ---------------------------------------
// financeNote() carries three things nothing else on the page says: how long
// ago the hand-entered fallback rate was last checked, the promo's own term
// cap, and the "unless a promo applies" framing. It had exactly one call site,
// the compare card's hint — and that card is hidden unless the reader has
// picked two trims. So an ordinary model page sorted by monthly payment ranked
// every car on a rate whose age was disclosed nowhere.
//
// The second half is the same disclosure on the row. A promo capped at 60
// months, under a 72-month setting, quoted "$818/mo at 2.99% · saves $82/mo"
// beside rows financed over 72 — two payments over different numbers of months,
// ranked against each other, with the 60 living only in a title= no phone can
// reach.
await step('the rate the page is ranking on', async () => {
  plan('a model page says what rate its payments assume',
       'and a capped promo names its own term on the row itself');
  const fin = (SHEET.buyer || {}).finance;
  if (!fin) return skipRest('this sheet has no finance block');
  const promo = (fin.promos || []).find((p) => p.active && p.apr != null);
  const subject = WATCHED.find((w) => w.cars > 1);
  if (!subject) return skipRest('no model on the watchlist holds cars today');
  await open(subject.q);
  await page.selectOption('#f-sort', 'payment');
  await page.waitForTimeout(350);
  const hidden = await page.locator('#compare-card').evaluate((n) => n.hidden);
  const hint = (await page.textContent('#list-hint')) || '';
  ok('a model page says what rate its payments assume',
     hidden && /Payments assume \d+ months at [\d.]+%/.test(hint),
     `compare card hidden: ${hidden} · hint says "${(hint.match(/Payments assume[^.]*\./) || ['nothing'])[0].slice(0, 90)}"`);

  // The cap is only observable where a live promo is capped shorter than the
  // longest term the sheet offers.
  const longest = Math.max(...(fin.terms || [60]));
  if (!promo || !promo.max_term || longest <= promo.max_term) {
    return skip('and a capped promo names its own term on the row itself',
                'no live promo is capped shorter than the longest term offered');
  }
  const [bk, mk] = String(promo.model).split('/');
  const home = WATCHED.find((w) => w.bk === bk && w.mk === mk);
  if (!home) return skip('and a capped promo names its own term on the row itself',
                         `the promo names ${promo.model}, which is not on the watchlist today`);
  await open(home.q);
  await page.selectOption('#f-sort', 'payment');
  await page.selectOption('#f-term', String(longest));
  await page.waitForTimeout(350);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(500); }
  // Every VISIBLE payment note quoting the promo rate, read as a reader reads
  // it — the text, not the tooltip.
  const notes = await page.locator('#list-table tbody .sc-note').evaluateAll(
    (ns, apr) => ns.map((n) => n.textContent.trim()).filter((t) => t.includes(apr + '%')), String(promo.apr));
  if (!notes.length) return skip('and a capped promo names its own term on the row itself',
                                 `no row on ${home.id} is financed at ${promo.apr}% today`);
  const named = notes.filter((t) => new RegExp(`over ${promo.max_term} mo`).test(t));
  const stillSaving = notes.filter((t) => /saves \$/.test(t));
  ok('and a capped promo names its own term on the row itself',
     named.length === notes.length && stillSaving.length === 0,
     `${notes.length} rows at ${promo.apr}% under a ${longest}-month setting · ${named.length} name their own `
     + `${promo.max_term} months · ${stillSaving.length} still claim a monthly saving · first: "${notes[0].slice(0, 60)}"`);
  await page.selectOption('#f-term', String(fin.default_term || 60));
});

// ---- a share names the cars it is a share of --------------------------------
// "Sits longer than 92% of the model" is a claim about the model, and the
// share behind it is computed against the cars that carry a listing date —
// 85 of the i5's 134. The other 49 have no date to be outlasted, so they are
// in neither half of that percentage, and a reader taking the sentence at its
// word overestimates how much of the market this car has outsat.
//
// The number was already honest; only the noun was wrong. It is checked on all
// three row forms because the page has three, and the first pass at this fixed
// two of them.
await step('a share names the cars it is a share of', async () => {
  plan('the wide table names the pool the share was taken from',
       'the narrow band does too', 'and so does the phone card',
       'and a trim view cannot read it against its own denominator');
  const subject = WATCHED.find((w) => ((SHEET.brands[w.bk].models[w.mk].listings) || [])
    .some((x) => x.stale_pct != null && x.stale_pct >= 0.6 && x.stale_of));
  if (!subject) return skipRest('no car on this sheet has outlasted 60% of its dated pool');
  // Read every visible note that makes the claim, at one width, and judge them
  // together: one row still saying "of the model" is the bug.
  const notes = async () => page.locator('#list-table, #list-cards').locator('.sc-note, .sc-media__foot span')
    .evaluateAll((ns) => ns.map((n) => n.textContent.trim()).filter((t) => /longer than \d+% of the/.test(t)));
  const judge = (label, seen) => {
    const named = seen.filter((t) => /longer than \d+% of the model's \d+ dated cars\b/.test(t));
    // "of the model" as the whole claim — not the "of the model's 85 dated
    // cars" that replaced it, which \b alone happily matches before the
    // apostrophe and quietly failed every row.
    const model = seen.filter((t) => /of the model(?!'s)\b/.test(t));
    ok(label, seen.length > 0 && named.length === seen.length && model.length === 0,
       `${seen.length} notes · ${named.length} name their pool · ${model.length} still claim the model`
       + (seen.length ? ` · first: "${seen[0].slice(0, 70)}"` : ''));
  };
  await page.setViewportSize({ width: 1280, height: 1000 });
  await open(subject.q);
  judge('the wide table names the pool the share was taken from', await notes());
  await page.setViewportSize({ width: 860, height: 1000 });
  await open(subject.q);
  judge('the narrow band does too', await notes());
  await page.setViewportSize({ width: 390, height: 844 });
  await open(subject.q);
  judge('and so does the phone card', await notes());
  await page.setViewportSize({ width: 1280, height: 1000 });

  // The share is the MODEL's — market_stats runs once per model over every
  // listing on it — while the market sentence beside it is recomputed for the
  // rows in view. On a trim view those are different pools, and the row used
  // to print a bare integer that the sentence one line above contradicted.
  const trim = (SHEET.brands[subject.bk].models[subject.mk].listings || [])
    .find((x) => x.stale_pct != null && x.stale_pct >= 0.6 && x.stale_of && x.trim_id);
  if (!trim) return skip('and a trim view cannot read it against its own denominator',
                         'no dated, long-sitting car on this model carries a trim');
  await open(`${subject.q}&trims=${encodeURIComponent(trim.trim_id)}`);
  const seen = await notes();
  const hint = (await page.textContent('#list-hint')) || '';
  const viewDated = (hint.match(/\((\d+) of \d+ dated\)/) || [])[1];
  const rowPools = [...new Set(seen.map((t) => (t.match(/of the model's (\d+) dated/) || [])[1]))];
  ok('and a trim view cannot read it against its own denominator',
     seen.length > 0 && seen.every((t) => /of the model's \d+ dated cars/.test(t)),
     `the view says ${viewDated || 'no'} dated · the rows say the model's ${rowPools.join('/')} `
     + `· ${seen.length} notes, all naming whose pool it is`);
});

// ---- a certified badge names who issued it ---------------------------------
// Manufacturer certification is issued by the manufacturer's own stores, so a
// car flagged certified at a dealership named for another marque is a claim to
// confirm rather than count on. 21 of the 46 certified cars on this sheet are
// exactly that. The listings table has said so since the promo strip was
// built; the departures card renders the builder's own `flags` array, which
// printed a bare "CPO" — the same badge, the same reader, one surface short.
await step('a certified badge on a departed car names who issued it', async () => {
  plan('a departed certified car at another marque says so');
  const caveat = (g) => (g.flags || []).some((f) => /seller not named/i.test(f));
  const subject = WATCHED.find((w) => ((SHEET.brands[w.bk].models[w.mk] || {}).gone || []).some(caveat));
  if (!subject) return skipRest('no departed car on this sheet is certified at a seller of another marque');
  const car = (SHEET.brands[subject.bk].models[subject.mk].gone || []).find(caveat);
  await open(subject.q);
  const more = page.locator('[data-fkey="more:gone"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(600); }
  const row = await page.locator('#gone-table tbody tr, #gone-cards > *').evaluateAll((rs, tail) => {
    const hit = rs.find((r) => r.innerText.toUpperCase().includes(tail));
    return hit ? hit.innerText.replace(/\s+/g, ' ') : '';
  }, String(car.vin).slice(-6).toUpperCase());
  if (!row) return skipRest(`${car.vin} is not on screen in the departures card`);
  ok('a departed certified car at another marque says so',
     /CPO \(seller not named [A-Za-z]+\)/.test(row),
     `row reads "${row.slice(0, 150)}"`);
});

// ---- a sentence that names a total and then splits it adds up --------------
// Four surfaces named a total, split it in two, and left something in
// neither: "49 of 151 are delivery-mileage stock … and the 99 used cars" (3
// publish no mileage), "113 of 151 dated — 49 dealer stock, 61 used" (3 dated
// cars publish none either), and the compare table's "134 / 18 drivable · 116
// beyond" (one car carries no state). Each is arithmetic a reader can do in
// their head, and each came out wrong by exactly the cars the page had
// quietly declined to classify — which it was right to decline, and wrong not
// to mention.
await step('a split adds up to the total it named', async () => {
  plan('the stock sentence accounts for every car it counted',
       'the typical-days split accounts for every dated car',
       'and the compare table puts an unplaced car in neither column');
  const subject = WATCHED.map((w) => {
    const L = SHEET.brands[w.bk].models[w.mk].listings || [];
    return { ...w, L, noMi: L.filter((x) => x.price != null && x.miles == null).length };
  }).sort((a, b) => b.noMi - a.noMi)[0];
  if (!subject || !subject.noMi) return skipRest('every priced car on this sheet publishes its mileage');
  await open(subject.q);
  const subs = await page.locator('#kpis .sc-tile__sub').evaluateAll((ns) => ns.map((n) => n.textContent.trim()));
  const stock = subs.find((t) => /delivery-mileage stock/.test(t)) || '';
  const m = stock.match(/(\d+) of (\d+) are delivery-mileage stock.*?the (\d+) used cars/);
  const neither = Number((stock.match(/(\d+) publish no mileage/) || [])[1] || 0);
  ok('the stock sentence accounts for every car it counted',
     !!m && Number(m[1]) + Number(m[3]) + neither === Number(m[2]),
     m ? `${m[1]} stock + ${m[3]} used + ${neither} unclassed = ${Number(m[1]) + Number(m[3]) + neither}, `
       + `against the ${m[2]} it named · "${stock.slice(0, 120)}"` : `no stock sentence on ${subject.id}`);

  const hint = (await page.textContent('#list-hint')) || '';
  const d = hint.match(/\((\d+) of \d+ dated\) — (\d+) dealer stock at \d+d, (\d+) used at \d+d/);
  if (!d) skip('the typical-days split accounts for every dated car',
               `${subject.id} does not print a typical-days split today`);
  else {
    const none = Number((hint.match(/, (\d+) with no mileage/) || [])[1] || 0);
    ok('the typical-days split accounts for every dated car',
       Number(d[2]) + Number(d[3]) + none === Number(d[1]),
       `${d[2]} + ${d[3]} + ${none} = ${Number(d[2]) + Number(d[3]) + none}, against the ${d[1]} dated it named`);
  }

  // The compare table opposes drivable to beyond by subtraction, so a car the
  // sheet cannot place lands in the second by default.
  const un = WATCHED.find((w) => (SHEET.brands[w.bk].models[w.mk].listings || [])
    .some((x) => !x.local && !String(x.state || '').trim()));
  if (!un) return skip('and the compare table puts an unplaced car in neither column',
                       'every live car on this sheet carries a state');
  // Two model chips is how a reader opens it, so that is how this opens it.
  await open('');
  const chips = page.locator('#f-model button');
  const label = (SHEET.brands[un.bk].models[un.mk] || {}).label || un.mk;
  const names = await chips.evaluateAll((ns) => ns.map((n) => n.textContent.trim()));
  const mine = names.findIndex((t) => t.startsWith(label));
  const theirs = names.findIndex((t, i) => i !== mine);
  if (mine < 0 || theirs < 0) return skip('and the compare table puts an unplaced car in neither column',
                                          'the watchlist holds fewer than two models today');
  await chips.nth(mine).click(); await page.waitForTimeout(250);
  await chips.nth(theirs).click(); await page.waitForTimeout(500);
  const cell = (await page.locator('#compare-table td').evaluateAll(
    (ns) => ns.map((n) => n.textContent.trim()))).find((t) => /drivable/.test(t)) || '';
  if (!cell) return skip('and the compare table puts an unplaced car in neither column',
                         'the compare table is not on screen for this pair');
  // The cell stacks a figure over a sub-line, so its text runs the total
  // straight into the split — "134" and "18 drivable …" read back as "13418".
  // Build what the sheet says it should be and look for that, rather than
  // trying to tell where one number ends and the next begins.
  const L = SHEET.brands[un.bk].models[un.mk].listings || [];
  const loc2 = L.filter((x) => x.local).length;
  const un2 = L.filter((x) => !x.local && !String(x.state || '').trim()).length;
  const want = `${loc2} drivable · ${L.length - loc2 - un2} beyond · ${un2} with no state`;
  ok('and the compare table puts an unplaced car in neither column',
     un2 > 0 && cell.includes(want),
     `expected "${want}" · cell reads "${cell.replace(/\s+/g, ' ').slice(0, 100)}"`);
});

// ---- the record and the page tell one story about one model ---------------
// Both read the same data.json and print the same market sentence, and they
// have drifted apart three times: a threshold on one side only, a word fixed
// in a heading and left in a footnote, a split the page printed and the record
// did not. The i7's "typical car 29d" is 49 dealer-stock cars at a median 78
// days blended with 61 used at 21 — a number no car on either side sits at —
// and the record published the blend alone.
//
// This reads the committed REPORT.md and the rendered page and compares the
// clause itself, so a constant that drifts between Tracking.py and this file
// is caught by the sentence disagreeing rather than by anyone remembering.
await step('the record and the page tell one story about one model', async () => {
  plan('the typical-days clause is the same on both surfaces',
       'and so is the clause that counts cars asking less than they did',
       'and so does the count of cars that arrived',
       'and so does the margin it prints on a car');
  const REPORT = join(ROOT, '..', 'REPORT.md');
  if (!existsSync(REPORT)) return skipRest('no REPORT.md beside this checkout');
  const md = readFileSync(REPORT, 'utf8');
  const rows = [];
  for (const w of WATCHED) {
    const label = (SHEET.brands[w.bk].models[w.mk] || {}).label;
    if (!label) continue;
    const at = md.indexOf(`## Shopping: ${label}\n`);
    if (at < 0) continue;                       // only the shopped models get a section
    const sect = md.slice(at, md.indexOf('\n## ', at + 4) + 1 || undefined);
    const line = (sect.match(/^_typical car .*$/m) || [''])[0];
    if (!line) continue;
    await open(w.q);
    const hint = (await page.textContent('#list-hint')) || '';
    // The clause, from the first character of the median to the end of the
    // split — everything the two surfaces are supposed to agree on here.
    const clause = (t) => ((t.match(/typical car \d+d on market \([^)]*\)( — [^·_]*)?/) || [''])[0]).trim();
    // The second clause of the same sentence, which took its numerator over
    // the cars that are not sawtooth and its denominator over all of them.
    const held = (t) => ((t.match(/\d+ of \d+ ask less than when first seen/) || [''])[0]).trim();
    // …and the arrival count, which each surface reached by its own clock:
    // the record compared first_seen to the wall clock and the page to the
    // record's newest day, under a comment in the page saying it mirrored the
    // record. On the committed sheet rebuilt one day later the page said seven
    // and nine and the record said "0 new" for both, out of one file.
    const brief = (sect.match(/^- \d+ on the market .*$/m) || [''])[0];
    const arrivals = (t) => ((t.match(/(\d+) new/) || [])[1] || '');
    const tiles = await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent));
    const tileNew = ((tiles.find((t) => /new/.test(t)) || '').match(/(\d+)\s*new/) || [])[1] || '';
    rows.push({ id: w.id, md: clause(line), page: clause(hint),
                mdHeld: held(line), pageHeld: held(hint),
                mdNew: arrivals(brief), pageNew: tileNew });
  }
  if (!rows.length) return skipRest('no shopped model has a market line in the record');
  const off = rows.filter((r) => r.md !== r.page);
  ok('the typical-days clause is the same on both surfaces', off.length === 0,
     rows.map((r) => `${r.id}: ${r.md === r.page ? `both "${r.md}"` : `record "${r.md}" vs page "${r.page}"`}`).join(' · '));
  const offHeld = rows.filter((r) => r.mdHeld !== r.pageHeld);
  ok('and so is the clause that counts cars asking less than they did',
     offHeld.length === 0 && rows.some((r) => r.mdHeld),
     rows.map((r) => `${r.id}: ${r.mdHeld === r.pageHeld ? `both "${r.mdHeld}"` : `record "${r.mdHeld}" vs page "${r.pageHeld}"`}`).join(' · '));
  // …and the margin itself, per car. Python rounds half to EVEN and Math.round
  // rounds half UP, so a margin landing on an exact half-percent was published
  // as two numbers for one car — "4% under typical" in the record and "5%" on
  // the page — with nothing to say which was the tool's answer. Read per VIN
  // off both surfaces, so any divergence shows, not only the boundary one.
  const pctOf = (t) => {
    const out = new Map();
    for (const m of t.matchAll(/(\d+)% under typical[\s\S]{0,400}?\b([A-HJ-NPR-Z0-9]{17})\b/g))
      if (!out.has(m[2])) out.set(m[2], m[1]);
    return out;
  };
  const mdPct = pctOf(md);
  const pagePct = new Map();
  for (const w of WATCHED) {
    const label = (SHEET.brands[w.bk].models[w.mk] || {}).label;
    if (!label || md.indexOf(`## Shopping: ${label}\n`) < 0) continue;
    await open(w.q);
    const more = page.locator('[data-fkey="more:list"]');
    if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(400); }
    for (const t of await page.locator('#list-table tbody tr').evaluateAll((trs) => trs.map((r) => r.innerText))) {
      const vin = (t.match(/\b[A-HJ-NPR-Z0-9]{17}\b/) || [])[0];
      const pc = (t.match(/(\d+)% under typical/) || [])[1];
      if (vin && pc) pagePct.set(vin, pc);
    }
  }
  const shared = [...mdPct.keys()].filter((v) => pagePct.has(v));
  const offPct = shared.filter((v) => mdPct.get(v) !== pagePct.get(v));
  if (!shared.length) skip('and so does the margin it prints on a car',
                           'no car carries a printed margin on both surfaces today');
  else ok('and so does the margin it prints on a car', offPct.length === 0,
          offPct.length
            ? offPct.slice(0, 3).map((v) => `${v}: record ${mdPct.get(v)}% vs page ${pagePct.get(v)}%`).join(' | ')
            : `${shared.length} cars carry a margin on both, every one the same number`
              + ` (e.g. ${shared[0]} at ${mdPct.get(shared[0])}%)`);
  const offNew = rows.filter((r) => r.mdNew !== r.pageNew);
  ok('and so does the count of cars that arrived',
     offNew.length === 0 && rows.some((r) => r.mdNew !== ''),
     rows.map((r) => `${r.id}: ${r.mdNew === r.pageNew ? `both ${r.mdNew || '—'} new` : `record ${r.mdNew || '—'} vs page ${r.pageNew || '—'}`}`).join(' · '));
});

// ---- a sentence does not outrun the view it was built from -----------------
// renderKpis() was taught to say "(filtered)" so "a floor under a filter is
// never quoted as the nation's", and how.html repeats it. Two sentences built
// from the same filtered rows were not: the next-callout — the page's last word
// and its only accent action — stated "The cheapest BMW i7 you can drive to
// today: $189,960" with year=2026 pressed, one line under a tile correctly
// reading "Lowest drivable (filtered) $189,960", where the answer without the
// filter is $48,909; and the comparison's "Lowest asking anywhere", under a
// title reading "The cheapest one on the market, wherever it is", showed
// $60,396 for that same i7 with the OH chip on.
await step('a sentence does not outrun the view it was built from', async () => {
  plan('the next card names the filter it was built under',
       'and says nothing of the sort when there is none',
       'the comparison stops saying anywhere when it is not anywhere');
  const sub = WATCHED.find((w) => {
    const m = SHEET.brands[w.bk].models[w.mk] || {};
    const years = new Set((m.listings || []).filter((x) => x.local && x.price).map((x) => String(x.year)));
    return years.size > 1;
  });
  if (!sub) return skipRest('no watched model has drivable cars from more than one model year');
  await open(sub.q);
  const clean = (await page.textContent('#next-figure')) || '';
  ok('and says nothing of the sort when there is none', !/filtered/.test(clean) && /\$/.test(clean),
     `unfiltered the card reads ${JSON.stringify(clean.trim().slice(0, 90))}`);
  const m = SHEET.brands[sub.bk].models[sub.mk];
  const years = [...new Set((m.listings || []).filter((x) => x.local && x.price).map((x) => String(x.year)))].sort();
  await page.selectOption('#f-year', years[years.length - 1]);
  await page.waitForTimeout(500);
  const narrowed = (await page.textContent('#next-figure')) || '';
  const tile = (await page.locator('#kpis .sc-tile').evaluateAll((ts) => ts.map((t) => t.textContent))).find((t) => /drivable/i.test(t)) || '';
  ok('the next card names the filter it was built under',
     /\(filtered/.test(narrowed) && /filtered/.test(tile),
     `with ${years[years.length - 1]} pressed the tile says ${JSON.stringify(tile.slice(0, 40))}`
     + ` and the card says ${JSON.stringify(narrowed.trim().slice(0, 110))}`);
  // …and the comparison row whose label is a claim about the whole market.
  const two = WATCHED.slice(0, 2);
  const wheres = ((SHEET.buyer || {}).states || []);
  if (two.length < 2 || !wheres.length)
    return skip('the comparison stops saying anywhere when it is not anywhere',
                'this sheet has fewer than two models or no buyer states to filter on');
  await open(`?models=${two.map((w) => `${w.bk}-${w.mk}`).join(',')}`);
  const before = (await page.textContent('#compare-table')) || '';
  const chips = page.locator('#f-where button');
  const names = await chips.evaluateAll((ns) => ns.map((n) => n.textContent.trim()));
  const i = names.findIndex((t) => t.startsWith(wheres[0]));
  if (i < 0) return skip('the comparison stops saying anywhere when it is not anywhere',
                         `no where chip for ${wheres[0]}`);
  await chips.nth(i).click(); await page.waitForTimeout(600);
  const after = (await page.textContent('#compare-table')) || '';
  ok('the comparison stops saying anywhere when it is not anywhere',
     /Lowest asking anywhere/.test(before) && !/Lowest asking anywhere/.test(after)
     && /Lowest asking \(filtered\)/.test(after),
     `unfiltered the row is labelled `
     + JSON.stringify(((before.match(/Lowest asking[^$]*/) || [''])[0]).trim().slice(0, 40))
     + `; with ${wheres[0]} pressed it is `
     + JSON.stringify(((after.match(/Lowest asking[^$]*/) || [''])[0]).trim().slice(0, 40)));
});

// ---- the car in hand is a car the reader can find --------------------------
// The VIN field is for a buyer standing at a dealer with seventeen characters
// off a windshield. The card's sentence was unconditional — "Its row is in the
// list below" — while the list is filtered(), and S.where is the one filter this
// page remembers between visits, so a plain reload with no query at all reaches
// it: a Florida car named over two Ohio rows. Its offered recovery, "the star
// keeps it", is a button on the card, and the card is what disappeared.
await step('the car in hand is a car the reader can find', async () => {
  plan('the card does not claim a row the filters have removed',
       'and its way out really brings the row back',
       'and "Open this car" lands on the car rather than the top of the page');
  const sub = WATCHED.find((w) => {
    const m = SHEET.brands[w.bk].models[w.mk] || {};
    return (m.listings || []).some((x) => x.price && x.state
      && !((SHEET.buyer || {}).states || []).includes(x.state));
  });
  const states = ((SHEET.buyer || {}).states || []);
  if (!sub || !states.length) return skipRest('no watched model holds a car outside the buyer states');
  const m = SHEET.brands[sub.bk].models[sub.mk];
  const far = (m.listings || []).find((x) => x.price && x.state && !states.includes(x.state));
  const keep = states[0];
  const prefs = JSON.stringify({ where: [keep], range: '90', term: null, down: 0, stars: {}, budget: 0, budgetKind: 'otd' });
  await open(sub.q);
  await page.evaluate((p) => localStorage.setItem('spicycar.prefs', p), prefs);
  await open(`?brand=${sub.bk}&m=${sub.mk}&vin=${far.vin}`);
  const inList = () => page.locator('#list-table tbody tr').evaluateAll((trs, v) => trs.some((t) => t.innerText.includes(v)), far.vin);
  const said = (await page.textContent('#notice')) || '';
  ok('the card does not claim a row the filters have removed',
     !(await inList()) && !/row is in the list below/.test(said) && /not in the list below/.test(said),
     `${far.vin} is in ${far.city}, ${far.state} and the list is filtered to ${keep};`
     + ` the card says ${JSON.stringify(said.replace(/\s+/g, ' ').slice(-95))}`);
  if (!(await page.locator('[data-fkey="notice:vin-clear"]').count()))
    skip('and its way out really brings the row back', 'the card offered no way out to press');
  else {
    await page.click('[data-fkey="notice:vin-clear"]');
    await page.waitForTimeout(600);
    const back = (await page.textContent('#notice')) || '';
    ok('and its way out really brings the row back',
       (await inList()) && /row is in the list below/.test(back),
       `after pressing it the count reads ${JSON.stringify(((await page.textContent('#filter-count')) || '').trim())}`
       + ` and the card says ${JSON.stringify(back.replace(/\s+/g, ' ').slice(-70))}`);
  }
  // The card's own link, from the same remembered filter: it cleared the trim
  // and the year and not the shared filters, so it scrolled to the top of a
  // page that did not hold the car, taking the card that named it along.
  await open(sub.q);
  await page.evaluate((p) => localStorage.setItem('spicycar.prefs', p), prefs);
  await open(`?brand=${sub.bk}&m=${sub.mk}&vin=${far.vin}`);
  const opener = page.locator('#notice [data-fkey$=":open"]').first();
  if (!(await opener.count())) return skip('and "Open this car" lands on the car rather than the top of the page',
                                           'the card has no open link on this sheet');
  await opener.click();
  await page.waitForTimeout(800);
  const y = await page.evaluate(() => Math.round(window.scrollY));
  ok('and "Open this car" lands on the car rather than the top of the page',
     (await inList()) && y > 0,
     `the row is ${(await inList()) ? 'on screen' : 'still filtered out'} and the page sits at y=${y}`);
  // This step is the only one that writes a where-chip into localStorage and
  // then leaves the page on a model, so it puts the profile back itself:
  // recover() runs only after a step that THREW, and a filter left behind here
  // reads as a failure three steps later, in checks that never touched it.
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
});

// ---- the way out of an empty page is a way out -----------------------------
// "All 134 cars are filtered out … Clear the filters" over a link that cleared
// seven filters and not the budget — so in the one state a budget can create,
// the page's only offered recovery changed nothing: the same count, the same
// notice, the budget still in localStorage. filterBits() lists the budget and
// the count line names it ("0 of 134 cars · under $20,000 all in"), so it is a
// filter by every other rule this page follows.
await step('the way out of an empty page is a way out', async () => {
  plan('a budget nothing fits empties the page and says so',
       'and clearing the filters really clears it');
  const sub = WATCHED.find((w) => ((SHEET.brands[w.bk].models[w.mk] || {}).listings || []).length > 3);
  if (!sub) return skipRest('no watched model holds enough cars to empty');
  await open(sub.q);
  await page.evaluate(() => localStorage.setItem('spicycar.prefs', JSON.stringify(
    { where: [], range: '90', term: null, down: 0, stars: {}, budget: 1000, budgetKind: 'otd' })));
  await open(sub.q);
  const count = () => page.textContent('#filter-count').then((t) => (t || '').replace(/\s+/g, ' ').trim());
  const said = (await page.textContent('#notice')) || '';
  const before = await count();
  ok('a budget nothing fits empties the page and says so',
     /showing 0 of/.test(before) && /filtered out/.test(said),
     `the count reads ${JSON.stringify(before)} and the notice ${JSON.stringify(said.replace(/\s+/g, ' ').slice(0, 90))}`);
  if (!(await page.locator('[data-fkey="notice:clear"]').count())) {
    await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
    return skip('and clearing the filters really clears it', 'the notice offered no way out to press');
  }
  await page.click('[data-fkey="notice:clear"]');
  await page.waitForTimeout(600);
  const after = await count();
  const left = await page.evaluate(() => { try { return (JSON.parse(localStorage.getItem('spicycar.prefs') || '{}').budget) || 0; } catch { return 0; } });
  ok('and clearing the filters really clears it',
     !/showing 0 of/.test(after) && left === 0,
     `the count now reads ${JSON.stringify(after)} and the remembered budget is ${left}`);
  await page.evaluate(() => { try { localStorage.removeItem('spicycar.prefs'); } catch { /* about:blank */ } });
});

// ---- a figure names the population it is over ------------------------------
// Two sentences counted one thing and named another. The scatter said "148
// priced cars" on a page whose market tile says 151 cars are priced — its own
// 49 + 99 add to 148 because three i7s publish no mileage and cannot be
// plotted, which is a fact about the PLOT and not about pricing. And the promo
// footnote said "13 of the 31 certified cars … BMW FS certified 2.99%" on the
// watchlist, where 31 is 23 i5s at 2.99% plus 8 iXs at 2.49%: two offers pooled
// under the name of one, and the iX cars are not certified for it at all.
await step('a figure names the population it is over', async () => {
  plan('the scatter counts what it can plot, and says so',
       'the promo footnote counts the promo it names');
  const sub = WATCHED.find((w) => {
    const L = (SHEET.brands[w.bk].models[w.mk] || {}).listings || [];
    return L.some((x) => x.price && x.miles == null) && L.some((x) => x.price && x.miles != null);
  });
  if (!sub) skip('the scatter counts what it can plot, and says so',
                 'no watched model mixes cars with and without a mileage, so the plot and the priced set are the same population');
  else {
    await open(sub.q);
    const L = (SHEET.brands[sub.bk].models[sub.mk] || {}).listings || [];
    const priced = L.filter((x) => x.price != null).length;
    const plottable = L.filter((x) => x.price != null && x.miles != null).length;
    const hint = ((await page.textContent('#scatter-hint')) || '').replace(/\s+/g, ' ');
    const said = Number((hint.match(/^(\d+)/) || [])[1]);
    ok('the scatter counts what it can plot, and says so',
       said === plottable && plottable !== priced && !/priced cars/.test(hint),
       `${sub.id}: ${priced} cars carry a price and ${plottable} carry a price and a mileage;`
       + ` the hint opens "${hint.slice(0, 70)}"`);
  }
  // …and the footnote, whose denominator must be the offer in its own heading.
  const promos = (((SHEET.buyer || {}).finance || {}).promos || []).filter((p) => p.active && p.apr != null);
  if (promos.length < 2) return skip('the promo footnote counts the promo it names',
                                     'this buyer has fewer than two live promos, so nothing can be pooled');
  await open('');
  const all = ((await page.textContent('#promo-foot')) || '').replace(/\s+/g, ' ');
  const head = ((await page.textContent('#promo-card')) || '').replace(/\s+/g, ' ');
  const named = promos.find((p) => head.includes(p.label));
  if (!named || !/of the \d+ certified/.test(all))
    return skip('the promo footnote counts the promo it names',
                `the watchlist card names ${named ? named.label : 'no promo'} and its footnote says ${JSON.stringify(all.slice(0, 60))}`);
  // Which cars a promo reaches is computed in the browser (annotateFinance),
  // so the sheet cannot answer it — but the page of the model that promo
  // belongs to can, and that view holds exactly the promo's own cars. The
  // watchlist pools every model in view, so the two disagreed: 13 of 31 there
  // against 11 of 23 here, for one offer.
  const home = WATCHED.find((w) => `${w.bk}/${w.mk}` === named.model)
    || WATCHED.find((w) => named.model && String(named.model).includes(w.mk));
  if (!home) return skip('the promo footnote counts the promo it names',
                         `no watched model matches ${named.label}'s own model key ${named.model}`);
  await open(home.q);
  const one = ((await page.textContent('#promo-foot')) || '').replace(/\s+/g, ' ');
  const nums = (t) => (t.match(/(\d+) of the (\d+) certified/) || []).slice(1, 3).join('/');
  ok('the promo footnote counts the promo it names',
     nums(all) !== '' && nums(all) === nums(one),
     `${named.label}: the watchlist says ${nums(all) || '(nothing)'} and its own`
     + ` model page says ${nums(one) || '(nothing)'}`);
});

// ---- the history the record publishes is the history the page draws -------
// flags() builds the "_1-owner · no accidents · ex-lease_" line under every car
// in REPORT.md and the `flags` array on every row of docs/data.json; the page
// re-derives the same words twice, in flagsCell() and flagsNote(). Three
// implementations of one rule, and until the unit tests beside them existed
// every branch of the owner and accident halves could be inverted with this
// suite green. Owners and accidents only: those are the words the two surfaces
// spell identically. The certification chip deliberately differs ("cpo" here,
// "CPO (seller not named BMW)" in the record, which says whose certification it
// is), and the usage words ride the same cell but are already pinned in Python.
await step('the history the record publishes is the history the page draws', async () => {
  plan('every row draws the owner and accident words the sheet gives it');
  const subject = WATCHED.find((w) => ((SHEET.brands[w.bk].models[w.mk] || {}).listings || [])
    .some((x) => (x.flags || []).some((f) => /owner|accident/.test(f))));
  if (!subject) return skipRest('no row on this sheet carries an owner or accident word');
  await open(subject.q);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(500); }
  const drawn = await page.locator('#list-table tbody tr').evaluateAll((trs) => trs.map((tr) => {
    const code = tr.querySelector('.sc-media__code');
    const cell = tr.querySelector('.flags');
    return { vin: code ? code.textContent.trim() : '', text: cell ? cell.innerText : '' };
  }));
  const want = new Map(((SHEET.brands[subject.bk].models[subject.mk] || {}).listings || [])
    .map((x) => [x.vin, (x.flags || []).filter((f) => /owner|accident/.test(f))]));
  const bad = [];
  let checked = 0;
  for (const row of drawn) {
    if (!want.has(row.vin)) continue;
    checked++;
    const said = want.get(row.vin);
    const missing = said.filter((f) => !row.text.includes(f));
    // …and the inverse, which is the half that catches a swap: the page must
    // not draw a history word the record does not give this car.
    const invented = (row.text.match(/\b(?:1-owner|\d+ owners|no accidents|\d+ accidents?)\b/g) || [])
      .filter((f) => !said.includes(f));
    if (missing.length || invented.length)
      bad.push(`${row.vin}: sheet says [${said.join(', ')}], cell reads "${row.text.replace(/\s+/g, ' ')}"`);
  }
  ok('every row draws the owner and accident words the sheet gives it',
     bad.length === 0 && checked > 0,
     bad.length ? bad.slice(0, 3).join(' | ')
                : `${checked} rows on ${subject.id}, every one drawing exactly the sheet's owner and accident words`);
});

// ---- a trim chip counts what its query returned ---------------------------
// A car two of a model's queries both returned is one row in the table, filed
// under whichever copy was cheapest with ties broken by list order — so the
// certified watch's own section reported the tie-break and not the watch. On
// every one of the seven days it had run it said the wrong thing: "2 vehicles ·
// lowest asking $64,491" on a day it returned four, the cheapest at $48,084,
// and "none found" on four days it returned cars. The record's sections and the
// page's chips are the same claim, so this reads the committed record's own
// headings and presses the chips.
await step('a trim chip counts what its query returned', async () => {
  plan('every trim section in the record has a chip saying the same number',
       'and pressing one shows that many rows',
       'and at the price that query returned',
       'and makes no value claim it measured on the other price',
       'and comparing two trims leaves it at the table\'s own price');
  const REPORT = join(ROOT, '..', 'REPORT.md');
  if (!existsSync(REPORT)) return skipRest('no REPORT.md beside this checkout');
  const md = readFileSync(REPORT, 'utf8');
  const rows = [];
  for (const w of WATCHED) {
    const m = SHEET.brands[w.bk].models[w.mk] || {};
    if (!m.label || Object.keys(m.trims || {}).length < 2) continue;
    const at = md.indexOf(`## Shopping: ${m.label}\n`);
    if (at < 0) continue;                       // only the shopped models get sections
    const sect = md.slice(at, md.indexOf('\n## ', at + 4) + 1 || undefined);
    await open(w.q);
    const chips = await page.locator('#f-trim button').evaluateAll((bs) => bs.map((b) => ({
      key: b.getAttribute('data-fkey'),
      label: b.firstChild ? String(b.firstChild.textContent).trim() : '',
      n: b.querySelector('.chip-n') ? Number(b.querySelector('.chip-n').textContent) : null })));
    for (const [id, t] of Object.entries(m.trims || {})) {
      const line = (sect.match(new RegExp('^### ' + t.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' — .*$', 'm')) || [''])[0];
      if (!line) continue;
      const said = /none found/.test(line) ? 0 : Number((line.match(/— (\d+) vehicles/) || [])[1]);
      const chip = chips.find((c) => c.key === 'trim:' + id);
      rows.push({ id, label: t.label, md: said, chip: chip ? chip.n : null, q: w.q });
    }
  }
  if (!rows.length) return skipRest('no shopped model on this sheet has a trim breakdown in the record');
  const off = rows.filter((r) => r.md !== r.chip);
  ok('every trim section in the record has a chip saying the same number', off.length === 0,
     rows.map((r) => `${r.label}: ${r.md === r.chip ? `both ${r.md}` : `record ${r.md} vs chip ${r.chip}`}`).join(' · '));
  // …and the chip is not just a number: pressing it has to put that many rows
  // on screen, or the count is a claim the table cannot back.
  const subject = rows.find((r) => r.md > 0 && r.md < 40) || rows.find((r) => r.md > 0);
  if (!subject) return skip('and pressing one shows that many rows', 'no trim on this sheet holds a car today');
  await open(subject.q);
  await page.click(`[data-fkey="trim:${subject.id}"]`);
  await page.waitForTimeout(400);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(400); }
  const shown = await page.locator('#list-table tbody tr').count();
  ok('and pressing one shows that many rows', shown === subject.md,
     `${subject.label}: the chip says ${subject.md}, the record says ${subject.md}, the table shows ${shown}`);

  // …at the price THAT query returned. Membership alone would price a shared
  // car by the table's rule — the cheapest copy — and of the nine VIN-days this
  // record holds in two targets, four carry two prices and one is certified at
  // $58,085 in the watch and NOT certified at $56,000 in its sibling. Both of
  // today's shared cars happen to be listed at the same price by both queries,
  // so no live sheet can tell the two rules apart: the divergence is planted.
  // The subject is a car the page already calls a value, because one of the
  // checks below is that the claim does NOT survive the swap — and the score is
  // computed in the browser (annotateValue), so it is read off the page rather
  // than out of the sheet, which carries no pick_* field at all.
  let twin = null;
  for (const w of WATCHED) {
    const m = SHEET.brands[w.bk].models[w.mk] || {};
    const ids = Object.keys(m.trims || {});
    if (ids.length < 2 || !(m.listings || []).length) continue;
    await open(w.q);
    const texts = await page.locator('#list-table tbody tr').evaluateAll((trs) => trs.map((r) => r.innerText));
    for (const t of texts) {
      if (!/under typical/.test(t)) continue;
      const vin = (t.match(/\b[A-HJ-NPR-Z0-9]{17}\b/) || [])[0];
      const x = vin && (m.listings || []).find((c) => c.vin === vin && c.price && c.trim_id);
      const other = x && ids.find((id) => id !== x.trim_id);
      if (other) { twin = { w, bk: w.bk, mk: w.mk, vin, own: x.price, other,
                            dear: x.price + 9000, mine: x.trim_id }; break; }
    }
    if (twin) break;
  }
  const cash = (n) => '$' + Number(n).toLocaleString('en-US');
  if (!twin) return skip('and at the price that query returned',
                         'no model on this sheet has two trims and a priced car');
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    const mm = sheet.brands[twin.bk].models[twin.mk];
    const car = (mm.listings || []).find((c) => c.vin === twin.vin);
    car.also = [{ trim_id: twin.other, trim_label: (mm.trims[twin.other] || {}).label || twin.other,
                  price: twin.dear, cpo: true, url: car.url, dealer: car.dealer }];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  try {
    await open(`?brand=${twin.bk}&m=${twin.mk}&trims=${twin.other}`);
    const rowText = await page.locator('#list-table tbody tr').filter({ hasText: twin.vin }).first().innerText().catch(() => '');
    ok('and at the price that query returned',
       rowText.includes(cash(twin.dear)) && !rowText.includes(cash(twin.own)),
       `${twin.vin} is ${cash(twin.own)} in its own query and ${cash(twin.dear)} in ${twin.other};`
       + ` under that trim the row reads ${JSON.stringify(rowText.replace(/\s+/g, ' ').slice(0, 110))}`);
    // The value score belongs to the price it was computed for. Carried over,
    // the row would print a margin measured against the cheaper listing beside
    // the dearer one's asking. The "before" is the same row under its OWN trim,
    // where no swap happens — read from the page, because the score is computed
    // in the browser and is in no sheet.
    await open(`?brand=${twin.bk}&m=${twin.mk}&trims=${twin.mine}`);
    const ownText = await page.locator('#list-table tbody tr').filter({ hasText: twin.vin }).first().innerText().catch(() => '');
    if (!/under typical/.test(ownText))
      skip('and makes no value claim it measured on the other price',
           `the page gives ${twin.vin} no value note under its own trim, so there is none to lose`);
    else ok('and makes no value claim it measured on the other price',
            !/under typical/.test(rowText),
            `under its own trim the row says ${JSON.stringify((ownText.match(/\d+% under typical/) || [''])[0])};`
            + ` under ${twin.other} at ${cash(twin.dear)} it reads `
            + JSON.stringify(rowText.replace(/\s+/g, ' ').slice(0, 90)));
    // With two trims pressed the page is comparing them, and a car in both has
    // no single answer to "at what price" — so it keeps its own, the table's.
    // The OTHER trim goes first in the URL on purpose: a swap that reached this
    // view would take the first selection, and with the car's own trim first it
    // would be invisible — the check would pass on a page picking arbitrarily
    // between two answers.
    await open(`?brand=${twin.bk}&m=${twin.mk}&trims=${twin.other},${twin.mine}`);
    const bothText = await page.locator('#list-table tbody tr').filter({ hasText: twin.vin }).first().innerText().catch(() => '');
    ok('and comparing two trims leaves it at the table\'s own price',
       bothText.includes(cash(twin.own)) && !bothText.includes(cash(twin.dear)),
       `pressing ${twin.other} and ${twin.mine} together, the row reads `
       + JSON.stringify(bothText.replace(/\s+/g, ' ').slice(0, 110)));
  } finally { await ctx.unroute('**/data.json'); }
});

// ---- a car the sheet cannot place is not a car beyond your states ----------
// "Drivable" is state membership and nothing else, so a listing the API
// returned with no state at all falls out of drivable and into every bucket
// opposed to it. The row then read "— · beyond your states": the location cell
// saying it does not know where the car is, and the clause beside it saying
// where the car is not. Three live cars are in that state today and eight VINs
// over the record. They keep the flat shipping estimate — a car that cannot be
// confirmed drivable has to be priced as though it ships — and lose the claim.
await step('a car the sheet cannot place is not beyond your states', async () => {
  plan('the decision tile says the state is missing, not that the car is elsewhere',
       'the scope chip says the same under its dash',
       'the model tile counts it apart from the cars it can place',
       'and so does the watchlist tile');
  const unplaced = (x) => !x.local && !String(x.state || '').trim();
  const subject = WATCHED.find((w) => (SHEET.brands[w.bk].models[w.mk].listings || []).some(unplaced));
  if (!subject) return skipRest('every live car on this sheet carries a state');
  // The decision tile shows one car per shopped model, and today's unplaced
  // cars are not the cheapest of theirs — so the line is reached by serving a
  // sheet in which they are. Blanking the state on every car of one shopped
  // model makes whichever car the tile picks an unplaced one.
  const shopped = WATCHED.find((w) => (SHEET.brands[w.bk].models[w.mk] || {}).shopping);
  if (!shopped) return skipRest('no model on this sheet is being shopped');
  await ctx.route('**/data.json', async (route) => {
    const r = await route.fetch();
    const sheet = JSON.parse(await r.text());
    for (const x of (sheet.brands[shopped.bk].models[shopped.mk].listings || [])) { x.state = ''; x.local = false; }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
  });
  let tileTxt = '', chipTitle = '';
  try {
    await open('');
    tileTxt = (await page.locator('#hero-cars .sc-tile__sub').evaluateAll(
      (ns) => ns.map((n) => n.textContent.trim())
        .filter((t) => /drivable|beyond your states|state not given/.test(t))))[0] || '';
    await open(shopped.q);
    chipTitle = await page.locator('#list-table tbody .sc-chip--case').first()
      .evaluate((n) => n.getAttribute('title') || '').catch(() => '');
  } finally { await ctx.unroute('**/data.json'); }
  ok('the decision tile says the state is missing, not that the car is elsewhere',
     !!tileTxt && /state not given/.test(tileTxt) && !/beyond your states/.test(tileTxt),
     `tile line reads "${tileTxt.slice(0, 140)}"`);
  ok('the scope chip says the same under its dash',
     /State not given/.test(chipTitle) && !/Outside your states/.test(chipTitle),
     `chip titled "${chipTitle}"`);

  await open(subject.q);

  // The tile opposes drivable to beyond, so an unplaced car silently joined
  // the second number. It is named instead, in the map hint's own words.
  const nUn = (SHEET.brands[subject.bk].models[subject.mk].listings || []).filter(unplaced).length;
  const tile = (await page.locator('#kpis .sc-tile__sub').evaluateAll((ns) => ns.map((n) => n.textContent.trim())))
    .find((t) => /drivable/.test(t)) || '';
  const beyond = Number((tile.match(/(\d+) beyond your states/) || [])[1]);
  const total = (SHEET.brands[subject.bk].models[subject.mk].listings || []).length;
  const local = (SHEET.brands[subject.bk].models[subject.mk].listings || []).filter((x) => x.local).length;
  ok('the model tile counts it apart from the cars it can place',
     new RegExp(`${nUn} with no state`).test(tile) && beyond === total - local - nUn,
     `tile says "${tile.slice(0, 110)}" · ${total} cars, ${local} drivable, ${nUn} unplaced`);

  await open('');
  const all = Object.values(SHEET.brands).flatMap((b) => Object.values(b.models || {}))
    .flatMap((m) => m.listings || []);
  const nUnAll = all.filter(unplaced).length;
  const wTile = (await page.locator('#kpis .sc-tile__sub').evaluateAll((ns) => ns.map((n) => n.textContent.trim())))
    .find((t) => /drivable/.test(t) && /model/.test(t)) || '';
  const wBeyond = Number((wTile.match(/(\d+) beyond your states/) || [])[1]);
  ok('and so does the watchlist tile',
     new RegExp(`${nUnAll} with no state`).test(wTile)
     && wBeyond === all.length - all.filter((x) => x.local).length - nUnAll,
     `tile says "${wTile.slice(0, 120)}" · ${all.length} cars, ${nUnAll} unplaced`);
});

// ---- the cut sort's own note may not call a sawtooth flip a cut -------------
// Sorting by "cut at the latest snapshot" prints the number the sort ran on
// beside each row, because the movement badge shows the LIFETIME change and
// would otherwise disagree with the ordering. It was printed off lastStep()
// alone, so a car seen at two prices got "cut $8,010 at the latest snapshot"
// directly under its own movement note reading "seen at $66,699 and $74,709" —
// the page contradicting itself twice in one cell. Seven cars were in that
// state on the day it was found.
await step('the cut sort does not call a sawtooth flip a cut', async () => {
  plan('no car seen at two prices carries a cut note',
       'and a car that really was cut still carries one');
  // The page's own rule, reproduced: two distinct prices, each seen at least
  // twice, with a step in each direction.
  const sawtooth = (x) => {
    const ps = (x.series || []).map((p) => p[1]).filter(Boolean);
    const d = [...new Set(ps)].sort((a, b) => a - b);
    if (d.length !== 2 || ps.length < 4) return false;
    if (d.some((v) => ps.filter((q) => q === v).length < 2)) return false;
    const steps = ps.slice(1).map((v, i) => v - ps[i]).filter(Boolean);
    return steps.some((v) => v < 0) && steps.some((v) => v > 0);
  };
  const down = (x) => (x.series || []).length >= 2
    && x.series[x.series.length - 1][1] < x.series[x.series.length - 2][1];
  const subject = WATCHED.find((w) => {
    const L = SHEET.brands[w.bk].models[w.mk].listings || [];
    return L.some((x) => sawtooth(x) && down(x)) && L.some((x) => !sawtooth(x) && down(x));
  });
  if (!subject) return skipRest('no model on this sheet holds both a sawtooth down-day and a real cut');
  await open(subject.q);
  await page.selectOption('#f-sort', 'cut');
  await page.waitForTimeout(350);
  const more = page.locator('[data-fkey="more:list"]');
  if (await more.count() && await more.isVisible()) { await more.click(); await page.waitForTimeout(600); }
  // Read each row whole, so the VIN and the note are judged together.
  const rows = await page.locator('#list-table tbody tr').evaluateAll(
    (rs) => rs.map((r) => r.innerText.replace(/\s+/g, ' ')));
  const L = SHEET.brands[subject.bk].models[subject.mk].listings || [];
  const tail = (v) => String(v || '').slice(-6).toUpperCase();
  const rowFor = (x) => rows.find((t) => t.toUpperCase().includes(tail(x.vin)));
  const saws = L.filter((x) => sawtooth(x) && down(x)).map(rowFor).filter(Boolean);
  const reals = L.filter((x) => !sawtooth(x) && down(x)).map(rowFor).filter(Boolean);
  const CUT = /cut \$[\d,]+ at the latest snapshot/;
  if (!saws.length) return skipRest('no sawtooth car is on screen under this sort');
  ok('no car seen at two prices carries a cut note',
     saws.every((t) => !CUT.test(t)),
     `${saws.length} sawtooth rows on screen · ${saws.filter((t) => CUT.test(t)).length} still claim a cut`
     + ` · first: "${saws[0].slice(0, 90)}"`);
  if (!reals.length) return skip('and a car that really was cut still carries one',
                                 'no ordinary cut is on screen under this sort');
  ok('and a car that really was cut still carries one',
     reals.some((t) => CUT.test(t)),
     `${reals.length} ordinary cut rows on screen · ${reals.filter((t) => CUT.test(t)).length} carry the note`);
});

// ---- the departures card holds absences, most of which are not departures ---
// The card lists every VIN seen on an earlier snapshot and not on the latest.
// On the i7 that is 114 rows, of which 38 are departures — the other 76 are the
// fetch window moving past a car that is still for sale. The hint has printed
// that split since the still-listed work, and one clause later the button
// offered to "show all 114 departures", which is the claim the split exists to
// refuse. The words around the split now say what the card holds and let the
// split say which of them left.
await step('the departures card counts absences, not departures', async () => {
  plan('the button offers the absences, not a count of departures',
       'and the truncated caption says the same',
       'and so do the heading over it and the link that jumps to it');
  const subject = WATCHED.map((w) => ({ ...w, gone: ((SHEET.brands[w.bk].models[w.mk].gone) || []).length }))
    .sort((a, b) => b.gone - a.gone)[0];
  if (!subject || subject.gone < 2) return skipRest('no model on this sheet has lost enough cars to truncate');
  await open(subject.q);
  const more = page.locator('[data-fkey="more:gone"]');
  if (!await more.count()) return skipRest(`${subject.id} shows all ${subject.gone} of its absences at once`);
  const label = (await more.textContent()).trim();
  // The noun matters: "Show all 114 missing from the latest snapshot" is a
  // sentence fragment, which is how the first pass at this left it.
  ok('the button offers the absences, not a count of departures',
     /^Show all \d+ vehicles missing from the latest snapshot$/.test(label)
     && !/\bdepartures\b/.test(label), `button says "${label}"`);
  const hint = (await page.textContent('#gone-hint')) || '';
  // The hint still NAMES departures — "confirmed departures first" is the sort
  // order, which is true — so this asks only that the noun for the whole set
  // is not "departed vehicles".
  ok('and the truncated caption says the same',
     /missing from the latest snapshot/.test(hint) && !/departed vehicles/.test(hint),
     `hint says "${hint.slice(0, 120)}"`);

  // The caption and the button were fixed first and the two labels around them
  // were not, so the card carried its own denial in three places: a heading
  // reading "Gone from the market", a movement-tile link titled "Jump to the
  // departed-vehicles list", and a caption saying 76 of the 114 had not gone.
  const head = ((await page.textContent('#gone-title')) || '').trim();
  const jump = await page.locator('[data-fkey="kpi:gone"]')
    .evaluateAll((ns) => ns.map((n) => n.getAttribute('title') || '(no title)'));
  ok('and so do the heading over it and the link that jumps to it',
     !/\bgone\b/i.test(head) && !/departed/i.test(head)
     && jump.length > 0 && jump.every((t) => !/departed/i.test(t)),
     `heading "${head}" · ${jump.length} jump link(s) titled ${JSON.stringify(jump)}`);
});

// --- the market studio is visible work, not a hidden component -------------
// The decision matrix describes the SAME exact cars already selected by the
// decision card. It is not another scoring or selection implementation. These
// checks pin identity, evidence and source uncertainty, then prove the new
// first-screen composition at the three widths in both themes. Fixtures are
// served through the ordinary data request; no test-only page APIs are needed.
const studioRows = () => page.locator('#decision-matrix tbody tr[data-signal-vin]').evaluateAll((rows) => rows.map((row) => ({
  vin: row.dataset.signalVin,
  cells: [...row.querySelectorAll('td')].map((td) => ({
    label: td.querySelector('.sc-signal__label')?.textContent.trim() || '',
    note: td.querySelector('.market-matrix__note')?.textContent.trim() || '',
    title: td.querySelector('.sc-signal')?.title || '',
    tone: (td.querySelector('.sc-signal')?.className.match(/sc-signal--(\w+)/) || [])[1] || 'neutral',
  })),
})));
const studioHero = () => page.locator('#hero-cars .sc-tile').evaluateAll((tiles) => tiles.map((tile) => ({
  vin: tile.querySelector('[data-fkey^="hero:"]')?.getAttribute('data-fkey').slice(5) || '',
  delta: tile.querySelector('.sc-delta')?.textContent.trim() || '',
  cohort: tile.querySelector('.sc-delta')?.title || '',
})).filter((tile) => tile.vin));
const studioSource = new Map(WATCHED.flatMap((w) => (SHEET.brands[w.bk].models[w.mk].listings || []).map((x) => [x.vin, x])));

await step('market studio identity and source facts', async () => {
  plan('the signal matrix is visible by default and names exactly the decision candidates',
       'the signal matrix remains a named native table with text alternatives',
       'each signal agrees with the exact listing and existing value evidence',
       'candidate photography comes from that candidate VIN, never a model substitute',
       'the matrix leads the decision and the map precedes the picks and listings');
  await open('');
  const hero = await studioHero();
  if (!hero.length) return skipRest('the sheet has no eligible configured shopping candidate');
  const rows = await studioRows();
  const vins = hero.map((h) => h.vin);
  ok('the signal matrix is visible by default and names exactly the decision candidates',
     await page.locator('#signal-card').isVisible() && JSON.stringify(rows.map((r) => r.vin)) === JSON.stringify(vins),
     `decision ${vins.join(', ')} · matrix ${rows.map((r) => r.vin).join(', ')}`);
  const native = await page.locator('#decision-matrix').evaluate((table) => ({
    tag: table.tagName, matrix: table.classList.contains('sc-signal-matrix'),
    caption: table.querySelector('caption')?.textContent.trim() || '',
    columns: [...table.querySelectorAll('thead th')].map((th) => [th.textContent.trim(), th.scope]),
    rowHeaders: [...table.querySelectorAll('tbody tr')].every((tr) => tr.firstElementChild?.tagName === 'TH' && tr.firstElementChild.scope === 'row'),
    glyphsHidden: [...table.querySelectorAll('.sc-signal__glyph')].every((glyph) => glyph.getAttribute('aria-hidden') === 'true'),
  }));
  ok('the signal matrix remains a named native table with text alternatives', native.tag === 'TABLE' && native.matrix && native.caption.length > 20
     && JSON.stringify(native.columns) === JSON.stringify(['Car', 'Value context', 'Reach', 'Certification', 'Accident record'].map((name) => [name, 'col']))
     && native.rowHeaders && native.glyphsHidden, JSON.stringify(native));
  const wrong = rows.filter((row) => {
    const x = studioSource.get(row.vin), h = hero.find((tile) => tile.vin === row.vin);
    if (!x || !h || row.cells.length !== 4) return true;
    const [value, reach, cpo, accident] = row.cells;
    const valueLabel = /% under a typical/.test(h.delta) ? (h.delta.match(/\d+%/) || [''])[0] + ' under typical'
      : /% over a typical/.test(h.delta) ? (h.delta.match(/\d+%/) || [''])[0] + ' over typical'
      : /^about typical/.test(h.delta) ? 'About typical' : 'Not enough evidence';
    const valueTone = /% under a typical/.test(h.delta) ? 'good' : /% over a typical/.test(h.delta) ? 'caution' : 'neutral';
    return value.label !== valueLabel || value.tone !== valueTone || value.title !== h.cohort
      || reach.label !== (x.local ? 'Drivable' : !String(x.state || '').trim() ? 'State not reported' : 'Shipping')
      || reach.tone !== (x.local || String(x.state || '').trim() ? 'info' : 'neutral')
      || cpo.label !== (x.cpo === true ? 'CPO' : 'Not marked CPO') || cpo.tone !== (x.cpo === true ? 'good' : 'neutral')
      || accident.label !== (x.accidents === 0 ? 'None reported' : x.accidents > 0 ? `${x.accidents} reported` : 'Unknown')
      || accident.tone !== (x.accidents === 0 ? 'good' : x.accidents > 0 ? 'caution' : 'neutral');
  });
  ok('each signal agrees with the exact listing and existing value evidence', rows.length > 0 && !wrong.length,
     wrong.length ? JSON.stringify(wrong[0]) : `${rows.length} exact VINs; value/cohort, reach, CPO and accident count agree`);

  // The normal harness replaces dealer photos with a pixel to test fallback.
  // Here use an inert image larger than thumbFor's rejection threshold so the
  // requested src remains inspectable. Its contents are not a visual baseline.
  const photos = new Set([...studioSource.values()].map((x) => x.image).filter((url) => /^https?:\/\//.test(url || '')));
  const photoRoute = (url) => photos.has(url.href);
  await ctx.route(photoRoute, (route) => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><rect width="80" height="60" fill="#999"/></svg>' }));
  try {
    await open('');
    const frames = await page.locator('#hero-cars .market-candidate').evaluateAll((tiles) => tiles.map((tile) => ({
      vin: tile.querySelector('[data-fkey^="hero:"]')?.getAttribute('data-fkey').slice(5),
      src: tile.querySelector('.sc-frame__img')?.getAttribute('src') || '',
      alt: tile.querySelector('.sc-frame__img')?.alt || '', empty: !!tile.querySelector('.sc-frame--empty'),
    })));
    const bad = frames.filter((frame) => {
      const source = studioSource.get(frame.vin);
      return !source || (photos.has(source.image) ? frame.src !== source.image || frame.alt !== '' : !frame.empty);
    });
    ok('candidate photography comes from that candidate VIN, never a model substitute', frames.length === vins.length && !bad.length,
       bad.length ? JSON.stringify(bad[0]) : `${frames.length} candidate-specific photos or explicit empty frames`);
  } finally { await ctx.unroute(photoRoute); }
  const order = await page.evaluate(() => {
    const before = (a, b) => !!(document.getElementById(a).compareDocumentPosition(document.getElementById(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
    return { sibling: document.getElementById('signal-card').nextElementSibling?.id,
      mapAfterFilters: before('filters-card', 'map-card'), mapAfterComparisons: before('finalists-card', 'map-card'),
      mapBeforePicks: before('map-card', 'takeaway'), mapBeforeList: before('map-card', 'list-card') };
  });
  ok('the matrix leads the decision and the map precedes the picks and listings', order.sibling === 'hero-card'
     && order.mapAfterFilters && order.mapAfterComparisons && order.mapBeforePicks && order.mapBeforeList, JSON.stringify(order));
});

await step('market studio uncertainty fixtures', async () => {
  plan('unknown and undersampled facts never receive a positive signal',
       'explicit certification and zero accidents keep their separate positive signals',
       'reported accidents remain caution and malformed certification remains neutral',
       'no matching candidates hides the matrix and its cover link');
  const candidates = WATCHED.filter((w) => Object.keys(SHEET.brands[w.bk].models[w.mk].trims || {}).some((id) => (SHEET.buyer?.shopping || []).includes(id)));
  if (!candidates.length) return skipRest('there are no configured shopping models to serve fixtures for');
  const serve = async (mutate, check) => {
    const sheet = structuredClone(SHEET);
    sheet.buyer.picks = { ...sheet.buyer.picks, exclude_accidents: false, exclude_rental: false };
    mutate(sheet);
    await ctx.route('**/data.json', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) }));
    try { await open(''); await check(await studioRows()); }
    finally { await ctx.unroute('**/data.json'); }
  };
  const allCars = (sheet, fn) => {
    for (const brand of Object.values(sheet.brands || {})) for (const model of Object.values(brand.models || {})) {
      for (const x of model.listings || []) fn(x);
    }
  };
  await serve((sheet) => {
    for (const w of candidates) {
      const model = sheet.brands[w.bk].models[w.mk];
      model.listings = (model.listings || []).filter((x) => sheet.buyer.shopping.includes(x.trim_id) && x.price != null && x.miles != null && x.miles <= (sheet.buyer.picks.max_miles || 50000)).slice(0, 1);
    }
    allCars(sheet, (x) => { x.cpo = null; x.accidents = null; x.local = false; x.state = null; });
  }, async (rows) => {
    ok('unknown and undersampled facts never receive a positive signal', rows.length > 0 && rows.every((r) =>
      JSON.stringify(r.cells.map((c) => c.label)) === JSON.stringify(['Not enough evidence', 'State not reported', 'Not marked CPO', 'Unknown'])
      && r.cells.every((c) => c.tone === 'neutral')), JSON.stringify(rows));
  });
  await serve((sheet) => allCars(sheet, (x) => { x.cpo = true; x.accidents = 0; x.local = true; x.state = 'IL'; }), async (rows) => {
    ok('explicit certification and zero accidents keep their separate positive signals', rows.length > 0 && rows.every((r) =>
      r.cells[1].label === 'Drivable' && r.cells[1].tone === 'info' && r.cells[2].label === 'CPO' && r.cells[2].tone === 'good'
      && r.cells[3].label === 'None reported' && r.cells[3].tone === 'good'), `${rows.length} explicit source records`);
  });
  await serve((sheet) => allCars(sheet, (x) => { x.cpo = 'false'; x.accidents = 2; x.local = false; x.state = 'CA'; }), async (rows) => {
    ok('reported accidents remain caution and malformed certification remains neutral', rows.length > 0 && rows.every((r) =>
      r.cells[1].label === 'Shipping' && r.cells[1].tone === 'info' && r.cells[2].label === 'Not marked CPO' && r.cells[2].tone === 'neutral'
      && r.cells[3].label === '2 reported' && r.cells[3].tone === 'caution'), `${rows.length} records, no inspection or title verdict inferred`);
  });
  await serve((sheet) => allCars(sheet, (x) => { x.price = null; }), async (rows) => {
    ok('no matching candidates hides the matrix and its cover link', !rows.length && await page.locator('#signal-card').isHidden()
      && await page.locator('.market-cover__index a[href="#signal-card"]').evaluate((a) => a.hidden), 'an empty candidate set produces no stale matrix');
  });
});

await step('market studio keyboard navigation', async () => {
  plan('the cover index uses keyboard-reachable native anchors to visible sections',
       'the signal matrix is a named focusable region that scrolls by keyboard',
       'opening a signal candidate preserves its exact VIN and hides overview-only navigation');
  await page.setViewportSize({ width: 390, height: 844 });
  try {
    await open('');
    if (!(await studioHero()).length) return skipRest('no configured candidate on this sheet');
    const anchors = page.locator('.market-cover__index a:not([hidden])');
    const links = await anchors.evaluateAll((as) => as.map((a) => ({ href: a.getAttribute('href'), visible: !!document.querySelector(a.getAttribute('href'))?.getBoundingClientRect().height, tag: a.tagName })));
    await page.keyboard.press('Tab'); // establish keyboard modality before testing :focus-visible
    await anchors.first().focus();
    const firstFocus = await anchors.first().evaluate((a) => document.activeElement === a && getComputedStyle(a).outlineStyle !== 'none');
    await page.keyboard.press('Tab');
    const secondFocus = links.length < 2 || await anchors.nth(1).evaluate((a) => document.activeElement === a);
    ok('the cover index uses keyboard-reachable native anchors to visible sections', links.length > 1 && links.every((a) => a.tag === 'A' && a.visible
      && ['#hero-card', '#signal-card', '#map-card', '#list-card'].includes(a.href)) && firstFocus && secondFocus, JSON.stringify(links));
    const region = page.locator('#signal-card .sc-table-scroll');
    await region.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(200);
    const scroll = await region.evaluate((r) => ({ role: r.getAttribute('role'), label: r.getAttribute('aria-label'), focused: document.activeElement === r,
      outline: getComputedStyle(r).outlineStyle, left: r.scrollLeft, needsScroll: r.scrollWidth > r.clientWidth, described: r.getAttribute('aria-describedby') }));
    ok('the signal matrix is a named focusable region that scrolls by keyboard', scroll.role === 'region' && !!scroll.label && scroll.focused
      && scroll.outline !== 'none' && !!scroll.described && (!scroll.needsScroll || scroll.left > 0), JSON.stringify(scroll));
    const link = page.locator('#decision-matrix [data-fkey^="signal:"]').first();
    const vin = (await link.getAttribute('data-fkey')).slice(7);
    await link.focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(400);
    const hidden = await page.locator('.market-cover__index a').evaluateAll((as) => as.every((a) => a.hidden === !!document.querySelector(a.getAttribute('href'))?.hidden));
    const landed = await page.evaluate((id) => {
      const star = document.querySelector(`#list-card [data-fkey="star:${id}"]`);
      const row = star?.closest('tr') || star?.closest('.sc-media');
      return !!row && (document.activeElement === row || row.contains(document.activeElement));
    }, vin);
    ok('opening a signal candidate preserves its exact VIN and hides overview-only navigation', await page.locator('#signal-card').isHidden()
      && await page.locator('#hero-card').isHidden() && hidden && landed,
      `focused listing ${vin}; ${page.url().split('/index.html')[1]}`);
  } finally { await page.setViewportSize({ width: 1280, height: 1000 }); }
});

await step('market studio cover links follow scoped filters', async () => {
  plan('cover links update when a budget hides sections and recover when it is cleared');
  await page.setViewportSize({ width: 1280, height: 1000 });
  await open('');
  if (!(await studioHero()).length) return skipRest('no configured candidate to remove with a budget');
  const linkState = () => page.locator('.market-cover__index a').evaluateAll((as) => as.map((a) => ({
    href: a.getAttribute('href'), hidden: a.hidden, targetHidden: document.querySelector(a.getAttribute('href'))?.hidden,
  })));
  try {
    await page.fill('#f-budget', '1');
    await page.locator('#f-budget').press('Tab');
    await page.waitForTimeout(400);
    const empty = await linkState();
    const matrixGone = await page.locator('#signal-card').isHidden();
    await page.fill('#f-budget', '');
    await page.locator('#f-budget').press('Tab');
    await page.waitForTimeout(400);
    const restored = await linkState();
    ok('cover links update when a budget hides sections and recover when it is cleared', matrixGone
      && await page.locator('#signal-card').isVisible()
      && empty.some((l) => l.href === '#signal-card' && l.hidden)
      && restored.some((l) => l.href === '#signal-card' && !l.hidden)
      && [...empty, ...restored].every((l) => l.hidden === !!l.targetHidden), JSON.stringify({ empty, restored }));
  } finally { await page.evaluate(() => localStorage.removeItem('spicycar.prefs')); }
});

await step('market studio responsive first screen', async () => {
  const sizes = [[390, 844], [820, 1180], [1280, 1000]];
  plan(...sizes.flatMap(([width]) => ['light', 'dark'].map((theme) => `the market studio fits ${width}px in ${theme} with a useful first screen and readable signals`)));
  for (const [width, height] of sizes) for (const theme of ['light', 'dark']) {
    const name = `the market studio fits ${width}px in ${theme} with a useful first screen and readable signals`;
    await page.setViewportSize({ width, height });
    await open('');
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
    await page.waitForTimeout(400);
    if (!(await studioHero()).length) { skip(name, 'no shopping candidate exists on this sheet'); continue; }
    const geometry = await page.evaluate(() => {
      const first = document.querySelector('#decision-matrix .sc-signal__label');
      const rect = first?.getBoundingClientRect();
      const bgFor = (node) => {
        for (let n = node; n; n = n.parentElement) {
          const c = getComputedStyle(n).backgroundColor.match(/[\d.]+/g)?.map(Number);
          if (c?.length === 3 || c?.[3] === 1) return c.slice(0, 3);
        }
        return [255, 255, 255];
      };
      const lum = (c) => c.map((v) => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
      const contrast = [...document.querySelectorAll('#decision-matrix .sc-signal__label')].map((label) => {
        const fg = getComputedStyle(label).color.match(/[\d.]+/g).slice(0, 3).map(Number), bg = bgFor(label);
        const [a, b] = [lum(fg), lum(bg)].sort((a, b) => b - a);
        return (a + .05) / (b + .05);
      });
      return { studio: document.body.classList.contains('market-studio'), width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        firstSignal: rect && { top: rect.top, bottom: rect.bottom, text: first.textContent }, height: innerHeight,
        contrast: Math.min(...contrast), labels: contrast.length,
        photos: document.querySelectorAll('#hero-cars .market-candidate .sc-frame').length };
    });
    ok(name, geometry.studio && geometry.scrollWidth <= geometry.width + 1 && geometry.firstSignal?.top >= 0
      && geometry.firstSignal.bottom < geometry.height && geometry.photos > 0 && geometry.labels > 0 && geometry.contrast >= 4.5,
      JSON.stringify(geometry));
    await shot(`market-studio-${width}-${theme}`);
    await page.locator('#signal-card').scrollIntoViewIfNeeded();
    await shot(`signal-matrix-${width}-${theme}`);
    await page.locator('#hero-card').scrollIntoViewIfNeeded();
    await shot(`decision-cards-${width}-${theme}`);
  }
  await page.setViewportSize({ width: 1280, height: 1000 });
});

await step('market studio motion and alternate rendering', async () => {
  plan('reduced motion leaves the market studio complete without running animation',
       'forced colors retains text labels and distinct matrix cell borders',
       'printing exposes all signal columns without sticky clipping');
  await open('');
  if (!(await studioHero()).length) return skipRest('no configured shopping candidate for alternate rendering');
  try {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(400);
    const motion = await page.evaluate(() => ({ reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
      active: document.getAnimations().filter((a) => a.playState === 'running' && a.effect?.target?.closest('.market-cover, #hero-card, #signal-card')).length,
      candidates: document.querySelectorAll('#hero-cars .market-candidate').length,
      matrix: !document.getElementById('signal-card').hidden }));
    ok('reduced motion leaves the market studio complete without running animation', motion.reduced && !motion.active && motion.candidates > 0 && motion.matrix, JSON.stringify(motion));
    await shot('market-studio-reduced-motion');
    await page.emulateMedia({ forcedColors: 'active' });
    await page.locator('#signal-card').scrollIntoViewIfNeeded();
    const forced = await page.locator('#decision-matrix tbody td').evaluateAll((cells) => cells.map((cell) => ({
      text: cell.textContent.trim(), border: getComputedStyle(cell).borderStyle, color: getComputedStyle(cell).color,
    })));
    ok('forced colors retains text labels and distinct matrix cell borders', forced.length > 0 && forced.every((cell) => cell.text && cell.border !== 'none'), `${forced.length} labeled bordered cells`);
    await shot('signal-matrix-forced-colors');
    await page.emulateMedia({ forcedColors: 'none', media: 'print' });
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
    const print = await page.locator('#decision-matrix').evaluate((table) => ({
      width: table.getBoundingClientRect().width, parent: table.parentElement.getBoundingClientRect().width,
      overflow: getComputedStyle(table.parentElement).overflowX,
      sticky: [...table.querySelectorAll('th')].some((th) => getComputedStyle(th).position === 'sticky'),
      labels: [...table.querySelectorAll('.sc-signal__label')].every((label) => getComputedStyle(label).display !== 'none' && label.getBoundingClientRect().height > 0),
    }));
    ok('printing exposes all signal columns without sticky clipping', print.width > 0 && print.width <= print.parent + 1
      && print.overflow === 'visible' && !print.sticky && print.labels, JSON.stringify(print));
    await shot('signal-matrix-print');
  } finally {
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    await page.emulateMedia({ media: 'screen', reducedMotion: 'no-preference', forcedColors: 'none' });
  }
});

await step('market studio shortlist benchmark sizing', async () => {
  plan('shortlist value rails use the matrix value variant and fit their own cells');
  await open('');
  const vins = (await studioHero()).map((h) => h.vin).slice(0, 2);
  if (vins.length < 2) return skipRest('fewer than two shopping candidates to build a shortlist');
  // The same local selection record the existing shortlist tests exercise.
  await page.evaluate((ids) => localStorage.setItem('spicycar.prefs', JSON.stringify({ stars: Object.fromEntries(ids.map((vin) => [vin, 'short'])) })), vins);
  await open('');
  const rails = await page.locator('#finalists-table').evaluate((table) => ({
    values: table.classList.contains('sc-signal-matrix--values'),
    figures: [...table.querySelectorAll('.sc-benchmark')].map((figure) => ({ width: figure.getBoundingClientRect().width, cell: figure.closest('td').getBoundingClientRect().width,
      track: figure.querySelector('.sc-benchmark__track').getBoundingClientRect().height,
      point: figure.querySelector('.sc-benchmark__point').getBoundingClientRect().width })),
  }));
  if (!rails.figures.length) skip('shortlist value rails use the matrix value variant and fit their own cells', 'neither selected car has a scored cohort');
  else ok('shortlist value rails use the matrix value variant and fit their own cells', rails.values
    && rails.figures.every((r) => r.width > 0 && r.width <= r.cell && r.track === 16 && r.point === 10), JSON.stringify(rails));
  await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
});

// --- what it costs to open -------------------------------------------------
// The page is one static file and one JSON file, and both grow every time a
// car joins the watchlist or a feature lands. Nothing measured them, so "how
// heavy is this page" was a number nobody had — and the raw byte counts, which
// are what `ls` reports and what everyone quotes, are not what a reader waits
// for: text is served compressed. Both figures are printed on every run so the
// trend is visible in a CI log, and the budget is set on the COMPRESSED size,
// which is the one the reader pays.
//
// Deliberately generous, and deliberately not a target to optimise towards: it
// is a tripwire for a change that adds a megabyte, not a style rule. Raise it
// on purpose, in the commit that needs it, the same way EXPECTED is raised.
await step('what it costs to open', async () => {
  plan('the page and its data stay inside their transfer budget');
  const { gzipSync } = await import('node:zlib');
  const BUDGET = { 'index.html': 200 * 1024, 'data.json': 250 * 1024 };
  const rows = [];
  for (const name of Object.keys(BUDGET)) {
    const file = join(ROOT, name);
    if (!existsSync(file)) continue;
    const raw = readFileSync(file);
    rows.push({ name, raw: raw.length, gz: gzipSync(raw, { level: 9 }).length, budget: BUDGET[name] });
  }
  if (!rows.length) return skipRest('neither file is beside this harness');
  const over = rows.filter((r) => r.gz > r.budget);
  const kb = (n) => `${(n / 1024).toFixed(0)}KB`;
  ok('the page and its data stay inside their transfer budget', over.length === 0,
     rows.map((r) => `${r.name} ${kb(r.raw)} raw, ${kb(r.gz)} compressed`
                     + (r.gz > r.budget ? ` — OVER its ${kb(r.budget)} budget` : ''))
         .join(' · ')
     + ` · together ${kb(rows.reduce((a, r) => a + r.gz, 0))} on the wire`);
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
// fewer subjects — but a skip is a RECORDED check, so the total is the declared
// one whether a check found a subject or not. The assertion used to be made only
// `when nothing skipped`, and the committed sheet produces one skip on every run,
// so on the configuration CI actually executes this guard has never once fired:
// six checks were added across two commits and it said nothing. (The per-step
// guard above, added for the same reason, says as much in its own comment and
// then leaves this one disarmed.) What the condition really needed was for the
// declared total not to move with the data, which is a property of the branches
// and not of this line — see GHOST, and the two-theme skip beside it.
// If you ADD a check, raise this number in the same commit. That is the point.
const EXPECTED = 322;
if (!ONLY && results.length !== EXPECTED) {
  console.log(`\n  !! this suite declares ${EXPECTED} checks and recorded ${results.length}`
    + `${skipped ? ` (${skipped} of them skipped, which still counts)` : ''}.`);
  console.log('     A check was lost or added silently.');
}
process.exit(failed || errors.length || (!ONLY && results.length !== EXPECTED) ? 1 : 0);
