/* SpicyChicken Design System — sc-theme.js v2.6.0 · source: build/theme.js · link it from <head> (before paint) or inline it */
(function () {
  var KEY = 'sc-theme', root = document.documentElement, saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
  function sync() {
    var cur = root.getAttribute('data-theme') || 'auto';
    var btns = document.querySelectorAll('.sc-theme-toggle button');
    for (var i = 0; i < btns.length; i++) btns[i].setAttribute('aria-pressed', btns[i].getAttribute('data-theme') === cur ? 'true' : 'false');
  }
  function set(v) {
    if (v === 'auto') { root.removeAttribute('data-theme'); try { localStorage.removeItem(KEY); } catch (e) {} }
    else { root.setAttribute('data-theme', v); try { localStorage.setItem(KEY, v); } catch (e) {} }
    sync();
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.sc-theme-toggle button');
    if (b) set(b.getAttribute('data-theme'));
  });
  document.addEventListener('DOMContentLoaded', sync);
  if (window.MutationObserver) new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  // Print: the stylesheet already prints "auto" pages light; a pinned-dark page
  // is switched to light for the print job and restored after (not persisted).
  var printPinned = false;
  function onPrint(printing) {
    if (printing) { printPinned = root.getAttribute('data-theme') === 'dark'; if (printPinned) root.setAttribute('data-theme', 'light'); }
    else if (printPinned) { root.setAttribute('data-theme', 'dark'); printPinned = false; }
  }
  if (window.addEventListener) {
    window.addEventListener('beforeprint', function () { onPrint(true); });
    window.addEventListener('afterprint', function () { onPrint(false); });
  } else if (window.matchMedia) {
    var mq = window.matchMedia('print');
    if (mq && mq.addListener) mq.addListener(function (m) { onPrint(m.matches); });
  }
})();
