/* The showroom and garage read the same costs and record as the dashboard. */
(function () {
  'use strict';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const button = (text, cls, run) => { const b = el('button', cls, text); b.type = 'button'; b.onclick = run; return b; };
  const money = (v) => Number.isFinite(v) ? '$' + Math.round(v).toLocaleString('en-US') : 'Unreported';
  const link = (text, url) => { const a = el('a', 'studio-button', text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; };
  const label = (text, control) => { const l = el('label', 'studio-field'); l.append(el('span', '', text), control); return l; };
  function create(api) {
    let notes = {}, mode = 'car', currentVin = null, opener = null, storageOkay = true;
    try { const raw = JSON.parse(localStorage.getItem('spicycar.garage') || '{}'); for (const [vin, note] of Object.entries(raw || {})) if (/^[A-Z0-9]{6,24}$/.test(vin) && typeof note === 'string') notes[vin] = note.slice(0, 3000); } catch (_) { storageOkay = false; }
    const toolbar = el('div', 'studio-toolbar');
    const mission = el('select', 'studio-select'); mission.id = 'studio-mission'; mission.setAttribute('aria-label', 'Deal radar');
    for (const [value, text] of [['all','All matches'],['near','Drivable cars'],['drops','Latest price drops'],['fresh','New at last fetch'],['saved','Saved cars']]) { const o = el('option', '', text); o.value = value; mission.append(o); }
    mission.onchange = () => api.mission(mission.value);
    const lenses = el('div', 'studio-lenses'); lenses.setAttribute('role', 'group'); lenses.setAttribute('aria-label', 'Display car prices');
    const price = button('Price', 'studio-lens', () => api.lens('price'));
    const monthly = button('Monthly', 'studio-lens', () => api.lens('monthly'));
    lenses.append(price, monthly); toolbar.append(mission, lenses);
    const paymentBar = el('div', 'studio-payment-bar');
    const ceiling = el('input', 'studio-input'); ceiling.id = 'studio-monthly-max'; ceiling.type = 'number'; ceiling.min = '0'; ceiling.max = '100000'; ceiling.step = '25'; ceiling.placeholder = 'No limit';
    ceiling.onchange = () => { const v = Number(ceiling.value); if (Number.isFinite(v) && v >= 0 && v <= 100000) api.budget(v); else update(); };
    const terms = el('p', 'studio-terms');
    paymentBar.append(label('Max / month', ceiling), terms, button('Edit financing', 'studio-text-button', () => { api.finance(); }));
    const host = document.getElementById('car-discovery'); host.prepend(toolbar, paymentBar);
    const garage = button('Garage', 'shop-nav-button shop-garage', openGarage); garage.setAttribute('aria-haspopup', 'dialog');
    const navigation=document.querySelector('.shop-navigation'); navigation.insertBefore(garage,navigation.querySelector('[data-shop-view="compare"]'));
    const dialog = el('dialog', 'studio-dialog'); dialog.setAttribute('aria-labelledby', 'studio-title');
    const head = el('div', 'studio-dialog-head'); const title = el('h2', '', 'Your garage'); title.id = 'studio-title'; title.tabIndex = -1;
    const back = button('← Garage', 'studio-text-button', openGarage); back.hidden = true;
    head.append(back, title, button('Close', 'studio-button', () => dialog.close()));
    const content = el('div', 'studio-content'); dialog.append(head, content); document.body.append(dialog);
    dialog.addEventListener('click', (e) => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
    dialog.addEventListener('close', () => { if (!opener?.isConnected) { const replacement = [...document.querySelectorAll('[data-focus-action="look"]')].find((n) => n.dataset.focusVin === currentVin); (replacement || garage).focus({preventScroll:true}); } });
    function show(next) { mode = next; if (!dialog.open) opener = document.activeElement; draw(); if (!dialog.open) dialog.showModal(); content.scrollTop = 0; title.focus({preventScroll:true}); }
    function open(vin) { if (!api.car(vin)) return; currentVin = vin; show('car'); }
    function openGarage() { show('garage'); }
    function saveNotes(vin, text) {
      if (text.trim()) notes[vin] = text.slice(0,3000); else delete notes[vin];
      try { localStorage.setItem('spicycar.garage', JSON.stringify(notes)); storageOkay = true; } catch (_) { storageOkay = false; }
    }
    function records() {
      return [...new Set([...api.savedVins(), ...Object.keys(notes)])].map((vin) => api.car(vin) || {vin, title:'Car no longer in the record', missing:true, status:api.status(vin)});
    }
    function photo(c, cls) {
      const wrap = el('div', cls); const empty = el('span', 'studio-photo-empty', 'Photo unavailable'); wrap.append(empty);
      if (c.image) { const img = el('img'); img.src = c.image; img.alt = c.title; img.referrerPolicy = 'no-referrer'; img.onload = () => empty.hidden = true; img.onerror = () => img.remove(); wrap.append(img); }
      return wrap;
    }
    function statusControl(c) {
      const select = el('select', 'studio-select'); select.setAttribute('aria-label', 'Status for ' + c.vin);
      for (const [value, text] of [['','Not saved'],['short','Saved'],['called','Contacted'],['out','Ruled out']]) { const o = el('option','',text); o.value = value; select.append(o); }
      select.value = c.status || ''; select.onchange = () => { api.status(c.vin, select.value); update(); if(mode==='garage'){ const y=content.scrollTop; draw(); content.scrollTop=y; const replacement=[...content.querySelectorAll('select')].find(n=>n.getAttribute('aria-label')==='Status for '+c.vin); (replacement||title).focus({preventScroll:true}); } };
      return select;
    }
    function notesField(c) {
      const input = el('textarea', 'studio-notes'); input.rows = 4; input.maxLength = 3000; input.value = notes[c.vin] || ''; input.placeholder = 'Questions, test-drive impressions, dealer quotes…';
      const state = el('p','studio-note-state',storageOkay ? 'Notes stay on this device. Copy a brief to keep a separate copy.' : 'Storage unavailable. Keep a copy of your notes before closing.'); state.setAttribute('role','status');
      input.oninput = () => { saveNotes(c.vin, input.value); state.textContent = storageOkay ? 'Saved on this device.' : 'Storage unavailable — copy your notes before closing.'; };
      const wrap = el('div'); wrap.append(label('Your notes',input),state); return wrap;
    }
    function stat(name, value, sub) { const n = el('div','studio-stat'); n.append(el('span','',name),el('strong','',value)); if (sub) n.append(el('small','',sub)); return n; }
    function history(c) {
      const section = el('section','studio-history'); section.append(el('h3','','The price journey'));
      const series = (c.series || []).filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p[0]) && Number.isFinite(p[1]) && p[1] > 0).slice().sort((a,b)=>a[0].localeCompare(b[0]));
      if (series.length < 2) { section.append(el('p','','One recorded price so far. A trend needs another observation.')); return section; }
      const first = series[0], last = series[series.length-1], diff = last[1]-first[1];
      section.append(el('p','studio-history-summary',(diff < 0 ? money(-diff)+' lower' : diff > 0 ? money(diff)+' higher' : 'Price unchanged') + ' since '+first[0]+'. Last observed '+last[0]+'.'));
      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 640 150'); svg.setAttribute('role','img'); svg.setAttribute('aria-label','Recorded asking price from '+money(first[1])+' to '+money(last[1]));
      const lo=Math.min(...series.map(p=>p[1])), hi=Math.max(...series.map(p=>p[1])), t0=Date.parse(first[0]), span=Math.max(1,Date.parse(last[0])-t0);
      const points=series.map(p=>[12+(Date.parse(p[0])-t0)/span*616,hi===lo?75:128-(p[1]-lo)/(hi-lo)*106]);
      const line=document.createElementNS(svg.namespaceURI,'polyline'); line.setAttribute('points',points.map(p=>p.join(',')).join(' ')); line.setAttribute('class','studio-history-line'); svg.append(line);
      for (const p of points) { const dot=document.createElementNS(svg.namespaceURI,'circle'); dot.setAttribute('cx',p[0]); dot.setAttribute('cy',p[1]); dot.setAttribute('r','3'); svg.append(dot); }
      section.append(svg);
      const detail=el('details'); detail.append(el('summary','','See recorded prices'));
      const table=el('table','studio-history-table'); const thead=el('thead'), headings=el('tr'); for(const t of ['Date','Asking price']) {const th=el('th','',t);th.scope='col';headings.append(th);} thead.append(headings);table.append(thead);
      const tbody=el('tbody');for(const p of series){const tr=el('tr');tr.append(el('td','',p[0]),el('td','',money(p[1])));tbody.append(tr);}table.append(tbody);detail.append(table);section.append(detail);return section;
    }
    function drawCar(c) {
      title.textContent = c.title; back.hidden = false;
      const hero=el('div','studio-showroom');hero.append(photo(c,'studio-hero-photo'));
      const info=el('div','studio-showroom-info');info.append(el('p','studio-eyebrow',c.gone ? 'LEFT THE TRACKED LISTINGS' : 'THE CAR IN FOCUS'),el('p','studio-hero-price',money(c.price)),el('p','studio-muted',c.gone?'Last recorded asking price':'Asking price · '+c.location));
      const stats=el('div','studio-cost-grid');stats.append(stat('Estimated all in',money(c.otd),'Includes configured tax, fees and shipping'),stat('Estimated monthly',c.payment==null?'Unavailable':money(c.payment)+'/mo',c.terms));info.append(stats);
      info.append(el('p','studio-muted',c.shipping));
      const actions=el('div','studio-actions');actions.append(statusControl(c)); if(c.url)actions.append(link('Dealer listing ↗',c.url));actions.append(button('Copy dealer brief','studio-button',()=>showBrief([c])));info.append(actions);hero.append(info);content.append(hero);
      const facts=el('div','studio-facts'); for(const [name,value] of [['Mileage',c.milesLabel],['Dealer',c.dealer],['VIN',c.vin],['Condition',c.flags || 'No condition details reported'],['First tracked',c.firstSeen || 'Unreported'],['Record through',c.through]]) facts.append(stat(name,value)); content.append(facts);
      content.append(history(c),notesField(c));
      content.append(el('p','studio-muted','Recorded listings and estimates. Confirm availability, condition and the written out-the-door quote with the dealer.'));
    }
    function drawGarage() {
      title.textContent='Your garage';back.hidden=true;
      const cars=records(),active=cars.filter(c=>['short','called'].includes(c.status));
      const tools=el('div','studio-garage-tools');tools.append(el('p','','Keep the contenders. Remember the conversations.'));
      const brief=button('Build dealer brief','studio-button studio-button-primary',()=>showBrief(records().filter(c=>['short','called'].includes(c.status))));brief.disabled=!active.length;tools.append(brief);content.append(tools);
      content.append(el('p','studio-muted','Statuses and notes are saved on this device. Your brief includes your notes; review it before sharing.'));
      if (!cars.length) {const empty=el('div','studio-empty');empty.append(el('h3','','Room for your next car.'),el('p','','Save a car from the results, or add a note in Quick look.'));content.append(empty);return;}
      const grid=el('div','studio-garage-grid');
      for(const c of cars) {const card=el('article','studio-garage-card');card.dataset.garageVin=c.vin; if(!c.missing)card.append(photo(c,'studio-garage-photo'));const body=el('div','studio-garage-body');body.append(el('h3','',c.title),el('p','studio-garage-price',c.missing?c.vin:money(c.price)),el('p','studio-muted',c.missing?'Your status and notes are retained.':c.gone?'No longer in the tracked listings':c.location+' · '+c.milesLabel));body.append(statusControl(c));if(!c.missing)body.append(button('Open showroom','studio-text-button',()=>open(c.vin)));if(notes[c.vin])body.append(el('p','studio-note-preview',notes[c.vin]));if(c.missing)body.append(notesField(c));card.append(body);grid.append(card);}content.append(grid);
      const compare=button('Compare saved cars','studio-button',()=>{dialog.close();api.compare();});compare.disabled=!active.length;content.append(compare);
    }
    function briefText(cars) {
      const lines=['SpicyCar · dealer conversation brief','Prepared from the tracked record through '+api.through(),'Estimates use my saved financing assumptions. Availability and final terms need confirmation.',''];
      for(const c of cars) {lines.push(c.title,'VIN: '+c.vin);if(!c.missing){lines.push('Dealer: '+c.dealer,'Location: '+c.location,(c.gone?'Last recorded asking: ':'Observed asking: ')+money(c.price),'Estimated all in: '+money(c.otd),'Estimated payment: '+(c.payment==null?'unavailable':money(c.payment)+'/mo')+' · '+c.terms,'Shipping: '+c.shipping,'Record through: '+c.through);if(c.url)lines.push('Listing: '+c.url);if(c.gone)lines.push('Status: no longer in tracked listings');}if(notes[c.vin])lines.push('My notes: '+notes[c.vin]);lines.push('Please confirm availability, title/accident history, included options and a written itemized out-the-door quote.','');}return lines.join('\n');
    }
    function showBrief(cars) {
      mode='brief';title.textContent='Your dealer brief';back.hidden=false;content.replaceChildren();
      content.append(el('p','studio-muted','Review your notes and estimates before sharing. Nothing is sent automatically.'));
      const text=el('textarea','studio-brief');text.rows=16;text.value=briefText(cars);text.setAttribute('aria-label','Dealer brief');content.append(text);
      const status=el('p','studio-note-state');status.setAttribute('role','status');
      content.append(button('Copy brief','studio-button studio-button-primary',async()=>{try{if(!navigator.clipboard?.writeText)throw new Error('Unavailable');await navigator.clipboard.writeText(text.value);status.textContent='Brief copied.';}catch(_){text.focus();text.select();status.textContent='Select and copy the brief above. Clipboard access is unavailable.';}}),status);content.scrollTop=0;title.focus({preventScroll:true});
    }
    function draw() {content.replaceChildren();if(mode==='garage')drawGarage();else {const c=api.car(currentVin);if(c)drawCar(c);else drawGarage();}}
    function update() {
      const state=api.state();mission.value=state.mission;price.setAttribute('aria-pressed',String(state.lens==='price'));monthly.setAttribute('aria-pressed',String(state.lens==='monthly'));monthly.disabled=!state.hasFinance;
      paymentBar.hidden=state.lens!=='monthly';if(document.activeElement!==ceiling)ceiling.value=state.monthlyBudget||'';terms.textContent=state.terms;
      const n=api.savedVins().filter(v=>api.status(v)!=='out').length;garage.textContent='Garage'+(n?' · '+n:'');
    }
    return {open,openGarage,update};
  }
  window.ShoppingStudio={create};
})();
