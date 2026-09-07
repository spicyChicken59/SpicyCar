/* SpicyChicken Design System — sc-charts.js v2.10.0 · chart and native-table presentation bundle · sources: build/charts.js, sc-matrix-nav.js, build/matrix-auto.js · needs sc.css */
(function (w) {
  'use strict';
  var SC = w.SC = (w.SC || {});
  var NS = 'http://www.w3.org/2000/svg';

  /* ---------- element helpers -------------------------------------------
     Exported because a hand-built chart needs the same two functions, and
     three files had written them out separately before this one existed. */
  function el(tag, attrs, kids) {
    var n = document.createElement(tag), k;
    for (k in attrs || {}) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      var v = attrs[k];
      if (v === null || v === undefined) continue;
      if (k.indexOf('on') === 0 && typeof v === 'function') n.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'text') n.textContent = v;
      else n.setAttribute(k, v);
    }
    append(n, kids);
    return n;
  }
  function svg(tag, attrs, kids) {
    var n = document.createElementNS(NS, tag), k;
    for (k in attrs || {}) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    }
    append(n, kids);
    return n;
  }
  function append(n, kids) {
    if (kids === null || kids === undefined) return;
    var list = Object.prototype.toString.call(kids) === '[object Array]' ? kids : [kids];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c === null || c === undefined || c === false) continue;
      n.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    }
  }

  /* ---------- tokens -----------------------------------------------------
     Resolve a token to its computed value. Use this only where a value must
     be READ (a measurement, a canvas, a legend <i> the sheet leaves
     uncoloured). To PAINT a mark, pass the slot through the --sc-tone
     channel instead — see the note above .sc-chart__series in sc.css. A
     resolved value is frozen at the moment it was read; a channel is not. */
  function tone(slot, contextEl) {
    var host = contextEl || document.documentElement;
    var v = getComputedStyle(host).getPropertyValue('--sc-' + slot);
    v = v ? v.replace(/^\s+|\s+$/g, '') : '';
    return v || 'currentColor';
  }
  /* The value to put in the channel: a live var() reference, not a colour. */
  function toneRef(slot) { return 'var(--sc-' + slot + ')'; }

  /* ---------- nice axis ticks -------------------------------------------
     The hi === lo guard is load-bearing, not cosmetic: without it a single
     tracked day gives span 0, so raw is 0, Math.log10(0) is -Infinity,
     step is 0, and the tick loop below never terminates. */
  function ticks(lo, hi, count) {
    if (hi === lo) {
      var pad = Math.max(1, Math.abs(lo) * 0.02);
      hi = lo + pad; lo = lo - pad;
    }
    var span = hi - lo, raw = span / (count || 4), p = Math.pow(10, Math.floor(Math.log10(raw)));
    var steps = [1, 2, 2.5, 5, 10], step = p * 10;
    for (var i = 0; i < steps.length; i++) { if (raw <= steps[i] * p) { step = steps[i] * p; break; } }
    var start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step, out = [];
    for (var t = start; t <= end + step / 2; t += step) out.push(Math.round(t * 1000) / 1000);
    return { ticks: out, lo: start, hi: end };
  }

  /* ---------- end-label collision solver ---------------------------------
     Push labels apart so they never overlap, then repair against BOTH edges.
     The copies this replaces clamped only the bottom, so a chart with more
     labels than room pushed them off the top and then piled them at the
     floor again. Mutates and returns the array, sorted by y. */
  function spreadLabels(items, opts) {
    opts = opts || {};
    var gap = opts.gap === undefined ? 13 : opts.gap;
    var max = opts.max === undefined ? Infinity : opts.max;
    var min = opts.min === undefined ? -Infinity : opts.min;
    items.sort(function (a, b) { return a.y - b.y; });
    var i;
    for (i = 1; i < items.length; i++) if (items[i].y - items[i - 1].y < gap) items[i].y = items[i - 1].y + gap;
    for (i = items.length - 1; i >= 0; i--) {
      var ceiling = max - (items.length - 1 - i) * gap;
      if (items[i].y > ceiling) items[i].y = ceiling;
    }
    for (i = 0; i < items.length; i++) {
      var floor = min + i * gap;
      if (items[i].y < floor) items[i].y = floor;
    }
    return items;
  }

  /* ---------- sparkline --------------------------------------------------
     The geometry is shared with the React twin through SC.sparkPoints, so the
     two cannot drift: pad 4 / r 3 is the only self-consistent pair against
     the sheet's 2px surface ring (pad must be r + stroke/2). */
  var SPARK = { PAD: 4, R: 3, W: 80, H: 26 };
  function sparkPoints(values, opts) {
    opts = opts || {};
    var w = opts.width || SPARK.W, h = opts.height || SPARK.H, pad = SPARK.PAD;
    var v = [], i;
    values = values || [];
    /* Number.isFinite, not != null: one NaN or one stringified number makes
       Math.min return NaN and silently empties the whole path. */
    for (i = 0; i < values.length; i++) if (typeof values[i] === 'number' && isFinite(values[i])) v.push(values[i]);
    if (!v.length) return [];
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), pts = [];
    for (i = 0; i < v.length; i++) {
      var x = v.length === 1 ? w / 2 : pad + i / (v.length - 1) * (w - pad * 2);
      var y = hi === lo ? h / 2 : pad + (hi - v[i]) / (hi - lo) * (h - pad * 2);
      /* rounded once, here, so the path end and the dot centre are the same
         numbers — the copies that rounded the path but not the circle put the
         dot up to 0.05px off the line */
      pts.push([Math.round(x * 10) / 10, Math.round(y * 10) / 10]);
    }
    return pts;
  }
  function sparkPath(pts) {
    if (pts.length < 2) return '';
    var d = '', i;
    for (i = 0; i < pts.length; i++) d += (i ? 'L' : 'M') + pts[i][0] + ',' + pts[i][1];
    return d;
  }
  function spark(values, opts) {
    opts = opts || {};
    var w = opts.width || SPARK.W, h = opts.height || SPARK.H;
    var cls = 'sc-spark' + (opts.emphasis ? ' sc-spark--emphasis' : '') + (opts.className ? ' ' + opts.className : '');
    var style = '';
    if (opts.tone) style += '--sc-tone:' + toneRef(opts.tone) + ';';
    if (opts.weight) style += '--sc-weight:' + opts.weight + ';';
    var node = svg('svg', {
      'class': cls, width: w, height: h, viewBox: '0 0 ' + w + ' ' + h,
      'aria-hidden': 'true', focusable: 'false', style: style || null
    });
    var pts = sparkPoints(values, opts);
    if (!pts.length) return node;
    if (pts.length > 1) node.appendChild(svg('path', { d: sparkPath(pts), 'stroke-dasharray': opts.dash || null }));
    var last = pts[pts.length - 1];
    node.appendChild(svg('circle', { cx: last[0], cy: last[1], r: opts.r || SPARK.R }));
    return node;
  }

  /* ---------- tooltip ----------------------------------------------------
     One controller replacing four hand-inlined copies of the same five lines,
     which had drifted to three different flip thresholds (0.62 / 0.60 / 0.55)
     and only one of which clamped the top edge. */
  function tooltip(host, opts) {
    opts = opts || {};
    var flipAt = opts.flip === undefined ? 0.6 : opts.flip;
    var dx = opts.offsetX === undefined ? 12 : opts.offsetX;
    /* the anchor is the datum; the tooltip sits above it so the cursor does
       not cover what it is describing */
    var dy = opts.offsetY === undefined ? -40 : opts.offsetY;
    /* a chart with a crosshair parks its tooltip at a fixed height instead of
       tracking the pointer vertically — the values move, the box should not */
    var fixedTop = opts.top;
    var pad = opts.pad === undefined ? 4 : opts.pad;
    var attrs = { 'class': 'sc-tooltip' };
    /* aria-live only where a keyboard cursor moves the tooltip. On a
       hover-only chart it announces on every pointer pixel. */
    if (opts.live) { attrs.role = 'status'; attrs['aria-live'] = 'polite'; }
    var node = el('div', attrs);
    host.appendChild(node);

    function clamp() {
      var rect = host.getBoundingClientRect();
      var top = parseFloat(node.style.top) || pad;
      var maxTop = rect.height - node.offsetHeight - pad;
      node.style.top = Math.max(pad, maxTop < pad ? pad : Math.min(top, maxTop)) + 'px';
    }
    /* `over` carries per-call offsets for the rare anchor that sits differently
       from the rest — a map's home marker beside its car dots, say. */
    function place(anchor, over) {
      over = over || {};
      var ox = over.offsetX === undefined ? dx : over.offsetX;
      var oy = over.offsetY === undefined ? dy : over.offsetY;
      var oTop = over.top === undefined ? fixedTop : over.top;
      var rect = host.getBoundingClientRect(), px, py;
      if (anchor && anchor.nodeType === 1) {
        var r = anchor.getBoundingClientRect();
        px = r.left + r.width / 2 - rect.left; py = r.top - rect.top;
      } else if (anchor && anchor.px !== undefined) { px = anchor.px; py = anchor.py; }
      else { px = anchor.clientX - rect.left; py = anchor.clientY - rect.top; }
      var flip = px > rect.width * flipAt;
      node.style.left = flip ? 'auto' : (px + ox) + 'px';
      node.style.right = flip ? (rect.width - px + ox) + 'px' : 'auto';
      node.style.top = Math.max(pad, oTop === undefined ? py + oy : oTop) + 'px';
      clamp();
    }
    function show(spec, anchor) {
      node.textContent = '';
      spec = spec || {};
      if (spec.image) {
        var img = el('img', { 'class': 'sc-tooltip__img', src: spec.image, alt: spec.imageAlt || '',
                              loading: 'lazy', referrerpolicy: 'no-referrer' });
        /* the photo changes the height after placement, so re-clamp when it
           settles either way */
        img.addEventListener('load', clamp);
        img.addEventListener('error', function () { if (img.parentNode) img.parentNode.removeChild(img); clamp(); });
        node.appendChild(img);
      }
      if (spec.title) node.appendChild(el('div', { 'class': 'sc-tooltip__date', text: spec.title }));
      var rows = spec.rows || [], i;
      for (i = 0; i < rows.length; i++) {
        var r = rows[i], key;
        if (r.dash) {
          key = svg('svg', { 'class': 'sc-tooltip__dash', width: 14, height: 6, viewBox: '0 0 14 6', 'aria-hidden': 'true' },
            svg('line', { x1: 0, y1: 3, x2: 14, y2: 3, 'stroke-width': 2, 'stroke-dasharray': r.dash,
                          style: 'stroke:' + (r.tone ? toneRef(r.tone) : r.color) }));
        } else {
          /* the one place an inline colour is right: sc.css gives these <i>
             a size and a radius and deliberately no colour, so nothing is
             being outranked */
          key = el('i', { style: 'background:' + (r.tone ? toneRef(r.tone) : r.color) });
        }
        node.appendChild(el('div', { 'class': 'sc-tooltip__row' }, [key, el('b', { text: r.value }), el('span', { text: r.label })]));
      }
      if (spec.meta) node.appendChild(el('div', { 'class': 'sc-tooltip__meta', text: spec.meta }));
      if (spec.link) node.appendChild(el('a', { 'class': 'sc-tooltip__link', href: spec.link.href, text: spec.link.text,
                                                target: spec.link.target || null, rel: spec.link.target ? 'noopener' : null }));
      node.classList.add('is-on');
      if (anchor) place(anchor);
    }
    function tap(on) { node.classList[on ? 'add' : 'remove']('sc-tooltip--tap'); }
    function hide() { node.classList.remove('is-on', 'sc-tooltip--tap'); }
    return { node: node, show: show, hide: hide, place: place, clamp: clamp, tap: tap,
             destroy: function () { if (node.parentNode) node.parentNode.removeChild(node); } };
  }

  /* ---------- accessible table twin --------------------------------------
     Every chart owes one. The copy this comes from is the only one that gave
     it an .sc-sr-only caption. */
  function tableTwin(mount, spec) {
    var numeric = spec.numeric || function (i) { return i > 0; };
    var head = [], i, j;
    for (i = 0; i < spec.head.length; i++) head.push(el('th', { 'class': numeric(i) ? 'sc-num' : null, text: spec.head[i] }));
    var body = [];
    for (i = 0; i < spec.rows.length; i++) {
      var cells = [];
      for (j = 0; j < spec.rows[i].length; j++) cells.push(el('td', { 'class': numeric(j) ? 'sc-num' : null, text: spec.rows[i][j] }));
      body.push(el('tr', null, cells));
    }
    var table = el('table', { 'class': 'sc-table sc-table--compact' }, [
      el('caption', { 'class': 'sc-sr-only', text: spec.caption }),
      el('thead', null, el('tr', null, head)),
      el('tbody', null, body)
    ]);
    if (spec.details === false) { mount.appendChild(table); return table; }
    mount.appendChild(el('details', { 'class': 'sc-details' }, [
      el('summary', { text: spec.summary || 'Table view' }),
      el('div', { 'class': 'sc-table-scroll' }, table)
    ]));
    return table;
  }

  SC.el = el; SC.svg = svg;
  SC.tone = tone; SC.toneRef = toneRef;
  SC.ticks = ticks; SC.spreadLabels = spreadLabels;
  SC.SPARK = SPARK; SC.sparkPoints = sparkPoints; SC.sparkPath = sparkPath; SC.spark = spark;
  SC.tooltip = tooltip; SC.tableTwin = tableTwin;
})(window);

