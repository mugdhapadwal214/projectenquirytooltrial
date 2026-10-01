"""
calculator.py — Calculation engine for the RM Estimator.

This is the only module that does math. Everything else (app.py,
meta.py, database.py) exists to feed this module clean data or to
expose it over HTTP.

CALCULATION LOGIC
------------------------------------------------------------------
1. Every part is reduced to a SHAPE (circle / ring / rectangle) with a
   thickness and a material — see meta.py for how each component type
   maps its inputs to a shape.

2. WEIGHT of any shape:
       volume_mm3 = area_mm2 x thickness_mm
       weight_kg  = volume_mm3 x 1e-9 [mm3 -> m3] x density_kg_per_m3

3. GROUPING: parts are grouped by (material, thickness), because that's
   what determines whether they can physically share one plate. Component
   TYPE is not part of the key by default — a dish end and a shell of the
   same material and thickness are just two outlines on one plate, and
   grouping them cuts the offcut. Pass combine=False to keep each type
   on its own plate.

4. NESTING (estimate, not real nesting software). Two modes:

   STANDARD PLATE — blanks are shelf-packed into whole stock plates of a
   fixed size (e.g. 1250 x 2500) and you pay for every plate you open,
   used or not. Each blank is also checked to see whether it fits the
   stock size at all, in either rotation. This is the realistic number.

   CONTINUOUS STRIP — plate bought cut to size. Blanks lie in one row;
   the plate is as wide as the widest blank and as long as the blanks
   plus gaps. This is the theoretical minimum material.

   In both modes each part's bounding box is computed (a circle's
   bounding box is a D x D square) and blanks are separated by a cutting
   gap. For the strip mode:

       gap          = CUT_GAP_FACTOR x thickness      (default 2 x t)
       plate_width  = widest part's bounding width
       plate_length = sum(bounding lengths) + gap x (n_parts - 1)

   The gap is the clearance left between adjacent blanks so the cutting
   head has room to run between them without undercutting the neighbour.
   It scales with thickness because thicker plate needs wider separation.
   It is applied (n-1) times — between parts only, not at the two outer
   plate edges.

5. SCRAP has two components. Kerf / cutting-process loss is deliberately
   NOT modelled — this tool estimates raw material only:
       nesting_scrap = plate_weight - sum(blank_weight)     [squaring
                       off round/irregular blanks against a rectangular
                       plate, now including the inter-part gaps]
       bore_scrap    = sum(blank_weight) - sum(net_weight)  [material
                       bored out, e.g. a flange's centre hole; zero for
                       shapes where blank == net]

6. PLATE-CUT COMPONENT COST is allocated to parts using their own material
   rate (Rs/kg). Different parts on the same plate may have different rates.
   Flanges are an exception: they are purchased finished at Rs/pc and are
   excluded from plate allocation and cutting. Their cost is quantity x rate.

       strip mode    : share by bounding LENGTH consumed (the part takes
                       the full plate width regardless)
       standard mode : share by bounding AREA (blanks share shelves
                       sideways, so width matters)
       rm_cost_i     = plate_share_i x rate_i

   The shares sum exactly to the plate weight, so no material is lost or
   double-counted in the allocation, and the gap and offcut are spread
   across the parts that made them necessary.
"""

import math

import cutting
import inventory
import nesting

MM3_TO_M3 = 1e-9

# Clearance between adjacent blanks, as a multiple of plate thickness.
CUT_GAP_FACTOR = 2.0


# ---------------------------------------------------------------- shapes --

def circle_area(d: float) -> float:
    """Area of a circle, mm^2, given diameter d in mm."""
    return math.pi / 4 * d ** 2


def ring_area(d: float, di: float) -> float:
    """Area of an annulus (ring), mm^2 — outer dia d, inner dia di."""
    di = di or 0
    return math.pi / 4 * (d ** 2 - di ** 2)


def rect_area(l: float, w: float) -> float:
    """Area of a rectangle, mm^2."""
    return l * w


def shape_area(shape: dict) -> float:
    kind = shape["kind"]
    if kind == "circle":
        return circle_area(shape["d"])
    if kind == "ring":
        return ring_area(shape["d"], shape.get("di"))
    if kind == "rect":
        return rect_area(shape["l"], shape["w"])
    raise ValueError(f"Unknown shape kind: {kind}")


def describe_shape(shape: dict) -> str:
    """Human-readable size string for a shape, so the user can see what the
    formula actually computed (e.g. the dish end development diameter)."""
    kind = shape["kind"]
    if kind == "circle":
        return f"\u00d8{shape['d']:.1f} mm"
    if kind == "ring":
        return f"\u00d8{shape['d']:.1f} mm (bore \u00d8{shape.get('di') or 0:.1f} mm)"
    if kind == "rect":
        # Width first, then length — same order as the "Plate size" column,
        # so a blank size and a plate size can be read side by side without
        # mentally transposing one of them.
        return f"{shape['w']:.0f} \u00d7 {shape['l']:.0f} mm"
    raise ValueError(f"Unknown shape kind: {kind}")


def bounding_box(shape: dict) -> tuple:
    """Return (width, length) of the smallest rectangle containing the shape."""
    kind = shape["kind"]
    if kind in ("circle", "ring"):
        return shape["d"], shape["d"]
    if kind == "rect":
        return shape["w"], shape["l"]
    raise ValueError(f"Unknown shape kind: {kind}")


# ---------------------------------------------------------- shell formula -

CONE_TYPES = ["Full cone (pointed)", "Frustum (truncated)"]


def cone_slant(height, r_large, r_small=0.0):
    """Slant height L — the one length that does not change from flat pattern
    to finished cone.
        full cone :  L = sqrt(H^2 + R^2)
        frustum   :  L = sqrt(H^2 + (R1 - R2)^2)
    """
    import math as _m
    h = float(height or 0)
    r1 = float(r_large or 0)
    r2 = float(r_small or 0)
    return _m.sqrt(h * h + (r1 - r2) ** 2)


def cone_development(height, d_large, d_small=0.0, thk=0.0, gores=1):
    """
    The flat pattern of a cone or a frustum, on the mean diameter.

    A full cone opens out into a sector of radius L and included angle
    theta = 2*pi*R/L. A frustum opens out into a ring sector between
    Ro = L*R1/(R1-R2) and Ri = Ro - L, with the same theta = 2*pi*R1/Ro.
    A cone whose theta comes to a full turn is a complete annulus, and the
    blank is then simply a disc or a ring.

    gores > 1 splits the pattern into that many equal pieces, which is how a
    cone too wide for the plate is actually made.

    Returns {"kind": ..., ...} for one piece, plus the numbers behind it.
    """
    import math as _m
    h = float(height or 0)
    t = float(thk or 0)
    # developed on the mean diameter, as a rolled shell is
    r1 = (float(d_large or 0) + t) / 2.0
    r2 = (float(d_small or 0) + t) / 2.0
    if r2 >= r1:
        r1, r2 = max(r1, r2), min(r1, r2)
    slant = cone_slant(h, r1, r2)
    if slant <= 0 or r1 <= 0:
        return {"shape": {"kind": "rect", "l": 0.0, "w": 0.0}, "slant": 0.0,
                "theta_deg": 0.0, "r_out": 0.0, "r_in": 0.0, "gores": 1}
    if r2 <= 0:                                   # pointed cone
        r_out, r_in = slant, 0.0
    else:                                         # truncated cone
        r_out = slant * r1 / (r1 - r2)
        r_in = r_out - slant
    theta = 2 * _m.pi * r1 / r_out                # radians, before splitting
    gores = max(1, int(gores or 1))
    seg = theta / gores

    if gores == 1 and theta >= 2 * _m.pi - 1e-9:  # a full turn: disc or ring
        shape = ({"kind": "circle", "d": 2 * r_out} if r_in <= 0
                 else {"kind": "ring", "d": 2 * r_out, "di": 2 * r_in})
    else:
        w, l = sector_bbox(r_out, r_in, seg)
        shape = {"kind": "rect", "l": l, "w": w}
    return {"shape": shape, "slant": slant, "theta_deg": _m.degrees(theta),
            "r_out": r_out, "r_in": r_in, "gores": gores,
            "seg_deg": _m.degrees(seg)}


def sector_bbox(r_out, r_in, angle):
    """Smallest rectangle that holds a ring sector of the given angle, with the
    sector laid symmetrically about the x axis (the way it is cut)."""
    import math as _m
    a = min(float(angle or 0), 2 * _m.pi)
    if a <= 0:
        return 0.0, 0.0
    if a >= 2 * _m.pi - 1e-9:
        return 2 * r_out, 2 * r_out
    half = a / 2.0
    xs = [r_out * _m.cos(half), r_out, r_in * _m.cos(half)]
    ys = [r_out * _m.sin(half), -r_out * _m.sin(half), 0.0]
    if half >= _m.pi / 2:                          # the sector passes straight up
        xs.append(-r_out)
        ys.extend([r_out, -r_out])
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return abs(y1 - y0), abs(x1 - x0)              # (width, length)


def cone_gores_needed(height, d_large, d_small, thk, plate_w, plate_l):
    """How many equal pieces the pattern has to be cut into to fit the plate."""
    for n in range(1, 25):
        dev = cone_development(height, d_large, d_small, thk, n)
        w, l = bounding_box(dev["shape"])
        if fits_on(w, l, plate_w, plate_l):
            return n
    return 0                                       # will not fit however it is split


