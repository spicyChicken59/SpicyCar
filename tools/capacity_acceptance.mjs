// Actual committed snapshot, fresh browser storage, local-only evidence.
// Vehicle images/fonts/maps are unavailable fixtures, never live-provider proof.
// node tools/capacity_acceptance.mjs /path/to/evidence
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { chromium } from 'playwright';
import Sheet from '../docs/sheet-transport.js';

const out = resolve(process.argv[2]), root = resolve('docs');
await mkdir(out, { recursive: true });
const data = Sheet.parse(await readFile(resolve(root, 'data.json'), 'utf8'));
const all = Object.entries(data.brands).flatMap(([bk, b]) => Object.entries(b.models).map(([mk, m]) => ({ bk, mk, m })));
const sourceOf = (vin) => all.flatMap(({ m }) => m.listings).find((x) => x.vin === vin);
const server = createServer(async (req, res) => {
  const file = resolve(root, '.' + new URL(req.url, 'http://local').pathname.replace(/\/$/, '/index.html'));
  if (!file.startsWith(root + '/')) return res.writeHead(403).end();
  try {
    res.setHeader('content-type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' })[extname(file)] || 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`, browser = await chromium.launch();
const evidence = [];
try {
  for (const [width, height, theme] of [[1280, 900, 'dark'], [320, 844, 'light']]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, reducedMotion: 'reduce' });
    await context.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.fulfill({ status: 200, body: '' }));
    const page = await context.newPage(), errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const shot = (name) => page.screenshot({ path: resolve(out, `${width}-${theme}-${name}.png`) });
    const prefs = () => page.evaluate(() => JSON.parse(localStorage.getItem('spicycar.prefs') || '{}'));
    const comparisonHeads = '#finalists-table thead [data-fin-vin], #finalists-pairs [data-fin-vin]';
    const heads = () => page.locator(comparisonHeads).evaluateAll((ns) => [...new Set(ns.map((n) => n.dataset.finVin))]);
    try {
      await page.goto(base); await page.locator('.car-place-card').first().waitFor();
      assert.deepEqual(await page.evaluate(async () => SpicyCarSheet.decode(await window.__data)), data);
      assert.deepEqual((await prefs()).stars || {}, {});
      const vin = await page.locator('.car-place-card').first().getAttribute('data-car-vin');
      const car = sourceOf(vin); assert.ok(car);
      await shot('explore');
      await page.locator(`[data-focus-vin="${vin}"][data-focus-action="look"]`).click();
      await page.locator('.studio-dialog[open]').waitFor();
      const facts = await page.locator('.studio-facts').textContent();
      assert.ok(facts.includes(vin) && facts.includes(car.first_seen));
      const last = car.series.at(-1)[0]; assert.ok(facts.includes(last));
      assert.equal(await page.getByRole('link', { name: 'Dealer listing ↗', exact: true }).getAttribute('href'), car.url);
      await page.getByText('See recorded prices', { exact: true }).click();
      const rows = await page.locator('.studio-history-table tbody tr').evaluateAll((ns) => ns.map((n) => [...n.cells].map((c) => c.textContent)));
      assert.deepEqual(rows.map(([date, price]) => [date, Number(price.replace(/[^\d.-]/g, ''))]), car.series);
      await page.locator(`select[aria-label="Status for ${vin}"]`).selectOption('short');
      const note = 'Capacity acceptance fixture: exact VIN and note persist. No dealer contact.';
      await page.locator('.studio-notes').fill(note);
      await page.waitForFunction(([v, n]) => JSON.parse(localStorage.getItem('spicycar.garage') || '{}')[v] === n, [vin, note]);
      await shot('detail-note'); await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Compare & save', exact: true }).click();
      await page.locator(comparisonHeads).first().waitFor();
      assert.deepEqual(await heads(), [vin]);
      await page.reload(); await page.locator(comparisonHeads).first().waitFor();
      assert.deepEqual(await heads(), [vin]);
      assert.equal((await prefs()).stars[vin], 'short');
      assert.equal(await page.evaluate((v) => JSON.parse(localStorage.getItem('spicycar.garage'))[v], vin), note);
      await page.locator('#finalists-card').scrollIntoViewIfNeeded(); await shot('comparison-reload');
      // Brand interest is an independent choice, and changes none of the save.
      const before = await prefs();
      await page.getByRole('button', { name: 'Choose cars', exact: true }).click();
      const brand = page.locator('.shop-brand-interest').last();
      const interestLabel = await brand.getAttribute('aria-label');
      await brand.click(); await page.locator('.shop-picker-bottom .shop-primary').click();
      const after = await prefs();
      assert.deepEqual(after.shoppingModels, before.shoppingModels);
      assert.deepEqual(after.stars, before.stars);
      assert.deepEqual(after.compareOut, before.compareOut);
      assert.notDeepEqual(after.interestedBrands, before.interestedBrands);
      // The actual missing record remains reachable in model research.
      const subject = all.find(({ bk, mk, m }) => bk === 'bmw' && mk === 'i5' && m.gone.length);
      await page.goto(`${base}/?brand=${subject.bk}&m=${subject.mk}&view=research`);
      await page.locator('#gone-card').waitFor();
      await page.locator('#gone-card').scrollIntoViewIfNeeded();
      const goneText = await page.locator('#gone-card').textContent();
      const gone = subject.m.gone.find((g) => goneText.includes(g.vin)); assert.ok(gone);
      assert.match(goneText, /not necessarily a sale/);
      await shot('missing-history');
      const actualUnknown = all.flatMap(({ m }) => m.listings).find((x) => x.miles === null);
      assert.ok(actualUnknown);
      await page.goto(`${base}/?car=${actualUnknown.vin}`);
      const unknownAction = page.locator(`[data-focus-vin="${actualUnknown.vin}"][data-focus-action="look"]`);
      await unknownAction.waitFor(); await unknownAction.click();
      await page.locator('.studio-dialog[open]').waitFor();
      const unknownFacts = await page.locator('.studio-facts').textContent();
      assert.ok(unknownFacts.includes(actualUnknown.vin));
      assert.match(unknownFacts, /not reported|unreported/i);
      await page.locator('.studio-facts').scrollIntoViewIfNeeded(); await shot('unknown-mileage');
      assert.deepEqual(errors, []);
      evidence.push({ viewport: { width, height }, theme, actualSnapshot: true, sourceRequests: 0,
        dataThrough: data.data_through, vin, firstSeen: car.first_seen, recordThrough: last,
        recordedPrices: rows, sourceLink: car.url, note, comparisonVinsAfterReload: [vin],
        brandInterestControl: interestLabel, modelChoicesUnchanged: true,
        missingVin: gone.vin, missingLastSeen: gone.last_seen, missingReason: gone.likely,
        actualUnknownMileageVin: actualUnknown.vin, actualUnknownMileageValue: actualUnknown.miles,
        actualUnknownFacts: unknownFacts,
        allDecodedValuesDeepEqual: true, pageErrors: errors });
    } finally { await context.close(); }
  }
} finally { await browser.close(); server.close(); }
await writeFile(resolve(out, 'browser-acceptance.json'), JSON.stringify(evidence, null, 2) + '\n');
console.log(JSON.stringify(evidence, null, 2));
