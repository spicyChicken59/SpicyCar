// Optional design-asset verification. The existing dashboard suite and its
// declared 274 checks stay unchanged; this opens the unchanged application
// with its checked-in chart + native-table presentation bundle.
//
// node tools/matrix_navigation_smoke.mjs <design-system-checkout> [--shots <dir>]
// Requires Playwright Chromium; missing browser/dependencies are a failure.
// Dealer images and atlas geometry use the existing dashboard harness's
// offline stand-ins. Screenshot labels must not claim live production proof.
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { extname, join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'docs');
const args = process.argv.slice(2);
const DS = args.find((arg) => !arg.startsWith('--'));
const SHOTS = args.includes('--shots') ? args[args.indexOf('--shots') + 1] : null;
if (!DS || !existsSync(join(DS, 'sc.css')) || !existsSync(join(DS, 'build/charts.js'))) {
  throw new Error('Pass the matching design-system checkout, including build/charts.js.');
}
const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
const server = createServer(async (req, res) => {
  const path = decodeURIComponent(req.url.split('?')[0]);
  const file = resolve(join(ROOT, path === '/' ? '/index.html' : path));
  if (!file.startsWith(ROOT + '/')) { res.writeHead(403).end(); return; }
  try { res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' }).end(await readFile(file)); }
  catch { res.writeHead(404).end('not found'); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const BASE = `http://127.0.0.1:${server.address().port}`;
if (SHOTS) await mkdir(SHOTS, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=', 'base64');
const stateSource = readFileSync(join(DS, 'sc-map.js'), 'utf8').match(/STATE_ABBR\s*=\s*(\{[\s\S]*?\});/)[1];
const names = [...stateSource.matchAll(/'?([A-Za-z][A-Za-z .]*?)'?\s*:\s*'[A-Z]{2}'/g)].map((item) => item[1].trim());
const arcs = [], geometries = [];
names.forEach((name, index) => {
  arcs.push([[20 + (index % 10) * 92, 20 + Math.floor(index / 10) * 95], [70, 0], [0, 70], [-70, 0], [0, -70]]);
  geometries.push({ type: 'Polygon', arcs: [[index]], id: String(index + 1).padStart(2, '0'), properties: { name } });
});
const ATLAS = JSON.stringify({ type: 'Topology', transform: { scale: [1, 1], translate: [0, 0] }, arcs,
  objects: { states: { type: 'GeometryCollection', geometries } } });
await context.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => /\.(png|jpe?g|webp|gif|svg)/i.test(route.request().url())
  ? route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
  : route.fulfill({ status: 200, contentType: 'text/plain', body: '' }));
await context.route('**://cdn.jsdelivr.net/**', (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.includes('us-atlas')) return route.fulfill({ contentType: 'application/json', body: ATLAS });
  const file = join(DS, path.replace(/^\/gh\/spicyChicken59\/design-system@[^/]+\//, ''));
  return existsSync(file) ? route.fulfill({ path: file, contentType: TYPES[extname(file)] }) : route.fulfill({ status: 404, body: 'not in design checkout' });
});
const page = await context.newPage();
const errors = [], checks = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
const check = (name, pass, detail = '') => {
  checks.push({ name, pass: !!pass });
  console.log(`  ${pass ? 'ok  ' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
};
const open = async () => {
  await page.goto(BASE + '/index.html', { waitUntil: 'load' });
  await page.waitForSelector('#decision-matrix tbody tr[data-signal-vin]');
  await page.waitForTimeout(250);
};
const shot = async (name) => { if (SHOTS) await page.screenshot({ path: join(SHOTS, name + '.png') }); };
const matrix = () => page.locator('#decision-matrix');
const region = () => matrix().locator('..');
const controls = () => page.locator('#signal-card [data-sc-matrix-controls]');
const buttons = () => controls().locator('button');
const facts = () => matrix().locator('tbody tr').evaluateAll((rows) => rows.map((row) => ({ vin: row.dataset.signalVin || '', text: row.textContent.replace(/\s+/g, ' ').trim() })));
const heroFacts = () => page.locator('#hero-cars .sc-tile').evaluateAll((tiles) => tiles.map((tile) => ({
  vin: tile.querySelector('[data-fkey^="hero:"]')?.getAttribute('data-fkey').slice(5) || '', text: tile.textContent.replace(/\s+/g, ' ').trim(),
})).filter((tile) => tile.vin));
const LABELS = ['Value context', 'Reach', 'Certification', 'Accident record'];
let baseline, heroBaseline;
try {
  for (const [width, height] of [[390, 844], [820, 1180], [1280, 1000]]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height });
    await open();
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await page.waitForTimeout(100);
    const geometry = await page.evaluate(() => {
      const table = document.getElementById('decision-matrix'), port = table.closest('.sc-table-scroll');
      const group = document.querySelector('#signal-card [data-sc-matrix-controls]');
      const first = table.querySelector('.sc-signal__label').getBoundingClientRect();
      return { pageWidth: document.documentElement.scrollWidth, width: innerWidth, height: innerHeight,
        firstBottom: first.bottom, overflow: port.scrollWidth > port.clientWidth + 1,
        controls: !!group && !group.hidden, groups: document.querySelectorAll('#signal-card [data-sc-matrix-controls]').length,
        labels: group ? [...group.querySelectorAll('button')].map((button) => button.textContent.trim()) : [],
        native: table.tagName === 'TABLE' && table.querySelectorAll('thead th[scope="col"]').length === 5,
        targets: group ? [...group.querySelectorAll('button')].map((button) => [button.getBoundingClientRect().width, button.getBoundingClientRect().height]) : [] };
    });
    check(`${width}px ${theme}: native matrix and full header navigation fit without page overflow`, geometry.native && geometry.pageWidth <= width + 1
      && geometry.groups === 1 && geometry.controls === geometry.overflow && JSON.stringify(geometry.labels) === JSON.stringify(LABELS)
      && (!geometry.controls || geometry.targets.every(([w, h]) => w >= 44 && h >= 44)), JSON.stringify(geometry));
    check(`${width}px ${theme}: the first signal remains on the opening screen`, geometry.firstBottom > 0 && geometry.firstBottom < height, `${geometry.firstBottom}px / ${height}px`);
    await shot(`matrix-navigation-first-${width}-${theme}`);
    await page.locator('#signal-card').scrollIntoViewIfNeeded();
    await shot(`matrix-navigation-${width}-${theme}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open();
  baseline = await facts(); heroBaseline = await heroFacts();
  const groupSemantics = await controls().evaluate((group) => ({ role: group.getAttribute('role'), name: group.getAttribute('aria-label'),
    buttons: [...group.querySelectorAll('button')].map((button) => ({ type: button.type, controls: button.getAttribute('aria-controls') })) }));
  const regionId = await region().getAttribute('id');
  check('criterion buttons name their native region with ordinary button semantics', groupSemantics.role === 'group' && !!groupSemantics.name
    && !!regionId && groupSemantics.buttons.every((button) => button.type === 'button' && button.controls === regionId), JSON.stringify(groupSemantics));
  await page.keyboard.press('Tab'); await buttons().first().focus();
  const focusRing = await buttons().first().evaluate((button) => document.activeElement === button && getComputedStyle(button).outlineStyle !== 'none' && parseFloat(getComputedStyle(button).outlineWidth) >= 2);
  await page.keyboard.press('Tab');
  check('criterion controls have visible keyboard focus and sequential tab stops', focusRing && await buttons().nth(1).evaluate((button) => document.activeElement === button));
  await page.locator('#signal-card').scrollIntoViewIfNeeded();
  await shot('matrix-navigation-default-phone');
  for (const [index, label] of LABELS.entries()) {
    await buttons().nth(index).click();
    const placement = await region().evaluate((port, column) => ({ left: port.scrollLeft, right: port.getBoundingClientRect().right - port.clientLeft,
      cells: [...port.querySelectorAll('tr')].map((row) => ({ identity: row.cells[0].getBoundingClientRect().right,
        left: row.cells[column].getBoundingClientRect().left, right: row.cells[column].getBoundingClientRect().right })) }), index + 1);
    await page.waitForTimeout(100);
    const settled = await region().evaluate((port) => port.scrollLeft);
    check(`${label}: complete evidence appears beside the sticky candidate without reduced-motion scrolling`, placement.cells.length > 1
      && placement.cells.every((cell) => cell.left >= cell.identity - 1 && cell.right <= placement.right + 1)
      && Math.abs(settled - placement.left) <= 1, JSON.stringify(placement));
  }
  await shot('matrix-navigation-history-phone');
  check('all criterion jumps preserve exact row VINs, prices, evidence and decision cards', JSON.stringify(await facts()) === JSON.stringify(baseline)
    && JSON.stringify(await heroFacts()) === JSON.stringify(heroBaseline), `${baseline.length} exact candidate rows`);
  if (!await page.locator('#f-budget').isVisible()) await page.locator('#filter-toggle').click();
  await page.fill('#f-budget', '1'); await page.locator('#f-budget').press('Tab'); await page.waitForTimeout(350);
  const removed = await page.locator('#signal-card').isHidden();
  await page.fill('#f-budget', ''); await page.locator('#f-budget').press('Tab'); await page.waitForTimeout(350);
  const unique = await controls().count() === 1 && await buttons().count() === LABELS.length;
  await buttons().last().click();
  check('ordinary filter rerenders restore one functional navigation group and original rows', removed && unique && await controls().isVisible()
    && await region().evaluate((port) => port.scrollLeft > 0) && JSON.stringify(await facts()) === JSON.stringify(baseline));
  await buttons().first().focus(); await page.setViewportSize({ width: 1280, height: 1000 }); await page.waitForTimeout(200);
  check('wide layout hides unnecessary controls and transfers their keyboard focus to the table', await controls().isHidden()
    && await region().evaluate((port) => port.scrollWidth <= port.clientWidth + 1 && document.activeElement === port));
  await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(200);
  check('phone resize restores one navigation group', await controls().count() === 1 && await controls().isVisible());
  await page.emulateMedia({ forcedColors: 'active' });
  const forced = await buttons().evaluateAll((nodes) => nodes.map((node) => ({ text: node.textContent.trim(), border: getComputedStyle(node).borderStyle })));
  check('forced colors retains labeled bordered controls', forced.length === 4 && forced.every((node) => node.text && node.border !== 'none'));
  await page.emulateMedia({ forcedColors: 'none', media: 'print' });
  check('print removes criterion navigation while retaining all matrix facts', await controls().isHidden() && JSON.stringify(await facts()) === JSON.stringify(baseline));
  await page.emulateMedia({ media: 'screen', reducedMotion: 'no-preference' });
  await page.evaluate(() => localStorage.removeItem('spicycar.prefs'));
  // Core chart primitives are the unchanged authored source. Loading only
  // those reproduces the previous design asset without the optional table
  // enhancement, while preserving the application's required chart API.
  await context.route('**/sc-charts.js', (route) => route.fulfill({ contentType: 'application/javascript', body: readFileSync(join(DS, 'build/charts.js'), 'utf8') }));
  try {
    await open(); await region().focus(); await page.keyboard.press('ArrowRight'); await page.waitForTimeout(200);
    const fallback = await region().evaluate((port) => ({ enhanced: typeof window.SCMatrixNav !== 'undefined', focus: document.activeElement === port,
      left: port.scrollLeft, role: port.getAttribute('role'), name: port.getAttribute('aria-label'), native: port.querySelector('table')?.tagName === 'TABLE' }));
    check('without the optional enhancement the unchanged application retains its native keyboard-scrollable matrix', !fallback.enhanced && fallback.focus && fallback.left > 0
      && fallback.role === 'region' && !!fallback.name && fallback.native && await controls().count() === 0
      && JSON.stringify(await facts()) === JSON.stringify(baseline), JSON.stringify(fallback));
  } finally { await context.unroute('**/sc-charts.js'); }
} catch (error) {
  check('the complete presentation verification runs', false, error.stack || error.message);
} finally {
  await browser.close(); server.close();
}
check('the application emits no page errors', errors.length === 0, errors.join(' | '));
const failed = checks.filter((item) => !item.pass);
console.log(`\nmatrix navigation: ${checks.length - failed.length}/${checks.length} checks`);
if (checks.length !== 26) console.log(`Expected 26 presentation checks; recorded ${checks.length}.`);
process.exit(failed.length || checks.length !== 26 ? 1 : 0);
