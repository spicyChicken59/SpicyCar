// Versioned lossless transport; shared by the page and its Node consumers.
// Decode the entire tree before exposing it to any ranking or absence logic.
(function (root) {
  'use strict';
  function decode(wire) {
    const invalid = () => { throw new Error('Malformed or unsupported SpicyCar snapshot transport'); };
    const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
    if (!object(wire)) invalid();
    if (!['format', 'version', 'schemas', 'data'].some((k) => Object.hasOwn(wire, k))) return wire;
    if (Object.keys(wire).sort().join(',') !== 'data,format,schemas,version' ||
        wire.format !== 'spicycar-sheet' || wire.version !== 1 || !Array.isArray(wire.schemas)) invalid();
    for (const keys of wire.schemas) {
      if (!Array.isArray(keys) || keys.some((k) => typeof k !== 'string') || new Set(keys).size !== keys.length) invalid();
    }
    function unpack(value) {
      if (Array.isArray(value)) {
        if (!value.length) invalid();
        if (value[0] === 0) return value.slice(1).map(unpack);
        if (value[0] !== 1 || !Number.isInteger(value[1]) || value[1] < 0 || value[1] >= wire.schemas.length) invalid();
        const keys = wire.schemas[value[1]];
        if (value.length !== keys.length + 2) invalid();
        // fromEntries preserves even a literal __proto__ as an own data key.
        return Object.fromEntries(keys.map((key, i) => [key, unpack(value[i + 2])]));
      }
      if (value === null || typeof value === 'string' || typeof value === 'boolean' ||
          (typeof value === 'number' && Number.isFinite(value))) return value;
      invalid();
    }
    const site = unpack(wire.data);
    if (!object(site)) invalid();
    return site;
  }
  const api = Object.freeze({ decode, parse: (text) => decode(JSON.parse(text)) });
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SpicyCarSheet = api;
})(globalThis);
