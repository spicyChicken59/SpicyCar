// CI-wired matrix verification. The existing dashboard suite and its
// declared 327 checks stay unchanged; this opens the application
// with its checked-in chart + native-table presentation bundle.
//
// WHERE THE MATRIX LIVES. A reader arrives in Explore — the workspace header,
// the filter bar and the first car card. The signal matrix is not on that
// route: shopping-workspace.css hides #signal-card under
// [data-workspace="explore"] deliberately, because Explore browses and
// "Compare & save" decides. This harness is about the matrix, so it arrives
// where a reader arrives and then presses the control a reader presses. It
// used to wait on the Explore route for a table that route does not show, and
// spent thirty seconds proving the page was not the one it was looking at.
//
// node tools/matrix_navigation_smoke.mjs <design-system-checkout> [--shots <dir>]
// Requires Playwright Chromium; missing browser/dependencies are a failure.
// Dealer images and atlas geometry use the existing dashboard harness's
// offline stand-ins. Screenshot labels must not claim live production proof.
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
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
// Keep the ordinary scrollbar gutter: the reviewed 320px scrollport is 239px,
// not the wider space produced by headless Chromium's hidden scrollbars.
const browser = await chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] });
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
// The nav control by its role and its accessible name: what a reader reaches
// for, and what a screen reader announces. Never a CSS override — a panel this
// route hides is the design, and forcing it visible would test a page nobody
// is served.
const compareButton = () => page.getByRole('button', { name: 'Compare & save', exact: true });
const arrive = async () => {
  await page.goto(BASE + '/index.html', { waitUntil: 'load' });
  // Explore's own readiness condition, the one the CI-wired browse and
  // discovery suites already wait on: a car, or the map saying it has none.
  await page.waitForSelector('.car-place-card, .car-map-unavailable');
  await page.waitForTimeout(250);
  return page.evaluate(() => {
    const card = document.querySelector('.car-place-card');
    const box = card && card.getBoundingClientRect();
    const signal = document.getElementById('signal-card');
    const press = [...document.querySelectorAll('.shop-nav-button')].find((b) => b.dataset.shopView === 'compare');
    const pressBox = press && press.getBoundingClientRect();
    return {
      workspace: document.body.dataset.workspace,
      firstCarOnScreen: !!box && box.height > 0 && box.top < innerHeight && box.bottom > 0,
      // The matrix is off this route, and off it by CSS rather than by being
      // unbuilt: the rows are in the document, carrying their exact VINs.
      matrixOffRoute: !!signal && getComputedStyle(signal).display === 'none',
      rowsInDocument: document.querySelectorAll('#decision-matrix tbody tr[data-signal-vin]').length,
      // and the way to it is a real control a reader can see and press
      pressReachable: !!press && !press.hidden && !!pressBox && pressBox.width >= 44 && pressBox.height >= 44
        && pressBox.top < innerHeight && pressBox.bottom > 0,
    };
  });
};
const open = async () => {
  const arrival = await arrive();
  await compareButton().click();
  await page.waitForSelector('#decision-matrix tbody tr[data-signal-vin]');
  await page.waitForTimeout(250);
  return arrival;
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
const textMeasurements = [];
const settleScroll = async () => region().evaluate((port) => new Promise((done) => {
  let previous = port.scrollLeft, stable = 0;
  const tick = () => {
    const next = port.scrollLeft;
    stable = Math.abs(next - previous) < .1 ? stable + 1 : 0;
    previous = next;
    if (stable >= 6) done(); else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}));
// Inspect rendered text, not just TDs. A cell can be in the DOM and still
// paint its qualification outside the scrollport or beneath sticky identity.
// Vertical scrolling makes every inspected block hit-testable without moving
// the horizontal position established by the reader's criterion control.
const readableCriterion = async (column) => region().evaluate((port, column) => {
  const failures = [], texts = [], lines = [];
  const before = port.scrollLeft;
  const bounds = (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
  };
  const rows = [...port.querySelectorAll('tr')];
  for (const row of rows) for (const cell of [row.cells[0], row.cells[column]]) {
    window.scrollBy(0, cell.getBoundingClientRect().top - 100);
    const identity = row.cells[0], id = bounds(identity), clip = bounds(port);
    // This consumer paints a -4px gutter shadow. Account for any outward
    // right-hand shadow too; shadows themselves do not participate in hit tests.
    const shadow = getComputedStyle(identity).boxShadow;
    const lengths = [...shadow.matchAll(/(-?[\d.]+)px/g)].map((m) => Number(m[1]));
    const rightShadow = shadow.includes('inset') ? 0 : Math.max(0, (lengths[0] || 0) + (lengths[2] || 0) + (lengths[3] || 0));
    const readableLeft = cell === identity ? clip.left + port.clientLeft : id.right + rightShadow;
    const readableRight = clip.left + port.clientLeft + port.clientWidth;
    texts.push({ vin: row.dataset.signalVin || 'header', text: cell.innerText });
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    let node, sampled = 0;
    while ((node = walker.nextNode())) {
      if (!node.textContent.trim() || !node.parentElement.checkVisibility()) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (!r.width || !r.height) continue;
        const line = { text: node.textContent.trim(), left: r.left, right: r.right, readableLeft, readableRight };
        lines.push(line);
        if (r.left < readableLeft - 1 || r.right > readableRight + 1) failures.push({ reason: 'outside readable area', ...line });
        for (let a = node.parentElement; a && port.contains(a); a = a.parentElement) {
          const s = getComputedStyle(a), b = a.getBoundingClientRect();
          if (/hidden|clip|auto|scroll/.test(s.overflowX) && (r.left < b.left + a.clientLeft - 1 || r.right > b.left + a.clientLeft + a.clientWidth + 1))
            failures.push({ reason: 'clipping ancestor', ancestor: a.tagName + '.' + a.className, ...line });
          if (/hidden|clip|auto|scroll/.test(s.overflowY) && (r.top < b.top + a.clientTop - 1 || r.bottom > b.top + a.clientTop + a.clientHeight + 1))
            failures.push({ reason: 'vertical clipping ancestor', text: line.text });
        }
      }
      // Sample actual glyph centers, including the first and last letters;
      // a broad span box or a center-only hit can miss a covered negation.
      for (let i = 0; i < node.length; i++) {
        if (!node.textContent[i].trim()) continue;
        range.setStart(node, i); range.setEnd(node, i + 1);
        const r = range.getBoundingClientRect(), x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
        if (y < 0 || y >= innerHeight) continue;
        sampled++;
        const hit = document.elementFromPoint(x, y);
        if (!hit || !node.parentElement.contains(hit)) failures.push({ reason: 'covered text', text: node.textContent.trim(), character: i, hit: hit?.tagName + '.' + hit?.className });
      }
    }
    if (!sampled) failures.push({ reason: 'no visible text sampled', text: cell.textContent });
  }
  return { width: innerWidth, theme: document.documentElement.dataset.theme, column, scrollLeft: before,
    maxScroll: port.scrollWidth - port.clientWidth, horizontalPositionUnchanged: before === port.scrollLeft, texts, lines, failures };
}, column);
const checkText = async (name, column) => {
  const measurement = await readableCriterion(column);
  textMeasurements.push({ name, ...measurement });
  check(name, measurement.horizontalPositionUnchanged && !measurement.failures.length, JSON.stringify(measurement.failures.slice(0, 3)));
};
const scrollToEnd = async () => {
  await region().scrollIntoViewIfNeeded(); await region().hover();
  await page.mouse.wheel(10000, 0); await settleScroll();
  await page.waitForFunction(() => {
    const port = document.querySelector('#decision-matrix').parentElement;
    return Math.abs(port.scrollLeft - (port.scrollWidth - port.clientWidth)) < 1;
  });
};
let baseline, heroBaseline;
try {
  for (const [width, height] of [[390, 844], [820, 1180], [1280, 1000]]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height });
    const arrival = await open();
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
    // What replaced "the first signal remains on the opening screen": that
    // sentence was true of the matrix-first homepage and is false of this one,
    // and the honest contract underneath it is that Explore arrives on the
    // cars, the matrix is one named press away, and what opens is complete.
    await page.locator('#decision-matrix .sc-signal__label').first().scrollIntoViewIfNeeded();
    const reach = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#decision-matrix tbody tr[data-signal-vin]')];
      const label = document.querySelector('#decision-matrix .sc-signal__label');
      const box = label.getBoundingClientRect();
      return { workspace: document.body.dataset.workspace,
        onScreen: box.height > 0 && box.top >= 0 && box.bottom <= innerHeight,
        rowsDrawn: rows.length, rowsShown: rows.filter((r) => r.getBoundingClientRect().height > 0).length,
        vins: rows.map((r) => r.dataset.signalVin) };
    });
    check(`${width}px ${theme}: Explore arrives on the cars and one press opens the whole matrix`,
      arrival.workspace === 'explore' && arrival.firstCarOnScreen && arrival.matrixOffRoute
      && arrival.rowsInDocument > 1 && arrival.pressReachable
      && reach.workspace === 'compare' && reach.onScreen && reach.rowsDrawn > 1 && reach.rowsShown === reach.rowsDrawn
      && reach.vins.every((vin) => /^[A-HJ-NPR-Z0-9]{17}$/.test(vin)), JSON.stringify({ arrival, reach }));
    await shot(`matrix-navigation-explore-arrival-${width}-${theme}`);
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
  // Actual committed snapshot, isolated preferences. Theme controls and reload
  // are exercised normally. These 48 checks extend, rather than replace, the
  // original 26 (including forced colors, print and enhancement fallback).
  for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width: 320, height: 844 });
    await open();
    await page.getByRole('button', { name: theme === 'light' ? 'Light' : 'Dark', exact: true }).filter({ visible: true }).click();
    await page.reload(); await compareButton().click();
    await matrix().waitFor();
    const original = await facts(), originalHero = await heroFacts();
    await page.emulateMedia({ reducedMotion: theme === 'dark' ? 'reduce' : 'no-preference' });
    for (const [step, width] of [320, 390, 320].entries()) {
      await page.setViewportSize({ width, height: 844 });
      for (const [index, label] of LABELS.entries()) {
        if (step === 2) { await buttons().nth(index).focus(); await page.keyboard.press('Enter'); }
        else await buttons().nth(index).click();
        await settleScroll();
        await checkText(`${theme} ${width}px visit ${step}: ${label} text is unclipped and unobscured`, index + 1);
      }
      await region().scrollIntoViewIfNeeded(); await shot(`matrix-text-snapshot-${width}-${theme}-${step}-jump`);
      await scrollToEnd();
      await checkText(`${theme} ${width}px visit ${step}: final qualification stays readable at maximum scroll`, 4);
      await region().scrollIntoViewIfNeeded(); await shot(`matrix-text-snapshot-${width}-${theme}-${step}-end`);
      check(`${theme} ${width}px visit ${step}: jumps and resize preserve exact VINs and evidence`,
        JSON.stringify(await facts()) === JSON.stringify(original) && JSON.stringify(await heroFacts()) === JSON.stringify(originalHero));
    }
    const keyboard = await buttons().last().evaluate((button) => ({ focused: document.activeElement === button,
      outline: getComputedStyle(button).outlineStyle, width: parseFloat(getComputedStyle(button).outlineWidth) }));
    check(`${theme} 320px: keyboard criterion activation retains visible focus`, keyboard.focused && keyboard.outline !== 'none' && keyboard.width >= 2, JSON.stringify(keyboard));
    // Controlled presentation fixture, not a new source observation: stress
    // wrapping in each column without depending on future snapshot wording.
    await matrix().locator('tbody tr').first().locator('.market-matrix__note').evaluateAll((notes) => notes.forEach((note) => {
      note.textContent = 'AUDIT FIXTURE — Unknown evidence; this record does not establish accident-free history, factory certification or replace an independent inspection and title check.';
    }));
    for (const [index, label] of LABELS.entries()) {
      await buttons().nth(index).click(); await settleScroll();
      await checkText(`${theme} 320px long qualification fixture: ${label} wraps without clipping or cover`, index + 1);
    }
    await scrollToEnd();
    await checkText(`${theme} 320px long qualification fixture: final column remains readable at maximum scroll`, 4);
    await region().scrollIntoViewIfNeeded(); await shot(`matrix-text-fixture-320-${theme}`);
    await open(); // discard the DOM-only fixture before the unchanged fallback check
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
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
  if (SHOTS) await writeFile(join(SHOTS, 'matrix-text-measurements.json'), JSON.stringify(textMeasurements, null, 2));
  await browser.close(); server.close();
}
check('the application emits no page errors', errors.length === 0, errors.join(' | '));
const failed = checks.filter((item) => !item.pass);
console.log(`\nmatrix navigation: ${checks.length - failed.length}/${checks.length} checks`);
if (checks.length !== 74) console.log(`Expected 74 presentation checks (26 existing + 48 text-visibility checks); recorded ${checks.length}.`);
process.exit(failed.length || checks.length !== 74 ? 1 : 0);
