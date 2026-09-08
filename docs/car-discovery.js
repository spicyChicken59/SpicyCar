/* SpicyHome-inspired geographic browsing. Reads presentation records only;
 * the dashboard owns filtering, ranking, shortlist state and listing actions. */
(function () {
  'use strict';
  const node = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const button = (label, cls, click) => {
    const b = node('button', cls, label); b.type = 'button'; b.onclick = click; return b;
  };
  const located = (c) => typeof c.lat === 'number' && Number.isFinite(c.lat) && Math.abs(c.lat) <= 90
    && typeof c.lng === 'number' && Number.isFinite(c.lng) && Math.abs(c.lng) <= 180 && (c.lat !== 0 || c.lng !== 0);
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function create({ root, openCar, starCar, quickCar, resetFilters }) {
    let cars = [], map = null, layer = null, selected = null, limit = 8, scope = null, mounted = false, tileFailure = '', activePopup = null, pinKey = '';
    let areaBounds = null, groupVins = null, display = 'cars', mapSized = false;
    const inView = () => cars.filter((c) => (!areaBounds || (located(c) && areaBounds.contains([c.lat,c.lng]))) && (!groupVins || groupVins.has(c.vin)));
    const touch = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;
    let moving = !touch;
    const markers = new Map();
    const layout = node('div', 'car-discovery-layout');
    const modes = node('div', 'car-view-switch'); modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', 'Browse cars or map');
    const carsMode = button('Cars', 'car-view-button', () => setDisplay('cars'));
    const mapMode = button('Map', 'car-view-button', () => setDisplay('map'));
    modes.append(carsMode, mapMode); root.append(modes);
    function setDisplay(next) {
      display = next; layout.dataset.display = next;
      carsMode.setAttribute('aria-pressed', String(next === 'cars')); mapMode.setAttribute('aria-pressed', String(next === 'map'));
      requestAnimationFrame(() => { if (map) { map.invalidateSize({ pan: false }); if (surface.clientWidth && !mapSized) { if (!selected) fit(); mapSized = true; } } });
    }
    const panel = node('aside', 'car-place-panel'); panel.setAttribute('aria-label', 'Vehicle locations');
    const heading = node('div', 'car-place-heading');
    const fitButton = button('Fit all cars', 'car-text-button', () => { areaBounds = null; groupVins = null; limit = 8; renderCards(); fit(); });
    const localButton = button('Drivable area', 'car-text-button', () => {
      const near = cars.filter((c) => c.local && located(c)); if (map && near.length) { map.fitBounds(near.map((c) => [c.lat,c.lng]), {padding:[32,32],maxZoom:9,animate:false}); }
    });
    const areaButton = button('Search this map area', 'car-area-button', () => { if (!map) return; areaBounds = map.getBounds(); groupVins = null; limit = 8; renderCards(); setDisplay('cars'); count.focus({preventScroll:true}); });
    const moveButton = button('Move map', 'car-text-button car-move-map', () => { moving = !moving; syncTouch(); });
    moveButton.hidden = !touch;
    heading.append(node('h3', null, 'Where could you buy?'), moveButton, localButton, fitButton);
    const surface = node('div', 'car-place-map'); surface.id = 'car-place-map'; surface.tabIndex = 0; surface.setAttribute('aria-label', 'Approximate vehicle locations');
    const status = node('p', 'car-map-status'); status.setAttribute('role', 'status');
    const legend = node('div', 'car-map-legend');
    for (const [cls, text] of [['is-local', 'Drivable'], ['is-shipping', 'Shipping added'], ['is-saved', 'Saved']]) { const item = node('span'); item.append(node('i', 'car-map-dot ' + cls), document.createTextNode(text)); legend.append(item); }
    const foot = node('p', 'car-map-foot', 'Approximate city or ZIP locations. Dots group overlapping cars; zoom in or tap to see them. Confirm the dealer address before traveling.');
    const gesture = node('p', 'car-map-gesture', touch ? 'Swipe to scroll the page. Pinch or use + / − to zoom. Tap Move map to drag.' : 'Drag to explore. Use + / − or pinch to zoom.');
    const say = node('p', 'sc-sr-only'); say.id = 'map-say'; say.setAttribute('role', 'status');
    panel.append(heading, surface, areaButton, gesture, legend, status, foot, say);
    const column = node('div', 'car-place-results');
    const count = node('p', 'car-place-count');
    count.tabIndex = -1;
    const resetArea = button('Show all matching cars', 'car-text-button car-reset-area', () => { areaBounds = null; groupVins = null; limit = 8; renderCards(); }); resetArea.hidden = true;
    const cards = node('div', 'car-place-cards');
    const more = button('Show more cars', 'car-discovery-more', () => {
      const firstNew = limit; limit += 8; renderCards();
      const next = cards.children[firstNew];
      if (next) { next.focus({ preventScroll: true }); next.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'instant' : 'smooth' }); }
    });
    column.append(count, resetArea, cards, more); layout.append(column, panel); root.append(layout); setDisplay(display);
    function ensureMap() {
      if (map || mounted) return; mounted = true;
      if (!window.L) {
        surface.append(node('p', 'car-map-unavailable', 'Map unavailable. Every car is available beside it and in the listings below.'));
        return;
      }
      map = L.map(surface, { scrollWheelZoom: false, zoomSnap: 0.25, dragging: moving, touchZoom: true, zoomControl: true, maxZoom: 16, minZoom: 2,
        fadeAnimation: !reduced(), zoomAnimation: !reduced(), markerZoomAnimation: !reduced() }).setView([39.5, -98], 4);
      const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      }).addTo(map);
      let tileErrors = 0;
      tiles.on('tileerror', () => {
        tileErrors++;
        tileFailure = ' Some map tiles could not load. The dots and all cars in the list are still available.';
        locationStatus();
      });
      tiles.on('loading', () => { tileErrors = 0; });
      tiles.on('load', () => { if (!tileErrors) tileFailure = ''; locationStatus(); });
      layer = L.layerGroup().addTo(map);
      // Group buckets use projected coordinates, so panning cannot change them.
      // Rebuilding on popup auto-pan would remove the popup just opened.
      map.on('zoomend', () => { pinKey = ''; drawPins(); });
      syncTouch();
      surface.addEventListener('focusout', (e) => { if (touch && !panel.contains(e.relatedTarget)) { moving = false; syncTouch(); } });
      const sizePopup = () => { if (activePopup) { activePopup.options.maxWidth = Math.max(120, Math.min(310, surface.clientWidth - 60)); activePopup.update(); } };
      map.on('popupopen', (e) => { activePopup = e.popup; sizePopup(); });
      map.on('popupclose', () => { activePopup = null; });
      new ResizeObserver(() => { if (!root.hidden && map) { map.invalidateSize({ pan: false }); sizePopup(); } }).observe(surface);
    }
    function syncTouch() {
      if (!map) return;
      if (moving) map.dragging.enable(); else map.dragging.disable();
      surface.classList.toggle('is-page-scroll', !moving);
      moveButton.textContent = moving ? 'Done moving' : 'Move map';
      moveButton.setAttribute('aria-pressed', String(moving));
      gesture.textContent = touch && moving ? 'Drag to move the map. Tap Done moving to scroll the page again.' : touch ? 'Swipe to scroll the page. Pinch or use + / − to zoom. Tap Move map to drag.' : 'Drag to explore. Use + / − or pinch to zoom.';
    }
    function locationStatus() {
      const n = cars.filter(located).length;
      status.textContent = n + (n === 1 ? ' car with coordinates' : ' cars with coordinates') + (cars.length - n ? ' · ' + (cars.length - n) + ' without a verified location; included in the full list.' : '.') + tileFailure;
    }
    function fit() {
      if (!map || !surface.clientWidth || !surface.clientHeight) return;
      const places = cars.filter(located).map((c) => [c.lat, c.lng]);
      if (places.length) map.fitBounds(places, { padding: [24, 24], maxZoom: 10, animate: false });
    }
    function highlight(vin, scroll) {
      selected = vin;
      if (!inView().some((c) => c.vin === vin) && cars.some((c) => c.vin === vin)) { areaBounds = null; groupVins = null; }
      const index = inView().findIndex((c) => c.vin === vin);
      if (index >= limit) limit = Math.ceil((index + 1) / 8) * 8;
      renderCards();
      for (const pill of surface.querySelectorAll('.car-map-dot')) pill.classList.remove('is-selected');
      const marker = markers.get(vin);
      if (marker && marker.getElement()) marker.getElement().querySelector('.car-map-dot').classList.add('is-selected');
      if (scroll) {
        setDisplay('cars');
        const card = [...cards.children].find((n) => n.dataset.carVin === vin);
        if (card) { card.focus({ preventScroll: true }); card.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'instant' : 'smooth' }); }
      }
    }
    function popup(group) {
      const box = node('div', 'car-location-popup');
      box.append(node('strong', null, group.length === 1 ? group[0].location : group.length + ' cars in this map area'),
        node('p', null, 'Approximate locations · asking prices'));
      if (group.length > 1) {
        box.append(button('Browse these ' + group.length + ' cars', 'car-cluster-browse', () => {
          groupVins = new Set(group.map((c) => c.vin)); areaBounds = null; limit = 8; renderCards(); setDisplay('cars'); map.closePopup(); count.focus({preventScroll:true});
        }));
        const unique = new Set(group.map((c) => c.lat + ',' + c.lng));
        if (unique.size > 1) box.append(button('Zoom closer', 'car-text-button', () => { map.closePopup(); map.fitBounds(group.map((c) => [c.lat,c.lng]), {padding:[48,48],maxZoom:14,animate:false}); }));
      }
      for (const c of group) box.append(button(c.priceLabel + ' · ' + c.title + ' · ' + c.location, 'car-popup-car', () => {
        highlight(c.vin, true); map.closePopup();
      }));
      return box;
    }
    function drawPins() {
      if (!map || !layer) return;
      const nextKey = JSON.stringify(cars.filter(located).map((c) => [c.vin, c.lat, c.lng, c.price, c.local, c.picked, c.saved, c.tone, c.title, c.location]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))));
      if (pinKey === nextKey) return;
      pinKey = nextKey;
      layer.clearLayers(); markers.clear();
      const groups = [];
      for (const c of cars.filter(located).slice().sort((a, b) => String(a.vin).localeCompare(String(b.vin)))) {
        const pt = map.project([c.lat, c.lng]);
        // Compact dots preserve the market's shape. Overlaps remain at an
        // actual listing coordinate; the popup lists every grouped VIN.
        const group = groups.find((g) => g.cars[0].local === c.local && Math.hypot(g.point.x - pt.x, g.point.y - pt.y) < 12);
        if (group) group.cars.push(c);
        else groups.push({ point: pt, cars: [c] });
      }
      for (const { cars: group } of groups) {
        const first = group[0], priced = group.filter((c) => typeof c.price === 'number' && Number.isFinite(c.price));
        const cheapest = priced.reduce((a, b) => !a || b.price < a.price ? b : a, null);
        const pill = node('div', 'car-map-dot' + (group.every((c) => c.local) ? ' is-local' : ' is-shipping')
          + (group.some((c) => c.saved) ? ' is-saved' : '')
          + (group.some((c) => c.vin === selected) ? ' is-selected' : '')
          + (group.length > 1 ? ' is-group' : ''), group.length >= 3 ? String(group.length) : '');
        if (group.length >= 3) { const size = Math.min(34, 18 + Math.log2(group.length) * 3); pill.style.width = size + 'px'; pill.style.height = size + 'px'; }
        pill.style.setProperty('--car-dot-tone', group.length === 1 ? first.tone : 'var(--sc-chart-context)');
        const marker = L.marker([first.lat, first.lng], {
          icon: L.divIcon({ className: 'car-dot-marker', html: pill, iconSize: [32, 32], iconAnchor: [16, 16] }),
          keyboard: true, title: group.length > 1 ? group.length + ' cars in this map area, from ' + (cheapest ? cheapest.priceLabel : 'an unreported price') : first.title + ', ' + first.priceLabel + ', ' + first.location,
          alt: group.length > 1 ? group.length + ' cars in this map area' : first.title
        }).addTo(layer).bindPopup(() => popup(group), { maxWidth: Math.max(120, Math.min(310, surface.clientWidth - 60)) });
        const markerElement = marker.getElement();
        if (markerElement) {
          markerElement.setAttribute('aria-label', marker.options.title);
          markerElement.dataset.carCount = group.length;
          markerElement.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') say.textContent = marker.options.title; });
        }
        for (const c of group) markers.set(c.vin, marker);
        marker.on('click', () => { if (group.length === 1) highlight(first.vin, false); });
      }
    }
    function showOnMap(c) {
      if (!map || !located(c)) return;
      setDisplay('map');
      map.stop();
      selected = c.vin; renderCards();
      map.setView([c.lat, c.lng], 11, { animate: false });
      pinKey = ''; drawPins();
      const marker = markers.get(c.vin); if (marker) marker.openPopup();
      surface.scrollIntoView({ block: 'center', behavior: reduced() ? 'instant' : 'smooth' });
      surface.focus({ preventScroll: true });
    }
    function renderCards() {
      const prior = document.activeElement;
      const focusVin = prior && prior.dataset && prior.dataset.focusVin;
      const focusAction = prior && prior.dataset && prior.dataset.focusAction;
      const focusStar = prior && cards.contains(prior) && prior.dataset.fkey;
      cards.replaceChildren();
      const visible = inView();
      count.textContent = visible.length + ' cars' + (groupVins ? ' at this location' : areaBounds ? ' in this map area' : ' matching your search');
      resetArea.hidden = !areaBounds && !groupVins;
      for (const c of visible.slice(0, limit)) {
        const card = node('article', 'car-place-card' + (selected === c.vin ? ' is-selected' : ''));
        card.dataset.carVin = c.vin; card.tabIndex = -1;
        const visual = node('div', 'car-place-photo');
        const placeholder = node('span', 'car-photo-placeholder', 'Photo unavailable'); visual.append(placeholder);
        if (c.image) {
          const img = node('img'); img.src = c.image; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
          img.onload = () => placeholder.hidden = true; img.onerror = () => img.remove(); visual.append(img);
        }
        const body = node('div', 'car-place-body');
        body.append(node('p', 'car-place-location', c.location), node('h3', null, c.title));
        const price = node('p', 'car-place-price', c.monthly ? c.paymentLabel : c.priceLabel); price.append(node('span', null, c.monthly ? ' estimated' : ' asking'));
        if(c.monthly)body.append(node('p','car-payment-note',c.priceLabel+' asking · '+c.paymentNote));
        body.append(price, node('p', 'car-place-shipping', c.shipping));
        const facts = node('div', 'car-place-facts');
        facts.append(node('span', null, c.milesLabel));
        if(c.radar)facts.append(node('span','car-radar-tag',c.radar));
        if (c.saved) facts.append(node('span', 'car-saved-label', 'Saved'));
        else if (c.picked) facts.append(node('span', 'car-value-label', 'Spicy pick'));
        if (!located(c)) facts.append(node('span', null, 'Location unverified'));
        body.append(facts, node('p', 'car-place-dealer', c.dealer));
        if (c.flags) body.append(node('p', 'car-place-flags', c.flags));
        const actions = node('div', 'car-place-actions');
        const action = (label, name, fn) => { const b = button(label, 'car-text-button', fn); b.dataset.focusVin = c.vin; b.dataset.focusAction = name; return b; };
        if (c.url) { const link = node('a', 'car-text-button', 'View listing ↗'); link.href = c.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link); }
        if(quickCar)actions.append(action('Quick look', 'look', () => quickCar(c.vin)));
        actions.append(action('Car details', 'view', () => openCar(c.vin)));
        if (map && located(c)) actions.append(action('Show on map', 'map', () => showOnMap(c)));
        // Reuse the dashboard's shortlist / called / ruled-out control and
        // its existing state cycle rather than introducing a second save model.
        const star = starCar(c.vin);
        if (star) actions.append(star); body.append(actions); card.append(visual, body); cards.append(card);
      }
      more.hidden = limit >= visible.length;
      more.textContent = 'Show ' + Math.min(8, visible.length - limit) + ' more cars';
      if (!visible.length) cards.append(node('p', 'car-map-unavailable', areaBounds ? 'No cars in this area. Move the map and search again, or show all matching cars.' : 'No cars match these filters. Change the filters above to bring them back.'));
      if (!visible.length && resetFilters) cards.append(button('Reset search filters', 'studio-button', () => { areaBounds=null;groupVins=null;limit=8;resetFilters(); }));
      if (focusVin && focusAction) {
        const restore = [...cards.querySelectorAll('[data-focus-vin]')].find((b) => b.dataset.focusVin === focusVin && b.dataset.focusAction === focusAction);
        if (restore) restore.focus({ preventScroll: true });
      }
      if (focusStar) { const restore = [...cards.querySelectorAll('[data-fkey]')].find((b) => b.dataset.fkey === focusStar); if (restore) restore.focus({preventScroll:true}); }
    }
    return {
      update(next, nextScope) {
        cars = next;
        if (scope !== nextScope) { limit = 8; selected = null; areaBounds = null; groupVins = null; }
        ensureMap(); fitButton.disabled = !map || !cars.some(located); localButton.disabled = !map || !cars.some((c) => c.local && located(c)); areaButton.disabled = !map; renderCards(); locationStatus();
        if (map) { if (scope !== nextScope) { map.closePopup(); fit(); pinKey = ''; } drawPins(); }
        scope = nextScope;
      },
      resize() { if (map) map.invalidateSize({ pan: false }); },
    };
  }
  window.CarDiscovery = { create };
})();
