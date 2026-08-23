"""Auto Market Tracker project mark — generated on the family's 24-unit grid.

The chick's grammar, applied to a car face: rounded body, file-fold corner
top-right, two cream lights where the eyes sit, a cream plate where the beak
sits, one spice element on top (a tracking ping instead of the flame),
mirror nubs, wheels below. Flat fills only.
"""
import math

U = 24  # canvas in grid units

# geometry (units)
BODY = dict(x0=2.0, y0=7.0, x1=22.0, y1=20.5, r=4.5, cut=4.5)
WHEEL = dict(w=3.8, h=3.0, r=1.2, y=19.2, lx=5.0, rx=15.2)
MIRROR = dict(w=2.0, h=2.2, r=0.8, y=10.4, lx=0.6, rx=21.4)
LIGHT = dict(w=4.6, h=1.8, r=0.9, y=12.3, lx=5.0, rx=14.4)
PLATE = dict(w=6.0, h=1.7, r=0.85, y=16.3)
PING = dict(cx=9.2, cy=7.0, dot_r=1.05, dot_cy=6.45,
            a1_in=2.1, a1_out=3.25, a2_in=4.0, a2_out=5.1,
            ang0=-135, ang1=-45)


def rrect(x, y, w, h, r, fill):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}"/>')


def body_path(fill):
    b = BODY
    x0, y0, x1, y1, r, c = b["x0"], b["y0"], b["x1"], b["y1"], b["r"], b["cut"]
    return (f'<path d="M{x0 + r} {y0} H{x1 - c} L{x1} {y0 + c} V{y1 - r} '
            f'A{r} {r} 0 0 1 {x1 - r} {y1} H{x0 + r} '
            f'A{r} {r} 0 0 1 {x0} {y1 - r} V{y0 + r} '
            f'A{r} {r} 0 0 1 {x0 + r} {y0} Z" fill="{fill}"/>')


def fold_path(fill):
    b = BODY
    x1, y0, c = b["x1"], b["y0"], b["cut"]
    return (f'<path d="M{x1 - c} {y0} L{x1} {y0 + c} H{x1 - c} Z" '
            f'fill="{fill}"/>')


def pt(cx, cy, radius, deg):
    a = math.radians(deg)
    return cx + radius * math.cos(a), cy + radius * math.sin(a)


def arc_band(cx, cy, r_in, r_out, a0, a1, fill):
    x0o, y0o = pt(cx, cy, r_out, a0)
    x1o, y1o = pt(cx, cy, r_out, a1)
    x0i, y0i = pt(cx, cy, r_in, a0)
    x1i, y1i = pt(cx, cy, r_in, a1)
    f = lambda v: f"{v:.2f}"
    return (f'<path d="M{f(x0i)} {f(y0i)} L{f(x0o)} {f(y0o)} '
            f'A{r_out} {r_out} 0 0 1 {f(x1o)} {f(y1o)} '
            f'L{f(x1i)} {f(y1i)} '
            f'A{r_in} {r_in} 0 0 0 {f(x0i)} {f(y0i)} Z" fill="{fill}"/>')


def mark(body, wheels, details, ping_core, ping_outer, fold, knockout=False):
    """knockout=True renders lights/plate as holes (mono forms)."""
    p, e = PING, []
    e.append(rrect(WHEEL["lx"], WHEEL["y"], WHEEL["w"], WHEEL["h"], WHEEL["r"], wheels))
    e.append(rrect(WHEEL["rx"], WHEEL["y"], WHEEL["w"], WHEEL["h"], WHEEL["r"], wheels))
    e.append(rrect(MIRROR["lx"], MIRROR["y"], MIRROR["w"], MIRROR["h"], MIRROR["r"], body))
    e.append(rrect(MIRROR["rx"], MIRROR["y"], MIRROR["w"], MIRROR["h"], MIRROR["r"], body))
    e.append(body_path(body))
    e.append(fold_path(fold))
    e.append(arc_band(p["cx"], p["cy"], p["a1_in"], p["a1_out"], p["ang0"], p["ang1"], ping_core))
    e.append(arc_band(p["cx"], p["cy"], p["a2_in"], p["a2_out"], p["ang0"], p["ang1"], ping_outer))
    e.append(f'<circle cx="{p["cx"]}" cy="{p["dot_cy"]}" r="{p["dot_r"]}" fill="{ping_core}"/>')
    if not knockout:
        e.append(rrect(LIGHT["lx"], LIGHT["y"], LIGHT["w"], LIGHT["h"], LIGHT["r"], details))
        e.append(rrect(LIGHT["rx"], LIGHT["y"], LIGHT["w"], LIGHT["h"], LIGHT["r"], details))
        e.append(rrect((U - PLATE["w"]) / 2, PLATE["y"], PLATE["w"], PLATE["h"], PLATE["r"], details))
        inner = "".join(e)
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {U} {U}">{inner}</svg>'
    # mono: knock the lights/plate out of the whole drawing with a mask
    holes = (rrect(LIGHT["lx"], LIGHT["y"], LIGHT["w"], LIGHT["h"], LIGHT["r"], "black")
             + rrect(LIGHT["rx"], LIGHT["y"], LIGHT["w"], LIGHT["h"], LIGHT["r"], "black")
             + rrect((U - PLATE["w"]) / 2, PLATE["y"], PLATE["w"], PLATE["h"], PLATE["r"], "black"))
    inner = "".join(e)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {U} {U}">'
            f'<mask id="m"><rect width="{U}" height="{U}" fill="white"/>{holes}</mask>'
            f'<g mask="url(#m)">{inner}</g></svg>')


FORMS = {
    # body, wheels, details, ping core, ping outer, fold
    "amt-mark-color-light": mark("#165194", "#111F31", "#F4F7FB", "#D24100", "#FE825C", "#FE825C"),
    "amt-mark-color-dark":  mark("#4682CC", "#1B3E69", "#F4F7FB", "#FE825C", "#FFAC92", "#FFAC92"),
    "amt-mark-mono-cream":  mark("#F4F7FB", "#F4F7FB", None, "#F4F7FB", "#F4F7FB", "#F4F7FB", knockout=True),
    "amt-mark-mono-ink":    mark("#111F31", "#111F31", None, "#111F31", "#111F31", "#111F31", knockout=True),
}

TILE = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'<rect width="512" height="512" rx="148" fill="#F4F7FB"/>'
        f'<g transform="translate(96 104) scale(13.33)">'
        + FORMS["amt-mark-color-light"].split(">", 1)[1].rsplit("</svg>", 1)[0]
        + "</g></svg>")

if __name__ == "__main__":
    import pathlib
    out = pathlib.Path(__file__).resolve().parent
    out.mkdir(exist_ok=True)
    for name, svg in FORMS.items():
        (out / f"{name}.svg").write_text(svg)
    (out / "amt-tile.svg").write_text(TILE)
    print("written", list(FORMS) + ["amt-tile"])
