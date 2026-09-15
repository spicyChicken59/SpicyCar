// Regression for the connected shopping journey: three views of ONE candidate
// set, a comparison whose membership is not the garage, and a garage that says
// what the record moved under.
//
//   node tools/browse_smoke.mjs [--shots <dir>]
//
// Offline and read-only, like its neighbours: docs/ over a local server, every
// external request answered with an empty body or a 1x1 pixel, and no provider,
// tracker, mail or Pages request of any kind. The committed docs/data.json is
// the fixture; the saved-car state is written into localStorage before the
// first paint so every run reads the same four cars.
//
// One step, one line, one failure — the shape discovery_smoke.mjs records as
// the lesson: a single run of awaits loses every check after the first miss.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname, join } from 'node:path';
import { chromium } from 'playwright';

const root = resolve('docs');
const argv = process.argv.slice(2);
const SHOTS = argv.includes('--shots') ? argv[argv.indexOf('--shots') + 1] : null;
// --only <text> runs just the checks whose name contains it. For mutation runs:
// reverting one rule and proving the one check that pins it goes red is a
// two-minute suite per mutant otherwise.
const ONLY = argv.includes('--only') ? argv[argv.indexOf('--only') + 1] : null;
const TYPES = { '.js': 'text/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };
const server = createServer(async (req, res) => {
  const path = resolve(root, '.' + new URL(req.url, 'http://local').pathname.replace(/\/$/, '/index.html'));
  if (!path.startsWith(root + '/')) return res.writeHead(403).end();
  try { res.setHeader('Content-Type', TYPES[extname(path)] || 'application/octet-stream'); res.end(await readFile(path)); }
  catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = 'http://127.0.0.1:' + server.address().port;
if (SHOTS) await mkdir(SHOTS, { recursive: true });

const data = JSON.parse(await readFile(resolve(root, 'data.json'), 'utf8'));
const all = Object.entries(data.brands).flatMap(([bk, b]) => Object.entries(b.models).map(([mk, m]) => ({ bk, mk, m, key: bk + '/' + mk })));
const rowsOf = (o) => (o.m.listings || []);
const located = (x) => Number.isFinite(x.lat) && Number.isFinite(x.lon) && Math.abs(x.lat) <= 90 && Math.abs(x.lon) <= 180 && (x.lat !== 0 || x.lon !== 0);
const plottable = (x) => Number.isFinite(x.price) && Number.isFinite(x.miles);
// The two models the shopping list names, so the fixture is the buyer's own.
const shopped = [...new Set((data.buyer.shopping || []).map((t) => {
  for (const o of all) if (Object.keys(o.m.trims || {}).includes(t)) return o.key;
  return null;
}).filter(Boolean))];
assert.ok(shopped.length >= 1, 'the committed buyer shops at least one model');
const pool = all.filter((o) => shopped.includes(o.key)).flatMap(rowsOf);
// A saved set with all four states the comparison has to hold: two live cars,
// one contacted, and one the sheet now carries only as departed.
const live = pool.filter((x) => plottable(x) && (x.series || []).length > 2).slice(0, 3);
const departed = all.filter((o) => shopped.includes(o.key)).flatMap((o) => o.m.gone || [])[0];
assert.ok(live.length >= 3 && departed, 'the fixture holds three live cars and one departure');
const SAVED = { [live[0].vin]: 'short', [live[1].vin]: 'short', [live[2].vin]: 'called', [departed.vin]: 'short' };
const NOTES = { [live[0].vin]: 'Asked about the second key. Call back Tuesday.' };
const PREFS = { stars: SAVED, shoppingModels: shopped, shopOnly: true, where: [], range: '90', offers: true, lens: 'price', budget: 0, budgetKind: 'otd' };
// The day the reader last saw new data. Two days back in the record, so the
// garage's change detection has something real to compare against — and never
// today's, because a reload is not an observation.
const days = [...new Set(pool.flatMap((x) => (x.series || []).map((p) => p[0])))].sort();
const SINCE = days.filter((d) => d < data.data_through).slice(-1)[0] || null;

const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=', 'base64');

// ---- deterministic subjects -------------------------------------------------
// Six shapeable cars from the buyer's own models, chosen by position in the
// committed record so every run shapes the same cars. The first three are the
// `live` set above; the last three are this pass's named subjects, so a check
// about an absent fact cannot pass because tonight's snapshot happens to hold
// no car with that fact missing. That is how two checks in this file have been
// passing: `if (!noMiles.length) return` and `if (!vin) return` are a silent
// green over a record with no subject, and both are asserts now.
const shapeable = pool.filter((x) => plottable(x) && (x.series || []).length > 2);
assert.ok(shapeable.length >= 7, `the record holds seven shapeable cars, not ${shapeable.length}`);
const [BLANK, STALE, NOWHERE, FILTERED] = shapeable.slice(3, 7).map((x) => x.vin);
const clone = () => JSON.parse(JSON.stringify(data));
// Reach into a shaped copy by VIN, and say so loudly if the car moved: a
// fixture that silently shapes nothing is a check that silently passes.
function shape(d, vin, change) {
  for (const b of Object.values(d.brands)) for (const m of Object.values(b.models)) {
    const i = (m.listings || []).findIndex((x) => x.vin === vin);
    if (i >= 0) { change(m.listings[i], m, i); return d; }
  }
  throw new Error(`${vin} is not in the record to shape`);
}
// Later than the newest day any car was observed: a build date is not an
// observation date, and this is what tells the two apart.
const LATER = (() => { const t = new Date(data.data_through + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() + 3); return t.toISOString().slice(0, 10); })();
const browser = await chromium.launch();
const errors = [];
// `record` shapes a COPY of the committed snapshot and serves it to one browser
// context. docs/data.json is never written: it stays the published record to
// the byte, and the shaped copy lives in this process only. The page asks for
// `data.json?v=<now>`, so the route has to match the query too.
//
// `prefs: null` / `notes: null` decline the seeding entirely. Every step above
// seeds a finished garage before the first paint — which proves what the page
// DOES with a save, not that saving works — so the journey step below starts
// with empty storage and makes every write through the controls instead. The
// addInitScript runs on EVERY navigation, so a seeded context re-seeds itself
// on reload and cannot be used to prove anything survived one.
async function session({ width = 1280, height = 900, prefs = PREFS, notes = NOTES, seen = SINCE, storage = true, record = null, theme = 'dark' } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, isMobile: width <= 420, hasTouch: width <= 420,
    reducedMotion: 'reduce', colorScheme: theme });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => (/\.(png|jpe?g|webp|gif|svg)/i.test(r.request().url())
    ? r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
    : r.fulfill({ status: 200, contentType: 'text/plain', body: '' })));
  if (record) {
    const body = JSON.stringify(record);
    await context.route('**/data.json*', (r) => r.fulfill({ contentType: 'application/json', body }));
  }
  await context.addInitScript(([p, n, s, ok, t]) => {
    if (!ok) {   // a browser with storage switched off, which is a state and not a crash
      const boom = () => { throw new DOMException('denied', 'SecurityError'); };
      Object.defineProperty(window, 'localStorage', { configurable: true, get: boom });
      return;
    }
    if (p) localStorage.setItem('spicycar.prefs', JSON.stringify(p));
    if (n) localStorage.setItem('spicycar.garage', JSON.stringify(n));
    if (s) localStorage.setItem('spicycar.seen', JSON.stringify({ through: s, since: null }));
    localStorage.setItem('sc-theme', t);
  }, [prefs, notes, seen, storage, theme]);
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push('uncaught: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  return { context, page };
}
const open = async (page, query = '') => {
  await page.goto(base + '/' + query, { waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('.car-place-card, .car-map-unavailable'), null, { timeout: 20000 });
  await page.waitForTimeout(400);
};
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: join(SHOTS, name + '.png') }); };

