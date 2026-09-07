// Design-only layout verification for the photo dossier and instrument strip.
// node tools/fieldwork_smoke.mjs <design-system-checkout> [--shots <dir>]
// Uses unchanged checked-in product data. External photographs are replaced
// with a visibly labeled geometry fixture; these captures are NOT live proof.
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
// The live candidates use near-4:3 photographs; a cinematic 16:9 stand-in
// understates how much stage height their complete, uncropped images need.
const PIXEL = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="953" height="768"><rect width="953" height="768" fill="#dce5ef"/><rect x="8" y="8" width="937" height="752" fill="none" stroke="#23384d" stroke-width="16"/><text x="476.5" y="384" text-anchor="middle" font-family="sans-serif" font-size="26" fill="#23384d">IMAGE GEOMETRY FIXTURE · NOT A LISTING PHOTO</text></svg>');
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
  ? route.fulfill({ status: 200, contentType: 'image/svg+xml', body: PIXEL })
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
const shot = async (name, locator) => {
  if (SHOTS) await locator.screenshot({ path: join(SHOTS, name + '.png'), animations: 'disabled' });
};
const facts = () => page.locator('#hero-cars, #kpis, #decision-matrix').evaluateAll((nodes) => nodes.map((node) => ({
  text: node.textContent.replace(/\s+/g, ' ').trim(),
  links: [...node.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href'), a.dataset.fkey]),
  photos: [...node.querySelectorAll('img.sc-frame__img')].map((img) => img.getAttribute('src')),
})));
const inspect = () => page.evaluate(() => {
  const box = (node) => { const b = node.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height, bottom: b.bottom }; };
  const rgb = (s) => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  const lum = (s) => rgb(s).map((v) => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const contrast = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
  const strip = document.getElementById('kpis'), stripBg = getComputedStyle(strip).backgroundColor;
  return {
    overflow: document.documentElement.scrollWidth - innerWidth,
    firstSignal: document.querySelector('#decision-matrix .sc-signal__label').getBoundingClientRect().bottom,
    heroHeight: document.getElementById('hero-card').getBoundingClientRect().height,
    photos: [...document.querySelectorAll('.market-candidate__media')].map((media) => {
      const image = media.querySelector('img.sc-frame__img'), frame = media.querySelector('.sc-frame'), caption = media.querySelector('.market-candidate__index');
      return { media: box(media), frame: box(frame), caption: box(caption), tile: box(media.parentElement),
        naturalWidth: image?.naturalWidth, naturalHeight: image?.naturalHeight,
        drawnWidth: image && Math.min(image.clientWidth, image.clientHeight * image.naturalWidth / image.naturalHeight),
        fit: image && getComputedStyle(image).objectFit, transform: image && getComputedStyle(image).transform };
    }),
    contrast: [...strip.querySelectorAll('.sc-tile__label,.sc-tile__value,.sc-tile__sub,a,.sc-delta')].map((node) => contrast(getComputedStyle(node).color, stripBg)),
    values: [...strip.querySelectorAll('.sc-tile__value')].map((node) => ({ box: box(node), parent: box(node.parentElement), whiteSpace: getComputedStyle(node).whiteSpace })),
  };
});
try {
  for (const [width, height] of [[390, 844], [820, 1180], [1280, 1000]]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width, height });
    await open();
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    const before = await facts();
    await page.locator('link[href="market-studio.css"]').evaluate((link) => { link.disabled = true; });
    const native = await facts();
    await page.locator('link[href="market-studio.css"]').evaluate((link) => { link.disabled = false; });
    await page.waitForFunction(() => document.querySelector('link[href="market-studio.css"]').sheet !== null
      && getComputedStyle(document.querySelector('.market-candidate__media')).aspectRatio !== 'auto');
    const shape = await inspect();
    check(`${width}px ${theme}: image stage fits, preserves full image and separates its caption`, shape.overflow <= 1
      && shape.photos.length === 2 && shape.photos.every((p) => p.media.w >= (width <= 600 ? 110 : p.tile.w - 50) && p.media.h >= (width <= 600 ? 112 : 168)
        && p.naturalWidth === 953 && p.naturalHeight === 768 && (width < 1000 || p.drawnWidth >= p.media.w * .65)
        && p.fit === 'contain' && p.transform === 'none' && p.frame.bottom <= p.caption.y + 1), JSON.stringify(shape.photos));
    check(`${width}px ${theme}: first signal stays on arrival and instrument values remain legible`, shape.firstSignal > 0 && shape.firstSignal < height
      && (width > 600 || shape.heroHeight <= height + 60)
      && shape.contrast.every((ratio) => ratio >= 4.5) && shape.values.every((v) => v.box.x >= v.parent.x && v.box.x + v.box.w <= v.parent.x + v.parent.w), JSON.stringify({ firstSignal: shape.firstSignal, minimumContrast: Math.min(...shape.contrast) }));
    check(`${width}px ${theme}: CSS preserves exact candidates, evidence, metric text, photos and links`, JSON.stringify(before) === JSON.stringify(native));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await shot(`fieldwork-dossier-${width}-${theme}`, page.locator(width <= 600 ? '#hero-cars > .market-candidate' : '#hero-card').first());
    await shot(`fieldwork-instrument-${width}-${theme}`, page.locator('#kpis'));
    await page.emulateMedia({ reducedMotion: 'no-preference' });
  }
  await page.setViewportSize({ width: 320, height: 760 });
  await open();
  const narrowShape = await inspect();
  check('320px: image remains meaningful and values wrap without page overflow', narrowShape.overflow <= 1 && narrowShape.photos.every((p) => p.media.h >= 112 && p.media.w >= 90));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open();
  const nativeDetails = page.locator('#hero-cars details').first(), summary = nativeDetails.locator('summary');
  await summary.focus();
  const focused = await summary.evaluate((node) => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none');
  await page.keyboard.press('Enter');
  check('phone evidence remains a native keyboard disclosure with visible focus', focused && await nativeDetails.evaluate((node) => node.open));
  const motion = await page.locator('.market-candidate__media img').first().evaluate((node) => ({ animation: getComputedStyle(node).animationName, duration: getComputedStyle(node).transitionDuration, transform: getComputedStyle(node).transform }));
  check('reduced motion keeps the photograph static and fully visible', motion.animation === 'none' && motion.duration === '0s' && motion.transform === 'none', JSON.stringify(motion));
  const kpiLink = page.locator('#kpis a').first();
  await kpiLink.focus();
  check('instrument links retain a visible keyboard focus ring', await kpiLink.evaluate((node) => document.activeElement === node && parseFloat(getComputedStyle(node).outlineWidth) >= 2 && getComputedStyle(node).outlineStyle !== 'none'));
  const originalFacts = await facts();
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
  check('forced colors keeps metric borders and removes decorative frame brackets', await page.locator('#kpis').evaluate((node) => getComputedStyle(node).borderTopStyle !== 'none')
    && await page.locator('.market-candidate__media').first().evaluate((node) => getComputedStyle(node, '::before').display === 'none'));
  await page.emulateMedia({ forcedColors: 'none', media: 'print', reducedMotion: 'reduce' });
  check('print removes photo ornament and retains the exact report facts on paper', await page.locator('.market-candidate__media').first().isHidden()
    && JSON.stringify(await facts()) === JSON.stringify(originalFacts) && await page.locator('#kpis').evaluate((node) => getComputedStyle(node).backgroundColor === 'rgb(255, 255, 255)'));
  await page.emulateMedia({ media: 'screen', reducedMotion: 'reduce' });
  await context.route(/\.(png|jpe?g|webp|gif|svg)/i, (route) => /^https?:\/\/127\.0\.0\.1/.test(route.request().url()) ? route.continue() : route.abort());
  await open();
  await page.waitForSelector('.market-candidate__media .sc-frame--empty');
  const empty = await page.locator('.market-candidate__media').first().evaluate((node) => {
    const frame = node.querySelector('.sc-frame--empty'), mark = frame?.querySelector('img');
    return { text: frame && getComputedStyle(frame, '::after').content, markWidth: mark?.getBoundingClientRect().width,
      src: mark?.getAttribute('src'), captionHidden: getComputedStyle(node.querySelector('.market-candidate__index')).display === 'none' };
  });
  check('failed photographs show the original unaltered mark and honest unavailable text', empty.text === '"photo unavailable"' && empty.markWidth >= 40 && empty.src?.includes('sc-mark-') && empty.captionHidden, JSON.stringify(empty));
  await shot('fieldwork-photo-unavailable-phone', page.locator('#hero-cars > .market-candidate').first());
  // Deliberately failed external photo requests are expected in this final case.
  check('no uncaught application errors during layout and input review', !errors.filter((error) => !error.includes('net::ERR_FAILED')).length, JSON.stringify(errors));
} finally {
  await context.close(); await browser.close(); await new Promise((done) => server.close(done));
}
const failed = checks.filter((entry) => !entry.pass);
console.log(`\n${checks.length - failed.length}/${checks.length} fieldwork checks passed`);
if (failed.length) process.exitCode = 1;