def shell_blank_plate(id_: float, thk: float, ht: float) -> tuple:
    """
    Blank plate size for a rolled cylindrical shell. Returns (width, length).

        bpw_shell = pi x (Di + t)        [width  — the unrolled perimeter]
        bpl_shell = h                    [length — the shell height]

    Di is the INSIDE diameter and t the plate thickness, so (Di + t) is the
    MEAN diameter — the neutral axis of the wall. Rolling stretches the outer
    fibre and compresses the inner one, and the mean diameter is the line that
    neither stretches nor compresses, so its circumference is the true
    developed length. Using Di would come out short by pi x t and using the
    outer diameter would come out long by the same amount.

    Rolling removes no material, so this blank is also the finished part's
    material content — blank == net for a shell.
    """
    return math.pi * ((id_ or 0) + (thk or 0)), (ht or 0)


# ------------------------------------------------------- dish end formulas -

def outer_from_inner(id_: float, thk: float) -> float:
    """
    Convert inner diameter to outer diameter: OD = ID + 2t.

    The dish end blank formulas below are all written in terms of the
    OUTER diameter D. The user enters ID, so the conversion happens here,
    once, explicitly — rather than being buried inside a formula where a
    wrong assumption would silently shift every blank size by 2t.
    """
    return (id_ or 0) + 2 * (thk or 0)


def dish_end_development_diameter(od: float, sf: float, knuckle_r: float,
                                   head_type: str, formula: str = None) -> float:
    """
    Development (blank) diameter for a dish end, by head type.

    Ellipsoidal and Hemispherical each have one formula (no alternates
    given for these, so `formula` is ignored for them):
        Ellipsoidal (2:1):    D x 1.15 + 2 x SF
        Hemispherical:        pi x D / 2                (no SF, no KR term)

    Torispherical has THREE competing approximations in circulation —
    DIN 28011 itself defines the head's proportions (Crown radius = D,
    Knuckle radius = 0.1D, Straight flange approx 3.5t) but does not
    publish one official blank-diameter formula; unrolling the true
    curved surface doesn't reduce to a single clean formula, so different
    fabricators/references approximate it differently:
        "Standard (1.11D+2SF)"      : D x 1.11 + 2 x SF   (from the
                                        original reference PDF)
        "DIN approx (1.09D+2SF)"    : D x 1.09 + 2 x SF   (a commonly
                                        cited alternative constant)
        "Explicit KR (D+D/42+2.3KR+2SF)": D + D/42 + 2.3 x KR + 2 x SF
                                        — uses knuckle radius directly.
                                        Only found in one secondary
                                        source during research, not
                                        corroborated against a primary
                                        DIN/manufacturer spec — treat as
                                        a rough rule of thumb.

    od (D)        : OUTER diameter, mm — derived from the entered ID via
                    outer_from_inner()
    sf (SF)       : straight flange length, mm (ignored for Hemispherical)
    knuckle_r (KR): knuckle radius, mm — only used by the "Explicit KR"
                    Torispherical formula; otherwise captured but unused
    head_type     : "Torispherical" | "Ellipsoidal" | "Hemispherical"
    formula       : which Torispherical approximation to use — only
                    meaningful when head_type == "Torispherical"
    """
    sf = sf or 0
    knuckle_r = knuckle_r or 0

    if head_type == "Ellipsoidal":
        return od * 1.15 + 2 * sf
    if head_type == "Hemispherical":
        return math.pi * od / 2
    if head_type == "Torispherical":
        formula = formula or "Standard (1.11D+2SF)"
        if formula.startswith("Standard"):
            return od * 1.11 + 2 * sf
        if formula.startswith("DIN approx"):
            return od * 1.09 + 2 * sf
        if formula.startswith("Explicit KR"):
            return od + od / 42 + 2.3 * knuckle_r + 2 * sf
        raise ValueError(f"Unknown Torispherical formula: {formula}")

    raise ValueError(f"Unknown dish end head type: {head_type}")


# --------------------------------------------------------------- weight ---

def weight_kg(area_mm2: float, thickness_mm: float, density_kg_m3: float) -> float:
    volume_mm3 = area_mm2 * thickness_mm
    return volume_mm3 * MM3_TO_M3 * density_kg_m3


# ------------------------------------------------------------- grouping ---

def _numeric_part(part: dict, schemas: dict) -> dict:
    """
    A copy of `part` with every field the schema calls numeric turned into a
    float. Blank stays blank, so "not filled in" is still distinguishable from
    zero and the missing-field check keeps working.
    """
    sch = schemas.get(part.get("_type"))
    if not sch:
        return part
    out = dict(part)
    for f in sch["fields"]:
        if not f.get("numeric"):
            continue
        v = out.get(f["key"])
        if v is None or v == "":
            continue
        try:
            out[f["key"]] = float(v)
        except (TypeError, ValueError):
            raise ValueError(
                f"{part.get('_type', 'Part')} "
                f"{part.get('part_no') or part.get('part_name') or ''}: "
                f"{f['label']} must be a number, not {v!r}.")
    return out


def group_key(part: dict, combine: bool) -> tuple:
    """
    What decides whether two parts can share one plate.

    Always (material, thickness) — you cannot cut a 5 mm dish end out of the
    same plate as an 8 mm shell, and you cannot cut mild steel out of a
    stainless plate.

    Component TYPE is deliberately NOT part of the key when combine=True. A
    dish end and a shell of the same material and thickness are just two
    outlines on one plate; the cutting machine has no opinion about which
    vessel part they later become. Grouping them together is what lets one
    plate serve both and cuts the offcut. Set combine=False to keep each
    component type on its own plate, which is what you want if the two
    components are made at different times or by different sub-vendors.

        THICKNESS IS NORMALISED TO A NUMBER FIRST, and that is not a detail.
    A row whose thickness arrived from the BOM as the text "3" and a row
    carrying the number 3.0 produced two DIFFERENT keys, so parts that belong
    on one plate were split across two - same material, same thickness, two
    plates, two lots of scrap and two lots of cost. Recalculating appeared to
    fix it only because passing through the part form turned the text into a
    number. Worse, a group whose first row held text crashed outright on
    `CUT_GAP_FACTOR * thickness`.
    """
    if combine:
        return (part["material"], _thk_of(part))
    return (part["material"], _thk_of(part), part.get("_type", ""))


