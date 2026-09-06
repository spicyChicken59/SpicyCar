/* SpicyChicken Design System — sc-motion.js v2.8.0
   Optional, visible-by-default motion. No application state or data is changed. */
(function (w, d) {
  'use strict';
  const SC = w.SC = w.SC || {};
  if (SC.motion) return;

  const selector = '.sc-reveal, [data-sc-motion], .sc-stagger, .sc-chick-arrive, .sc-rule-reveal';
  const records = new Map();
  const roots = new Set();
  const excluded = new Set();
  let observer = null;
  let mutations = null;
  let listening = false;
  let observerFailed = false;
  let refreshQueued = false;
  let autoStart = true;
  let readyHandler = null;
  let paused = d.documentElement.getAttribute('data-sc-motion-paused') === 'true';
  let preference = null;
  try { preference = w.matchMedia ? w.matchMedia('(prefers-reduced-motion: reduce)') : null; } catch (_) {}
  let reduced = !!(preference && preference.matches);

  function within(root, element) {
    return root === element || (root && typeof root.contains === 'function' && root.contains(element));
  }
  function validRoot(root) {
    return root && typeof root.querySelectorAll === 'function';
  }
  function focused(element) {
    return d.activeElement && within(element, d.activeElement);
  }
  function removeAnimation(element) {
    element.classList.remove('sc-motion-in');
  }
  function finish(element, record) {
    record.done = true;
    removeAnimation(element);
    if (observer) {
      try { observer.unobserve(element); } catch (_) {}
    }
  }
  function finishAll() {
    records.forEach((record, element) => finish(element, record));
  }
  function failOpen() {
    observerFailed = true;
    if (observer) observer.disconnect();
    observer = null;
    finishAll();
  }
  function enter(element) {
    const record = records.get(element);
    if (!record || record.done) return;
    record.done = true;
    if (observer) {
      try { observer.unobserve(element); } catch (_) {}
    }
    if (!paused && !reduced && !focused(element)) element.classList.add('sc-motion-in');
  }
  function ensureObserver() {
    if (observer || observerFailed) return;
    if (!w.IntersectionObserver) { observerFailed = true; return; }
    try {
      observer = new w.IntersectionObserver(entries => {
        try {
          entries.forEach(entry => { if (entry.isIntersecting) enter(entry.target); });
        } catch (_) { failOpen(); }
      }, { threshold: 0.08 });
    } catch (_) { failOpen(); }
  }
  function arm(element, record) {
    if (paused || reduced || observerFailed || focused(element)) {
      finish(element, record);
      return;
    }
    try { observer.observe(element); } catch (_) { failOpen(); }
  }
  function register(element, order) {
    if (Array.from(excluded).some(root => within(root, element))) return;
    if (!element.classList || element.classList.contains('sc-stagger') || /^(?:SCRIPT|STYLE|TEMPLATE)$/.test(element.tagName)) return;
    let record = records.get(element);
    if (!record) {
      record = {
        done: false,
        ownsOrder: order !== undefined,
        order: element.style.getPropertyValue('--sc-motion-order'),
        priority: element.style.getPropertyPriority('--sc-motion-order')
      };
      records.set(element, record);
      if (order !== undefined) element.style.setProperty('--sc-motion-order', String(Math.min(order, 5)));
      arm(element, record);
    }
  }
  function scan(root) {
    const found = [];
    if (root.matches && root.matches(selector)) found.push(root);
    root.querySelectorAll(selector).forEach(element => found.push(element));
    found.forEach(element => {
      if (element.classList.contains('sc-stagger')) {
        Array.from(element.children).forEach((child, index) => register(child, index));
        // A stagger group moves its children, never the whole group as well.
      } else {
        const parent = element.parentElement;
        const order = parent && parent.classList.contains('sc-stagger')
          ? Array.prototype.indexOf.call(parent.children, element) : undefined;
        register(element, order);
      }
    });
  }
  function release(element, record) {
    finish(element, record);
    if (record.ownsOrder) {
      if (record.order) element.style.setProperty('--sc-motion-order', record.order, record.priority);
      else element.style.removeProperty('--sc-motion-order');
    }
    records.delete(element);
  }
  function refresh(root) {
    if (root && !validRoot(root)) return api;
    ensureObserver();
    roots.forEach(registered => { if (registered.isConnected === false) roots.delete(registered); });
    excluded.forEach(registered => { if (registered.isConnected === false) excluded.delete(registered); });
    records.forEach((record, element) => {
      if (element.isConnected === false) release(element, record);
    });
    try {
      if (root) scan(root);
      else roots.forEach(scan);
    } catch (_) { failOpen(); }
    syncControls();
    return api;
  }
  function onMutation() {
    if (refreshQueued) return;
    refreshQueued = true;
    Promise.resolve().then(() => {
      refreshQueued = false;
      if (listening) refresh();
    });
  }
  function syncControls() {
    d.querySelectorAll('[data-sc-motion-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', paused ? 'true' : 'false');
      const status = button.querySelector('[data-sc-motion-status]');
      const text = paused ? 'Motion paused' : 'Motion enabled';
      if (status && status.textContent !== text) status.textContent = text;
    });
  }
  function setPaused(value) {
    paused = !!value;
    if (paused) d.documentElement.setAttribute('data-sc-motion-paused', 'true');
    else d.documentElement.removeAttribute('data-sc-motion-paused');
    if (paused) finishAll();
    syncControls();
    return api;
  }
  function onPreference(event) {
    reduced = !!event.matches;
    if (reduced) finishAll();
    // Re-enabling motion never hides or replays already-seen content.
  }
  function onFocus(event) {
    records.forEach((record, element) => {
      if (within(element, event.target)) finish(element, record);
    });
  }
  function onEnd(event) {
    if (event.animationName && !event.animationName.startsWith('sc-motion-')) return;
    if (records.has(event.target)) removeAnimation(event.target);
  }
  function onClick(event) {
    const target = event.target && event.target.closest ? event.target : event.target && event.target.parentElement;
    const toggle = target && target.closest('[data-sc-motion-toggle]');
    if (toggle) { setPaused(!paused); return; }
    const trigger = target && target.closest('[data-sc-motion-replay]');
    if (!trigger) return;
    const value = trigger.getAttribute('data-sc-motion-replay');
    const root = value === 'all' ? d : value && value.charAt(0) === '#' ? d.getElementById(value.slice(1)) : null;
    if (root) replay(root);
  }
  function onPrint() { finishAll(); }
  function listen() {
    if (listening) return;
    reduced = !!(preference && preference.matches);
    if (reduced) finishAll();
    listening = true;
    d.addEventListener('focusin', onFocus, true);
    d.addEventListener('animationend', onEnd);
    d.addEventListener('animationcancel', onEnd);
    d.addEventListener('click', onClick);
    w.addEventListener('beforeprint', onPrint);
    if (preference) {
      if (preference.addEventListener) preference.addEventListener('change', onPreference);
      else if (preference.addListener) preference.addListener(onPreference);
    }
    if (w.MutationObserver) {
      try {
        mutations = new w.MutationObserver(onMutation);
        mutations.observe(d.documentElement, { childList: true, subtree: true });
      } catch (_) { mutations = null; }
    }
  }
  function init(root) {
    root = root || d;
    if (!validRoot(root)) return api;
    autoStart = true;
    excluded.forEach(registered => { if (within(root, registered) || within(registered, root)) excluded.delete(registered); });
    listen();
    roots.add(root);
    return refresh(root);
  }
  function replay(root) {
    root = root || d;
    if (!validRoot(root)) return api;
    init(root);
    const targets = [];
    records.forEach((record, element) => {
      if (within(root, element)) {
        finish(element, record);
        targets.push([element, record]);
      }
    });
    if (paused || reduced || observerFailed || !targets.length) return api;
    // One layout read per explicit replay resets CSS keyframes, including a
    // replay clicked twice in one frame. Normal scrolling performs no reads.
    void (root.nodeType === 9 ? root.documentElement : root).offsetWidth;
    targets.forEach(([element, record]) => {
      record.done = false;
      arm(element, record);
    });
    return api;
  }
  function destroy(root) {
    root = root || d;
    if (root !== d && validRoot(root)) excluded.add(root);
    records.forEach((record, element) => {
      if (within(root, element)) release(element, record);
    });
    roots.forEach(registered => { if (within(root, registered)) roots.delete(registered); });
    if (root === d || roots.size === 0) {
      autoStart = false;
      if (readyHandler) d.removeEventListener('DOMContentLoaded', readyHandler);
      readyHandler = null;
      listening = false;
      roots.clear();
      excluded.clear();
      records.forEach((record, element) => release(element, record));
      if (observer) observer.disconnect();
      observer = null;
      if (mutations) mutations.disconnect();
      mutations = null;
      d.removeEventListener('focusin', onFocus, true);
      d.removeEventListener('animationend', onEnd);
      d.removeEventListener('animationcancel', onEnd);
      d.removeEventListener('click', onClick);
      w.removeEventListener('beforeprint', onPrint);
      if (preference) {
        if (preference.removeEventListener) preference.removeEventListener('change', onPreference);
        else if (preference.removeListener) preference.removeListener(onPreference);
      }
    }
    return api;
  }
  const api = SC.motion = {
    init, refresh, replay, destroy, setPaused,
    get paused() { return paused; },
    get reducedMotion() { return reduced; }
  };
  if (d.readyState === 'loading') {
    readyHandler = function start() {
      d.removeEventListener('DOMContentLoaded', start);
      readyHandler = null;
      if (autoStart) init();
    };
    d.addEventListener('DOMContentLoaded', readyHandler);
  } else init();
})(window, document);
