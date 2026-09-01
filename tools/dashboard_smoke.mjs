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
// A check whose SUBJECT is not in today's data has not passed and has not
// failed — it had nothing to look at. Saying so keeps the count honest about
// what was actually covered; scoring it a pass would quietly retire the check
// on the day the tracker stops watching the car it needed.
const skip = (name, detail = '') => results.push({ name, skip: true, detail });
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
// car tests the config instead of the page. No subject today is a skip, not a
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

// "Only this →" takes its own card off the page; the keyboard must land
// somewhere, not on <body>.
await open('?brand=bmw&m=i5&trims=bmw-i5-edrive40,bmw-i5-xdrive40');
await page.evaluate(() => document.querySelector('[data-fkey^="cmp:"]').focus());
await page.keyboard.press('Enter');
await page.waitForTimeout(400);
ok('narrowing to one column keeps the keyboard somewhere',
  (await page.evaluate(() => document.activeElement.tagName)) !== 'BODY');

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
// The SUBJECT is read out of the sheet, never typed here. The tracker adds and
// retires models and trims nightly, so a check that names the i7 asserts the
// watchlist's contents alongside the page's behaviour and goes red on a night
// that only changed the watchlist. Take the first model the sheet gives more
// than one trim (buildFilters hides #f-trim-field below two) and press its
// busiest chip. A night with no such model is a thinner watchlist, not a
// broken dashboard: say skip and assert nothing, the way a missing chromium
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

await browser.close();
server.close();

for (const r of results) console.log(`  ${r.skip ? 'skip' : r.pass ? 'ok  ' : 'FAIL'}  ${r.name}${r.detail ? '  — ' + r.detail : ''}`);
if (errors.length) { console.log('\n  the page logged errors:'); for (const e of [...new Set(errors)]) console.log('      - ' + e); }
const failed = results.filter((r) => !r.pass && !r.skip).length;
const skipped = results.filter((r) => r.skip).length;
console.log(`\ndashboard smoke: ${results.length - skipped - failed}/${results.length - skipped} checks`
  + `${skipped ? `, ${skipped} skipped for want of a subject` : ''}, ${errors.length} page error${errors.length === 1 ? '' : 's'}`);
process.exit(failed || errors.length ? 1 : 0);
