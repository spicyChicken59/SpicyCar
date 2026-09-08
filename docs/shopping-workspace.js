/* Shopping controls are device preferences. The published tracking record stays intact. */
(function () {
  'use strict';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const button = (text, cls, fn) => { const n = el('button', cls, text); n.type = 'button'; n.onclick = fn; return n; };
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
    scope.append(all, mine, summary);
    const nav = el('nav', 'shop-navigation'); nav.setAttribute('aria-label', 'Shopping workspace');
    const navButtons = new Map();
    for (const [key, label] of [['explore', 'Explore cars'], ['compare', 'Compare & save'], ['research', 'Market history'], ['report', 'Full report']]) {
      const b = button(label, 'shop-nav-button', () => api.view(key)); b.dataset.shopView = key; navButtons.set(key, b); nav.append(b);
    }
    host.append(intro, scope, nav);

    const dialog = el('dialog', 'shop-picker'); dialog.setAttribute('aria-labelledby', 'shop-picker-title');
    const heading = el('div', 'shop-picker-head'); const words = el('div');
    const h = el('h2', '', 'What are you shopping for?'); h.id = 'shop-picker-title';
    words.append(h, el('p', '', 'Choose any mix of models. Your choices shape the cars, recommendations and comparisons.'));
    heading.append(words, button('Close', 'shop-quiet', () => dialog.close()));
    const controls = el('div', 'shop-picker-controls');
    const search = el('input', 'shop-search'); search.type = 'search'; search.placeholder = 'Search make or model'; search.setAttribute('aria-label', 'Search available car models');
    const selectAll = button('Select all', 'shop-quiet', () => { draft = new Set(api.models().map((m) => m.key)); drawChoices(); });
    const clear = button('Clear', 'shop-quiet', () => { draft.clear(); drawChoices(); });
    controls.append(search, selectAll, clear);
    const choices = el('div', 'shop-model-grid');
    const empty = el('p', 'shop-no-models', 'No models match that search.'); empty.hidden = true;
    const bottom = el('div', 'shop-picker-bottom');
    const selected = el('p'); selected.setAttribute('role', 'status');
    const apply = button('Shop these models', 'shop-primary', () => { api.shop([...draft]); dialog.close(); });
    bottom.append(selected, apply); dialog.append(heading, controls, choices, empty, bottom); document.body.append(dialog);
    let draft = new Set();
    search.oninput = drawChoices;
    dialog.addEventListener('click', (e) => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
    function openPicker() {
      draft = new Set(api.models().filter((m) => m.shopping).map((m) => m.key));
      search.value = ''; drawChoices(); dialog.showModal(); search.focus();
    }
    function drawChoices() {
      choices.replaceChildren(); const term = search.value.trim().toLocaleLowerCase();
      const models = api.models(); const shown = models.filter((m) => m.label.toLocaleLowerCase().includes(term));
      for (const m of shown) {
        const label = el('label', 'shop-model-choice');
        const check = el('input'); check.type = 'checkbox'; check.value = m.key; check.checked = draft.has(m.key); check.setAttribute('aria-label', m.label);
        check.onchange = () => { if (check.checked) draft.add(m.key); else draft.delete(m.key); label.classList.toggle('is-chosen', check.checked); countSelection(); };
        label.classList.toggle('is-chosen', check.checked);
        const photo = el('div', 'shop-model-photo');
        if (m.image) { const img = el('img'); img.src = m.image; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.onerror = () => img.remove(); photo.append(img); }
        const info = el('span', 'shop-model-info'); info.append(el('strong', '', m.label), el('span', '', m.cars + ' cars' + (m.price ? ' · from ' + m.price : ' · awaiting listings')));
        label.append(check, photo, info); choices.append(label);
      }
      empty.hidden = shown.length > 0; selectAll.textContent = 'Select all ' + models.length; countSelection();
    }
    function countSelection() { selected.textContent = draft.size + ' model' + (draft.size === 1 ? '' : 's') + ' selected'; apply.disabled = draft.size === 0; }
    function update() {
      const models = api.models(), current = api.scope(), saved = models.filter((m) => m.shopping), view = api.currentView();
      title.textContent = current.length === 1 ? models.find((m) => m.key === current[0])?.label || 'Find your next EV.' : 'Find your next EV.';
      subtitle.textContent = api.count() + ' matching cars · ' + current.length + ' model' + (current.length === 1 ? '' : 's') + ' · data through ' + api.through();
      choose.textContent = 'Choose cars'; all.textContent = 'All ' + models.length + ' models'; mine.textContent = 'My choices' + (saved.length ? ' · ' + saved.length : ''); mine.disabled = !saved.length;
      all.setAttribute('aria-pressed', String(current.length === models.length));
      mine.setAttribute('aria-pressed', String(saved.length > 0 && current.length === saved.length && saved.every((m) => current.includes(m.key))));
      const names = current.length === models.length ? [] : models.filter((m) => current.includes(m.key)).map((m) => m.label);
      summary.textContent = names.slice(0, 3).join(' · ') + (names.length > 3 ? ' + ' + (names.length - 3) + ' more' : '');
      for (const [key, b] of navButtons) { b.setAttribute('aria-pressed', String(key === view)); b.hidden = key === 'report' && !['research', 'report'].includes(view); }
    }
    return { update, openPicker };
  }
  window.ShoppingWorkspace = { create };
})();