/* Optional signal-matrix criterion navigation */
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

/* Automatic native signal-table navigation in the chart presentation bundle.
 * Reads table headers and layout only. It never reads, changes or infers data.
 * Standalone sc-matrix-nav.js remains explicitly opt-in.
 */
(function (w, d) {
  'use strict';
  if (!w || !d || typeof d.querySelectorAll !== 'function' || !d.documentElement
    || typeof d.documentElement.contains !== 'function' || !w.SCMatrixNav) return;
  const SC = w.SC = w.SC || {};
  if (SC.matrixTables) return;
  const selector = '.sc-table-scroll > table.sc-signal-matrix, [data-sc-matrix-nav] > table';
  const records = new Map();
  let observer = null;
  let queued = false;
  let active = false;
  let ready = null;

  function eligible(table) {
    const region = table.parentElement;
    if (!region || region.querySelector('table') !== table || !table.matches(selector)
      || region.getAttribute('data-sc-matrix-nav') === 'off'
      || table.getAttribute('data-sc-matrix-nav') === 'off') return false;
    const explicit = region.hasAttribute('data-sc-matrix-nav') || table.hasAttribute('data-sc-matrix-nav');
    if (table.classList.contains('sc-signal-matrix--values') && !explicit) return false;
    const rows = table.tHead && table.tHead.rows;
    return !!rows && rows.length === 1 && rows[0].cells.length > 1
      && Array.from(rows[0].cells).every(cell => cell.tagName === 'TH' && cell.colSpan === 1 && cell.rowSpan === 1);
  }
  function headerState(table, region) {
    const cells = table.tHead ? Array.from(table.tHead.querySelectorAll('th, td')) : [];
    const disclosures = [];
    for (let parent = region.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') disclosures.push(parent);
    }
    return {
      cells, disclosures,
      label: region.getAttribute('aria-label') + '|' + region.getAttribute('data-sc-matrix-nav-label'),
      words: cells.map(cell => [cell.textContent, cell.getAttribute('data-sc-label'), cell.colSpan, cell.rowSpan].join('\u001f')).join('\u001e')
    };
  }
  function same(a, b) {
    return a.label === b.label && a.words === b.words && a.cells.length === b.cells.length
      && a.cells.every((cell, index) => cell === b.cells[index])
      && a.disclosures.length === b.disclosures.length
      && a.disclosures.every((details, index) => details === b.disclosures[index]);
  }
  function refresh() {
    if (!active) return api;
    records.forEach((record, region) => {
      const current = region.querySelector('table');
      if (!d.documentElement.contains(region) || !current || !eligible(current) || current.parentElement !== region) {
        record.controller.destroy();
        records.delete(region);
      }
    });
    d.querySelectorAll(selector).forEach(table => {
      if (!eligible(table)) return;
      const region = table.parentElement;
      const state = headerState(table, region);
      const record = records.get(region);
      if (record) {
        const moved = record.parent !== region.parentElement || record.controls.nextElementSibling !== region;
        const focus = moved && record.controls.contains(d.activeElement) ? d.activeElement.dataset.scMatrixColumn : null;
        if (moved) region.before(record.controls);
        if (moved || record.table !== table || !same(record.state, state)) {
          record.controller.refresh();
          record.table = table;
          record.state = state;
          record.parent = region.parentElement;
        }
        if (focus !== null) {
          const replacement = Array.from(record.controls.querySelectorAll('button')).find(button => button.dataset.scMatrixColumn === focus);
          if (replacement && !record.controls.hidden) replacement.focus({ preventScroll: true });
          else region.focus({ preventScroll: true });
        }
      } else {
        const controller = w.SCMatrixNav.attach(region);
        if (controller) records.set(region, { table, state, controller, parent: region.parentElement, controls: region.previousElementSibling });
      }
    });
    return api;
  }
  function schedule() {
    if (queued || !active) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      if (active) refresh();
    });
  }
  function relevant(change) {
    const element = change.target.nodeType === 1 ? change.target : change.target.parentElement;
    if (!element || element.closest('[data-sc-matrix-controls], [data-sc-matrix-nav-style]')) return false;
    const table = element.closest('table');
    if (table && (table.matches(selector) || records.has(table.parentElement))) return true;
    if (records.has(element) || element.matches('[data-sc-matrix-nav], .sc-table-scroll')) return true;
    if (change.type !== 'childList') return false;
    return Array.from(change.addedNodes).concat(Array.from(change.removedNodes)).some(node =>
      node.nodeType === 1 && !node.matches('[data-sc-matrix-controls], [data-sc-matrix-nav-style]')
      && (node.matches(selector) || node.matches('.sc-table-scroll, [data-sc-matrix-nav]') || !!node.querySelector(selector)));
  }
  function init() {
    active = true;
    refresh();
    if (!observer && w.MutationObserver) {
      observer = new w.MutationObserver(changes => { if (changes.some(relevant)) schedule(); });
      observer.observe(d.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true,
        attributeFilter: ['class', 'data-sc-matrix-nav', 'data-sc-label', 'aria-label', 'data-sc-matrix-nav-label', 'colspan', 'rowspan']
      });
    }
    return api;
  }
  function destroy() {
    active = false;
    if (ready) { d.removeEventListener('DOMContentLoaded', ready); ready = null; }
    if (observer) { observer.disconnect(); observer = null; }
    records.forEach(record => record.controller.destroy());
    records.clear();
    return api;
  }
  const api = SC.matrixTables = { init, refresh, destroy };
  if (d.readyState === 'loading' && typeof d.addEventListener === 'function') {
    ready = () => { ready = null; init(); };
    d.addEventListener('DOMContentLoaded', ready, { once: true });
  } else init();
})(typeof window === 'undefined' ? null : window, typeof document === 'undefined' ? null : document);
