// The user-facing search flow, with no provider requests or external writes.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve, extname} from 'node:path';
import {chromium} from 'playwright';
const root = resolve('docs');
const data = JSON.parse(await readFile(resolve(root, 'data.json'), 'utf8'));
const models = Object.entries(data.brands).flatMap(([bk,b]) => Object.entries(b.models).map(([mk,m]) => ({key:bk+'/'+mk,bk,mk,...m})));
const selected = models.filter((m) => m.bk !== 'bmw' && m.listings.length > 5).slice(0,2);
assert.equal(selected.length,2);
const expected = selected.reduce((n,m) => n+m.listings.length,0);
const server = createServer(async (req,res) => {
  const path = resolve(root,'.'+new URL(req.url,'http://local').pathname.replace(/\/$/,'/index.html'));
  if (!path.startsWith(root+'/')) return res.writeHead(403).end();
  try { res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'})[extname(path)]||'application/octet-stream'); res.end(await readFile(path)); }
  catch {res.writeHead(404).end();}
});
await new Promise((r) => server.listen(0,'127.0.0.1',r));
const base = 'http://127.0.0.1:'+server.address().port;
const browser = await chromium.launch();
const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
await context.route(/^https?:\/\/(?!127\.0\.0\.1)/,(r) => r.abort());
const page = await context.newPage(); const errors=[]; page.on('pageerror',(e) => errors.push(e.message));
const choose = async (p) => {await p.getByRole('button',{name:'Choose cars',exact:true}).click(); await p.getByRole('dialog').waitFor();};
const filters = async (p) => {if(await p.locator('#filter-toggle').getAttribute('aria-expanded')!=='true') await p.locator('#filter-toggle').click();};
try {
  await page.goto(base); await page.locator('.car-place-card').first().waitFor();
  assert.equal(await page.locator('#hero-card').isVisible(),false,'browse opens before recommendation panels');
  assert.equal(await page.locator('#promo-card').isVisible(),false,'no promotional hero');
  await choose(page); await page.getByRole('button',{name:'Clear',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'Shop these models'}).isDisabled(),true);
  for(const m of selected) await page.getByRole('checkbox',{name:m.label,exact:true}).check();
  await page.getByRole('button',{name:'Shop these models'}).click();
  assert.match(await page.locator('.shop-subtitle').textContent(),new RegExp('^'+expected+' matching cars'));
  await page.goBack(); await page.locator('.car-place-card').first().waitFor();
  assert.match(await page.locator('.shop-subtitle').textContent(),new RegExp(models.length+' models'));
  await page.goForward(); await page.locator('.car-place-card').first().waitFor();
  await page.reload(); await page.locator('.car-place-card').first().waitFor();
  assert.match(await page.locator('.shop-subtitle').textContent(),new RegExp('^'+expected+' matching cars'));
  await page.getByRole('button',{name:'Compare & save',exact:true}).click();
  const chosenLabels=await page.locator('#hero-cars .sc-tile__label').allTextContents();
  assert.deepEqual(chosenLabels.map((s)=>s.split(' — ')[0].trim()).sort(),selected.map((m)=>m.label).sort());
  assert.equal(await page.locator('#promo-card').isVisible(),false,'BMW offers do not lead a non-BMW search');
  await page.locator('#shop-apr').fill('0'); await page.locator('#shop-offers').uncheck();
  await page.locator('#shop-cost-controls').click(); await page.locator('#f-sort').selectOption('payment');
  assert.match(await page.locator('#list-hint').textContent(),/0%/);
  assert.doesNotMatch(await page.locator('#list-hint').textContent(),/2\.99%|BMW FS/);
  await page.getByRole('button',{name:'Explore cars',exact:true}).click();
  const star=page.locator('.car-place-actions [data-fkey^="star:"]').first();
  await star.click();
  assert.match(await page.locator('.car-place-card').first().textContent(),/Saved/);
  assert.equal(await page.locator('.car-place-actions [data-fkey^="star:"]').first().evaluate((n)=>n===document.activeElement),true);
  await page.getByRole('button',{name:'Compare & save',exact:true}).click();
  assert.equal(await page.locator('#finalists-card').isVisible(),true,'one saved car stays accessible');
  assert.equal(await page.locator('#finalists-table thead [data-fkey^="fin:"]').count(),1);
  await page.getByRole('button',{name:'Explore cars',exact:true}).click();
  await choose(page); await page.getByRole('button',{name:'Select all '+models.length,exact:true}).click();
  await page.getByRole('button',{name:'Shop these models'}).click();
  assert.match(await page.locator('.shop-subtitle').textContent(),new RegExp(models.length+' models'));
  console.log('ok choose any/all models, shopping roles, Back, persistence, contextual finance, and a single saved car');
  // Every candidate still receives the entered zero rate, including certified BMWs.
  await page.getByRole('button',{name:'Compare & save',exact:true}).click();
  await page.locator('#shop-offers').check();
  assert.equal(await page.locator('#promo-card').isVisible(),false,'a higher offer cannot override the user APR');
  await page.goto(base+'/?brand='+selected[0].bk+'&m='+selected[0].mk);
  await page.locator('.car-place-card').first().waitFor();
  assert.equal(await page.locator('.shop-title').textContent(),selected[0].label,'explicit model links override saved scope');
  for(const width of [320,390,820,1440]) {
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),width+'px overflow');
  }
  const phone=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,reducedMotion:'reduce'});
  await phone.route(/^https?:\/\/(?!127\.0\.0\.1)/,(r)=>r.abort());
  const mobile=await phone.newPage(); mobile.on('pageerror',(e)=>errors.push(e.message));
  await mobile.goto(base); await mobile.locator('.car-place-card').first().waitFor();
  assert.equal(await mobile.locator('.car-place-panel').isVisible(),false);
  await mobile.getByRole('button',{name:'Map',exact:true}).click();
  assert.equal(await mobile.locator('.car-place-panel').isVisible(),true);
  await mobile.waitForTimeout(200);
  const visibleDots=await mobile.locator('.car-dot-marker').evaluateAll((ns)=>ns.filter((n)=>{const r=n.getBoundingClientRect(),m=n.closest('.car-place-map').getBoundingClientRect();return r.right>m.left&&r.left<m.right&&r.bottom>m.top&&r.top<m.bottom;}).length);
  assert.ok(visibleDots>0,'first phone map fits actual cars');
  await mobile.getByRole('button',{name:'Search this map area',exact:true}).click();
  assert.equal(await mobile.locator('.car-place-results').isVisible(),true);
  assert.match(await mobile.locator('.car-place-count').textContent(),/in this map area/);
  await mobile.getByRole('button',{name:'Show all matching cars'}).click();
  await choose(mobile); await mobile.getByRole('searchbox',{name:'Search available car models'}).fill(selected[0].label);
  assert.equal(await mobile.locator('.shop-model-choice').count(),1);
  await mobile.getByRole('button',{name:'Close',exact:true}).click();
  assert.deepEqual(errors,[]);
  await phone.close();
  console.log('ok phone Cars/Map switch, initial map fit, area browsing, searchable picker, and 320–1440px layouts');
  console.log('workspace smoke: all checks passed, zero page errors');
} finally {await context.close();await browser.close();server.close();}
