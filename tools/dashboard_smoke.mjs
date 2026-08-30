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
import { readFile, mkdir, writeFile } from 'node:fs/promises';
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

// --- the phone -------------------------------------------------------------
await page.setViewportSize({ width: 390, height: 844 });
await open('?models=bmw-i5,bmw-ix');
ok('the comparison survives a phone', await page.locator('#compare-card').isVisible());
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok('and does not scroll the page sideways', overflow <= 1, `${overflow}px of overflow`);
await shot('compare-phone');

await browser.close();
server.close();

for (const r of results) console.log(`  ${r.pass ? 'ok  ' : 'FAIL'}  ${r.name}${r.detail ? '  — ' + r.detail : ''}`);
if (errors.length) { console.log('\n  the page logged errors:'); for (const e of [...new Set(errors)]) console.log('      - ' + e); }
const failed = results.filter((r) => !r.pass).length;
console.log(`\ndashboard smoke: ${results.length - failed}/${results.length} checks, ${errors.length} page error${errors.length === 1 ? '' : 's'}`);
process.exit(failed || errors.length ? 1 : 0);
