/* SpicyChicken — optional criterion navigation for native signal matrices.
 * Load with defer, then add data-sc-matrix-nav to a .sc-table-scroll region.
 * Or call SCMatrixNav.attach(scroller); refresh() after replacing its headers.
 * No data, ranking, selected state, or table semantics are changed.
 */
(function (w, d) {
  'use strict';
  if (!w || !d || typeof d.querySelectorAll !== 'function' || w.SCMatrixNav) return;
  const instances = new WeakMap();
  let serial = 0;

  function ensureStyle() {
    if (d.querySelector('style[data-sc-matrix-nav-style]')) return;
    const style = d.createElement('style');
    style.setAttribute('data-sc-matrix-nav-style', '');
    style.textContent = `
[data-sc-matrix-controls] { display:flex; flex-wrap:wrap; gap:var(--sc-s1); align-items:center; min-width:0; }
[data-sc-matrix-controls][hidden] { display:none; }
[data-sc-matrix-controls] > button { min-height:44px; min-width:44px; max-width:100%; padding:var(--sc-s1); font-size:var(--sc-text-sm); line-height:1.25; text-align:start; overflow-wrap:anywhere; }
[data-sc-matrix-controls] > button:focus-visible { outline:2px solid var(--sc-focus); outline-offset:2px; }
@media (max-width:600px) {
  [data-sc-matrix-controls] { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
  [data-sc-matrix-controls] > button { justify-content:center; text-align:center; }
  [data-sc-matrix-controls] > button:last-child:nth-child(odd) { grid-column:1 / -1; }
}
@media (prefers-reduced-motion:reduce) { [data-sc-matrix-controls] > button { transition:none; } }
@media (forced-colors:active) { [data-sc-matrix-controls] > button { border-color:ButtonText; } }
@media print { [data-sc-matrix-controls] { display:none; } }
`;
    d.head.append(style);
  }

  function attach(scroller) {
    if (!scroller || scroller.nodeType !== 1 || !scroller.querySelector('table')) return null;
    if (scroller.getAttribute('data-sc-matrix-nav') === 'off' || scroller.querySelector('table').getAttribute('data-sc-matrix-nav') === 'off') return null;
    if (instances.has(scroller)) return instances.get(scroller);
    ensureStyle();
    const group = d.createElement('div');
    group.setAttribute('data-sc-matrix-controls', '');
    group.setAttribute('role', 'group');
    group.hidden = true;
    let generatedId = null;
    if (!scroller.id) {
      do { generatedId = `sc-matrix-region-${++serial}`; } while (d.getElementById(generatedId));
      scroller.id = generatedId;
    }
    scroller.before(group);
    let table = null;
    let headers = [];
    let destroyed = false;
    let observer = null;
    let disclosures = [];

    function measure() {
      if (destroyed) return;
      const hide = headers.length < 2 || !!scroller.closest('details:not([open])') || scroller.clientWidth === 0 || scroller.scrollWidth <= scroller.clientWidth + 1;
      if (hide && group.contains(d.activeElement) && scroller.clientWidth > 0) scroller.focus({ preventScroll: true });
      group.hidden = hide;
    }

    function jump(event) {
      const button = event.target.closest('button[data-sc-matrix-column]');
      if (!button || !group.contains(button)) return;
      const column = headers[Number(button.dataset.scMatrixColumn)];
      const identity = headers[0];
      if (!column || !identity || !scroller.contains(column)) return;
      // Sticky cells use the scrollport's padding edge. Include both borders
      // and padding so the requested criterion never slides under identity.
      const port = scroller.getBoundingClientRect();
      const first = identity.getBoundingClientRect();
      const inset = parseFloat(w.getComputedStyle(identity).left) || 0;
      const padding = parseFloat(w.getComputedStyle(scroller).paddingLeft) || 0;
      const edge = port.left + scroller.clientLeft + padding + inset + first.width;
      const target = scroller.scrollLeft + column.getBoundingClientRect().left - edge;
      const left = Math.max(0, Math.min(target, scroller.scrollWidth - scroller.clientWidth));
      const reduced = w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)').matches;
      scroller.scrollTo({ left, behavior: reduced ? 'instant' : 'smooth' });
    }

    function refresh() {
      if (destroyed) return api;
      disclosures.forEach(details => details.removeEventListener('toggle', measure));
      disclosures = [];
      for (let parent = scroller.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') {
          disclosures.push(parent);
          parent.addEventListener('toggle', measure);
        }
      }
      const nextTable = scroller.querySelector('table');
      if (observer && table !== nextTable) {
        if (table) observer.unobserve(table);
        if (nextTable) observer.observe(nextTable);
      }
      table = nextTable;
      // Grouped/multirow headers need an authored navigation model; leave
      // their native table untouched instead of guessing its columns.
      headers = table && table.tHead && table.tHead.rows.length === 1
        ? Array.from(table.tHead.rows[0].cells) : [];
      if (headers.some(cell => cell.tagName !== 'TH' || cell.colSpan !== 1 || cell.rowSpan !== 1)) headers = [];
      const focused = group.contains(d.activeElement) ? d.activeElement.dataset.scMatrixColumn : null;
      group.replaceChildren();
      group.setAttribute('aria-label', scroller.getAttribute('data-sc-matrix-nav-label') || `Jump to criterion in ${scroller.getAttribute('aria-label') || 'signal matrix'}`);
      headers.slice(1).forEach((header, index) => {
        const button = d.createElement('button');
        button.type = 'button';
        button.className = 'sc-btn sc-btn--secondary';
        button.dataset.scMatrixColumn = String(index + 1);
        button.textContent = (header.getAttribute('data-sc-label') || header.textContent).trim().replace(/\s+/g, ' ');
        button.title = header.textContent.trim().replace(/\s+/g, ' ');
        button.setAttribute('aria-controls', scroller.id);
        group.append(button);
      });
      measure();
      if (focused !== null && !group.hidden) {
        const replacement = Array.from(group.querySelectorAll('button')).find(button => button.dataset.scMatrixColumn === focused);
        if (replacement) replacement.focus({ preventScroll: true });
        else scroller.focus({ preventScroll: true });
      } else if (focused !== null && scroller.clientWidth > 0) scroller.focus({ preventScroll: true });
      return api;
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (observer) observer.disconnect();
      else w.removeEventListener('resize', measure);
      disclosures.forEach(details => details.removeEventListener('toggle', measure));
      group.removeEventListener('click', jump);
      group.remove();
      if (generatedId && scroller.id === generatedId) scroller.removeAttribute('id');
      instances.delete(scroller);
    }

    const api = { refresh, destroy };
    instances.set(scroller, api);
    group.addEventListener('click', jump);
    if (w.ResizeObserver) {
      observer = new w.ResizeObserver(measure);
      observer.observe(scroller);
    } else w.addEventListener('resize', measure);
    return refresh();
  }

  w.SCMatrixNav = { attach };
  const init = () => d.querySelectorAll('[data-sc-matrix-nav]').forEach(attach);
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})(typeof window !== 'undefined' ? window : null, typeof document !== 'undefined' ? document : null);