def _thk_of(part: dict) -> float:
    """
    Thickness as a number, whatever the page sent.

    Rounded to 3 decimals so 3.0 and 3.0000001 are the same plate, which is
    true of any real sheet and stops floating point splitting a group.
    """
    try:
        return round(float(part.get("thk") or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def group_parts(parts: list, combine: bool = True) -> dict:
    """Group parts into plate groups. Returns {key: [parts]} preserving order."""
    groups = {}
    for p in parts:
        groups.setdefault(group_key(p, combine), []).append(p)
    return groups


# -------------------------------------------------------------- nesting ---

def fits_on(w: float, l: float, plate_w: float, plate_l: float) -> bool:
    """Does a w x l blank fit a plate_w x plate_l sheet, in either rotation?"""
    return (w <= plate_w and l <= plate_l) or (l <= plate_w and w <= plate_l)


def _all_circles(boxes, kinds):
    """
    True when every blank waiting to be placed is a disc.

    `boxes` are (w, l, part_idx); `kinds` maps part_idx to its blank shape
    kind, since the box tuple has already thrown the shape away.
    """
    return bool(boxes) and all(kinds.get(b[2]) == "circle" for b in boxes)


def _nest_circles(boxes, plate_w, plate_l, gap, edge=0.0):
    """
    Nest circular blanks properly, and hand back the shape shelf_pack returns
    so nothing downstream has to know which packer ran.

    Every layout has already been checked for overlap inside nesting.nest_all;
    a plausible-looking packing that destroys parts is worse than none.
    """
    # A circular blank's box is square, so either side is its diameter.
    diams = [float(b[0]) for b in boxes]
    plates, left = nesting.nest_all(diams, gap, plate_w, plate_l, edge)

    # Match placements back to the original blanks by diameter, so each keeps
    # the part index it belongs to.
    pool = list(range(len(boxes)))
    layouts, used_area = [], 0.0
    for pl in plates:
        items = []
        for (x, y, d) in pl["placed"]:
            hit = next((i for i in pool
                        if abs(float(boxes[i][0]) - d) < nesting.SAME_D_TOL), None)
            if hit is None:
                continue
            pool.remove(hit)
            items.append({"key": boxes[hit][2], "x": x - d / 2.0,
                          "y": y - d / 2.0, "w": d, "l": d,
                          "cx": x, "cy": y, "d": d, "rotated": False})
            used_area += math.pi * (d / 2.0) ** 2
        layouts.append({"items": items,
                        "shelves": _nested_shelves(items, plate_w),
                        "layout_name": pl.get("layout", "")})

    return {
        "plates": len(layouts),
        "shelves": len(layouts),
        "used_area_mm2": used_area,
        "oversize": [boxes[i] for i in pool],
        "layouts": layouts,
        "nested": True,
    }


def _nested_shelves(items, plate_w):
    """
    The pseudo-shelf a nested plate presents to inventory.free_rects().

    What is genuinely reclaimable from a nested plate is clean rectangle, NOT
    the gaps between circles - those are curved slivers no future part can be
    cut from, and reporting them as stock would put phantom offcut in the
    rack. free_rects() reads a shelf list, so one shelf covering the packed
    band describes the plate correctly.

    `used` is where the blanks actually stop, not the full plate width. The
    band of clear steel past the last column is exactly as real and as
    rackable as the strip past the last row, and claiming the shelf runs the
    whole width threw it away - on a 2500 plate holding five 439.6 discs
    that is a 250 mm strip the full height of the band, written off as scrap
    every time.
    """
    if not items:
        return [{"y": 0.0, "h": 0.0, "used": 0.0}]
    band = max(i["y"] + i["l"] for i in items)
    used = min(max(i["x"] + i["w"] for i in items), plate_w)
    return [{"y": 0.0, "h": band, "used": used}]


def _needs_true_shape(boxes, kinds) -> bool:
    """
    Whether this group has to go through the true-shape packer.

    Two reasons, and either on its own is enough:

      a ring is present   its bore is a hole in the middle of the blank that
                          gets cut out regardless. Any part small enough to
                          sit inside it costs no plate at all, so a bore must
                          never be left empty while a part that fits it is
                          being given open steel. Only the true-shape packer
                          can see a bore; the shelf packer sees a square.

      discs and plates    a rectangle small enough to live in the waste
                          between four discs is free plate too, and the shelf
                          packer cannot see that space either because each
                          disc has already claimed its whole bounding square.
    """
    ks = {kinds.get(b[2]) for b in boxes}
    if "ring" in ks:
        return True
    return "circle" in ks and "rect" in ks


# Above this many pieces the true-shape packer is too slow to sit behind a
# button, so the group falls back to shelf packing. Nothing is wrong with the
# answer it would give; it just is not worth the wait.
#
# This matters more than it looks: the app re-estimates 400 ms after every
# keystroke, so this cost is paid on every character typed into the table,
# not once per Calculate press.
#
# Measured on a 1250 x 2500 plate, dish ends plus small pads, after the row
# prefilter and the ring guard:
#   80 pieces  ~0.5 s   120 pieces  ~0.9 s   160 pieces  ~2.1 s   240 pieces  ~7 s
# (before those two fixes: 4 s, 3.5 s, 17 s and over a minute.)
# 200 is the ceiling because of the per-keystroke re-estimate above.
MIXED_NEST_LIMIT = 1500


def _blank_spec(detail: dict) -> tuple:
    """The part's blank as nesting.py wants it: true outline, not its box."""
    kind = detail.get("shape_kind")
    if kind == "circle":
        return ("circle", float(detail["shape_d"]))
    if kind == "ring":
        return ("ring", float(detail["shape_d"]), float(detail.get("shape_di") or 0.0))
    return ("rect", float(detail["blank_w"]), float(detail["blank_l"]))


def _nest_mixed(boxes, parts_detail, plate_w, plate_l, gap, edge=0.0):
    """
    Nest discs and rectangles onto the same plates, true shape against true
    shape, and hand back what shelf_pack hands back.

    WHY
        Shelf packing gives every blank its bounding box, so a disc claims a
        square and the four corners around it are written off. Put a small
        plate and a disc in the same group and the packer cannot see that the
        plate fits in the waste between four discs, or in the strip left at
        the end of a row - it opens another sheet instead.

        The packer this calls tries the holes between discs before it tries
        open steel, so a part small enough to live in the waste costs no
        extra plate at all.

    Every layout has already passed nesting.check_shapes(); a packing that
    merely looks plausible is worse than none, because it becomes a cutting
    plan that destroys parts.
    """
    specs = [_blank_spec(parts_detail[b[2]]) for b in boxes]
    plates, left = nesting.nest_shapes_all(specs, gap, plate_w, plate_l, edge)

    layouts, used_area = [], 0.0
    for pl in plates:
        items = []
        # keys[k] is the index in `specs` (and so in `boxes`) that placed[k]
        # came from - built alongside the placements, so a piece can never be
        # drawn or costed as the wrong part.
        for shape, idx in zip(pl["placed"], pl["keys"]):
            key = boxes[idx][2]
            if shape[0] in ("circle", "ring"):
                cx, cy, d = shape[1], shape[2], shape[3]
                items.append({"key": key, "x": cx - d / 2.0, "y": cy - d / 2.0,
                              "w": d, "l": d, "rotated": False})
            else:
                cx, cy, w, h = shape[1], shape[2], shape[3], shape[4]
                items.append({"key": key, "x": cx - w / 2.0, "y": cy - h / 2.0,
                              "w": w, "l": h,
                              "rotated": abs(w - specs[idx][1]) > 1e-6})
            used_area += nesting.shape_area(shape)
        layouts.append({"items": items,
                        "shelves": _nested_shelves(items, plate_w),
                        "layout_name": "true-shape (mixed)"})

    return {
        "plates": len(layouts),
        "shelves": len(layouts),
        "used_area_mm2": used_area,
        "oversize": [boxes[i] for i in left],
        "layouts": layouts,
        "nested": True,
    }


def _pack_group(pending, parts_detail, horiz, vert, gap, kinds):
    """
    Pack one material/thickness group onto plates of the given size.

    Which packer runs depends only on the shapes present:
      all discs      the hex/inline grid, optimal for equal circles
      discs + flats  true shape against true shape, so a small plate can take
                     the waste between four discs or a ring's bore
      all flats      shelf packing, which is what rectangles want anyway
    """
    if not pending:
        return {"plates": 0, "shelves": 0, "used_area_mm2": 0.0,
                "oversize": [], "layouts": []}
    if _all_circles(pending, kinds):
        return _nest_circles(pending, horiz, vert, gap)
    if _needs_true_shape(pending, kinds) and len(pending) <= MIXED_NEST_LIMIT:
        return _nest_mixed(pending, parts_detail, horiz, vert, gap)
    return shelf_pack(pending, horiz, vert, gap)


def _smaller_stock(horiz, vert, stock_sizes):
    """
    Stock sizes worth trying for a group, smallest first.

    Never larger than the size selected for the run - that is the biggest sheet
    the shop is willing to buy, and quietly ordering something bigger because
    it nests better is not a decision this app gets to make.
    """
    out = {(horiz, vert)}
    for p in stock_sizes or []:
        try:
            w, l = float(p["width"]), float(p["length"])
        except (KeyError, TypeError, ValueError):
            continue
        h, v = (l, w) if l >= w else (w, l)
        if h <= horiz + 1e-6 and v <= vert + 1e-6:
            out.add((h, v))
    return sorted(out, key=lambda hv: hv[0] * hv[1])


def shelf_pack(boxes: list, plate_w: float, plate_l: float, gap: float) -> dict:
    """
    Pack a set of blanks into standard plates and report where each one landed.

    The packing is MaxRects (best-short-side-fit) with free rotation of every
    blank. It keeps a list of the empty rectangles still available on each
    plate, places each blank in the one that wastes least, and splits what is
    left. Shelf packing — the way this used to work — could only stack full-width
    strips, so a plate with a 994 mm strip down one side had to leave it empty
    even when a blank would have fitted there turned 90 degrees. That strip is
    now used, which is exactly the "the whole plate surface should be utilised"
    case, and the leftovers it reports are the real empty rectangles rather
    than a guess from the shelf heights.

    This is still an ESTIMATE, not real nesting: blanks are placed as upright
    rectangles, never at an angle and never interlocked. True nesting software
    will generally do at least as well, so treat the plate count as an upper
    bound you are unlikely to exceed.

    boxes : list of (w, l, key) — one per physical piece. `key` identifies which
            part the piece belongs to.

    Returns {plates, shelves, used_area_mm2, oversize, layouts}
      layouts  = one entry per plate: {"items": [...], "free": [...], "shelves": [...]}
      oversize = pieces that do not fit the plate in any rotation
    """
    oversize = [b for b in boxes if not fits_on(b[0], b[1], plate_w, plate_l)]
    usable = [b for b in boxes if fits_on(b[0], b[1], plate_w, plate_l)]

    # Biggest first: the awkward pieces get the run of an empty plate, and the
    # small ones drop into what they leave behind.
    order = sorted(usable, key=lambda b: (-max(b[0], b[1]) * min(b[0], b[1]),
                                          -max(b[0], b[1])))
    plates = []                      # each: {"items": [...], "free": [rects]}

    def _place(plate, w, l, key):
        """Best short side fit, either way round. Returns True when placed."""
        best = None
        for (pw, ph, rot) in ((w, l, False), (l, w, True)):
            if pw == ph and rot:
                continue             # a square does not need a second try
            for i, fr in enumerate(plate["free"]):
                need_w = pw + (gap if fr["x"] + pw < plate_w - 1e-9 else 0)
                need_h = ph + (gap if fr["y"] + ph < plate_l - 1e-9 else 0)
                if fr["w"] + 1e-9 < need_w or fr["h"] + 1e-9 < need_h:
                    continue
                leftover = (min(fr["w"] - need_w, fr["h"] - need_h),
                            max(fr["w"] - need_w, fr["h"] - need_h))
                if best is None or leftover < best[0]:
                    best = (leftover, i, pw, ph, rot, need_w, need_h)
        if best is None:
            return False
        _lo, idx, pw, ph, rot, need_w, need_h = best
        fr = plate["free"][idx]
        x, y = fr["x"], fr["y"]
        plate["items"].append({"key": key, "x": x, "y": y, "w": pw, "l": ph, "rotated": rot})
        _split_free(plate["free"], {"x": x, "y": y, "w": need_w, "h": need_h})
        _prune_free(plate["free"])
        return True

    for (w, l, key) in order:
        if any(_place(pl, w, l, key) for pl in plates):
            continue
        plate = {"items": [], "free": [{"x": 0.0, "y": 0.0, "w": plate_w, "h": plate_l}]}
        _place(plate, w, l, key)
        plates.append(plate)

    for pl in plates:                                  # what inventory harvests
        pl["free"] = [f for f in pl["free"] if f["w"] > 0 and f["h"] > 0]
        pl["shelves"] = _free_as_shelves(pl["free"])

    used_area = sum(w * l for (w, l, _k) in usable)
    return {
        "plates": len(plates),
        "shelves": sum(len(p["shelves"]) for p in plates),
        "used_area_mm2": used_area,
        "oversize": oversize,
        "layouts": plates,
    }


def _split_free(free, used):
    """MaxRects split: every free rectangle the placed blank touches is cut into
    the pieces of itself that the blank does not cover."""
    ux0, uy0 = used["x"], used["y"]
    ux1, uy1 = ux0 + used["w"], uy0 + used["h"]
    out = []
    for fr in free:
        fx0, fy0 = fr["x"], fr["y"]
        fx1, fy1 = fx0 + fr["w"], fy0 + fr["h"]
        if ux0 >= fx1 - 1e-9 or ux1 <= fx0 + 1e-9 or uy0 >= fy1 - 1e-9 or uy1 <= fy0 + 1e-9:
            out.append(fr)                              # no overlap
            continue
        if ux0 > fx0 + 1e-9:
            out.append({"x": fx0, "y": fy0, "w": ux0 - fx0, "h": fr["h"]})
        if ux1 < fx1 - 1e-9:
            out.append({"x": ux1, "y": fy0, "w": fx1 - ux1, "h": fr["h"]})
        if uy0 > fy0 + 1e-9:
            out.append({"x": fx0, "y": fy0, "w": fr["w"], "h": uy0 - fy0})
        if uy1 < fy1 - 1e-9:
            out.append({"x": fx0, "y": uy1, "w": fr["w"], "h": fy1 - uy1})
    free[:] = [f for f in out if f["w"] > 1e-6 and f["h"] > 1e-6]


def _prune_free(free):
    """Drop free rectangles wholly inside another one."""
    keep = []
    for i, a in enumerate(free):
        inside = False
        for j, b in enumerate(free):
            if i == j:
                continue
            if (b["x"] - 1e-9 <= a["x"] and b["y"] - 1e-9 <= a["y"] and
                    b["x"] + b["w"] + 1e-9 >= a["x"] + a["w"] and
                    b["y"] + b["h"] + 1e-9 >= a["y"] + a["h"] and
                    (b["w"] * b["h"] > a["w"] * a["h"] or j < i)):
                inside = True
                break
        if not inside:
            keep.append(a)
    free[:] = keep


def _free_as_shelves(free):
    """The old shelf shape, kept so anything still reading it keeps working."""
    return [{"y": f["y"], "h": f["h"], "used": f["x"]} for f in free]


# -------------------------------------------------------------- estimate --

def plate_key(material, thickness) -> str:
    """The key a per-group plate choice is stored under."""
    return f"{material}|{float(thickness):g}"


def estimate_group(parts: list, schemas: dict, density: float,
                   standard_plate: dict = None, cut_cfg: dict = None,
                   stock_sizes: list = None, auto_plate: bool = True,
                   remnants: list = None, plate_override: dict = None) -> dict:
    """
    Plate size, weights, scrap and cost for one plate group.

    Two costing modes:

    CONTINUOUS STRIP (standard_plate is None)
        Plate bought cut to size. Blanks sit in one row; the plate is as wide
        as the widest blank and as long as the blanks plus gaps. This is the
        theoretical minimum material, and there is only ever one plate, so
        cost is allocated once across the whole group.

    STANDARD PLATE (standard_plate = {"width": W, "length": L})
        Plate bought in a fixed stock size. Blanks are packed into whole
        plates and you pay for every plate opened, used or not.

        Costing here is done PER PLATE, not per group. Each plate's weight is
        split only among the parts actually cut from it, so a part carries the
        offcut of its own plate and nothing else. That makes each plate a
        self-contained costing unit: its scrap and its cost stand on their own
        and can be checked against a real cutting sheet.

        The trade-off is worth knowing: which plate a part lands on is decided
        by the packing heuristic, so two identical parts can be charged
        differently if one ends up on a fuller plate than the other. The
        per-plate figures are right for the plate; treat a single part's cost
        as an allocation, not a quotation.

        Any blank too big for the stock plate in either rotation is reported
        in `oversize` and given its own pseudo-plate, costed on its own
        bounding rectangle, so the total is never silently understated.
    """
    cut_cfg = cut_cfg or {}
    remnants = remnants or []
    # Same normalisation as the group key. Everything downstream multiplies
    # this - cut gap, weight, cost - so it must be a number, not whatever the
    # page happened to send.
    thickness = _thk_of(parts[0])
    gap = CUT_GAP_FACTOR * thickness

    boxes = []               # one bounding box per physical piece
    parts_detail = []
    sum_blank_area = 0.0
    sum_net_area = 0.0

    for part_idx, p in enumerate(parts):
        schema = schemas[p["_type"]]
        qty = int(p.get("qty") or 1)
        # A cone cut in gores is several blanks per part; everything else is one.
        per_part = 1
        if schema.get("pieces"):
            try:
                per_part = max(1, int(schema["pieces"](p) or 1))
            except Exception:
                per_part = 1
        pieces = qty * per_part
        rate = float(p.get("rate") or 0)
        blank = schema["blank_shape"](p)
        net = schema["net_shape"](p)
        blank_area = shape_area(blank)
        net_area = shape_area(net)
        box_w, box_l = bounding_box(blank)

        unit_blank_wt = weight_kg(blank_area, thickness, density)
        unit_net_wt = weight_kg(net_area, thickness, density)
        # The blank's own area, not the square it happens to sit in. A disc is
        # pi/4 = 78.5% of its bounding box, so charging the box overstated
        # every circular part by a quarter and made good nesting look pointless.
        unit_bbox_wt = weight_kg(blank_area, thickness, density)
        cut = cutting.summarise(blank, pieces, cut_cfg)
        label = p.get("part_name") or p.get("head_type") or p.get("part_no") or "part"

        fits = True
        if standard_plate:
            fits = fits_on(box_w, box_l, standard_plate["width"],
                           standard_plate["length"])

        parts_detail.append({
            "row_id": p.get("_row"),      # echoed back so the UI can match the
                                          # result to the exact input row, even
                                          # when two rows share a part number
            "component_type": p.get("_type", ""),
            "label": label,
            "part_no": p.get("part_no") or "",
            "qty": qty,
            "pieces": pieces,             # blanks actually cut (gores x qty)
            "pieces_per_qty": per_part,
            "rate": rate,
            "blank_size": describe_shape(blank),
            "blank_w": box_w,
            "blank_l": box_l,
            "fits_standard": fits,
            "shape_kind": blank["kind"],          # "circle" | "ring" | "rect"
            "shape_d": blank.get("d"),             # for circle/ring, so the
            "shape_di": blank.get("di"),           # diagram can draw the true
                                                    # outline, not just its box
            "bbox_size": describe_shape(blank),
            # Cutting scales with perimeter and pierce count, not with area,
            # so it is tracked separately from material all the way through.
            "perimeter_mm": cut["perimeter_mm"],
            "pierces_each": cut["pierces_each"],
            "cut_length_m": cut["cut_length_m"],
            "pierces": cut["pierces"],
            "cut_cost": cut["cut_cost"],
            "unit_bbox_area_mm2": blank_area,
            "unit_blank_area_mm2": blank_area,
            "unit_net_area_mm2": net_area,
            "unit_bbox_weight_kg": unit_bbox_wt,
            "unit_blank_weight_kg": unit_blank_wt,
            "unit_net_weight_kg": unit_net_wt,
            "bbox_weight_kg": unit_bbox_wt * pieces,
            "line_blank_weight_kg": unit_blank_wt * pieces,
            "line_net_weight_kg": unit_net_wt * pieces,
            # A part costs its own bounding box of material — independent of
            # which plate it lands on, so the same part always costs the same.
            # Allocation basis. The plate actually bought is shared out across
            # the parts in proportion to the metal each really occupies, so the
            # part costs add up to the plate cost exactly. Charging each part
            # only its own outline would leave the gaps between blanks unpaid
            # for, and they are a real part of what the plate cost.
            "plate_share_kg": unit_bbox_wt * pieces,
            "rm_cost": unit_bbox_wt * pieces * rate,
            "_length": box_l * pieces,             # strip-mode allocation basis
            "_area": blank_area * pieces,          # plate-mode allocation basis
            "blank_kind": blank["kind"],        # lets the nester spot discs
        })

        for _ in range(pieces):
            boxes.append((box_w, box_l, part_idx))
            sum_blank_area += blank_area
            sum_net_area += net_area

    n = len(boxes)
    blank_wt = weight_kg(sum_blank_area, thickness, density)
    net_wt = weight_kg(sum_net_area, thickness, density)

    result = {
        "material": parts[0]["material"],
        "thickness": thickness,
        "part_count": n,
        "component_types": sorted({p.get("_type", "") for p in parts}),
        "parts_detail": parts_detail,
        "cut_gap_mm": gap,
        "blank_weight_kg": blank_wt,
        "net_weight_kg": net_wt,
    }

    # Offcut in the rack gets used before anyone buys new plate.
    #
    # BEST FIT, NOT LARGEST FIRST.
    #
    # This used to take the biggest offcut in the rack each time. One small
    # bracket would therefore open a 2.85 m2 sheet, which then went back to the
    # rack as an awkward L that no future job could use, while a 0.28 m2 piece
    # that would have taken the bracket exactly sat untouched. The rack filled
    # up with big sheets ruined by one cut.
    #
    # Each round now tries EVERY remnant and takes the one that leaves the
    # least material behind. That naturally works the small pieces off first
    # and keeps the big clean sheets whole, which is what a storeman does by
    # hand. Ties go to the smaller piece for the same reason.
    #
    # Cost is O(remnants^2) calls to shelf_pack, which is nothing at rack
    # sizes - and the alternative is buying plate that did not need buying.
    #
    # This runs BEFORE the standard-plate/cut-to-size split below, and for
    # BOTH modes — offcut should be tried first regardless of whether the
    # leftover requirement then gets bought as a stock plate or cut to size.
    remnant_rows = []
    harvested_from_remnants = []
    pending = list(boxes)
    available = list(remnants)
    # Needed here as well as further down: the offcut trial below now uses the
    # same packer chooser as a stock plate, and that chooser has to know which
    # blanks are round.
    _kinds = {i: (d.get("blank_kind") or "") for i, d in enumerate(parts_detail)}

    while pending and available:
        best = None
        for rem in available:
            rh, rv = (max(rem["width"], rem["length"]),
                      min(rem["width"], rem["length"]))
            # SAME PACKER AS A STOCK PLATE.
            #
            # This was the one place still calling shelf_pack directly, so
            # every blank got its bounding box when cut from the rack: a disc
            # claimed a square, and a ring's bore was solid metal. An offcut is
            # smaller than a plate, so that waste matters MORE there - a rack
            # piece that could have taken the parts was judged too small, and a
            # whole new plate was bought instead.
            trial = _pack_group(pending, parts_detail, rh, rv, gap, _kinds)
            if not trial["layouts"]:
                continue
            first = trial["layouts"][0]
            if not first["items"]:
                continue
            used = sum(it["w"] * it["l"] for it in first["items"])
            waste = rh * rv - used
            # Least material left behind; then the smaller piece; then the id,
            # so the same rack always gives the same answer rather than
            # depending on dict ordering.
            score = (round(waste, 3), round(rh * rv, 3), str(rem.get("id", "")))
            if best is None or score < best[0]:
                best = (score, rem, first, rh, rv)
        if best is None:
            break

        _score, rem, first, rh, rv = best
        available.remove(rem)
        remnant_rows.append((rem, first, rh, rv))
        # Rebuild `pending` as whatever this remnant could not take.
        counts = {}
        for it in first["items"]:
            counts[it["key"]] = counts.get(it["key"], 0) + 1
        leftover = []
        for (bw_, bl_, k) in pending:
            if counts.get(k, 0) > 0:
                counts[k] -= 1
            else:
                leftover.append((bw_, bl_, k))
        pending = leftover

    def _plate_row(number, pieces, plate_area, plate_wt, spec,
                   source="stock", remnant_id=None, sheet_w=None, sheet_l=None):
        """Cost one plate against only the parts cut from it.

        pieces: raw per-piece placements for this plate, straight from
        shelf_pack — [{"key","x","y","w","l","rotated"}, ...], one entry
        per physical piece (not grouped by part). Used both to build the
        aggregated per-part summary (for the results table) and the
        per-piece "layout" (for drawing the diagram) from the same data,
        so the two can never disagree about what's on the plate.

        sheet_w/sheet_l: the physical sheet this plate/remnant is, given
        explicitly by the caller rather than closed over, so this helper
        works the same whether the sheet is a fixed stock size or a
        cut-to-size strip.
        """
        items_by_key = {}
        for piece in pieces:
            items_by_key[piece["key"]] = items_by_key.get(piece["key"], 0) + 1

        used_a = bw = nw = bbw = parts_cost = 0.0
        cut_len = cut_cost = 0.0
        n_pierce = 0
        rate_num = rate_den = 0.0
        items = []
        # Share of THIS plate that each part carries, in proportion to the
        # true area it occupies. Charging a part only its own outline leaves
        # the gaps between blanks unpaid for, and those gaps are steel that
        # was bought. Allocating instead means the part costs add up to the
        # plate cost exactly, and a part gets cheaper when it is nested
        # better - which is the whole point of nesting it properly.
        area_total = sum(parts_detail[k]["unit_blank_area_mm2"] * c
                         for k, c in items_by_key.items()) or 1.0
        plate_wt_here = weight_kg(plate_area, thickness, density)

        for k, c in sorted(items_by_key.items()):
            d = parts_detail[k]
            share = (d["unit_blank_area_mm2"] * c) / area_total
            # Two different weights, and they must not be confused:
            #   line_bbox_wt   this part's share of the whole plate, scrap
            #                  included. Still needed for the plate-level
            #                  roll-up, because the plate is what gets bought.
            #   line_blank_wt  the metal inside the part's own outline - what
            #                  the torch actually cuts out. This is what the
            #                  part is charged on.
            # Charging the blank means the part costs no longer add up to the
            # plate cost. The difference is the offcut, and it is still in the
            # quote: plate_cost carries it.
            line_bbox_wt = plate_wt_here * share
            line_blank_wt = d["unit_blank_weight_kg"] * c
            line_cost = line_blank_wt * d["rate"]
            used_a += d["unit_blank_area_mm2"] * c
            bw += d["unit_blank_weight_kg"] * c
            nw += d["unit_net_weight_kg"] * c
            bbw += line_bbox_wt
            parts_cost += line_cost
            # Cutting is per piece on this plate, so scale the per-part
            # figures by how many of them actually landed here.
            frac = c / d["qty"] if d["qty"] else 0.0
            cut_len += d["cut_length_m"] * frac
            cut_cost += d["cut_cost"] * frac
            n_pierce += int(round(d["pierces"] * frac))
            a = d["unit_bbox_area_mm2"] * c
            rate_num += d["rate"] * a
            rate_den += a
            items.append({
                "key": k,
                "label": d["label"],
                "component_type": d["component_type"],
                "part_no": d["part_no"],
                "blank_size": d["blank_size"],
                "bbox_size": d["bbox_size"],
                "qty": c,
                "rate": d["rate"],
                "bbox_weight_kg": line_bbox_wt,    # share of the PLATE, scrap included
                "blank_weight_kg": line_blank_wt,   # the metal actually cut
                "rm_cost": line_cost,               # charged on the blank
                "cut_length_m": d["cut_length_m"] * (c / d["qty"] if d["qty"] else 0),
                "cut_cost": d["cut_cost"] * (c / d["qty"] if d["qty"] else 0),
                "perimeter_mm": d["perimeter_mm"],
            })

        # Plate-level costs need one rate. Average the parts on this plate,
        # weighted by bounding-box area — exact when they share a rate.
        avg_rate = (rate_num / rate_den) if rate_den else 0.0

        # The rectangle the blanks genuinely occupy, measured from where
        # the packer actually put them.
        if pieces:
            req_w = max(pc["x"] + pc["w"] for pc in pieces)
            req_l = max(pc["y"] + pc["l"] for pc in pieces)
        else:
            req_w = req_l = 0.0
        req_area = req_w * req_l
        req_wt = weight_kg(req_area, thickness, density)

        layout = [{
            "key": piece["key"],
            "x": piece["x"], "y": piece["y"],
            "w": piece["w"], "l": piece["l"],
            "rotated": piece["rotated"],
            "label": parts_detail[piece["key"]]["label"],
            "component_type": parts_detail[piece["key"]]["component_type"],
            "shape_kind": parts_detail[piece["key"]]["shape_kind"],
            "shape_d": parts_detail[piece["key"]]["shape_d"],
            "shape_di": parts_detail[piece["key"]]["shape_di"],
        } for piece in pieces] if number is not None else []

        charge = 1.0 if source == "stock" else float(
            cut_cfg.get("remnant_charge_pct", 0)) / 100.0
        plate_cost = plate_wt * avg_rate * charge

        return {
            "plate": number,
            "spec": spec,
            "source": source,               # "stock" | "remnant" | "oversize"
            "remnant_id": remnant_id,
            "on_plate": number is not None,
            "cut_length_m": cut_len,
            "pierces": n_pierce,
            "cut_cost": cut_cost,
            "material_cost": plate_cost,
            "total_cost": plate_cost + cut_cost,
            "items": items,
            "layout": layout,
            "plate_w_mm": sheet_w if number is not None else None,
            "plate_l_mm": sheet_l if number is not None else None,
            "plate_area_m2": plate_area / 1e6,
            "used_area_m2": used_a / 1e6,
            "free_area_m2": max(plate_area - used_a, 0.0) / 1e6,
            "utilisation_pct": (used_a / plate_area * 100) if plate_area else 0.0,
            "plate_weight_kg": plate_wt,
            "bbox_weight_kg": bbw,
            "blank_weight_kg": bw,
            "net_weight_kg": nw,
            "scrap_kg": max(plate_wt - nw, 0.0),
            "required_w_mm": req_w,
            "required_l_mm": req_l,
            "required_spec": f"{req_w:.0f} \u00d7 {req_l:.0f} mm",
            "required_area_m2": req_area / 1e6,
            "required_weight_kg": req_wt,
            "required_cost": req_wt * avg_rate,
            "avg_rate": avg_rate,
            "parts_cost": parts_cost,
            "plate_cost": plate_cost,
            "cost": plate_cost,            # headline = the plate you buy
        }

    plate_rows = []
    n_plate = 0
    remnant_area = 0.0
    for (rem, first, rh, rv) in remnant_rows:
        n_plate += 1
        r_area = rem["width"] * rem["length"]
        remnant_area += r_area
        row = _plate_row(n_plate, first["items"], r_area,
                         weight_kg(r_area, thickness, density),
                         f"{rem['width']:.0f} \u00d7 {rem['length']:.0f} mm",
                         source="remnant", remnant_id=rem["id"],
                         sheet_w=rh, sheet_l=rv)
        # What is still left of this remnant after the job.
        row["harvest"] = inventory.worth_keeping(
            inventory.free_rects(first, rh, rv, gap))
        harvested_from_remnants.append((rem["id"], row["harvest"]))
        plate_rows.append(row)

    if standard_plate:
        pw, pl = standard_plate["width"], standard_plate["length"]

        # shelf_pack(boxes, plate_w, plate_l, gap) treats its second argument
        # as the horizontal/shelf axis and its third as the vertical/stacking
        # axis. A stock plate physically sits with its longer edge running
        # left-to-right (the mill's rolling/feed direction), so pack — and
        # later draw — against the LONGER of width/length horizontally and
        # the shorter one vertically, regardless of which the user typed
        # into which box.
        horiz, vert = (pl, pw) if pl >= pw else (pw, pl)
        plate_note = ""
        _kinds = {i: (d.get("blank_kind") or "")
                  for i, d in enumerate(parts_detail)}

        # RIGHT-SIZE THE PLATE FOR THIS GROUP.
        #
        # The plate size is chosen once for the whole estimate, but it is only
        # ever needed by the largest blank in ONE group. A 2000 x 6000 sheet
        # bought because a single big ring demands it was being handed to every
        # other material and thickness as well - so a handful of 300 mm pads on
        # a different grade sat alone on six square metres of steel and the rest
        # was written off.
        #
        # Groups do not share plate: you cannot cut stainless from a mild steel
        # sheet, and you cannot cut 5 mm parts from 3 mm plate. So each group is
        # free to buy the smallest stock size its own parts fit on.
        #
        # Only sizes no larger than the one selected are considered, and a size
        # is rejected outright if anything fails to fit on it. The result can
        # only cost less than today, never more.
        # A SIZE CHOSEN BY HAND FOR THIS GROUP BEATS BOTH.
        #
        # Right-sizing minimises the plate AREA bought, which is the correct
        # answer only when the offcut is worth something. It is not always:
        # a 3 mm sheet needed for one bracket, in a grade nothing else uses,
        # leaves a remnant that will sit in the rack until it is scrapped, so
        # the smallest sheet that does the job is genuinely best. A 5 mm sheet
        # in a grade used on every job is the opposite - a bigger sheet costs
        # more today and gives back a remnant the next job will actually cut
        # from. The estimator cannot know which is which; only you know what
        # the rack turns over. So the choice is yours to make per material and
        # thickness, and it wins over the automatic one.
        forced = None
        if plate_override:
            o = plate_override.get(plate_key(parts[0]["material"], thickness))
            if o and o.get("width") and o.get("length"):
                ow, ol = float(o["width"]), float(o["length"])
                fh, fv = (ol, ow) if ol >= ow else (ow, ol)
                trial = _pack_group(pending, parts_detail, fh, fv, gap, _kinds)
                # Refused if anything will not fit on it. Silently nesting onto
                # a sheet the parts do not fit would produce a cutting plan
                # that cannot be cut.
                if not trial["oversize"]:
                    forced = (fh, fv)
                else:
                    plate_note = (
                        f"{ow:g} \u00d7 {ol:g} mm was selected for this group but "
                        f"{len(trial['oversize'])} blank(s) do not fit on it, so "
                        f"the automatic size was used instead.")

        if forced:
            horiz, vert = forced
        elif auto_plate and stock_sizes:
            best = None
            for (ch, cv) in _smaller_stock(horiz, vert, stock_sizes):
                trial = _pack_group(pending, parts_detail, ch, cv, gap, _kinds)
                if trial["oversize"]:
                    continue
                area = trial["plates"] * ch * cv
                if best is None or area < best[0] - 1e-6:
                    best = (area, ch, cv, trial)
            if best is not None:
                _, horiz, vert, _trial = best

        # TRUE-SHAPE NESTING.
        #
        # Shelf packing treats a disc as its bounding square and discards the
        # corners: 10 x Ø439.6 on a 1250 x 2500 plate, 48.6% used. Nesting the
        # circles as circles gives 15 on the same plate, 72.9%.
        #
        #   all discs      the hex/inline grid, which is optimal for equal
        #                  circles and cannot be beaten by a greedy packer
        #   discs + plates true shape against true shape, so a small plate can
        #                  take the waste between four discs or the strip at
        #                  the end of a row instead of opening a new sheet
        #   all plates     shelf packing, which is what rectangles want anyway
        #
        # Only the mixed case is new. A group that is all one or the other
        # goes exactly where it went before.
        pack = _pack_group(pending, parts_detail, horiz, vert, gap, _kinds)

        # Cost and label follow the plate this group ACTUALLY buys, which after
        # right-sizing may be smaller than the one selected for the run. Using
        # the selected size here would have quietly charged every group for the
        # biggest sheet while nesting them onto a smaller one - the numbers
        # would not have described any real plate.
        one_area = horiz * vert
        one_wt = weight_kg(one_area, thickness, density)
        spec = f"{vert:.0f} \u00d7 {horiz:.0f} mm"

        for i, plate in enumerate(pack["layouts"], start=1):
            row = _plate_row(n_plate + i, plate["items"], one_area, one_wt, spec,
                             sheet_w=horiz, sheet_l=vert)
            row["harvest"] = inventory.worth_keeping(
                inventory.free_rects(plate, horiz, vert, gap))
            plate_rows.append(row)
        n_plate += len(pack["layouts"])

        # Oversize pieces sit on no stock plate. Give them their own row,
        # costed on their own bounding rectangle, so nothing goes missing.
        # No real x/y — they were never placed — so layout will be empty
        # for this row; there's nothing to draw.
        oversize_area = 0.0
        if pack["oversize"]:
            oversize_pieces = []
            for (w, l, k) in pack["oversize"]:
                oversize_pieces.append({"key": k, "x": 0, "y": 0, "w": w, "l": l, "rotated": False})
                oversize_area += w * l
            plate_rows.append(_plate_row(
                None, oversize_pieces, oversize_area,
                weight_kg(oversize_area, thickness, density),
                "exceeds stock plate"))

        plate_area_mm2 = one_area * pack["plates"] + oversize_area + remnant_area
        plate_wt = sum(r["plate_weight_kg"] for r in plate_rows)
        group_cost = sum(r["cost"] for r in plate_rows)

        result.update({
            "mode": "standard",
            "plate_key": plate_key(parts[0]["material"], thickness),
            "plate_note": plate_note,
            "plate_rows": plate_rows,
            "plate_spec": spec,
            "plates_required": pack["plates"],
            "remnants_used": [r["id"] for (r, _f, _h, _v) in remnant_rows],
            "shelves": pack["shelves"],
            "oversize_count": len(pack["oversize"]),
            "plate_width_mm": pw,
            "plate_length_mm": pl,
            "gap_allowance_mm": gap,
        })

    else:
        # Cut-to-size mode. Same "offcut first" rule as standard-plate mode
        # (handled by the shared remnant loop above) — whatever offcut
        # couldn't absorb is bought as one strip cut exactly to fit.
        if pending:
            plate_width = max(w for w, _l, _k in pending)
            length_sum = sum(l for _w, l, _k in pending)
            gap_allowance = gap * max(len(pending) - 1, 0)
            plate_length = length_sum + gap_allowance
            plate_area_leftover = plate_width * plate_length
            plate_wt_leftover = weight_kg(plate_area_leftover, thickness, density)

            # Pieces sit one after another along the length axis (the order
            # they were appended to `boxes`), each drawn at its OWN width
            # rather than stretched to the plate width — narrower parts show
            # real unused margin, which is the honest picture even though
            # they're still costed as if claiming the full width.
            strip_pieces = []
            y = 0.0
            for (w, l, k) in pending:
                if strip_pieces:
                    y += gap
                strip_pieces.append({"key": k, "x": 0, "y": y, "w": w, "l": l, "rotated": False})
                y += l

            n_plate += 1
            row = _plate_row(n_plate, strip_pieces, plate_area_leftover, plate_wt_leftover,
                             f"{plate_width:.0f} \u00d7 {plate_length:.0f} mm",
                             sheet_w=plate_width, sheet_l=plate_length)
            row["harvest"] = []  # cut exactly to size — nothing worth racking
            plate_rows.append(row)
            plate_spec = row["spec"]
            plate_width_mm, plate_length_mm, gap_allowance_mm = plate_width, plate_length, gap_allowance
        elif plate_rows:
            # Offcut covered the whole group — no new plate bought at all.
            plate_spec = plate_rows[-1]["spec"]
            plate_width_mm = plate_length_mm = gap_allowance_mm = 0
        else:
            plate_spec = "0 \u00d7 0 mm"
            plate_width_mm = plate_length_mm = gap_allowance_mm = 0

        plate_area_mm2 = sum(r["plate_area_m2"] for r in plate_rows) * 1e6
        plate_wt = sum(r["plate_weight_kg"] for r in plate_rows)
        group_cost = sum(r["cost"] for r in plate_rows)

        result.update({
            "mode": "strip",
            "plate_rows": plate_rows,
            "plate_spec": plate_spec,
            "plates_required": len(plate_rows),
            "remnants_used": [r["id"] for (r, _f, _h, _v) in remnant_rows],
            "shelves": 0,
            "oversize_count": 0,
            "plate_width_mm": plate_width_mm,
            "plate_length_mm": plate_length_mm,
            "gap_allowance_mm": gap_allowance_mm,
        })

    for d in parts_detail:
        del d["_length"]
        del d["_area"]

    nesting_scrap = max(plate_wt - blank_wt, 0.0)
    bore_scrap = max(blank_wt - net_wt, 0.0)
    total_scrap = nesting_scrap + bore_scrap

    # Area accounting. "Used" is the true blank outline, not its bounding box,
    # so it matches the utilisation figure. "Free" is everything else on the
    # plate — note that not all of it is a usable offcut: some is the narrow
    # slivers between blanks, which is material you own but can't cut a part
    # from.
    result.update({
        "plate_area_m2": plate_area_mm2 / 1e6,
        "used_area_m2": sum_blank_area / 1e6,
        "free_area_m2": max(plate_area_mm2 - sum_blank_area, 0.0) / 1e6,
        "plate_weight_kg": plate_wt,
        "nesting_scrap_kg": nesting_scrap,
        "bore_scrap_kg": bore_scrap,
        "total_scrap_kg": total_scrap,
        "scrap_pct": (total_scrap / plate_wt * 100) if plate_wt else 0.0,
        "utilisation_pct": (blank_wt / plate_wt * 100) if plate_wt else 0.0,
        "bbox_weight_kg": sum(d["bbox_weight_kg"] for d in parts_detail),
        "required_area_m2": sum(r["required_area_m2"] for r in result["plate_rows"]),
        "required_weight_kg": sum(r["required_weight_kg"] for r in result["plate_rows"]),
        # Three cost figures, always in this order:
        #   parts_cost    <= required_cost <= plate_cost
        #   (bounding box)   (cut to size)    (stock plate you buy)
        # Quote from plate_cost; the other two under-recover.
        "cut_length_m": sum(r.get("cut_length_m", 0) for r in result["plate_rows"]),
        "pierces": sum(r.get("pierces", 0) for r in result["plate_rows"]),
        "cut_cost": sum(r.get("cut_cost", 0) for r in result["plate_rows"]),
        "parts_cost": sum(r["parts_cost"] for r in result["plate_rows"]),
        "required_cost": sum(r["required_cost"] for r in result["plate_rows"]),
        "plate_cost": sum(r["plate_cost"] for r in result["plate_rows"]),
        "total_cost": sum(r["plate_cost"] for r in result["plate_rows"])
                      + sum(r.get("cut_cost", 0) for r in result["plate_rows"]),
        "cost": group_cost,
    })
    return result


def _plate_purchases(plate_rows):
    """
    Group the newly bought plates by material, thickness and size, so the
    buying team sees "3 off 1250 x 2500 x 5 mm" rather than three plate rows.
    """
    out = {}
    for r in plate_rows:
        if r.get("source") != "stock" or not r.get("on_plate"):
            continue
        key = (r.get("material"), r.get("thickness"), r.get("spec"))
        e = out.setdefault(key, {
            "material": key[0], "thickness": key[1], "size": key[2],
            "qty": 0, "weight_kg": 0.0, "cost": 0.0,
        })
        e["qty"] += 1
        e["weight_kg"] += float(r.get("plate_weight_kg") or 0)
        e["cost"] += float(r.get("material_cost") or 0)
    return sorted(out.values(), key=lambda x: (str(x["material"]), x["thickness"] or 0))


def _density_of(materials, name):
    """
    Density for a material, or a message that says what to do about it.

    A bare KeyError here surfaces to the user as "Invalid or missing part
    data: 'Mild Steel'", which does not explain that the material list was
    edited and this part still refers to the old name.
    """
    if name in materials:
        return materials[name]
    known = ", ".join(sorted(materials)) or "(none defined)"
    raise ValueError(
        f"Unknown material {name!r}. It is not in database.py MATERIALS. "
        f"If you renamed the materials, this part (or a saved remnant or job) "
        f"still refers to the old name. Available: {known}")


def estimate(parts: list, schemas: dict, materials: dict,
             standard_plate: dict = None, combine: bool = True,
             cut_cfg: dict = None, remnant_stock: list = None,
             remnant_only_row_ids: list = None,
             stock_sizes: list = None, auto_plate: bool = True,
             plate_overrides: dict = None) -> dict:
    """
    Main entry point.

    Flanges are purchased finished and are therefore handled separately from
    plate/nesting calculations:
      * no plate allocation
      * no raw-material (Rs/kg) cost
      * no cutting cost
      * purchase cost = quantity x purchase rate (Rs/pc)

    All other component types continue through the existing plate workflow.
    """
    # Flanges are bought as finished components. Keep them completely outside
    # the plate groups so they can never consume plate, remnant stock or
    # cutting charges.
    # Bought-in components never touch a plate. Keeping them out of the
    # nesting list is the whole point — otherwise the app would report needing
    # more plate because of items that arrive finished.
    # EVERY NUMBER IS A NUMBER FROM HERE ON.
    #
    # The page can send "3" where 3.0 is meant - text typed into a field, a
    # value carried over from a BOM line, a figure read out of a spreadsheet.
    # Downstream nothing checks: group_key split "3" from 3.0 into two plates
    # of the same material and thickness, and the shape builders raised
    # TypeError trying to add a float to a string. Coercing once here, against
    # each type's own schema, is the only place that has to know.
    parts = [_numeric_part(p, schemas) for p in parts]

    # Round Bar joins these: bar stock is bought by the length, not cut from a
    # sheet, so nesting it onto plate would buy steel that nobody buys and
    # report scrap that does not exist.
    # Custom joins these for a different reason: its size is free text, so no
    # area can be derived and nesting it would mean nesting a zero-sized
    # blank. Listing it without costing it is honest; quietly nesting a
    # nothing-shaped part and reporting a plate cost of zero is not.
    PURCHASED_TYPES = ("Flange", "Pipe", "Round Bar", "Custom")
    plate_parts = [p for p in parts if p.get("_type") not in PURCHASED_TYPES]
    flange_parts = [p for p in parts if p.get("_type") == "Flange"]
    pipe_parts = [p for p in parts if p.get("_type") == "Pipe"]
    bar_parts = [p for p in parts if p.get("_type") == "Round Bar"]
    custom_parts = [p for p in parts if p.get("_type") == "Custom"]

    # Existing/calculated parts are locked to the standard-plate result.
    # Only rows explicitly marked as newly added may consume offcut stock.
    # This prevents a later offcut calculation from silently re-cutting parts
    # that were already allocated to a standard plate.
    remnant_only_row_ids = {str(x) for x in (remnant_only_row_ids or [])}
    locked_parts = [p for p in plate_parts
                    if str(p.get("_row")) not in remnant_only_row_ids]
    remnant_parts = [p for p in plate_parts
                     if str(p.get("_row")) in remnant_only_row_ids]

    group_results = []
    plate_rows = []
    purchase_items = []
    totals = {"plate_weight_kg": 0.0, "blank_weight_kg": 0.0, "net_weight_kg": 0.0,
              "total_scrap_kg": 0.0, "cost": 0.0, "plates_required": 0,
              "plate_area_m2": 0.0, "used_area_m2": 0.0, "free_area_m2": 0.0,
              "purchase_cost": 0.0}
    oversize = []

    # Purchased flanges: cost strictly by quantity and per-piece purchase rate.
    for p in flange_parts:
        qty = int(p.get("qty") or 1)
        rate = float(p.get("rate") or 0)
        purchase_cost = qty * rate
        purchase_items.append({
            "row_id": p.get("_row"),
            "component_type": "Flange",
            "label": p.get("part_name") or p.get("flange_type") or p.get("part_no") or "Flange",
            "part_no": p.get("part_no") or "",
            "part_name": p.get("part_name") or "",
            "pressure_class": p.get("pressure_class") or "",
            "nps": p.get("nps") or "",
            "flange_type": p.get("flange_type") or "",
            "od": float(p.get("od") or 0),
            "id": float(p.get("id_") or 0),
            "thickness": float(p.get("thk") or 0),
            "material": p.get("material") or "",
            "qty": qty,
            "rate": rate,
            "unit": "Rs/pc",
            "purchase_cost": purchase_cost,
            "cost": purchase_cost,
        })
        totals["purchase_cost"] += purchase_cost

    # Purchased pipe. Priced either by the metre or by weight — both are
    # normal in the trade, and quoting the wrong one is an easy way to be out
    # by a factor of several, so the basis is explicit rather than assumed.
    for p in custom_parts:
        # Carried through so it reaches the BOM, the summary and the RFQ, with
        # no weight and no cost because neither can be known from free text.
        # `uncosted` lets the page say so rather than print a convincing 0.
        purchase_items.append({
            "row_id": p.get("_row"),
            "component_type": "Custom",
            "label": p.get("part_name") or "Custom part",
            "part_no": p.get("part_no") or "",
            "part_name": p.get("part_name") or "",
            "material": p.get("material") or "",
            "qty": int(p.get("qty") or 1),
            "size": str(p.get("dimensions") or "").strip(),
            "remarks": str(p.get("remarks") or "").strip(),
            "thickness": float(p.get("thk") or 0),
            "weight_kg": 0.0,
            "rate": 0.0,
            "unit": "",
            "purchase_cost": 0.0,
            "uncosted": True,
        })

    for p in bar_parts:
        # Solid cylinder: pi/4 x d^2 x L. Priced per kg, like any bar stock.
        qty = int(p.get("qty") or 1)
        dia = float(p.get("dia") or 0)
        length_mm = float(p.get("length") or 0)
        rate = float(p.get("rate") or 0)
        # Density per part, not per group: bar stock never reaches the group
        # loop where a group-wide density is worked out.
        bar_density = _density_of(materials, p.get("material"))
        vol_mm3 = math.pi / 4.0 * dia * dia * length_mm
        weight = vol_mm3 * MM3_TO_M3 * bar_density * qty
        purchase_cost = weight * rate
        purchase_items.append({
            "row_id": p.get("_row"),
            "component_type": "Round Bar",
            "label": p.get("part_name") or f"\u00d8{dia:g} bar",
            "part_no": p.get("part_no") or "",
            "part_name": p.get("part_name") or "",
            "od": dia,
            "id": 0.0,
            "thickness": 0.0,
            "material": p.get("material") or "",
            "qty": qty,
            "length_m": length_mm / 1000.0,
            "total_length_m": qty * length_mm / 1000.0,
            "weight_kg": weight,
            "rate": rate,
            "unit": "Rs/kg",
            "purchase_cost": purchase_cost,
            "size": f"\u00d8{dia:g} \u00d7 {length_mm:g} mm",
        })

    for p in pipe_parts:
        qty = int(p.get("qty") or 1)
        length_m = float(p.get("length_m") or 0)
        rate = float(p.get("rate") or 0)
        basis = str(p.get("rate_basis") or "Rs/m")
        kg_m = float(p.get("kg_m") or 0)

        total_len = qty * length_m
        weight = total_len * kg_m
        if basis.endswith("/kg"):
            purchase_cost = weight * rate
            unit = "Rs/kg"
        else:
            purchase_cost = total_len * rate
            unit = "Rs/m"

        purchase_items.append({
            "row_id": p.get("_row"),
            "component_type": "Pipe",
            "label": p.get("part_name") or f'NPS {p.get("nps", "")} SCH {p.get("schedule", "")}',
            "part_no": p.get("part_no") or "",
            "part_name": p.get("part_name") or "",
            "nps": p.get("nps") or "",
            "schedule": p.get("schedule") or "",
            "od": float(p.get("od") or 0),
            "id": float(p.get("id_") or 0),
            "thickness": float(p.get("thk") or 0),
            "material": p.get("material") or "",
            "qty": qty,
            "length_m": length_m,
            "total_length_m": total_len,
            "kg_m": kg_m,
            "weight_kg": weight,
            "rate": rate,
            "unit": unit,
            "purchase_cost": purchase_cost,
            "cost": purchase_cost,
        })
        totals["purchase_cost"] += purchase_cost
        totals["pipe_length_m"] = totals.get("pipe_length_m", 0.0) + total_len
        totals["pipe_weight_kg"] = totals.get("pipe_weight_kg", 0.0) + weight

    # Locked parts: always standard plate only. They are never eligible for
    # the offcut rack after the user has already calculated them.
    locked_groups = group_parts(locked_parts, combine)
    for key, members in locked_groups.items():
        density = _density_of(materials, members[0]["material"])
        r = estimate_group(members, schemas, density, standard_plate,
                           cut_cfg=cut_cfg, remnants=[],
                           stock_sizes=stock_sizes, auto_plate=auto_plate,
                           plate_override=plate_overrides)
        group_results.append(r)
        group_idx = len(group_results) - 1

        # Locked parts are still part of the current job total. They are only
        # excluded from the *new* offcut allocation; they must never disappear
        # from totals, plate rows, or cost calculations on a later recalc.
        totals["plate_weight_kg"] += r["plate_weight_kg"]
        totals["blank_weight_kg"] += r["blank_weight_kg"]
        totals["net_weight_kg"] += r["net_weight_kg"]
        totals["total_scrap_kg"] += r["total_scrap_kg"]
        totals["cost"] += r["cost"]
        totals["plates_required"] += r["plates_required"]
        totals["plate_area_m2"] += r["plate_area_m2"]
        totals["used_area_m2"] += r["used_area_m2"]
        totals["free_area_m2"] += r["free_area_m2"]
        for _k in ("bbox_weight_kg", "required_weight_kg", "required_area_m2",
                   "parts_cost", "required_cost", "plate_cost", "total_cost",
                   "cut_length_m", "cut_cost", "pierces"):
            totals[_k] = totals.get(_k, 0.0) + r[_k]
        for pr in r.get("plate_rows", []):
            # group_idx lets the UI find the exact group this plate came from,
            # instead of matching by material+thickness — which breaks once a
            # locked group and a remnant group share the same material/thickness.
            plate_rows.append(dict(pr, material=r["material"], thickness=r["thickness"],
                                   group_idx=group_idx))
        for d in r["parts_detail"]:
            if not d["fits_standard"]:
                oversize.append({
                    "label": d["label"],
                    "part_no": d["part_no"],
                    "component_type": d["component_type"],
                    "blank_size": d["blank_size"],
                })

    # Newly added parts: offcut first (when enabled by the caller), then a
    # separate standard-plate calculation for anything the offcut could not
    # accommodate. They never get mixed back into the already-cut groups.
    remnant_groups = group_parts(remnant_parts, combine)
    for key, members in remnant_groups.items():
        density = _density_of(materials, members[0]["material"])
        mat, thk = members[0]["material"], members[0]["thk"]
        usable = [x for x in (remnant_stock or [])
                  if x["material"] == mat
                  and abs(float(x["thickness"]) - float(thk)) < 1e-9]
        r = estimate_group(members, schemas, density, standard_plate,
                           cut_cfg=cut_cfg,
                           remnants=usable if remnant_stock is not None else [],
                           stock_sizes=stock_sizes, auto_plate=auto_plate,
                           plate_override=plate_overrides)
        group_results.append(r)
        group_idx = len(group_results) - 1
        totals["plate_weight_kg"] += r["plate_weight_kg"]
        totals["blank_weight_kg"] += r["blank_weight_kg"]
        totals["net_weight_kg"] += r["net_weight_kg"]
        totals["total_scrap_kg"] += r["total_scrap_kg"]
        totals["cost"] += r["cost"]
        totals["plates_required"] += r["plates_required"]
        totals["plate_area_m2"] += r["plate_area_m2"]
        totals["used_area_m2"] += r["used_area_m2"]
        totals["free_area_m2"] += r["free_area_m2"]
        for _k in ("bbox_weight_kg", "required_weight_kg", "required_area_m2",
                   "parts_cost", "required_cost", "plate_cost", "total_cost",
                   "cut_length_m", "cut_cost", "pierces"):
            totals[_k] = totals.get(_k, 0.0) + r[_k]
        for pr in r.get("plate_rows", []):
            plate_rows.append(dict(pr, material=r["material"], thickness=r["thickness"],
                                   group_idx=group_idx))
        for d in r["parts_detail"]:
            if not d["fits_standard"]:
                oversize.append({
                    "label": d["label"],
                    "part_no": d["part_no"],
                    "component_type": d["component_type"],
                    "blank_size": d["blank_size"],
                })

    totals["scrap_pct"] = (
        totals["total_scrap_kg"] / totals["plate_weight_kg"] * 100
        if totals["plate_weight_kg"] else 0.0
    )
    totals["utilisation_pct"] = (
        totals["blank_weight_kg"] / totals["plate_weight_kg"] * 100
        if totals["plate_weight_kg"] else 0.0
    )

    # total_cost is the complete job cost: existing plate/cutting cost plus
    # purchased finished-flange cost. Flanges do not affect any plate metric.
    totals["total_cost"] = totals.get("total_cost", 0.0) + totals["purchase_cost"]
    totals["cost"] = totals.get("cost", 0.0) + totals["purchase_cost"]

    return {
        "cut_gap_factor": CUT_GAP_FACTOR,
        "mode": "standard" if standard_plate else "strip",
        "combine": combine,
        "standard_plate": standard_plate,
        "groups": group_results,
        "plate_rows": plate_rows,
        "purchase_items": purchase_items,
        "harvest": [dict(h, material=r["material"], thickness=r["thickness"])
                    for r in group_results
                    for pr in r.get("plate_rows", [])
                    for h in pr.get("harvest", [])],
        "remnants_used": [rid for r in group_results
                          for rid in r.get("remnants_used", [])],
        "oversize": oversize,
        # Plates actually BOUGHT, grouped for the buying team. Only "stock"
        # rows count: a remnant came off the rack and was paid for by an
        # earlier job, so counting it again would charge the same steel twice.
        "plates_purchased": _plate_purchases(plate_rows),
        "totals": totals,
    }

