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
const browser = await chromium.launch();
const errors = [];
async function session({ width = 1280, height = 900, prefs = PREFS, notes = NOTES, seen = SINCE, storage = true } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, isMobile: width <= 420, hasTouch: width <= 420,
    reducedMotion: 'reduce', colorScheme: 'dark' });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => (/\.(png|jpe?g|webp|gif|svg)/i.test(r.request().url())
    ? r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
    : r.fulfill({ status: 200, contentType: 'text/plain', body: '' })));
  await context.addInitScript(([p, n, s, ok]) => {
    if (!ok) {   // a browser with storage switched off, which is a state and not a crash
      const boom = () => { throw new DOMException('denied', 'SecurityError'); };
      Object.defineProperty(window, 'localStorage', { configurable: true, get: boom });
      return;
    }
    localStorage.setItem('spicycar.prefs', JSON.stringify(p));
    localStorage.setItem('spicycar.garage', JSON.stringify(n));
    if (s) localStorage.setItem('spicycar.seen', JSON.stringify({ through: s, since: null }));
    localStorage.setItem('sc-theme', 'dark');
  }, [prefs, notes, seen, storage]);
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
      if (!noMiles.length) return;   // nothing to omit today; the check has no subject
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
      if (!vin) return;
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

  assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
  if (failures) { console.log(`browse smoke: ${failures} check(s) failed`); process.exitCode = 1; }
  else console.log(`browse smoke: ${ONLY ? 'the checks named "' + ONLY + '" passed' : 'all checks passed'}, zero page errors`);
} catch (e) {
  console.log('browse smoke: ' + String((e && e.message) || e).split('\n')[0]);
  process.exitCode = 1;
} finally { await browser.close(); server.close(); }
