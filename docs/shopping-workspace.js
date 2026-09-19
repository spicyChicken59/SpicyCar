/* Shopping controls are device preferences. The published tracking record stays intact. */
(function () {
  'use strict';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const button = (text, cls, fn) => { const n = el('button', cls, text); n.type = 'button'; n.onclick = fn; return n; };
  const sameSet = (a, b) => a.size === b.size && [...a].every((k) => b.has(k));
  function create(api) {
    const host = document.getElementById('shopping-workspace');
    const intro = el('div', 'shop-intro');
    const titleWrap = el('div');
    titleWrap.append(el('p', 'shop-kicker', 'SPICYCAR / YOUR NEXT MOVE'));
    const title = el('h1', 'shop-title', 'Find your next EV.');
    const subtitle = el('p', 'shop-subtitle', 'Your cars. Your budget. A clearer way to choose.');
    titleWrap.append(title, subtitle);
    const choose = button('Choose cars', 'shop-primary', openPicker);
    intro.append(titleWrap, choose);
    const scope = el('div', 'shop-scope');
    const all = button('All models', 'shop-scope-button', () => api.browse(null));
    const mine = button('My choices', 'shop-scope-button', () => api.browse(api.models().filter((m) => m.shopping).map((m) => m.key)));
    const summary = el('span', 'shop-selection-summary');
    // Brands the reader is keeping in consideration. A reading of the saved
    // interest and nothing else: it is not a scope button, because browsing a
    // brand's models would write S.models and turn curiosity into eligibility.
    const interest = el('span', 'shop-interest-summary'); interest.hidden = true;
    scope.append(all, mine, summary, interest);
    const nav = el('nav', 'shop-navigation'); nav.setAttribute('aria-label', 'Shopping workspace');
    const navButtons = new Map();
    for (const [key, label] of [['explore', 'Explore cars'], ['compare', 'Compare & save'], ['research', 'Market history'], ['report', 'Full report']]) {
      const b = button(label, 'shop-nav-button', () => api.view(key)); b.dataset.shopView = key; navButtons.set(key, b); nav.append(b);
    }
    host.append(intro, scope, nav);

    const dialog = el('dialog', 'shop-picker'); dialog.setAttribute('aria-labelledby', 'shop-picker-title'); dialog.setAttribute('aria-describedby', 'shop-picker-guide');
    const heading = el('div', 'shop-picker-head'); const words = el('div');
    const h = el('h2', '', 'Choose cars'); h.id = 'shop-picker-title';
    words.append(h);
    heading.append(words, button('Close', 'shop-quiet', () => dialog.close()));
    const guide = el('p', 'shop-picker-guide', 'Interested brands stay in view. Only selected models shape recommendations.'); guide.id = 'shop-picker-guide';
    const controls = el('div', 'shop-picker-controls');
    const search = el('input', 'shop-search'); search.type = 'search'; search.placeholder = 'Search make or model'; search.setAttribute('aria-label', 'Search available car models');
    // Narrows the list to the brands marked interesting — a view of the draft,
    // never a choice. Every other tracked brand is one press away again.
    const interestedOnly = button('Interested brands', 'shop-quiet shop-interest-filter', () => { onlyInterested = !onlyInterested; drawChoices(); });
    interestedOnly.setAttribute('aria-pressed', 'false');
    const selectAll = button('Select all', 'shop-quiet', () => { draft = new Set(api.models().map((m) => m.key)); drawChoices(); });
    const clear = button('Clear', 'shop-quiet', () => { draft.clear(); drawChoices(); });
    controls.append(search, interestedOnly, selectAll, clear);
    const choices = el('div', 'shop-model-groups');
    const empty = el('p', 'shop-no-models', 'No models match that search.'); empty.hidden = true;
    const bottom = el('div', 'shop-picker-bottom');
    const selected = el('p'); selected.setAttribute('role', 'status');
    const apply = button('Shop these models', 'shop-primary', () => {
      // Two drafts, two doors. Interest is saved on its own path and never
      // through shop(): marking a brand must not choose its models, move the
      // browse scope, or turn an inherited default into an explicit list. So
      // a press that changed the interest and not the models saves the
      // interest alone; a changed model draft is shopped as drawn — a cleared
      // set as a cleared set, never refilled from the record's defaults; and
      // a press that changed nothing keeps the promise on the button and
      // shops the models it names, exactly as it always has.
      const interestChanged = !sameSet(draftInterest, initialInterest), modelsChanged = !sameSet(draft, initialModels);
      if (interestChanged) api.interest([...draftInterest]);
      if (modelsChanged || (!interestChanged && draft.size)) api.shop([...draft]);
      dialog.close();
    });
    bottom.append(selected, apply); dialog.append(heading, guide, controls, choices, bottom); document.body.append(dialog);
    let draft = new Set(), initialModels = new Set(), draftInterest = new Set(), initialInterest = new Set(), order = [], onlyInterested = false;
    search.oninput = drawChoices;
    dialog.addEventListener('click', (e) => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
    function openPicker() {
      const brands = api.brands();
      draft = new Set(api.models().filter((m) => m.shopping).map((m) => m.key)); initialModels = new Set(draft);
      draftInterest = new Set(brands.filter((b) => b.interested).map((b) => b.key)); initialInterest = new Set(draftInterest);
      // The brands already marked lead, then every other tracked brand, each
      // run in the record's own order. Fixed when the dialog opens, from what
      // was saved, so a group never moves under the pointer that just marked it.
      order = [...brands.filter((b) => b.interested), ...brands.filter((b) => !b.interested)].map((b) => b.key);
      onlyInterested = false; search.value = ''; drawChoices(); dialog.showModal(); search.focus();
    }
    function paintInterest(mark, key) {
      const on = draftInterest.has(key);
      mark.setAttribute('aria-pressed', String(on)); mark.textContent = on ? '✓ Interested' : 'Interested';
    }
    // The filter reads the draft, so every mark repaints it: a brand marked
    // moments ago is a brand the filter can already narrow to.
    function paintFilter() {
      if (onlyInterested && !draftInterest.size) onlyInterested = false;
      interestedOnly.setAttribute('aria-pressed', String(onlyInterested)); interestedOnly.disabled = !draftInterest.size;
      interestedOnly.textContent = 'Interested brands' + (draftInterest.size ? ' · ' + draftInterest.size : '');
    }
    function modelChoice(m) {
      const label = el('label', 'shop-model-choice');
      const check = el('input'); check.type = 'checkbox'; check.value = m.key; check.checked = draft.has(m.key); check.setAttribute('aria-label', m.label);
      check.onchange = () => { if (check.checked) draft.add(m.key); else draft.delete(m.key); label.classList.toggle('is-chosen', check.checked); countSelection(); };
      label.classList.toggle('is-chosen', check.checked);
      const photo = el('div', 'shop-model-photo');
      if (m.image) { const img = el('img'); img.src = m.image; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.onerror = () => img.remove(); photo.append(img); }
      const info = el('span', 'shop-model-info'); info.append(el('strong', '', m.label), el('span', '', m.cars + ' cars' + (m.price ? ' · from ' + m.price : ' · awaiting listings')));
      label.append(check, photo, info);
      return label;
    }
    function drawChoices() {
      choices.replaceChildren(); const term = search.value.trim().toLocaleLowerCase();
      const models = api.models(), brands = new Map(api.brands().map((b) => [b.key, b]));
      paintFilter();
      let shown = 0;
      for (const key of order) {
        const brand = brands.get(key); if (!brand || (onlyInterested && !draftInterest.has(key))) continue;
        // Grouped by the record's own brand key, carried on every model: the
        // label is display text and is never split to find the brand.
        const tracked = models.filter((m) => m.brand === key);
        const list = brand.label.toLocaleLowerCase().includes(term) ? tracked : tracked.filter((m) => m.label.toLocaleLowerCase().includes(term));
        if (!list.length) continue;
        shown += list.length;
        const group = el('section', 'shop-brand-group'); group.dataset.brand = key; group.setAttribute('aria-label', brand.label);
        const head = el('div', 'shop-brand-head');
        // "1 model tracked" is a fact about the watchlist, not a verdict that
        // only one of that brand's cars fits.
        const count = el('span', 'shop-brand-count', tracked.length + (tracked.length === 1 ? ' model tracked' : ' models tracked'));
        const mark = button('Interested', 'shop-brand-interest', () => {
          if (draftInterest.has(key)) draftInterest.delete(key); else draftInterest.add(key);
          // Under the interested-only filter an unmarked brand leaves the list,
          // so the list is redrawn and the focus is handed to the same brand's
          // control if it is still there, else to the filter that hid it.
          if (onlyInterested) { drawChoices(); (choices.querySelector('.shop-brand-group[data-brand="' + CSS.escape(key) + '"] .shop-brand-interest') || interestedOnly).focus(); return; }
          paintInterest(mark, key); group.classList.toggle('is-interested', draftInterest.has(key)); paintFilter(); countSelection();
        });
        mark.setAttribute('aria-label', 'Interested in ' + brand.label); paintInterest(mark, key);
        head.append(el('h3', 'shop-brand-name', brand.label), count, mark);
        const grid = el('div', 'shop-model-grid');
        for (const m of list) grid.append(modelChoice(m));
        group.classList.toggle('is-interested', draftInterest.has(key));
        group.append(head, grid); choices.append(group);
      }
      empty.hidden = shown > 0; choices.append(empty); selectAll.textContent = 'Select all ' + models.length; countSelection();
    }
    function countSelection() {
      const n = draft.size, k = draftInterest.size;
      selected.textContent = n + ' model' + (n === 1 ? '' : 's') + ' selected · ' + (k ? 'interested in ' + k + ' brand' + (k === 1 ? '' : 's') : 'no brands marked');
      // Zero models is a choice the reader can make and keep: the brands stay
      // marked and no model is chosen for them.
      const interestOnly = !sameSet(draftInterest, initialInterest) && sameSet(draft, initialModels);
      apply.textContent = interestOnly ? 'Save brand interest' : n ? 'Shop these models' : 'Save with no models';
    }
    function update() {
      const models = api.models(), current = api.scope(), saved = models.filter((m) => m.shopping), view = api.currentView();
      title.textContent = current.length === 1 ? models.find((m) => m.key === current[0])?.label || 'Find your next EV.' : 'Find your next EV.';
      // The masthead carries "data through <day>" at every width; saying it
      // again here cost a whole line above the first car on a phone.
      subtitle.textContent = api.count() + ' matching cars · ' + current.length + ' model' + (current.length === 1 ? '' : 's')
        + (window.matchMedia('(max-width: 720px)').matches ? '' : ' · data through ' + api.through());
      choose.textContent = 'Choose cars'; all.textContent = 'All ' + models.length + ' models'; mine.textContent = 'My choices' + (saved.length ? ' · ' + saved.length : ''); mine.disabled = !saved.length;
      all.setAttribute('aria-pressed', String(current.length === models.length));
      mine.setAttribute('aria-pressed', String(saved.length > 0 && current.length === saved.length && saved.every((m) => current.includes(m.key))));
      const names = current.length === models.length ? [] : models.filter((m) => current.includes(m.key)).map((m) => m.label);
      summary.textContent = names.slice(0, 3).join(' · ') + (names.length > 3 ? ' + ' + (names.length - 3) + ' more' : '');
      const interested = api.brands().filter((b) => b.interested).map((b) => b.label);
      interest.hidden = !interested.length;
      interest.textContent = interested.length ? 'Interested in ' + interested.join(' · ') + (saved.length ? '' : ' · no models chosen yet') : '';
      for (const [key, b] of navButtons) { b.setAttribute('aria-pressed', String(key === view)); b.hidden = key === 'report' && !['research', 'report'].includes(view); }
    }
    return { update, openPicker };
  }
  window.ShoppingWorkspace = { create };
})();
