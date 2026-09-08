// Interaction regression for the Home-inspired Places & cars view.
// Offline and read-only: no provider, mail, or tracker requests.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { chromium } from 'playwright';
const root = resolve('docs');
const server = createServer(async (req, res) => {
  const path = resolve(root, '.' + new URL(req.url, 'http://local').pathname.replace(/\/$/, '/index.html'));
  if (!path.startsWith(root + '/')) return res.writeHead(403).end();
  try { res.setHeader('Content-Type', ({ '.js':'text/javascript', '.html':'text/html', '.css':'text/css', '.json':'application/json', '.svg':'image/svg+xml' })[extname(path)] || 'application/octet-stream'); res.end(await readFile(path)); }
  catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = 'http://127.0.0.1:' + server.address().port;
const data = JSON.parse(await readFile(resolve(root, 'data.json'), 'utf8'));
const watched = Object.entries(data.brands).flatMap(([bk, b]) => Object.entries(b.models).map(([mk, m]) => ({ bk, mk, count: (m.listings || []).length }))).sort((a, b) => b.count - a.count)[0];
const query = '?view=report&brand=' + watched.bk + '&m=' + watched.mk;
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, reducedMotion: 'reduce', colorScheme: 'light' });
// Keep the old atlas unavailable to prove the new map/list is independent.
await context.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
const page = await context.newPage();
const errors = []; page.on('pageerror', (e) => errors.push(e.message));
const open = async () => { await page.goto(base + query); await page.locator('.car-place-card').first().waitFor(); };
const order = () => page.locator('.car-place-card').evaluateAll((cs) => cs.map((c) => c.dataset.carVin));
const rows = () => page.locator('#list-table [data-fkey^="star:"]').evaluateAll((bs) => bs.map((b) => b.dataset.fkey.slice(5)));
try {
  await open();
  assert.equal(await page.locator('#car-discovery').isVisible(), true);
  assert.equal(await page.locator('.car-place-card').count(), 8);
  assert.ok(await page.locator('.car-dot-marker').count() > 0);
  assert.deepEqual(await order(), (await rows()).slice(0, 8));
  assert.equal(await page.locator('#car-atlas, #car-atlas-switch').count(), 0, 'only one map');
  const shown = await page.locator('.car-dot-marker').evaluateAll((ms) => ms.reduce((sum, m) => sum + Number(m.dataset.carCount), 0));
  const withCoordinates = data.brands[watched.bk].models[watched.mk].listings
    .filter((c) => Number.isFinite(c.lat) && Number.isFinite(c.lon)).length;
  assert.equal(shown, withCoordinates, 'clusters account for all cars with coordinates, including later table pages');
  console.log('ok one dot map, eight cards, and listing order');
  await page.locator('#f-sort').selectOption('price');
  assert.deepEqual(await order(), (await rows()).slice(0, 8));
  await page.locator('[data-fkey="where:IL"]').click();
  assert.deepEqual(await order(), (await rows()).slice(0, 8));
  assert.match(await page.locator('.car-place-location').first().textContent(), /IL/);
  console.log('ok filter and sort propagation');
  await page.locator('.car-place-actions [data-fkey^="star:"]').first().click();
  assert.match(await page.locator('.car-place-actions [data-fkey^="star:"]').first().textContent(), /shortlisted/);
  await page.locator('.car-place-actions [data-fkey^="star:"]').first().click();
  assert.match(await page.locator('.car-place-actions [data-fkey^="star:"]').first().textContent(), /called/);
  console.log('ok existing shortlist and called state cycle');
  await page.getByRole('button', { name: 'Show on map', exact: true }).first().click();
  assert.ok(await page.locator('.leaflet-popup').count() > 0);
  await page.waitForTimeout(750);
  assert.ok(await page.locator('.leaflet-popup').count() > 0, 'popup remains open after automatic pan');
  await page.locator('.car-popup-car').first().click();
  assert.equal(await page.locator('.car-place-card.is-selected').count(), 1);
  assert.equal(await page.locator('.car-place-card.is-selected').evaluate((n) => document.activeElement === n), true);
  console.log('ok card-to-pin, popup-to-card and keyboard focus');
  await page.waitForTimeout(500);
  assert.match(await page.locator('.car-map-status').textContent(), /tiles could not load/);
  await page.locator('#f-sort').selectOption('miles');
  assert.match(await page.locator('.car-map-status').textContent(), /tiles could not load/);
  console.log('ok tile failure remains explicit after sorting');
  for (const width of [320, 390, 820, 1280]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate((theme) => document.documentElement.dataset.theme = theme, theme);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), width + 'px ' + theme + ' overflow');
  }
  console.log('ok 320/390/820/1280 layouts in light and dark');
  await page.setViewportSize({ width: 320, height: 844 });
  await page.getByRole('button', { name: 'Show on map', exact: true }).first().click();
  await page.waitForTimeout(750);
  const popup = await page.locator('.leaflet-popup').boundingBox();
  const surface = await page.locator('.car-place-map').boundingBox();
  assert.ok(popup.width <= surface.width && popup.x >= surface.x - 1 && popup.x + popup.width <= surface.x + surface.width + 1, 'phone popup fits map');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.locator('[data-fkey="where:IL"]').click();
  while (await page.locator('.car-discovery-more').isVisible()) await page.locator('.car-discovery-more').click();
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains('car-place-card')), true);
  console.log('ok narrow popup and pagination focus');
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.evaluate(() => { localStorage.removeItem('spicycar.prefs'); document.documentElement.dataset.theme = 'light'; });
  const feed = JSON.parse(await readFile(resolve(root, 'data.json'), 'utf8'));
  for (const b of Object.values(feed.brands)) for (const m of Object.values(b.models)) for (const c of m.listings || []) { c.lat = null; c.lon = null; }
  await page.route('**/data.json*', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify(feed) }));
  await open();
  assert.equal(await page.locator('#map-card').isVisible(), true);
  assert.equal(await page.locator('.car-dot-marker').count(), 0);
  assert.match(await page.locator('.car-map-status').textContent(), /0 cars with coordinates/);
  assert.equal(await page.getByRole('button', { name: 'Show on map', exact: true }).count(), 0);
  assert.equal(await page.locator('.car-place-card').count(), 8);
  console.log('ok missing coordinates keep every listing accessible');
  await page.unroute('**/data.json*');
  await page.route('**/vendor/leaflet/leaflet.js', (r) => r.abort());
  await open();
  assert.match(await page.locator('.car-map-unavailable').textContent(), /Every car is available/);
  assert.equal(await page.locator('.car-place-card').count(), 8);
  console.log('ok missing Leaflet degrades to the complete card list');
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce' });
  await phone.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  const mobile = await phone.newPage();
  mobile.on('pageerror', (e) => errors.push(e.message));
  await mobile.goto(base + query);
  await mobile.locator('.car-place-card').first().waitFor();
  assert.equal(await mobile.locator('.car-place-map').evaluate((n) => getComputedStyle(n).touchAction), 'pan-y');
  await mobile.getByRole('button', { name: 'Move map', exact: true }).click();
  assert.equal(await mobile.getByRole('button', { name: 'Done moving', exact: true }).getAttribute('aria-pressed'), 'true');
  await mobile.getByRole('button', { name: 'Done moving', exact: true }).click();
  assert.equal(await mobile.locator('.car-place-map').evaluate((n) => getComputedStyle(n).touchAction), 'pan-y');
  assert.ok(await mobile.locator('.sc-input, .sc-select').evaluateAll((ns) => ns.every((n) => parseFloat(getComputedStyle(n).fontSize) >= 16)));
  await mobile.getByRole('button', { name: 'Check for listing updates' }).click();
  await mobile.getByText('You have the latest published listings.', { exact: false }).waitFor();
  assert.ok(await mobile.locator('.car-place-card').count() > 0);
  await phone.close();
  console.log('ok touch scroll mode, explicit panning, 16px phone fields, and feed refresh');
  assert.deepEqual(errors, []);
  console.log('discovery smoke: all checks passed, zero page errors');
} finally { await context.close(); await browser.close(); server.close(); }
