# Leaflet 1.9.4

Unmodified Leaflet JavaScript and CSS distributions, copied from the existing SpicyHome integration. BSD-2-Clause license is included in `LICENSE`. Source: https://github.com/Leaflet/Leaflet/tree/v1.9.4/dist

The consumer linter verifies the exact stylesheet SHA-256 before excluding this third-party distribution from SpicyChicken token linting. App overrides remain in `car-discovery.css` and are linted normally. This integration uses HTML price icons, so Leaflet marker images and layer-selector assets are not needed.

OpenStreetMap standard tiles are loaded only for the interactive map, with visible attribution. No key, geocoder, tracking API, or routing service is added. Vehicles come from the existing `data.json` snapshot.
