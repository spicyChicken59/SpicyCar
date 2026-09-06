/* SpicyChicken Design System — sc-charts.js v2.10.0 · source: build/charts.js · needs sc.css; defer it or load at the end of <body> */
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