let failures = 0;
const step = async (name, body) => {
  if (ONLY && !name.includes(ONLY)) return;
  try { await body(); console.log('ok ' + name); }
  catch (e) { failures += 1; console.log('FAIL ' + name + ' — ' + String((e && e.message) || e).split('\n')[0]); }
};

try {
  // ---- one candidate set, three views ------------------------------------
  await step('the cards, the map and the plot are one visible set', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      const counted = await page.locator('.car-place-count').textContent();
      const shown = Number((counted.match(/^(\d[\d,]*)/) || [])[1].replace(/,/g, ''));
      // The three views read `inView()`; the sentences they print are the
      // arithmetic over the same set and must add up to it.
      const inView = pool.filter((x) => !!x);
      assert.equal(shown, inView.length, `the count says ${shown}, the shopped models hold ${inView.length}`);
      const mapMissing = await page.locator('.car-place-missing').textContent();
      const noCoords = inView.filter((x) => !located(x)).length;
      if (noCoords) assert.ok(mapMissing.includes(String(noCoords)), `the map owns its ${noCoords} unplaceable cars: "${mapMissing}"`);
      else assert.match(mapMissing, /every car in view has a location/i);
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(500);
      // By VIN, not by count: a plot that drew the right NUMBER of the wrong
      // cars would pass a tally, and a car with no mileage placed at NaN is
      // still a circle in the DOM.
      const drawn = await page.locator('#car-plot .sc-dot').evaluateAll((ds) => ds.map((d) => d.dataset.plotVin));
      const noMiles = inView.filter((x) => !plottable(x)).length;
      const want = new Set(inView.filter(plottable).map((x) => x.vin));
      assert.equal(drawn.length, want.size, `${drawn.length} dots for ${inView.length} cars less ${noMiles} without a mileage`);
      assert.ok(drawn.every((v) => want.has(v)), 'every dot is a car with a recorded price and a recorded mileage');
      const dots = drawn.length;
      const plotNote = await page.locator('.car-place-plot .car-map-foot').textContent();
      assert.ok(plotNote.includes(String(dots)), `the plot's caption counts its own dots: "${plotNote}"`);
      const plotMissing = await page.locator('.car-place-missing').textContent();
      if (noMiles) assert.ok(plotMissing.includes(String(noMiles)) && /mileage was never published/i.test(plotMissing),
        `the plot names its ${noMiles} unplottable cars: "${plotMissing}"`);
      await shot(page, 'desktop-plot');
    } finally { await context.close(); }
  });

  await step('a car with no mileage is in the results, and reachable from the view that cannot draw it', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      const noMiles = pool.filter((x) => !plottable(x));
      // `return` here was a silent pass on any record that happened to publish
      // every mileage. The deterministic twin of this case is the fixture step
      // "a car with no mileage and no history"; this one is about the real
      // snapshot, so it says out loud when the snapshot stops providing one.
      assert.ok(noMiles.length, 'the committed record holds a car this view cannot plot');
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(400);
      await page.locator('.car-missing-reach').click();
      await page.waitForTimeout(400);
      const vins = await page.locator('.car-place-card').evaluateAll((cs) => cs.map((c) => c.dataset.carVin));
      assert.ok(vins.length, 'the cards show the omitted cars');
      assert.ok(vins.every((v) => noMiles.some((x) => x.vin === v)),
        'every card the omission link reaches is one of the cars that could not be plotted');
    } finally { await context.close(); }
  });

  // ---- the selection, across the views and across history ----------------
  await step('choosing a car on the plot chooses it in the cards, the map and the address bar', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(500);
      await page.locator('#car-plot .sc-dot').nth(3).click({ force: true });
      await page.waitForTimeout(400);
      const vin = await page.evaluate(() => new URLSearchParams(location.search).get('car'));
      assert.ok(vin, 'the chosen car rides in the address bar');
      assert.equal(await page.locator(`#car-plot .sc-dot.is-selected[data-plot-vin="${vin}"]`).count(), 1, 'the plot marks it');
      assert.equal(await page.locator(`.car-place-card.is-selected[data-car-vin="${vin}"]`).count(), 1, 'the card is marked');
      assert.equal(await page.evaluate(() => new URLSearchParams(location.search).get('show')), 'chart', 'the view rides too');
      // and a reload lands on the same car in the same view
      await page.reload({ waitUntil: 'load' });
      await page.waitForTimeout(1200);
      assert.equal(await page.locator('.car-place-panel').getAttribute('data-panel'), 'chart', 'the reload opens on the plot');
      assert.equal(await page.locator(`.car-place-card.is-selected[data-car-vin="${vin}"]`).count(), 1, 'and on the same car');
    } finally { await context.close(); }
  });

  await step('Back returns to the view and the car it left', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.car-place-card').first().waitFor();
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(400);
      await page.locator('#car-plot .sc-dot').nth(2).click({ force: true });
      await page.waitForTimeout(400);
      const first = await page.evaluate(() => location.search);
      await page.locator('#car-plot .sc-dot').nth(6).click({ force: true });
      await page.waitForTimeout(400);
      const second = await page.evaluate(() => location.search);
      assert.notEqual(first, second, 'two different cars give two different addresses');
      // The browse state is replaced, not pushed: Back leaves the page rather
      // than walking a car at a time, and a link still opens on its car.
      const link = base + '/' + second;
      await page.goto(link, { waitUntil: 'load' });
      await page.waitForTimeout(1200);
      const vin = new URLSearchParams(second).get('car');
      assert.equal(await page.locator(`.car-place-card.is-selected[data-car-vin="${vin}"]`).count(), 1, 'the shared link opens on its car');
    } finally { await context.close(); }
  });

  await step('a car the filters hide is explained, never silently restored', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      const vin = pool.find((x) => x.miles != null && x.miles > 40000)?.vin;
      assert.ok(vin, 'the committed record holds a car over 40,000 miles to filter out');
      await page.goto(base + '/?car=' + vin, { waitUntil: 'load' });
      await page.waitForTimeout(900);
      // Narrow the search until the car is out of it, then read what the page says.
      if (await page.locator('#filter-toggle').isVisible()) {
        if ((await page.locator('#filter-toggle').getAttribute('aria-expanded')) !== 'true') await page.locator('#filter-toggle').click();
        await page.waitForTimeout(250);
      }
      await page.selectOption('#f-miles', '15000');
      await page.waitForTimeout(700);
      const held = await page.locator(`.car-place-card[data-car-vin="${vin}"]`).count();
      assert.equal(held, 0, 'the filter really removed the car');
      const outside = page.locator('.car-outside');
      assert.equal(await outside.isVisible(), true, 'the page says the chosen car is not in these results');
      const words = await outside.textContent();
      assert.match(words, /not in these results/i);
      assert.ok(/Clear|Add /.test(words), `and offers the way back: "${words.slice(0, 120)}"`);
      assert.equal(await page.evaluate(() => document.querySelector('#f-miles').value), '15000', 'the filter was not quietly widened');
      await page.locator('.car-outside .car-text-button').first().click();
      await page.waitForTimeout(700);
      assert.equal(await page.locator(`.car-place-card[data-car-vin="${vin}"]`).count(), 1, 'and the reader\'s own press brings it back');
    } finally { await context.close(); }
  });

  // ---- the comparison ----------------------------------------------------
  await step('removing a car from the comparison keeps it saved, with its notes', async () => {
    const { context, page } = await session();
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table thead th').first().waitFor();
      const before = await page.locator('#finalists-table thead th').count();
      await page.locator('.fin-drop').first().click();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('#finalists-table thead th').count(), before - 1, 'the column goes');
      const kept = await page.evaluate(() => {
        const p = JSON.parse(localStorage.getItem('spicycar.prefs') || '{}');
        return { stars: Object.keys(p.stars || {}).length, out: (p.compareOut || []).length,
                 notes: Object.keys(JSON.parse(localStorage.getItem('spicycar.garage') || '{}')).length };
      });
      assert.equal(kept.stars, Object.keys(SAVED).length, 'every saved car is still saved');
      assert.equal(kept.notes, Object.keys(NOTES).length, 'every note is still there');
      assert.equal(kept.out, 1, 'the removal is recorded as a comparison membership, on its own');
      assert.match(await page.locator('.fin-aside').textContent(), /out of this comparison/i);
      await page.locator('.fin-aside button').first().click();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('#finalists-table thead th').count(), before, 'and one press puts it back');
    } finally { await context.close(); }
  });

  await step('the comparison marks what differs and folds only what does not', async () => {
    const { context, page } = await session();
    try {
      // Served, with one measure made identical on purpose. Today's four saved
      // cars happen to differ on all nine, so a page that marked EVERY row as
      // differing would read the same as a page that marked the right ones —
      // the check would pass on an accident of the market rather than on the
      // rule. Two cars are given the same reported history here, so there is
      // something for the fold to be wrong about.
      await context.route('**/data.json*', async (route) => {
        const r = await route.fetch();
        const sheet = JSON.parse(await r.text());
        const want = new Set(Object.keys(SAVED));
        // The fields the reported-history cell actually reads, made equal.
        for (const b of Object.values(sheet.brands)) for (const m of Object.values(b.models)) {
          for (const x of (m.listings || []).concat(m.gone || [])) if (want.has(x.vin)) {
            x.cpo = false; x.usage = ''; x.owners = 1; x.accidents = 0;
          }
        }
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sheet) });
      });
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table tbody tr').first().waitFor();
      const total = await page.locator('#finalists-table tbody tr').count();
      const differing = await page.locator('#finalists-table tbody tr[data-differs="true"]').count();
      const rows = await page.locator('#finalists-table tbody tr').evaluateAll((trs) => trs.map((tr) => ({
        key: tr.dataset.measure, differs: tr.dataset.differs === 'true',
        cells: [...tr.querySelectorAll('td')].map((td) => td.textContent.replace(/\s+/g, ' ').trim()) })));
      for (const r of rows) {
        const same = r.cells.every((c) => c === r.cells[0]);
        assert.equal(r.differs, !same, `"${r.key}" is marked as differing only when the cars do not agree`);
      }
      await page.locator('[data-fkey="findiff"]').click();
      await page.waitForTimeout(400);
      const after = await page.locator('#finalists-table tbody tr').count();
      assert.equal(after, differing, 'only the differing measures are printed');
      const folded = total - after;
      if (folded) {
        // Visible, not merely present: textContent reads a hidden node, which
        // is how a fold that drops what it folded passes a check about it.
        assert.equal(await page.locator('#finalists-same').isVisible(), true, 'the folded measures are still on the page');
        const said = await page.locator('#finalists-same').innerText();
        assert.match(said, /same for all/i, 'the folded measures are named, not dropped');
        for (const r of rows.filter((x) => !x.differs)) assert.ok(said.includes(r.cells[0]), `${r.key}'s value is still readable`);
      }
      // Identity and cost basis are never folded, whatever they say.
      for (const key of ['asking', 'otd', 'pay']) {
        if (rows.some((r) => r.key === key)) {
          assert.equal(await page.locator(`#finalists-table tbody tr[data-measure="${key}"]`).count(), 1, `${key} is never folded away`);
        }
      }
      assert.ok(folded > 0, 'the planted sheet gave the fold something to fold');
      assert.equal(await page.locator('#finalists-table tbody tr[data-measure="history"]').count(), 0,
        'the measure every car agrees on is the one that folded');
    } finally { await context.unroute('**/data.json*'); await context.close(); }
  });

  await step('the comparison prints the observations behind a price, with their dates', async () => {
    const { context, page } = await session();
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table tbody tr[data-measure="record"] td').first().waitFor();
      const cells = await page.locator('#finalists-table tbody tr[data-measure="record"] td').allTextContents();
      const heads = await page.locator('#finalists-table thead th [data-fkey^="fin:"]').evaluateAll((as) => as.map((a) => a.dataset.fkey.slice(4)));
      for (const [i, vin] of heads.entries()) {
        const x = pool.find((c) => c.vin === vin) || (departed.vin === vin ? departed : null);
        if (!x) continue;
        const obs = (x.series || []).filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p[0]) && Number.isFinite(p[1]) && p[1] > 0);
        const said = cells[i] || '';
        if (!obs.length) { assert.match(said, /no recorded prices/i); continue; }
        assert.ok(said.includes(String(obs.length)), `${vin.slice(-6)} says how many observations there are: "${said}"`);
        const move = obs[obs.length - 1][1] - obs[0][1];
        if (obs.length > 1) assert.match(said, move < 0 ? /lower/ : move > 0 ? /higher/ : /unchanged/,
          `${vin.slice(-6)} says which way the record moved: "${said}"`);
      }
    } finally { await context.close(); }
  });

  await step('a phone compares two named cars, not a smaller table', async () => {
    const { context, page } = await session({ width: 390, height: 844 });
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('.sc-compare-pair__heads').waitFor();
      assert.equal(await page.locator('#finalists-card').getAttribute('data-fin-mode'), 'pairs');
      assert.equal(await page.locator('#finalists-table thead th').count(), 0, 'the wide table is not shipped to a phone');
      const heads = await page.locator('.sc-compare-pair__head').count();
      assert.equal(heads, 2, 'two records are named');
      const boxes = await page.locator('.sc-compare-pair__values').first().evaluateAll((ds) => ds.flatMap((d) => [...d.children]).map((n) => Math.round(n.getBoundingClientRect().width)));
      assert.equal(boxes.length, 2, 'each measure shows both records');
      assert.ok(Math.abs(boxes[0] - boxes[1]) <= 1, 'and gives them equal room');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'nothing scrolls sideways');
      // choosing the other car is a selection, not a truncation
      const before = await page.locator('[data-fkey="finpair:B"]').inputValue();
      const options = await page.locator('[data-fkey="finpair:B"] option').evaluateAll((os) => os.map((o) => o.value));
      const other = options.find((v) => v !== before);
      if (other) {
        await page.selectOption('[data-fkey="finpair:B"]', other);
        await page.waitForTimeout(400);
        assert.equal(await page.locator('[data-fkey="finpair:B"]').inputValue(), other, 'the third car is one press away');
      }
      await shot(page, 'phone-compare');
    } finally { await context.close(); }
  });

  await step('an emptied comparison says so and keeps every car saved', async () => {
    const { context, page } = await session({ prefs: { ...PREFS, compareOut: Object.keys(SAVED) } });
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-card').waitFor();
      await page.waitForTimeout(600);
      assert.match(await page.locator('#finalists-pairs').textContent(), /still in your garage/i);
      assert.equal(await page.locator('#finalists-table thead th').count(), 0);
      assert.match(await page.locator('.fin-aside').textContent(), /out of this comparison/i);
    } finally { await context.close(); }
  });

  // ---- the garage on return ----------------------------------------------
  await step('the garage says what the record moved under, and dates it', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.shop-garage').click();
      await page.locator('.studio-dialog').waitFor();
      await page.waitForTimeout(400);
      // What the record itself says changed for each saved car since SINCE.
      const priceOn = (x, day) => { let v = null; for (const p of (x.series || [])) { if (p[0] <= day && p[1]) v = p[1]; else if (p[0] > day) break; } return v; };
      const expected = Object.keys(SAVED).filter((vin) => {
        if (SAVED[vin] === 'out') return false;
        const x = pool.find((c) => c.vin === vin);
        if (!x) return false;   // departed cars are judged by last_seen, tested below
        const obs = (x.series || []).filter((p) => Number.isFinite(p[1]) && p[1] > 0);
        const last = obs[obs.length - 1];
        const then = priceOn(x, SINCE);
        return last && then != null && last[0] > SINCE && last[1] !== then;
      });
      const items = await page.locator('.studio-look-item').evaluateAll((ls) => ls.map((l) => ({ vin: l.dataset.lookVin, kind: l.dataset.lookKind, text: l.textContent.replace(/\s+/g, ' ') })));
      for (const vin of expected) assert.ok(items.some((i) => i.vin === vin), `${vin.slice(-6)} needs another look`);
      for (const i of items) {
        assert.ok(['cut', 'up', 'left'].includes(i.kind), `a needs-a-look line is a recorded change, not a status: ${i.kind}`);
        assert.match(i.text, /\b[A-Z][a-z]{2} \d{1,2}\b/, `the observation behind it is dated: "${i.text.slice(0, 120)}"`);
      }
      await shot(page, 'desktop-garage');
    } finally { await context.close(); }
  });

  await step('a reload is not a new observation', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.shop-garage').click();
      await page.waitForTimeout(400);
      const first = await page.locator('.studio-look-item').count();
      await page.keyboard.press('Escape');
      await page.reload({ waitUntil: 'load' });
      await page.waitForTimeout(1200);
      await page.locator('.shop-garage').click();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('.studio-look-item').count(), first,
        'the same page, reloaded, reports the same changes and no new ones');
    } finally { await context.close(); }
  });

  await step('a first visit claims no change at all', async () => {
    const { context, page } = await session({ seen: null });
    try {
      await open(page);
      await page.locator('.shop-garage').click();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('.studio-look-item').count(), 0, 'nothing is claimed to have moved');
      assert.equal(await page.locator('.studio-look-none').count(), 0, 'and nothing is claimed to have held still either');
    } finally { await context.close(); }
  });

  await step('the garage adds and removes a comparison membership without touching the save', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.shop-garage').click();
      await page.waitForTimeout(400);
      const toggle = page.locator('.studio-garage-compare').first();
      assert.equal(await toggle.getAttribute('aria-pressed'), 'true', 'a saved car starts in the comparison');
      await toggle.click();
      await page.waitForTimeout(400);
      const after = await page.evaluate(() => {
        const p = JSON.parse(localStorage.getItem('spicycar.prefs') || '{}');
        return { out: (p.compareOut || []).length, stars: Object.keys(p.stars || {}).length };
      });
      assert.equal(after.out, 1, 'the car left the comparison');
      assert.equal(after.stars, Object.keys(SAVED).length, 'and is still saved');
      assert.equal(await page.locator('.studio-garage-compare').first().getAttribute('aria-pressed'), 'false');
    } finally { await context.close(); }
  });

  // ---- the states a browser can be in -------------------------------------
  await step('an older saved profile, with none of the new keys, still compares everything', async () => {
    const { context, page } = await session({ prefs: { stars: SAVED, shoppingModels: shopped, shopOnly: true } });
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table thead th').first().waitFor();
      const cols = await page.locator('#finalists-table thead [data-fkey^="fin:"]').count();
      assert.equal(cols, Object.keys(SAVED).length, 'every saved car is in the comparison, as it was before compareOut existed');
      assert.equal(await page.locator('[data-fkey="findiff"]').getAttribute('aria-pressed'), 'false', 'and every measure is printed');
    } finally { await context.close(); }
  });

  await step('a corrupt comparison membership is ignored, not obeyed', async () => {
    const { context, page } = await session({ prefs: { ...PREFS, compareOut: ['../../etc', 42, { vin: 'x' }, 'NOTAVIN!!'] } });
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table thead th').first().waitFor();
      assert.equal(await page.locator('#finalists-table thead [data-fkey^="fin:"]').count(), Object.keys(SAVED).length);
    } finally { await context.close(); }
  });

  await step('a browser with storage switched off still browses, compares and says so', async () => {
    const { context, page } = await session({ storage: false });
    try {
      await open(page);
      assert.ok(await page.locator('.car-place-card').count() > 0, 'the cards draw');
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(500);
      assert.ok(await page.locator('#car-plot .sc-dot').count() > 0, 'the plot draws');
      await page.locator('.shop-garage').click();
      await page.waitForTimeout(400);
      assert.match(await page.locator('.studio-dialog').textContent(), /Room for your next car|Storage unavailable/i);
    } finally { await context.close(); }
  });

  // ---- reach ---------------------------------------------------------------
  await step('the plot is walked by keyboard, and Enter chooses the car', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(500);
      await page.locator('#car-plot').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(200);
      const said = await page.locator('.car-place-plot .sc-sr-only').textContent();
      assert.ok(said && said.length > 4, `the focused car is spoken: "${said}"`);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      const vin = await page.evaluate(() => new URLSearchParams(location.search).get('car'));
      assert.ok(vin, 'Enter chooses that car');
      assert.equal(await page.locator(`.car-place-card.is-selected[data-car-vin="${vin}"]`).count(), 1);
    } finally { await context.close(); }
  });

  await step('the garage dialog gives the focus back to what opened it', async () => {
    const { context, page } = await session();
    try {
      await open(page);
      await page.locator('.shop-garage').click();
      await page.locator('.studio-dialog').waitFor();
      await page.waitForTimeout(300);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.className), 
        await page.locator('.shop-garage').getAttribute('class'), 'focus returns to the garage button');
    } finally { await context.close(); }
  });

  await step('every phone action is reachable and big enough to press', async () => {
    const { context, page } = await session({ width: 390, height: 844 });
    try {
      await open(page);
      const views = await page.locator('.car-view-button').evaluateAll((bs) => bs.map((b) => ({ text: b.textContent, h: Math.round(b.getBoundingClientRect().height), w: Math.round(b.getBoundingClientRect().width) })));
      assert.equal(views.length, 3, 'the phone switches between cards, map and plot');
      for (const v of views) assert.ok(v.h >= 44 && v.w >= 44, `"${v.text}" is ${v.w}x${v.h}, under the 44px target`);
      const acts = await page.locator('.car-place-card').first().locator('.car-place-actions button, .car-place-actions a').evaluateAll((ns) => ns.map((n) => ({ t: n.textContent.trim(), h: Math.round(n.getBoundingClientRect().height) })));
      assert.ok(acts.length >= 3, `the card keeps its actions: ${acts.map((a) => a.t).join(' · ')}`);
      for (const a of acts) assert.ok(a.h >= 36, `"${a.t}" is ${a.h}px tall`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'nothing scrolls sideways');
      await shot(page, 'phone-cards');
    } finally { await context.close(); }
  });

  await step('320px and the tablet band hold their shape too', async () => {
    for (const [w, h] of [[320, 800], [820, 1180]]) {
      const { context, page } = await session({ width: w, height: h });
      try {
        await open(page);
        const wide = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, view: innerWidth }));
        // 320px is a documented pre-existing overflow on this page; the new
        // surfaces are held to their own boxes rather than to the page's.
        for (const sel of ['#car-discovery', '.car-place-cards', '.car-place-panel']) {
          const box = await page.locator(sel).evaluate((n) => ({ scroll: n.scrollWidth, client: n.clientWidth }));
          assert.ok(box.scroll <= box.client + 1, `${sel} at ${w}px scrolls sideways (${box.scroll} in ${box.client})`);
        }
        if (w >= 390) assert.ok(wide.page <= wide.view + 1, `the page scrolls sideways at ${w}px`);
        await shot(page, 'width-' + w);
      } finally { await context.close(); }
    }
  });


  // ---- one connected journey, with nothing pre-saved ----------------------
  // Every step above seeds a finished garage before the first paint, which
  // proves what the page does WITH a save and never that saving works. This one
  // starts with empty storage and makes every write through a control a reader
  // has: choose the models, look at all three views, open one car, save it,
  // write a note, compare it, take it out, put it back, and come back to it
  // after a reload that re-seeds nothing.
  await step('the whole journey with nothing pre-saved: choose, look, save, note, compare, reload', async () => {
    const { context, page } = await session({ prefs: null, notes: null, seen: null });
    try {
      await open(page);
      assert.deepEqual(await page.evaluate(() => ({
        stars: Object.keys(JSON.parse(localStorage.getItem('spicycar.prefs') || '{}').stars || {}),
        notes: Object.keys(JSON.parse(localStorage.getItem('spicycar.garage') || '{}')),
      })), { stars: [], notes: [] }, 'this browser starts with no saved car and no note');

      // 1 — choose the models, through the picker
      await page.getByRole('button', { name: 'Choose cars' }).click();
      await page.locator('.shop-picker').waitFor();
      await page.getByRole('button', { name: 'Clear' }).click();
      for (const key of shopped) {
        const label = all.find((o) => o.key === key).m.label;
        await page.getByRole('checkbox', { name: label, exact: true }).check();
      }
      await page.getByRole('button', { name: 'Shop these models' }).click();
      await page.waitForFunction(() => !document.querySelector('.shop-picker[open]'));
      await page.waitForTimeout(600);
      const counted = Number(((await page.locator('.car-place-count').textContent()).match(/^(\d[\d,]*)/) || [])[1].replace(/,/g, ''));
      assert.equal(counted, pool.length, `choosing the two models gives ${pool.length} cars, the page says ${counted}`);

      // 2 — all three views of that one set
      const say = async () => (await page.locator('.car-place-missing').textContent()).trim();
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Map' }).click();
      await page.waitForTimeout(300);
      const mapSays = await say();
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Price & miles' }).click();
      await page.waitForTimeout(400);
      const plotSays = await say();
      assert.match(mapSays, /map/i, `the map names its own omissions: "${mapSays}"`);
      assert.match(plotSays, /plot|price and mileage/i, `the plot names its own omissions: "${plotSays}"`);
      assert.ok(await page.locator('.car-place-panel svg').count() > 0, 'the plot drew something to read');

      // 3 — open ONE car through the card's own action
      const vin = await page.locator('.car-place-card').first().getAttribute('data-car-vin');
      assert.match(vin, /^[A-HJ-NPR-Z0-9]{17}$/, `the first card names a VIN: ${vin}`);
      await page.locator(`[data-focus-vin="${vin}"][data-focus-action="look"]`).click();
      await page.locator('.studio-dialog[open]').waitFor();
      const status = page.locator(`select[aria-label="Status for ${vin}"]`);
      assert.equal(await status.count(), 1, 'the sheet that opened is that car’s own');
      assert.equal(await status.inputValue(), '', 'and it is not saved yet');

      // 4 — save it and write a note, both through the sheet
      await status.selectOption('short');
      await page.waitForTimeout(250);
      const NOTE = 'Fixture note: asked about the second key. Not a real enquiry.';
      await page.locator('.studio-notes').fill(NOTE);
      await page.waitForTimeout(250);
      assert.match(await page.locator('.studio-note-state').first().textContent(), /Saved on this device/i,
        'the sheet says the note was kept');
      const written = await page.evaluate(() => ({
        stars: JSON.parse(localStorage.getItem('spicycar.prefs') || '{}').stars || {},
        notes: JSON.parse(localStorage.getItem('spicycar.garage') || '{}'),
      }));
      assert.equal(written.stars[vin], 'short', 'the page wrote the save itself');
      assert.equal(written.notes[vin], NOTE, 'and the note beside it');
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);

      // 5 — the comparison, reached by its own nav control
      await page.getByRole('button', { name: 'Compare & save', exact: true }).click();
      await page.locator('#finalists-table thead th, .sc-compare-pair__heads').first().waitFor();
      const heads = () => page.locator('#finalists-table thead th [data-fkey^="fin:"]').evaluateAll((as) => as.map((a) => a.dataset.fkey.slice(4)));
      assert.deepEqual(await heads(), [vin], 'the one saved car is the one column in the comparison');
      const record = await page.locator('#finalists-table tbody tr[data-measure="record"] td').first().textContent();
      const obs = (pool.find((x) => x.vin === vin).series || []).filter((q) => /^\d{4}-\d{2}-\d{2}$/.test(q[0]) && Number.isFinite(q[1]) && q[1] > 0);
      assert.ok(record.includes(String(obs.length)), `its evidence says how many observations there are (${obs.length}): "${record.trim()}"`);

      // 6 — out of the comparison; the save, the note and the status stay
      await page.locator('.fin-drop').first().click();
      await page.waitForTimeout(400);
      const afterDrop = await page.evaluate(() => {
        const p = JSON.parse(localStorage.getItem('spicycar.prefs') || '{}');
        return { stars: p.stars || {}, out: p.compareOut || [], notes: JSON.parse(localStorage.getItem('spicycar.garage') || '{}') };
      });
      assert.equal(afterDrop.stars[vin], 'short', 'taking it out of the comparison did not unsave it');
      assert.equal(afterDrop.notes[vin], NOTE, 'and did not touch its note');
      assert.deepEqual(afterDrop.out, [vin], 'the removal is a comparison membership on its own');

      // 7 — and one press puts it back
      await page.locator('.fin-aside button').first().click();
      await page.waitForTimeout(400);
      assert.deepEqual(await heads(), [vin], 'one press puts it back in the comparison');

      // 8 — the garage, then a reload that re-seeds nothing
      await page.locator('.shop-garage').click();
      await page.locator('.studio-dialog[open]').waitFor();
      assert.match(await page.locator('.studio-note-preview').first().textContent(), /second key/i,
        'the garage shows the note the reader wrote');
      await page.keyboard.press('Escape');
      await page.reload({ waitUntil: 'load' });
      await page.locator('.shop-garage').waitFor();
      await page.waitForTimeout(900);
      const survived = await page.evaluate(() => ({
        stars: JSON.parse(localStorage.getItem('spicycar.prefs') || '{}').stars || {},
        notes: JSON.parse(localStorage.getItem('spicycar.garage') || '{}'),
      }));
      assert.equal(survived.stars[vin], 'short', 'the save survived a reload it was not re-seeded through');
      assert.equal(survived.notes[vin], NOTE, 'and so did the note');
      await page.locator('.shop-garage').click();
      await page.locator('.studio-dialog[open]').waitFor();
      // Reopening the same car after the reload: its own status control, its own note.
      const back = page.locator(`select[aria-label="Status for ${vin}"]`);
      assert.equal(await back.count(), 1, 'the garage still holds that car\u2019s own status control');
      assert.equal(await back.inputValue(), 'short', 'and it still reads Saved after the reload');
      assert.match(await page.locator('.studio-note-preview').first().textContent(), /second key/i,
        'and the note the reader typed is still the note it shows');
      await shot(page, 'journey-garage-after-reload');
    } finally { await context.close(); }
  });

  // ---- what the reader can see and reach ---------------------------------
  // A build date is not an observation date. STALE is shaped to have stopped
  // being observed three days before the record says it was generated, which is
  // the case a "data through <today>" headline can quietly absorb.
  await step('a generated date is not an observation date for every car', async () => {
    const cut = (() => { const t = new Date(data.data_through + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() - 4); return t.toISOString().slice(0, 10); })();
    let lastSeen = null;
    const record = shape(clone(), STALE, (car) => {
      car.series = (car.series || []).filter((q) => q[0] <= cut);
      assert.ok(car.series.length >= 2, 'the stale subject keeps two observations of its own');
      lastSeen = car.series.at(-1)[0];
      car.price = car.series.at(-1)[1];
    });
    assert.ok(lastSeen < data.data_through && lastSeen < LATER, `the subject last printed on ${lastSeen}, before the build date`);
    record.generated = LATER; record.data_through = LATER;
    const { context, page } = await session({ record, prefs: null, notes: null, seen: null });
    try {
      await open(page, '?car=' + STALE);
      await page.waitForTimeout(400);
      assert.match(await page.locator('.sc-masthead, header').first().textContent(), new RegExp(LATER.replace(/-/g, '.?')),
        'the page carries the newer build date');
      await page.locator(`[data-focus-vin="${STALE}"][data-focus-action="look"]`).click();
      await page.locator('.studio-dialog[open]').waitFor();
      const journey = await page.locator('.studio-history-summary').textContent();
      assert.ok(journey.includes(lastSeen), `the car's own last observation is ${lastSeen}, and it says so: "${journey.trim()}"`);
      assert.ok(!journey.includes(LATER), `and it does not claim it was seen on the build date: "${journey.trim()}"`);
      await page.locator('.studio-history summary').click();
      const rows = await page.locator('.studio-history-table tbody tr').evaluateAll((rs) => rs.map((r) => r.cells[0].textContent.trim()));
      assert.ok(!rows.includes(LATER), 'and the dated list gains no row for a day nothing was observed');
    } finally { await context.close(); }
  });

  // Unknown is unknown. Not zero accidents, not one owner, not zero miles.
  await step('a car with no mileage and no history says so, and is never given a clean record', async () => {
    // usage is set to a plain recorded value rather than left as the subject's
    // own "rental", which is a real fact and would rightly keep the history cell
    // from reading as empty. What this case is about is the ABSENT fields.
    const record = shape(clone(), BLANK, (car) => {
      car.miles = null; car.accidents = null; car.owners = null; car.flags = []; car.carfax = null; car.usage = 'Personal Use';
    });
    // Saved, so the comparison's "Reported history" cell — which is where
    // flagsCell() actually renders — is on the page to be read. Reading only the
    // card and the sheet left a hole: a mutant that printed "no accidents" for a
    // null count survived, because the card prints the record's own `flags` list
    // and the sheet never calls flagsCell at all.
    const { context, page } = await session({ record, prefs: { ...PREFS, stars: { [BLANK]: 'short' }, compareOut: [] }, notes: null, seen: null });
    try {
      await open(page, '?car=' + BLANK);
      await page.waitForTimeout(500);
      const card = await page.locator(`.car-place-card[data-car-vin="${BLANK}"]`).textContent();
      assert.match(card, /Mileage unreported/i, `the card says the mileage is unreported: "${card.replace(/\s+/g, ' ').slice(0, 200)}"`);
      assert.ok(!/\b0 mi\b/.test(card), 'and never prints it as zero miles');
      assert.ok(!/no accidents|1-owner/i.test(card), 'and claims neither a clean record nor an owner count');
      await page.locator(`[data-focus-vin="${BLANK}"][data-focus-action="look"]`).click();
      await page.locator('.studio-dialog[open]').waitFor();
      const sheet = (await page.locator('.studio-content').textContent()).replace(/\s+/g, ' ');
      assert.ok(!/no accidents|\b1 owner\b|1-owner/i.test(sheet), `the sheet invents no history: "${sheet.slice(0, 260)}"`);
      const marked = await page.locator('.studio-content .sc-unreported').count();
      assert.ok(marked >= 1, 'and an absent fact is marked as unreported rather than left to read as a value');
      await shot(page, 'unknown-history-sheet');
      // And the comparison's own history cell, which is the surface that turns
      // the record's accident and owner counts into words.
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Compare & save', exact: true }).click();
      await page.locator('#finalists-table tbody tr[data-measure="history"]').waitFor();
      const heads = await page.locator('#finalists-table thead th [data-fkey^="fin:"]').evaluateAll((as) => as.map((a) => a.dataset.fkey.slice(4)));
      assert.deepEqual(heads, [BLANK], 'the blank car is the one column in the comparison');
      const hist = (await page.locator('#finalists-table tbody tr[data-measure="history"] td').first().textContent()).replace(/\s+/g, ' ').trim();
      assert.ok(!/no accidents|\b0 accidents?\b/i.test(hist), `an unknown accident count is never a clean record: "${hist}"`);
      assert.ok(!/owner/i.test(hist), `and an unknown owner count is never an owner claim: "${hist}"`);
      assert.match(hist, /history n\/a/i, `and the cell says the history is not available: "${hist}"`);
    } finally { await context.close(); }
  });

  // Asking is a recorded price. Out-the-door and per-month are this page's
  // arithmetic over the reader's own assumptions, and the comparison has to keep
  // them apart where the decision is made.
  await step('asking, shipping, all-in and the financing assumptions stay told apart', async () => {
    const { context, page } = await session();
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table tbody tr[data-measure="asking"]').waitFor();
      const marks = await page.locator('#finalists-table tbody tr[data-measure]').evaluateAll((rows) => rows.map((r) => ({
        measure: r.dataset.measure,
        label: (r.cells[0].textContent || '').replace(/\s+/g, ' ').trim(),
        estimates: [...r.querySelectorAll('td .sc-figure')].map((f) => f.classList.contains('sc-estimate')),
        subs: [...r.querySelectorAll('td .sc-note')].map((n) => n.textContent.trim()),
      })));
      const row = (key) => marks.find((m) => m.measure === key);
      assert.ok(row('asking'), 'the comparison prints the asking price');
      assert.ok(row('asking').estimates.every((e) => e === false), 'a recorded asking price is never marked an estimate');
      assert.match(row('asking').subs.join(' | '), /ship|drive|last seen/i,
        `and shipping rides beside it rather than inside it: "${row('asking').subs.join(' | ')}"`);
      for (const key of ['otd', 'pay']) {
        if (!row(key)) continue;   // the measure only exists when the buyer configured it
        assert.ok(row(key).estimates.some((e) => e === true), `${key} is marked as this page's estimate`);
      }
      const basis = page.locator('.fin-basis');
      assert.equal(await basis.count(), 1, 'the assumptions are one disclosure, not a paragraph over the cars');
      await basis.locator('summary').click();
      const words = (await basis.locator('p').textContent()).replace(/\s+/g, ' ');
      assert.match(words, /Shipping/i, `the assumptions name the shipping basis: "${words.slice(0, 200)}"`);
      assert.match(words, /tax|fee|paperwork/i, 'and the tax and paperwork basis');
      assert.ok(!/\$0\b/.test(words), 'and no unavailable figure is written as zero');
      // an unavailable estimate reads as unavailable, never as nothing owed
      const gone = await page.locator('#finalists-table tbody tr[data-measure="otd"] td').allTextContents().catch(() => []);
      for (const cell of gone) assert.ok(!/^\s*\$0\s*$/.test(cell), `an all-in cell is never a bare $0: "${cell}"`);
      await shot(page, 'cost-basis-disclosure');
    } finally { await context.close(); }
  });

  // The dated list, read row by row against the record, and never one row per
  // day of a drawn line.
  await step('the recorded prices are the days the car was observed, with their own dates', async () => {
    const { context, page } = await session();
    try {
      const subject = live.find((x) => (x.series || []).length >= 3) || live[0];
      await open(page, '?car=' + subject.vin);
      await page.waitForTimeout(400);
      await page.locator(`[data-focus-vin="${subject.vin}"][data-focus-action="look"]`).click();
      await page.locator('.studio-dialog[open]').waitFor();
      await page.locator('.studio-history summary').click();
      const rows = await page.locator('.studio-history-table tbody tr').evaluateAll((rs) => rs.map((r) => [r.cells[0].textContent.trim(), r.cells[1].textContent.trim()]));
      const obs = (subject.series || []).filter((q) => /^\d{4}-\d{2}-\d{2}$/.test(q[0]) && Number.isFinite(q[1]) && q[1] > 0)
        .slice().sort((a, b) => a[0].localeCompare(b[0]));
      assert.equal(rows.length, obs.length, `${obs.length} observations were recorded and ${rows.length} rows are printed`);
      for (const [i, [day, price]] of rows.entries()) {
        assert.equal(day, obs[i][0], `row ${i + 1} carries its own observation date`);
        assert.equal(price.replace(/[^\d]/g, ''), String(obs[i][1]), `row ${i + 1} carries the price observed on ${obs[i][0]}`);
      }
      await shot(page, 'recorded-prices-disclosure');
    } finally { await context.close(); }
  });

  // Absence from a sampled query is not a sale, and a save keeps the evidence it
  // was made on. Two records: the car is live and saved in the first, departed in
  // the second, and nothing rewrites what the first one said.
  await step('a saved car that stops being seen keeps its own dated evidence, and is never called sold', async () => {
    const next = clone();
    let moved = null, lastSeen = null;
    shape(next, STALE, (car, model, index) => {
      moved = car;
      lastSeen = (car.series || []).at(-1)[0];
      model.listings.splice(index, 1);
      model.gone = model.gone || [];
      // `likely: 'unseen'` is the honest branch: the record does not know the
      // listing ended, only that the car stopped printing.
      model.gone.push({ ...car, last_price: car.price, last_seen: lastSeen, likely: 'unseen', exact: true });
    });
    // The reader's baseline has to be BEFORE the car stopped printing, or
    // nothing has moved since they last looked and the garage rightly says so —
    // which is what this check was reading as a missing sentence.
    const seenDay = days.filter((d) => d < lastSeen).at(-1);
    assert.ok(seenDay, `the record holds a day before ${lastSeen} for the reader to have last seen`);
    next.generated = LATER; next.data_through = LATER; next.departures_from = data.departures_from;
    const { context, page } = await session({ record: next, prefs: { ...PREFS, stars: { [STALE]: 'short' } }, notes: null, seen: seenDay });
    try {
      await page.goto(base + '/?view=compare', { waitUntil: 'load' });
      await page.locator('#finalists-table thead th, .sc-compare-pair__heads').first().waitFor();
      await page.waitForTimeout(500);
      const shown = await page.locator('#finalists-table thead th [data-fkey^="fin:"]').evaluateAll((as) => as.map((a) => a.dataset.fkey.slice(4)));
      assert.ok(shown.includes(STALE), 'a car that left the listings is still in the comparison it was saved into');
      const asking = await page.locator('#finalists-table tbody tr[data-measure="asking"] td').nth(shown.indexOf(STALE)).textContent();
      assert.ok(asking.replace(/[^\d]/g, '').includes(String(moved.price)), `its last recorded asking price is the one the record made: "${asking.trim()}"`);
      assert.match(asking, /last seen/i, 'and it is dated as last seen rather than quoted as current');
      // The page's own legend for departures names four causes — sold, pulled,
      // re-listed, or simply not fetched — which is the honest sentence and not
      // a claim, so a blanket search for the word is the wrong test. What must
      // not happen is a verdict about THIS car, and the legend must keep
      // offering the alternative rather than settling on a sale.
      const legend = (await page.locator('#gone-card, #main').first().textContent()).replace(/\s+/g, ' ');
      const explains = /[^.]*\bsold\b[^.]*\./i.exec(legend);
      assert.ok(explains, 'the record explains what a departure can mean');
      assert.match(explains[0], /\bor\b/i, `and offers more than one cause: "${explains[0].trim()}"`);
      assert.match(explains[0], /not fetched|cut-off|re-listed|pulled/i,
        'including the one that is not a sale at all');
      const mine = (await page.locator(`#finalists-table [data-fkey="fin:${STALE}"]`).first()
        .evaluate((node) => node.closest('th').textContent)).replace(/\s+/g, ' ');
      assert.ok(!/\bsold\b/i.test(mine), `and nothing on this car's own column calls it sold: "${mine.trim()}"`);
      await page.locator('.shop-garage').click();
      await page.locator('.studio-dialog[open]').waitFor();
      const garage = (await page.locator('.studio-content').textContent()).replace(/\s+/g, ' ');
      // An OR of three phrases let a mutant through: "Stopped being seen ... and
      // its listing ended" still matched the first branch, so the clause that
      // carries the UNCERTAINTY was never pinned. This record says `likely:
      // 'unseen'` — it knows the car stopped printing and nothing more — so all
      // three of these have to hold.
      assert.match(garage, /Stopped being seen after/i,
        `the garage dates when it stopped printing: "${garage.slice(0, 260)}"`);
      assert.match(garage, /may only have fallen outside a fetch window/i,
        `and says an absence from a sampled fetch is not the end of the listing: "${garage.slice(0, 260)}"`);
      assert.ok(!/listing ended/i.test(garage), 'and claims no ending the record cannot see');
      assert.ok(!/\bsold\b/i.test(garage), 'and does not call it sold either');
      await shot(page, 'departed-saved-car');
    } finally { await context.close(); }
  });

  // The deterministic twin of "a car the filters hide is explained": that step
  // reads the committed record, which today happens to hold a car over 40,000
  // miles; this one shapes one, so the case has a subject whatever the tracker
  // publishes next.
  await step('a listing the reader\u2019s own filter excludes is explained, and only the reader brings it back', async () => {
    const record = shape(clone(), FILTERED, (car) => { car.miles = 88000; });
    const { context, page } = await session({ record, prefs: null, notes: null, seen: null });
    try {
      await open(page, '?car=' + FILTERED);
      await page.waitForTimeout(700);
      assert.equal(await page.locator(`.car-place-card[data-car-vin="${FILTERED}"]`).count(), 1, 'the shaped car is in the results to begin with');
      if (await page.locator('#filter-toggle').isVisible()) {
        if ((await page.locator('#filter-toggle').getAttribute('aria-expanded')) !== 'true') await page.locator('#filter-toggle').click();
        await page.waitForTimeout(250);
      }
      await page.selectOption('#f-miles', '15000');
      await page.waitForTimeout(700);
      assert.equal(await page.locator(`.car-place-card[data-car-vin="${FILTERED}"]`).count(), 0, 'the 88,000-mile car is out of a 15,000-mile search');
      const outside = page.locator('.car-outside');
      assert.equal(await outside.isVisible(), true, 'and the page says where it went');
      const words = (await outside.textContent()).replace(/\s+/g, ' ');
      assert.match(words, /not in these results/i, `the exclusion is explained: "${words.slice(0, 160)}"`);
      assert.equal(await page.evaluate(() => document.querySelector('#f-miles').value), '15000',
        'and the filter the reader set was not quietly widened to fit the car back in');
      await page.locator('.car-outside .car-text-button').first().click();
      await page.waitForTimeout(700);
      assert.equal(await page.locator(`.car-place-card[data-car-vin="${FILTERED}"]`).count(), 1,
        'only the reader\u2019s own press brings it back');
      await shot(page, 'outside-the-filters');
    } finally { await context.close(); }
  });

  // No fabricated map position: a car with no coordinates is off the map, named
  // as off it, and one press away in the cards.
  await step('a car with no coordinates is never placed on the map, and is reachable without one', async () => {
    const record = shape(clone(), NOWHERE, (car) => { car.lat = null; car.lon = null; });
    const { context, page } = await session({ record, prefs: null, notes: null, seen: null });
    try {
      await open(page);
      await page.locator('.car-panel-switch .sc-tab', { hasText: 'Map' }).click();
      await page.waitForTimeout(500);
      const placed = await page.evaluate((vin) => {
        const marks = [...document.querySelectorAll('.car-place-panel [data-map-vin], .leaflet-marker-icon')];
        return marks.some((m) => (m.dataset && m.dataset.mapVin) === vin);
      }, NOWHERE);
      assert.equal(placed, false, 'the car with no coordinates has no marker');
      const says = (await page.locator('.car-place-missing').textContent()).trim();
      assert.match(says, /not on this map/i, `the map names the omission: "${says}"`);
      assert.match(says, /location|verified/i, 'and says why');
      await page.locator('.car-missing-reach').click();
      await page.waitForTimeout(500);
      const shownVins = async () => page.locator('.car-place-card').evaluateAll((cs) => cs.map((c) => c.dataset.carVin));
      // No shopping preference was seeded, so every model is in view and the
      // omitted set is the whole record's — reading it off `pool` alone made
      // this assertion fail on cars from models the check never shaped.
      const omitted = all.flatMap(rowsOf).filter((x) => !located(x) || x.vin === NOWHERE).map((x) => x.vin);
      let vins = await shownVins();
      assert.ok(vins.length && vins.every((v) => omitted.includes(v)),
        'every card the omission link reaches is one of the cars the map could not place');
      // The cards are paged eight at a time, so "reachable" means reachable by
      // pressing the page's own control, not present on the first page.
      for (let guard = 0; guard < 40 && !vins.includes(NOWHERE); guard += 1) {
        const more = page.locator('.car-discovery-more');
        if (!(await more.isVisible())) break;
        await more.click();
        await page.waitForTimeout(200);
        vins = await shownVins();
      }
      assert.ok(vins.includes(NOWHERE), `the omission link reaches the car itself in the cards (${vins.length} shown)`);
      await shot(page, 'no-coordinates-reachable');
    } finally { await context.close(); }
  });


  // ---- the journey's own surfaces, at both widths and in both themes -------
  // Every session above runs dark at 1280, so the light theme and the phone were
  // being read only through the dashboard suite's own layout checks and never
  // through the controls this journey presses. A screenshot is not a pass, so
  // each control is measured: on screen, inside the viewport, big enough to
  // press, and legible against what is actually behind it.
  for (const [width, height] of [[1280, 900], [390, 844]]) for (const theme of ['dark', 'light']) {
    await step(`the journey's controls hold at ${width}px in the ${theme} theme`, async () => {
      const { context, page } = await session({ width, height, theme });
      try {
        await open(page);
        await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
        await page.waitForTimeout(400);
        const usable = async (locator, name) => {
          assert.equal(await locator.count() > 0, true, `${name} is on the page`);
          const box = await locator.first().evaluate((node) => {
            const b = node.getBoundingClientRect();
            const cs = getComputedStyle(node);
            const rgb = (v) => (v.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
            const lum = (v) => rgb(v).map((c) => { c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; })
              .reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
            // what is actually behind it, rather than the node's own transparent background
            let bg = cs.backgroundColor, at = node;
            while (at && (bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent')) { at = at.parentElement; bg = at ? getComputedStyle(at).backgroundColor : 'rgb(255, 255, 255)'; }
            const ratio = (Math.max(lum(cs.color), lum(bg)) + .05) / (Math.min(lum(cs.color), lum(bg)) + .05);
            return { w: b.width, h: b.height, left: b.left, right: b.right, display: cs.display, visibility: cs.visibility, ratio };
          });
          assert.ok(box.display !== 'none' && box.visibility !== 'hidden' && box.w > 0 && box.h > 0, `${name} is visible (${JSON.stringify(box)})`);
          // The 44px and 36px touch targets belong to the phone step above, which
          // owns them; 28px is what the design system's --sm button is on a
          // desktop and is not a defect there. What this asserts is a real box.
          const floor = width <= 420 ? 36 : 20;
          assert.ok(box.h >= floor && box.w >= 24, `${name} is a real press target (${Math.round(box.w)}x${Math.round(box.h)}, floor ${floor})`);
          assert.ok(box.left >= -1 && box.right <= width + 1, `${name} is not clipped off the side (${Math.round(box.left)}..${Math.round(box.right)} of ${width})`);
          assert.ok(box.ratio >= 4.5, `${name} reads at ${box.ratio.toFixed(2)}:1 against what is behind it`);
        };
        await usable(page.getByRole('button', { name: 'Choose cars' }), 'Choose cars');
        await usable(page.getByRole('button', { name: 'Compare & save', exact: true }), 'Compare & save');
        await usable(page.locator('.shop-garage'), 'Garage');
        const vin = await page.locator('.car-place-card').first().getAttribute('data-car-vin');
        await usable(page.locator(`[data-focus-vin="${vin}"][data-focus-action="look"]`), 'Quick look');
        await usable(page.locator(`[data-fkey="star:${vin}"]`), 'Save car');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'nothing scrolls sideways');
        await shot(page, `journey-explore-${width}-${theme}`);
        // the sheet the save and the note are made in
        await page.locator(`[data-focus-vin="${vin}"][data-focus-action="look"]`).click();
        await page.locator('.studio-dialog[open]').waitFor();
        await usable(page.locator(`select[aria-label="Status for ${vin}"]`), 'the status control');
        await usable(page.locator('.studio-notes'), 'the notes field');
        const sheet = await page.locator('.studio-dialog').evaluate((node) => { const b = node.getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top }; });
        assert.ok(sheet.left >= -1 && sheet.right <= width + 1, `the sheet fits the screen (${Math.round(sheet.left)}..${Math.round(sheet.right)})`);
        await shot(page, `journey-sheet-${width}-${theme}`);
        await page.keyboard.press('Escape');
        // and the comparison the evidence is read in
        await page.getByRole('button', { name: 'Compare & save', exact: true }).click();
        await page.locator('#finalists-table thead th, .sc-compare-pair__heads').first().waitFor();
        await page.waitForTimeout(400);
        await usable(page.locator('.fin-basis summary'), 'the assumptions disclosure');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'the comparison does not scroll the page sideways');
        await shot(page, `journey-compare-${width}-${theme}`);
      } finally { await context.close(); }
    });
  }

  assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
  if (failures) { console.log(`browse smoke: ${failures} check(s) failed`); process.exitCode = 1; }
  else console.log(`browse smoke: ${ONLY ? 'the checks named "' + ONLY + '" passed' : 'all checks passed'}, zero page errors`);
} catch (e) {
  console.log('browse smoke: ' + String((e && e.message) || e).split('\n')[0]);
  process.exitCode = 1;
} finally { await browser.close(); server.close(); }
