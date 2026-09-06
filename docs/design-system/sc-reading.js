/* SpicyChicken Design System — sc-reading.js v2.7.0 — optional chapter navigation. Native anchors remain native. */
(function (w, d) {
  'use strict';
  if (!w || !d || typeof d.querySelectorAll !== 'function') return;
  const SC = w.SC = w.SC || {};
  if (SC.reading) return;

  const selector = 'nav[data-sc-reading]';
  const records = new Map();
  let listening = false;
  let scheduled = null;
  let scheduleKind = null;
  let readyHandler = null;

  function within(root, node) {
    return root === node || !!(root && typeof root.contains === 'function' && root.contains(node));
  }
  function validRoot(root) {
    return !!(root && typeof root.querySelectorAll === 'function');
  }
  function restore(link, record) {
    if (record.current === null) link.removeAttribute('aria-current');
    else link.setAttribute('aria-current', record.current);
  }
  function release(nav) {
    const record = records.get(nav);
    if (record) record.links.forEach((linkRecord, link) => restore(link, linkRecord));
    records.delete(nav);
  }
  function shown(node) {
    if (!within(d, node)) return false;
    for (let parent = node; parent && parent.nodeType === 1; parent = parent.parentElement) {
      if (parent.hasAttribute('hidden') || parent.getAttribute('aria-hidden') === 'true') return false;
    }
    try {
      if (node.getClientRects && !node.getClientRects().length) return false;
      if (w.getComputedStyle) {
        const style = w.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      }
    } catch (_) { return false; }
    return true;
  }
  function targetFor(link) {
    const href = link.getAttribute('href');
    if (!href || typeof d.getElementById !== 'function') return null;
    try {
      const page = new URL(d.URL || w.location.href);
      const url = new URL(href, d.baseURI || page.href);
      if (url.origin !== page.origin || url.pathname !== page.pathname || url.search !== page.search || !url.hash) return null;
      const target = d.getElementById(decodeURIComponent(url.hash.slice(1)));
      return within(d, target) ? target : null;
    } catch (_) { return null; }
  }
  function reconcile(nav) {
    let record = records.get(nav);
    if (!record) { record = { links: new Map() }; records.set(nav, record); }
    const found = new Set();
    nav.querySelectorAll('a[href]').forEach(link => {
      // A nested navigator owns its own links.
      if (link.closest && link.closest(selector) !== nav) return;
      const target = targetFor(link);
      if (!target) return;
      found.add(link);
      const existing = record.links.get(link);
      if (existing) existing.target = target;
      else record.links.set(link, { target, current: link.getAttribute('aria-current') });
    });
    record.links.forEach((linkRecord, link) => {
      if (!found.has(link)) { restore(link, linkRecord); record.links.delete(link); }
    });
  }
  function cancelScheduled() {
    if (scheduled === null) return;
    if (scheduleKind === 'frame' && w.cancelAnimationFrame) w.cancelAnimationFrame(scheduled);
    if (scheduleKind === 'timer' && w.clearTimeout) w.clearTimeout(scheduled);
    scheduled = null;
    scheduleKind = null;
  }
  function unlisten() {
    if (listening) {
      d.removeEventListener('scroll', schedule);
      w.removeEventListener('resize', schedule);
    }
    listening = false;
    cancelScheduled();
  }
  function update() {
    const height = w.innerHeight || (d.documentElement && d.documentElement.clientHeight) || 768;
    const readingLine = Math.min(160, Math.max(48, height * .2));
    records.forEach((record, nav) => {
      if (!within(d, nav) || !nav.matches(selector)) { release(nav); return; }
      let before = null;
      let after = null;
      if (shown(nav)) record.links.forEach((linkRecord, link) => {
        // Re-resolve the anchor so changed hrefs and replaced sections cannot
        // leave an indicator attached to stale application markup.
        const target = targetFor(link);
        if (!within(nav, link) || !target || !shown(link) || !shown(target)) return;
        let top;
        try { top = target.getBoundingClientRect().top; } catch (_) { return; }
        if (!Number.isFinite(top)) return;
        if (top <= readingLine) {
          if (!before || top > before.top) before = { link, top };
        } else if (!after || top < after.top) after = { link, top };
      });
      const current = (before || after || {}).link;
      record.links.forEach((linkRecord, link) => {
        if (link === current) {
          if (link.getAttribute('aria-current') !== 'location') link.setAttribute('aria-current', 'location');
        } else if (link.hasAttribute('aria-current')) link.removeAttribute('aria-current');
      });
    });
    if (!records.size) unlisten();
  }
  function schedule() {
    if (scheduled !== null || !records.size) return;
    const run = () => { scheduled = null; scheduleKind = null; update(); };
    if (w.requestAnimationFrame) {
      scheduleKind = 'frame';
      scheduled = w.requestAnimationFrame(run);
    } else if (w.setTimeout) {
      scheduleKind = 'timer';
      scheduled = w.setTimeout(run, 16);
    } else update();
  }
  function listen() {
    if (listening || !records.size || !d.addEventListener || !w.addEventListener) return;
    d.addEventListener('scroll', schedule, { passive: true });
    w.addEventListener('resize', schedule, { passive: true });
    listening = true;
  }
  function refresh(root) {
    root = root || d;
    if (!validRoot(root)) return api;
    records.forEach((record, nav) => {
      if (!within(d, nav) || !nav.matches(selector)) release(nav);
      else if (within(root, nav)) reconcile(nav);
    });
    const navs = [];
    if (root.matches && root.matches(selector)) navs.push(root);
    root.querySelectorAll(selector).forEach(nav => navs.push(nav));
    navs.forEach(nav => { if (within(d, nav)) reconcile(nav); });
    listen();
    cancelScheduled();
    update();
    return api;
  }
  function init(root) { return refresh(root); }
  function destroy(root) {
    root = root || d;
    records.forEach((record, nav) => { if (within(root, nav)) release(nav); });
    if (root === d && readyHandler) {
      d.removeEventListener('DOMContentLoaded', readyHandler);
      readyHandler = null;
    }
    if (!records.size) unlisten();
    return api;
  }
  const api = SC.reading = { init, refresh, destroy };
  if (d.readyState === 'loading' && d.addEventListener) {
    readyHandler = () => {
      d.removeEventListener('DOMContentLoaded', readyHandler);
      readyHandler = null;
      init();
    };
    d.addEventListener('DOMContentLoaded', readyHandler);
  } else init();
})(typeof window === 'undefined' ? null : window, typeof document === 'undefined' ? null : document);
