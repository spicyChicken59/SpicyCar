/* Collection transparency; renders only dated, recorded measurements. */
(function () {
  'use strict';
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  Promise.all([window.__data, new Promise(resolve => {
    if (document.readyState !== 'loading') resolve();
    else document.addEventListener('DOMContentLoaded', resolve, { once: true });
  })]).then(([data]) => {
    const c = data.collection;
    if (!c || !Array.isArray(c.models)) return;
    const panel = node('details', null, 'collection-health');
    const summary = node('summary', 'How fresh is the hunt?');
    panel.append(summary);
    panel.append(node('p', `${c.models.length} models share collection capacity. Each gets a turn every ${c.models[0]?.model_cadence || 2} days, with trims rotating between turns. Extra searches follow market size and useful results.`));
    panel.append(node('p', `${c.month_spent.toLocaleString()} / ${c.monthly_cap.toLocaleString()} recorded calls this month · ${c.reserve} calls reserved for recovery. Shopping selections do not change anyone’s collection share.`));
    const wrap = node('div', null, 'collection-table');
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', 'Collection measurements, scroll to see all columns');
    const table = node('table');
    const caption = node('caption', 'Collection measurements · refreshed with each tracker run');
    table.append(caption);
    const head = node('thead'), tr = node('tr');
    ['Model', 'Each trim', 'Market count', 'Calls · 7d', 'New / changed · 7d', 'Useful / call'].forEach(s => {
      const th = node('th', s); th.scope = 'col'; tr.append(th);
    });
    head.append(tr); table.append(head);
    const body = node('tbody');
    [...c.models].sort((a,b) => (b.market_total ?? -1) - (a.market_total ?? -1) || a.label.localeCompare(b.label)).forEach(m => {
      const row = node('tr');
      const name = node('th', m.label); name.scope = 'row'; row.append(name);
      const market = m.market_total == null ? 'Learning' : `${m.market_total.toLocaleString()} · ${m.market_as_of}`;
      [`${m.trim_cadence} days`, market, m.calls_7d || '—', m.calls_7d ? m.useful_7d : '—', m.calls_7d ? (m.useful_7d / m.calls_7d).toFixed(1) : '—'].forEach(v => row.append(node('td', v)));
      body.append(row);
    });
    table.append(body); wrap.append(table); panel.append(wrap);
    panel.append(node('p', 'Market counts cover the provider’s national make, model and year query before our listing filters. Learning means a fresh count has not arrived. New / changed counts unique VINs first seen or whose price or mileage changed; seven-day metrics begin when this collector starts.'));
    document.querySelector('main')?.append(panel);
  }).catch(() => { /* Existing dashboard handles a data-load failure. */ });
})();
