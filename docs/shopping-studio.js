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
    // The one car in focus gets the design system's own photo dossier rather
    // than a box sized by the column of text beside it: a 16:10 stage on the
    // raised ground, the dealer's photograph shown WHOLE (--studio fits by
    // contain, so nothing is cropped away by a layout decision), the corner
    // brackets, and the shared "no photo" band when the listing has none.
    // The garage grid keeps photo() above — its cards are a cover crop by
    // design, and this stage is for the record being decided on.
    function heroPhoto(c) {
      const card = el('div', 'sc-photo-card sc-dossier sc-dossier--studio studio-hero-stage');
      const media = el('div', 'sc-photo-card__media');
      const fallback = () => { media.replaceChildren(el('span', 'sc-frame sc-frame--empty', 'photo unavailable')); };
      if (c.image) {
        const frame = el('div', 'sc-frame sc-frame--photo');
        const img = el('img', 'sc-frame__img'); img.src = c.image; img.alt = c.title; img.referrerPolicy = 'no-referrer';
        img.onload = () => img.classList.add('is-loaded'); img.onerror = fallback;
        frame.append(img); media.append(frame);
      } else fallback();
      card.append(media); return card;
    }
    function statusControl(c, after) {
      const select = el('select', 'studio-select'); select.setAttribute('aria-label', 'Status for ' + c.vin);
      for (const [value, text] of [['','Not saved'],['short','Saved'],['called','Contacted'],['out','Ruled out']]) { const o = el('option','',text); o.value = value; select.append(o); }
      select.value = c.status || ''; select.onchange = () => { api.status(c.vin, select.value); update(); if (after) after(select.value); if(mode==='garage'){ const y=content.scrollTop; draw(); content.scrollTop=y; const replacement=[...content.querySelectorAll('select')].find(n=>n.getAttribute('aria-label')==='Status for '+c.vin); (replacement||title).focus({preventScroll:true}); } };
      return select;
    }
    function notesField(c) {
      const input = el('textarea', 'studio-notes'); input.rows = 4; input.maxLength = 3000; input.value = notes[c.vin] || ''; input.placeholder = 'Questions, test-drive impressions, dealer quotes…';
      const state = el('p','studio-note-state',storageOkay ? 'Notes stay on this device. Copy a brief to keep a separate copy.' : 'Storage unavailable. Keep a copy of your notes before closing.'); state.setAttribute('role','status');
      input.oninput = () => { saveNotes(c.vin, input.value); state.textContent = storageOkay ? 'Saved on this device.' : 'Storage unavailable — copy your notes before closing.'; };
      const wrap = el('div'); wrap.append(label('Your notes',input),state); return wrap;
    }
    // basis: how the figure was arrived at, and nothing else. 'estimate' is a
    // number this page computed from the reader's own financing and fee
    // assumptions; 'unreported' is a fact the source never supplied. Recorded
    // is the default and carries no class, which is the design system's rule.
    // Nothing here decides which is which — the caller passes what the record
    // already said, and the label beside the value still says the word.
    function stat(name, value, sub, basis) {
      const n = el('div','studio-stat'); const v = el('strong', basis === 'estimate' ? 'sc-estimate' : basis === 'unreported' ? 'sc-unreported' : null, value);
      n.append(el('span','',name), v); if (sub) n.append(el('small','',sub)); return n;
    }
    // The words Tracking.py writes when a listing field is absent. A value is
    // "unreported" only when it IS one of them: a zero, a dash or an empty
    // string is not an absence and must not be dressed as one.
    const UNREPORTED = new Set(['Unreported','Mileage unreported','Dealer unreported','No condition details reported']);
    const basisOf = (value) => UNREPORTED.has(value) ? 'unreported' : undefined;
    // The same four words the status control offers, as a chip on the action
    // bar. One map, so the chip and the control cannot disagree.
    const STATUS_WORD = { '': 'not saved', short: 'saved', called: 'contacted', out: 'ruled out' };
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
      const hero=el('div','studio-showroom');hero.append(heroPhoto(c));
      const info=el('div','studio-showroom-info');info.append(el('p','studio-eyebrow',c.gone ? 'LEFT THE TRACKED LISTINGS' : 'THE CAR IN FOCUS'),el('p','studio-hero-price',money(c.price)),el('p','studio-muted',c.gone?'Last recorded asking price':'Asking price · '+c.location));
      // The asking price above is what the listing says. These two are not:
      // they are this page's arithmetic over the reader's own tax, fee and
      // financing settings, and they now say so in the figure as well as the
      // label. An unavailable payment is an absence, not a number.
      const stats=el('div','studio-cost-grid');stats.append(stat('Estimated all in',money(c.otd),'Includes configured tax, fees and shipping','estimate'),stat('Estimated monthly',c.payment==null?'Unavailable':money(c.payment)+'/mo',c.terms,c.payment==null?'unreported':'estimate'));info.append(stats);
      info.append(el('p','studio-muted',c.shipping));
      hero.append(info);content.append(hero);
      // One action bar, the design system's: the saved-state word, the one
      // sentence that used to sit alone at the foot of the dialog, and the one
      // action. The status control and the dealer's own listing follow it.
      const bar=el('div','sc-actionbar studio-bar');
      const chip=el('span','sc-chip','');
      const sayStatus=(s)=>{chip.textContent=STATUS_WORD[s||'']||'not saved';chip.className='sc-chip '+(['short','called'].includes(s)?'sc-chip--brand':'sc-chip--neutral');};
      sayStatus(c.status);
      bar.append(chip,
        el('p','','Recorded listing and estimates. Confirm availability, condition and the written out-the-door quote with the dealer.'),
        button('Copy dealer brief','sc-btn sc-btn--secondary',()=>showBrief([c])));
      const more=el('div','sc-actionbar__more');more.append(label('Status',statusControl(c,sayStatus)));if(c.url)more.append(link('Dealer listing ↗',c.url));
      bar.append(more);content.append(bar);
      const facts=el('div','studio-facts'); for(const [name,value] of [['Mileage',c.milesLabel],['Dealer',c.dealer],['VIN',c.vin],['Condition',c.flags || 'No condition details reported'],['First tracked',c.firstSeen || 'Unreported'],['Record through',c.through]]) facts.append(stat(name,value,null,basisOf(value))); content.append(facts);
      content.append(history(c),notesField(c));
    }
    // What the RECORD says has happened to a saved car since the reader last
    // saw new data — never what this visit did. api.change is the dashboard's
    // own detection over the same `spicycar.seen` day the front page reads, so
    // a reload changes nothing here and a rebuilt site is not a new observation.
    function changeOf(c) { return (!c.missing && api.change) ? api.change(c.vin) : null; }
    const CHANGE_WORD = { cut: 'asking price cut', up: 'asking price up', left: 'no longer being seen', held: 'still listed' };
    function drawGarage() {
      title.textContent='Your garage';back.hidden=true;
      const cars=records(),active=cars.filter(c=>['short','called'].includes(c.status));
      const tools=el('div','studio-garage-tools');tools.append(el('p','','Keep the contenders. Remember the conversations.'));
      const brief=button('Build dealer brief','studio-button studio-button-primary',()=>showBrief(records().filter(c=>['short','called'].includes(c.status))));brief.disabled=!active.length;tools.append(brief);content.append(tools);
      content.append(el('p','studio-muted','Statuses and notes are saved on this device. Your brief includes your notes; review it before sharing.'));
      if (!cars.length) {const empty=el('div','studio-empty');empty.append(el('h3','','Room for your next car.'),el('p','','Save a car from the results, or add a note in Quick look.'));content.append(empty);return;}
      // Needs another look: the cars the record moved under, named with the day
      // it was observed, and one press each to the car itself. A car whose
      // price has not moved is not in this band — it is a line on its own card.
      const moved = active.map((c) => ({ c, ch: changeOf(c) })).filter((o) => o.ch && o.ch.look);
      const since = api.lastSeenDay && api.lastSeenDay();
      if (moved.length) {
        const band = el('section', 'studio-look'); band.setAttribute('aria-labelledby', 'studio-look-title');
        const h = el('h3', '', moved.length === 1 ? 'One saved car needs another look' : moved.length + ' saved cars need another look'); h.id = 'studio-look-title';
        band.append(h, el('p', 'studio-muted', 'What the tracker recorded since you last saw new data' + (since ? ' (' + since + ')' : '') + '.'));
        const list = el('ul', 'studio-look-list');
        for (const { c, ch } of moved) {
          const li = el('li', 'studio-look-item'); li.dataset.lookVin = c.vin; li.dataset.lookKind = ch.kind;
          li.append(el('span', 'studio-look-tag studio-look-tag--' + ch.kind, CHANGE_WORD[ch.kind] || 'changed'));
          const words = el('div', 'studio-look-words');
          words.append(el('strong', '', c.title), el('p', '', ch.words));
          const go = button('Open showroom', 'studio-text-button', () => open(c.vin));
          go.setAttribute('aria-label', 'Open showroom for ' + c.title);
          li.append(words, go);
          list.append(li);
        }
        band.append(list); content.append(band);
      } else if (since) {
        content.append(el('p', 'studio-muted studio-look-none', 'Nothing the tracker recorded has moved on your saved cars since ' + since + '.'));
      }
      const grid=el('div','studio-garage-grid');
      for(const c of cars) {
        const card=el('article','studio-garage-card');card.dataset.garageVin=c.vin;
        if(!c.missing)card.append(photo(c,'studio-garage-photo'));
        const body=el('div','studio-garage-body');
        body.append(el('h3','',c.title),el('p','studio-garage-price',c.missing?c.vin:money(c.price)),
          el('p','studio-muted',c.missing?'Your status and notes are retained.':c.gone?'No longer in the tracked listings':c.location+' · '+c.milesLabel));
        // The band above already names every car the record MOVED under, in the
        // same words; repeating them on the card is the same sentence twice on
        // one screen. What the band does not carry is the quieter fact — the
        // car is still there at the same price — so that is what the card says.
        const ch = changeOf(c);
        if (ch && !ch.look) { const line = el('p', 'studio-garage-change studio-garage-change--' + ch.kind, ch.words); line.dataset.changeKind = ch.kind; body.append(line); }
        body.append(statusControl(c));
        // One primary way in, and the rest quiet: the showroom holds the photo,
        // the price journey, the note and the brief, so the card does not need
        // to carry four buttons of equal weight beside it.
        if(!c.missing){
          const acts=el('div','studio-garage-acts');
          const openBtn=button('Open showroom','studio-button studio-garage-open',()=>open(c.vin));
          openBtn.setAttribute('aria-label','Open showroom for '+c.title);
          acts.append(openBtn);
          if (api.compared && ['short','called'].includes(c.status)) {
            const inCmp = api.compared(c.vin);
            const t = button(inCmp ? 'In the comparison' : 'Add to comparison', 'studio-text-button studio-garage-compare', () => {
              /* membership only: the car stays saved and keeps its notes either way */
              api.compared(c.vin, !inCmp);
              const y = content.scrollTop; draw(); content.scrollTop = y;
              const again = [...content.querySelectorAll('.studio-garage-compare')].find((n) => n.closest('[data-garage-vin]')?.dataset.garageVin === c.vin);
              (again || title).focus({ preventScroll: true });
            });
            t.setAttribute('aria-pressed', String(inCmp));
            t.setAttribute('aria-label', (inCmp ? 'Take ' + c.title + ' out of the comparison' : 'Add ' + c.title + ' to the comparison') + ' — it stays saved either way');
            acts.append(t);
          }
          body.append(acts);
        }
        if(notes[c.vin])body.append(el('p','studio-note-preview',notes[c.vin]));
        if(c.missing)body.append(notesField(c));
        card.append(body);grid.append(card);
      }
      content.append(grid);
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
