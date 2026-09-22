"""Lossless, deterministic JSON transport for the complete SpicyCar sheet.

Version 1 interns object key lists. Values stay JSON values; arrays are tagged
0, objects are tagged 1 followed by a schema index and one value per key.
Tagging *every* container avoids collisions with user strings/arrays/keys.
No listing selection, ordering, arithmetic, or retention happens here.
"""
import json
import math

FORMAT = "spicycar-sheet"
VERSION = 1


def encode_sheet(site):
    schemas, indices = [], {}

    def pack(value):
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Snapshot object keys must be strings")
            keys = tuple(sorted(value))
            if keys not in indices:
                indices[keys] = len(schemas)
                schemas.append(list(keys))
            return [1, indices[keys], *(pack(value[key]) for key in keys)]
        if isinstance(value, (list, tuple)):
            return [0, *(pack(item) for item in value)]
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        raise ValueError("Snapshot contains a non-JSON or nonfinite value")

    if not isinstance(site, dict):
        raise ValueError("Snapshot root must be an object")
    data = pack(site)
    return {"format": FORMAT, "version": VERSION, "schemas": schemas, "data": data}


def decode_sheet(wire):
    """Decode fully before returning; legacy plain JSON snapshots still work."""
    def invalid():
        raise ValueError("Malformed or unsupported SpicyCar snapshot transport")

    if not isinstance(wire, dict):
        invalid()
    # These names belong to the transport envelope, not the logical sheet.
    if not any(key in wire for key in ("format", "version", "schemas", "data")):
        return wire
    if (set(wire) != {"format", "version", "schemas", "data"}
            or wire["format"] != FORMAT
            or type(wire["version"]) is not int or wire["version"] != VERSION
            or not isinstance(wire["schemas"], list)):
        invalid()
    schemas = wire["schemas"]
    for keys in schemas:
        if (not isinstance(keys, list) or any(type(key) is not str for key in keys)
                or len(set(keys)) != len(keys)):
            invalid()

    def unpack(value):
        if isinstance(value, list):
            if not value or type(value[0]) is not int:
                invalid()
            if value[0] == 0:
                return [unpack(item) for item in value[1:]]
            if (value[0] != 1 or len(value) < 2 or type(value[1]) is not int
                    or not 0 <= value[1] < len(schemas)):
                invalid()
            keys = schemas[value[1]]
            if len(value) != len(keys) + 2:
                invalid()
            return {key: unpack(item) for key, item in zip(keys, value[2:])}
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        invalid()

    site = unpack(wire["data"])
    if not isinstance(site, dict):
        invalid()
    return site


def parse_sheet(text):
    return decode_sheet(json.loads(text))
