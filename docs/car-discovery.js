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
    && typeof c.lng === 'number' && Number.isFinite(c.lng) && Math.abs(c.lng) <= 180;
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function create({ root, openCar, starCar }) {
    let cars = [], map = null, layer = null, selected = null, limit = 8, scope = null, mounted = false, tileFailure = '', activePopup = null;
    const markers = new Map();
    const layout = node('div', 'car-discovery-layout');
    const panel = node('aside', 'car-place-panel'); panel.setAttribute('aria-label', 'Vehicle locations');
    const heading = node('div', 'car-place-heading');
    const fitButton = button('Fit all cars', 'car-text-button', () => fit());
    heading.append(node('h3', null, 'Find your corner of the market.'), fitButton);
    const surface = node('div', 'car-place-map'); surface.id = 'car-place-map'; surface.setAttribute('aria-label', 'Approximate vehicle locations');
    const status = node('p', 'car-map-status'); status.setAttribute('role', 'status');
    const foot = node('p', 'car-map-foot', 'Approximate listing locations, often a city or ZIP centroid. Confirm the dealer address before traveling. Area pins group nearby cars; zoom in to separate them.');
    panel.append(heading, surface, status, foot);
    const column = node('div', 'car-place-results');
    const count = node('p', 'car-place-count');
    const cards = node('div', 'car-place-cards');
    const more = button('Show more cars', 'car-discovery-more', () => {
      const firstNew = limit; limit += 8; renderCards();
      const next = cards.children[firstNew];
      if (next) { next.focus({ preventScroll: true }); next.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'instant' : 'smooth' }); }
    });
    column.append(count, cards, more); layout.append(panel, column); root.append(layout);
    function ensureMap() {
      if (map || mounted) return; mounted = true;
      if (!window.L) {
        surface.append(node('p', 'car-map-unavailable', 'Map unavailable. Every car is available beside it and in the listings below.'));
        return;
      }
      map = L.map(surface, { scrollWheelZoom: false, zoomControl: true, maxZoom: 13, minZoom: 0,
        fadeAnimation: !reduced(), zoomAnimation: !reduced(), markerZoomAnimation: !reduced() }).setView([39.5, -98], 4);
      const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      }).addTo(map);
      let tileErrors = 0;
      tiles.on('tileerror', () => {
        tileErrors++;
        tileFailure = ' Some map tiles could not load. All cars remain in the list; the Market atlas offers another view.';
        locationStatus();
      });
      tiles.on('loading', () => { tileErrors = 0; });
      tiles.on('load', () => { if (!tileErrors) tileFailure = ''; locationStatus(); });
      layer = L.layerGroup().addTo(map);
      // Group buckets use projected coordinates, so panning cannot change them.
      // Rebuilding on popup auto-pan would remove the popup just opened.
      map.on('zoomend', drawPins);
      const sizePopup = () => { if (activePopup) { activePopup.options.maxWidth = Math.max(120, Math.min(310, surface.clientWidth - 60)); activePopup.update(); } };
      map.on('popupopen', (e) => { activePopup = e.popup; sizePopup(); });
      map.on('popupclose', () => { activePopup = null; });
      new ResizeObserver(() => { if (!root.hidden && map) { map.invalidateSize({ pan: false }); sizePopup(); } }).observe(surface);
    }
    function locationStatus() {
      const n = cars.filter(located).length;
      status.textContent = n + (n === 1 ? ' car with coordinates' : ' cars with coordinates') + (cars.length - n ? ' · ' + (cars.length - n) + ' without a verified location; included in the list.' : '.') + tileFailure;
    }
    function fit() {
      if (!map) return;
      const places = cars.filter(located).map((c) => [c.lat, c.lng]);
      if (places.length) map.fitBounds(places, { padding: [76, 40], maxZoom: 10, animate: false });
    }
    function highlight(vin, scroll) {
      selected = vin;
      const index = cars.findIndex((c) => c.vin === vin);
      if (index >= limit) limit = Math.ceil((index + 1) / 8) * 8;
      renderCards();
      for (const pill of surface.querySelectorAll('.car-map-price')) pill.classList.remove('is-selected');
      const marker = markers.get(vin);
      if (marker && marker.getElement()) marker.getElement().querySelector('.car-map-price').classList.add('is-selected');
      if (scroll) {
        const card = [...cards.children].find((n) => n.dataset.carVin === vin);
        if (card) { card.focus({ preventScroll: true }); card.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'instant' : 'smooth' }); }
      }
    }
    function popup(group) {
      const box = node('div', 'car-location-popup');
      box.append(node('strong', null, group.length === 1 ? group[0].location : group.length + ' cars in this map area'),
        node('p', null, 'Approximate locations · asking prices'));
      for (const c of group) box.append(button(c.priceLabel + ' · ' + c.title + ' · ' + c.location, 'car-popup-car', () => {
        highlight(c.vin, true); map.closePopup();
      }));
      return box;
    }
    function drawPins() {
      if (!map || !layer) return;
      layer.clearLayers(); markers.clear();
      const groups = [];
      for (const c of cars.filter(located)) {
        const pt = map.project([c.lat, c.lng]);
        // Keep complete price pills apart, including neighboring grid cells.
        // Representatives stay at actual source coordinates; we never jitter
        // a pin into an invented dealer location.
        const group = groups.find((g) => Math.abs(g.point.x - pt.x) < 140 && Math.abs(g.point.y - pt.y) < 48);
        if (group) group.cars.push(c);
        else groups.push({ point: pt, cars: [c] });
      }
      for (const { cars: group } of groups) {
        const first = group[0], priced = group.filter((c) => typeof c.price === 'number' && Number.isFinite(c.price));
        const cheapest = priced.reduce((a, b) => !a || b.price < a.price ? b : a, null);
        const label = group.length > 1 ? group.length + ' · ' + (cheapest ? cheapest.priceLabel + '+' : 'Price unreported') : first.priceLabel;
        const pill = node('div', 'car-map-price' + (group.some((c) => c.vin === selected) ? ' is-selected' : ''), label);
        const marker = L.marker([first.lat, first.lng], {
          icon: L.divIcon({ className: 'car-price-marker', html: pill, iconSize: [110, 34], iconAnchor: [55, 17] }),
          keyboard: true, title: group.length > 1 ? group.length + ' cars in this map area, from ' + (cheapest ? cheapest.priceLabel : 'an unreported price') : first.title + ', ' + first.priceLabel + ', ' + first.location,
          alt: group.length > 1 ? group.length + ' cars in this map area' : first.title
        }).addTo(layer).bindPopup(() => popup(group), { maxWidth: Math.max(120, Math.min(310, surface.clientWidth - 60)) });
        for (const c of group) markers.set(c.vin, marker);
        marker.on('click', () => { if (group.length === 1) highlight(first.vin, false); });
      }
    }
    function showOnMap(c) {
      if (!map || !located(c)) return;
      map.stop();
      selected = c.vin; renderCards();
      map.setView([c.lat, c.lng], 11, { animate: false });
      drawPins();
      const marker = markers.get(c.vin); if (marker) marker.openPopup();
      surface.scrollIntoView({ block: 'center', behavior: reduced() ? 'instant' : 'smooth' });
      surface.focus({ preventScroll: true });
    }
    function renderCards() {
      const prior = document.activeElement;
      const focusVin = prior && prior.dataset && prior.dataset.focusVin;
      const focusAction = prior && prior.dataset && prior.dataset.focusAction;
      cards.replaceChildren();
      count.textContent = cars.length + ' cars · same filters and sort as your listings';
      for (const c of cars.slice(0, limit)) {
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
        const price = node('p', 'car-place-price', c.priceLabel); price.append(node('span', null, ' asking'));
        body.append(price, node('p', 'car-place-shipping', c.shipping));
        const facts = node('div', 'car-place-facts');
        facts.append(node('span', null, c.milesLabel), node('span', null, located(c) ? 'Approximate pin' : 'No verified location'));
        body.append(facts, node('p', 'car-place-dealer', c.dealer));
        if (c.flags) body.append(node('p', 'car-place-flags', c.flags));
        const actions = node('div', 'car-place-actions');
        const action = (label, name, fn) => { const b = button(label, 'car-text-button', fn); b.dataset.focusVin = c.vin; b.dataset.focusAction = name; return b; };
        actions.append(action('View car →', 'view', () => openCar(c.vin)));
        if (map && located(c)) actions.append(action('Show on map', 'map', () => showOnMap(c)));
        // Reuse the dashboard's shortlist / called / ruled-out control and
        // its existing state cycle rather than introducing a second save model.
        const star = starCar(c.vin);
        if (star) actions.append(star); body.append(actions); card.append(visual, body); cards.append(card);
      }
      more.hidden = limit >= cars.length;
      more.textContent = 'Show ' + Math.min(8, cars.length - limit) + ' more cars';
      if (!cars.length) cards.append(node('p', 'car-map-unavailable', 'No cars match these filters. Change the filters above to bring them back.'));
      if (focusVin && focusAction) {
        const restore = [...cards.querySelectorAll('[data-focus-vin]')].find((b) => b.dataset.focusVin === focusVin && b.dataset.focusAction === focusAction);
        if (restore) restore.focus({ preventScroll: true });
      }
    }
    return {
      update(next, nextScope) {
        cars = next;
        if (scope !== nextScope) { limit = 8; selected = null; }
        ensureMap(); fitButton.disabled = !map || !cars.some(located); renderCards(); locationStatus();
        if (map) { map.closePopup(); if (scope !== nextScope) fit(); drawPins(); }
        scope = nextScope;
      },
      resize() { if (map) map.invalidateSize({ pan: false }); },
    };
  }
  window.CarDiscovery = { create };
})();
