/* SpicyChicken Design System — sc-map.js v2.10.0 · source: build/map.js · projection, topojson and the pan/zoom view engine */
(function (w) {
  'use strict';
  var SC = w.SC = (w.SC || {});

  /* =====================================================================
     GEO — pure functions. No DOM, no window: importable anywhere, and the
     half of a map that never needed to be a component.
     ===================================================================== */

  /* The frame the us-atlas topology is already projected into. Every number
     below is in these units, so a point and a state share one coordinate
     space without a second projection step. */
  var EXTENT = { x: 0, y: 0, w: 975, h: 610 };

  /* Albers equal-area conic for the lower 48, tuned (k, tx, ty) to land in
     EXTENT. Lower 48 only — the name says usa48, not usa, because Alaska and
     Hawaii land somewhere wrong rather than nowhere, which is worse. */
  var albersUsa48 = (function () {
    var rad = Math.PI / 180, y0 = 29.5 * rad, y1 = 45.5 * rad;
    var n = (Math.sin(y0) + Math.sin(y1)) / 2;
    var c = 1 + Math.sin(y0) * (2 * n - Math.sin(y0));
    var r0 = Math.sqrt(c) / n;
    function raw(lam, phi) {
      var r = Math.sqrt(c - 2 * n * Math.sin(phi)) / n;
      return [r * Math.sin(lam * n), r0 - r * Math.cos(lam * n)];
    }
    var k = 1300, tx = 487.5, ty = 305, cc = raw(-0.6 * rad, 38.7 * rad);
    return function (lon, lat) {
      var p = raw((lon + 96) * rad, lat * rad);
      return [tx + k * (p[0] - cc[0]), ty - k * (p[1] - cc[1])];
    };
  })();

  /* Quantized-topojson reader: delta-decodes the arcs through topo.transform,
     resolves negative arc indices as reversed, and returns one entry per
     geometry with a path, a bbox and a label anchor.
     The anchor is the centroid of the LARGEST ring, not of the bbox, so
     Michigan labels its lower peninsula and Florida its mainland rather than
     a point out in the water. */
  function decodeTopology(topo, objectName) {
    var t = topo.transform, sx = t.scale[0], sy = t.scale[1], dx = t.translate[0], dy = t.translate[1];
    var arcs = topo.arcs.map(function (arc) {
      var x = 0, y = 0;
      return arc.map(function (p) { x += p[0]; y += p[1]; return [x * sx + dx, y * sy + dy]; });
    });
    var ring = function (ai) { return ai < 0 ? arcs[~ai].slice().reverse() : arcs[ai]; };
    var obj = topo.objects[objectName || Object.keys(topo.objects)[0]];
    return obj.geometries.map(function (g) {
      var polys = g.type === 'Polygon' ? [g.arcs] : g.arcs;
      var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
      var bestA = 0, cx = 0, cy = 0, d = '';
      polys.forEach(function (poly) {
        poly.forEach(function (r, ri) {
          var pts = [];
          r.forEach(function (ai) { pts = pts.concat(ring(ai)); });
          for (var i = 0; i < pts.length; i++) {
            if (pts[i][0] < minX) minX = pts[i][0];
            if (pts[i][0] > maxX) maxX = pts[i][0];
            if (pts[i][1] < minY) minY = pts[i][1];
            if (pts[i][1] > maxY) maxY = pts[i][1];
          }
          if (ri === 0) {
            var a = 0, mx = 0, my = 0;
            for (var j = 0; j < pts.length; j++) {
              var p1 = pts[j], p2 = pts[(j + 1) % pts.length];
              var cross = p1[0] * p2[1] - p2[0] * p1[1];
              a += cross; mx += (p1[0] + p2[0]) * cross; my += (p1[1] + p2[1]) * cross;
            }
            if (Math.abs(a) > Math.abs(bestA)) { bestA = a; cx = mx / (3 * a); cy = my / (3 * a); }
          }
          d += 'M' + pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join('L') + 'Z';
        });
      });
      return { name: (g.properties || {}).name || '', id: g.id, d: d,
               bbox: [minX, minY, maxX, maxY], center: [cx, cy] };
    });
  }

  /* A geodesic circle of `dist` around [lat, lon], projected — a real "within
     N of here", not an ellipse drawn in screen space. `earth` is the radius in
     the same unit as `dist`: 3958.8 miles (default) or 6371 km. */
  function geoRing(lat, lon, dist, opts) {
    opts = opts || {};
    var project = opts.project || albersUsa48;
    var R = opts.earth || 3958.8, steps = opts.steps || 72;
    var rad = Math.PI / 180, d = dist / R, p1 = lat * rad, l1 = lon * rad, out = [];
    for (var i = 0; i <= steps; i++) {
      var th = (i / steps) * 2 * Math.PI;
      var p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(th));
      var l2 = l1 + Math.atan2(Math.sin(th) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
      out.push(project(l2 / rad, p2 / rad));
    }
    return out;
  }
  function ringPath(pts) {
    return pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join('') + 'Z';
  }

  /* Union some boxes, pad them, lock to the extent's aspect. Takes
     [minX, minY, maxX, maxY] or {x,y,w,h}; always returns {x,y,w,h}, so a
     caller's fit function has one shape to produce and one to consume.

     The pad is a fraction of the LARGER dimension plus a flat margin, not a
     fraction of each axis: a tall narrow selection given a proportional
     horizontal pad ends up hugging its own edges. And a union that overflows
     the extent falls back to the whole extent rather than showing a frame
     that is mostly nothing. Both are the shape a production map converged on
     rather than a first guess. */
  function fitBoxes(boxes, opts) {
    opts = opts || {};
    var ext = opts.extent || EXTENT;
    var frac = opts.pad === undefined ? 0.12 : opts.pad;
    var flat = opts.padPx === undefined ? 8 : opts.padPx;
    var full = { x: ext.x, y: ext.y, w: ext.w, h: ext.h };
    var list = (boxes || []).filter(Boolean).map(function (b) {
      return b.length ? b : [b.x, b.y, b.x + b.w, b.y + b.h];
    });
    if (!list.length) return full;
    var x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
    for (var i = 0; i < list.length; i++) {
      x1 = Math.min(x1, list[i][0]); y1 = Math.min(y1, list[i][1]);
      x2 = Math.max(x2, list[i][2]); y2 = Math.max(y2, list[i][3]);
    }
    var pad = Math.max(x2 - x1, y2 - y1) * frac + flat;
    x1 -= pad; y1 -= pad; x2 += pad; y2 += pad;
    var bw = x2 - x1, bh = y2 - y1, ar = ext.w / ext.h;
    if (bw / bh > ar) { var nh = bw / ar; y1 -= (nh - bh) / 2; bh = nh; }
    else { var nw = bh * ar; x1 -= (nw - bw) / 2; bw = nw; }
    if (bw > ext.w) return full;
    return { x: x1, y: y1, w: bw, h: bh };
  }

  var STATE_ABBR = { Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA',
    Colorado: 'CO', Connecticut: 'CT', Delaware: 'DE', 'District of Columbia': 'DC', Florida: 'FL',
    Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL', Indiana: 'IN', Iowa: 'IA', Kansas: 'KS',
    Kentucky: 'KY', Louisiana: 'LA', Maine: 'ME', Maryland: 'MD', Massachusetts: 'MA', Michigan: 'MI',
    Minnesota: 'MN', Mississippi: 'MS', Missouri: 'MO', Montana: 'MT', Nebraska: 'NE', Nevada: 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
    'North Carolina': 'NC', 'North Dakota': 'ND', Ohio: 'OH', Oklahoma: 'OK', Oregon: 'OR',
    Pennsylvania: 'PA', 'Puerto Rico': 'PR', 'Rhode Island': 'RI', 'South Carolina': 'SC',
    'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT', Vermont: 'VT', Virginia: 'VA',
    Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI', Wyoming: 'WY' };

  /* =====================================================================
     VIEW — pan, zoom and the marks that must not scale with it.
     Per instance: two maps on one page do not share state.
     ===================================================================== */

  /* Marks inside the svg opt into non-scaling behaviour with data attributes,
     which is the whole contract:
       data-br   base radius   — kept constant on screen as the view tightens
       data-bf   base font-size — same, for labels
       data-di + data-px/py    — a collocation spiral index and its true point,
                                 so overlapping marks keep a constant on-screen
                                 spread instead of exploding under zoom
     Nothing else in the svg is touched. */
  function mapView(svg, opts) {
    opts = opts || {};
    var ext = opts.extent || EXTENT;
    var minW = ext.w / (opts.maxZoom || 40);
    var maxW = ext.w * (opts.minZoom || 1.15);
    var labelAt = opts.labelAt === undefined ? 0.72 : opts.labelAt;
    var onApply = opts.onApply || function () {};
    var view = opts.view ? clamp(opts.view) : { x: ext.x, y: ext.y, w: ext.w, h: ext.h };
    var anim = null, ptrs = {}, ptrCount = 0, dragDist = 0;

    function clamp(v) {
      var wid = Math.min(Math.max(v.w, minW), maxW);
      var hei = wid * ext.h / ext.w;
      return { w: wid, h: hei,
        x: Math.min(Math.max(v.x, ext.x - wid * 0.25), ext.x + ext.w - wid * 0.75),
        y: Math.min(Math.max(v.y, ext.y - hei * 0.25), ext.y + ext.h - hei * 0.75) };
    }
    function apply(v) {
      view = v;
      svg.setAttribute('viewBox', v.x + ' ' + v.y + ' ' + v.w + ' ' + v.h);
      var s = Math.max(v.w / ext.w, 0.12);
      var i, n, list;
      list = svg.querySelectorAll('[data-br]');
      for (i = 0; i < list.length; i++) list[i].setAttribute('r', (parseFloat(list[i].getAttribute('data-br')) * s).toFixed(2));
      list = svg.querySelectorAll('[data-di]');
      for (i = 0; i < list.length; i++) {
        n = list[i];
        var di = +n.getAttribute('data-di');
        if (di < 2) continue;
        var a = di * 2.39996, rr = 2.4 * Math.sqrt(di) * s;
        n.setAttribute('cx', (+n.getAttribute('data-px') + rr * Math.cos(a)).toFixed(1));
        n.setAttribute('cy', (+n.getAttribute('data-py') + rr * Math.sin(a)).toFixed(1));
      }
      list = svg.querySelectorAll('[data-bf]');
      for (i = 0; i < list.length; i++) list[i].setAttribute('font-size', (parseFloat(list[i].getAttribute('data-bf')) * s).toFixed(2));
      if (labelAt) svg.classList.toggle('sc-map--labels', v.w < ext.w * labelAt);
      onApply(v);
    }
    function animateTo(to) {
      if (anim) cancelAnimationFrame(anim);
      to = clamp(to);
      if (w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)').matches) { apply(to); return; }
      var from = view, t0 = performance.now(), D = opts.duration || 340;
      var step = function (now) {
        var u = Math.min(1, (now - t0) / D);
        var e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
        apply({ x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e,
                w: from.w + (to.w - from.w) * e, h: from.h + (to.h - from.h) * e });
        if (u < 1) anim = requestAnimationFrame(step);
      };
      anim = requestAnimationFrame(step);
    }
    function toView(clientX, clientY) {
      var r = svg.getBoundingClientRect();
      return [view.x + (clientX - r.left) / r.width * view.w,
              view.y + (clientY - r.top) / r.height * view.h];
    }
    function zoomAt(vx, vy, f) {
      apply(clamp({ w: view.w * f, h: view.h * f, x: vx - (vx - view.x) * f, y: vy - (vy - view.y) * f }));
    }

    /* Plain scroll keeps scrolling the page — the embedded-map convention —
       so the modifier has to be taught rather than assumed. */
    function onWheel(e) {
      if (!e.ctrlKey && !e.metaKey) { if (opts.onPlainScroll) opts.onPlainScroll(); return; }
      e.preventDefault();
      var p = toView(e.clientX, e.clientY);
      zoomAt(p[0], p[1], e.deltaY < 0 ? 0.82 : 1 / 0.82);
    }
    function onDown(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (opts.onPressBackground && !(opts.isPointNode && opts.isPointNode(e.target))) opts.onPressBackground(e);
      dragDist = 0;
      if (!ptrs[e.pointerId]) ptrCount++;
      ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (ptrCount === 2) svg.setPointerCapture(e.pointerId);
    }
    function onMove(e) {
      if (!ptrs[e.pointerId]) return;
      var prev = ptrs[e.pointerId];
      var px = prev.x, py = prev.y;
      ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
      dragDist += Math.sqrt(Math.pow(e.clientX - px, 2) + Math.pow(e.clientY - py, 2));
      /* capture only once a real drag starts: capturing on pointerdown would
         re-target the eventual click at the svg and kill every mark's click */
      if (dragDist > 4) {
        if (opts.onDragStart) opts.onDragStart();
        if (e.pointerType === 'mouse' && !svg.hasPointerCapture(e.pointerId)) svg.setPointerCapture(e.pointerId);
      }
      var r = svg.getBoundingClientRect(), scale = view.w / r.width;
      if (ptrCount === 1 && e.pointerType === 'mouse') {
        apply(clamp({ w: view.w, h: view.h, x: view.x - (e.clientX - px) * scale, y: view.y - (e.clientY - py) * scale }));
      } else if (ptrCount === 2) {
        e.preventDefault();
        var ids = Object.keys(ptrs), other = ptrs[ids[0] === String(e.pointerId) ? ids[1] : ids[0]];
        if (!other) return;
        var dPrev = Math.sqrt(Math.pow(px - other.x, 2) + Math.pow(py - other.y, 2));
        var dNow = Math.sqrt(Math.pow(e.clientX - other.x, 2) + Math.pow(e.clientY - other.y, 2));
        var mPrevX = (px + other.x) / 2, mPrevY = (py + other.y) / 2;
        var mNowX = (e.clientX + other.x) / 2, mNowY = (e.clientY + other.y) / 2;
        var nv = { w: view.w, h: view.h, x: view.x - (mNowX - mPrevX) * scale, y: view.y - (mNowY - mPrevY) * scale };
        if (dPrev > 24 && dNow > 24) {
          var f = dPrev / dNow, p2 = toView(mNowX, mNowY);
          nv = { w: nv.w * f, h: nv.h * f, x: p2[0] - (p2[0] - nv.x) * f, y: p2[1] - (p2[1] - nv.y) * f };
        }
        apply(clamp(nv));
      }
    }
    function onUp(e) { if (ptrs[e.pointerId]) { delete ptrs[e.pointerId]; ptrCount--; } }

    svg.addEventListener('wheel', onWheel, { passive: false });
    svg.addEventListener('pointerdown', onDown);
    svg.addEventListener('pointermove', onMove, { passive: false });
    svg.addEventListener('pointerup', onUp);
    svg.addEventListener('pointercancel', onUp);
    apply(view);

    return {
      get: function () { return view; },
      set: function (v) { apply(clamp(v)); },
      animateTo: animateTo,
      zoomBy: function (f) { zoomAt(view.x + view.w / 2, view.y + view.h / 2, f); },
      reset: function (to) { animateTo(to || { x: ext.x, y: ext.y, w: ext.w, h: ext.h }); },
      /* how far the last gesture travelled: > 4 means that was a pan, and
         whatever it ended on must not be treated as a click */
      dragDistance: function () { return dragDist; },
      destroy: function () {
        if (anim) cancelAnimationFrame(anim);
        svg.removeEventListener('wheel', onWheel);
        svg.removeEventListener('pointerdown', onDown);
        svg.removeEventListener('pointermove', onMove);
        svg.removeEventListener('pointerup', onUp);
        svg.removeEventListener('pointercancel', onUp);
      }
    };
  }

  SC.geo = { EXTENT: EXTENT, albersUsa48: albersUsa48, decodeTopology: decodeTopology,
             ring: geoRing, ringPath: ringPath, fitBoxes: fitBoxes, STATE_ABBR: STATE_ABBR };
  SC.mapView = mapView;
})(window);
