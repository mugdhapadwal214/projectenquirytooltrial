"""
nesting.py — true-shape nesting for circular blanks.

WHY THIS EXISTS
    Shelf packing treats every blank as its bounding box, so a disc occupies a
    square and the four corners are thrown away. For a Ø439.6 dish end on a
    1250 x 2500 plate that is 10 blanks at 48.6% utilisation. Nesting the
    circles as circles gives 15 at 72.9% — half as many plates again from the
    same steel.

TWO LAYOUTS, AND WHY THERE IS NOTHING TO SEARCH
    inline      rows straight across, centres a pitch apart both ways
    staggered   alternate rows shifted by exactly pitch/2, row spacing
                pitch * sqrt(3)/2

    The offset is NOT a free parameter to optimise. A row repeats every pitch,
    so shifting row 2 by `off` puts its circles `off` from one neighbour above
    and `pitch - off` from the next. The binding distance is
    min(off, pitch - off), which peaks at exactly pitch/2.

    Searching past that looks like it works and does not: an earlier version
    of this reported 18 blanks at 87.4% utilisation on a plate whose true
    capacity is 15. It had twelve overlapping pairs. Hence:

VALIDATION IS NOT OPTIONAL
    Every layout this module returns has passed check_layout(). A packing that
    is merely plausible is worse than no packing at all, because it produces a
    cutting plan that destroys parts. The check is O(n^2) on centres, which is
    nothing at these quantities.

WHAT IT DOES NOT DO
    Rectangles. Shells and custom plates stay on the shelf packer; a group
    containing any non-circle is routed there untouched. Mixing the two is a
    larger problem and this is deliberately the smaller, safer half.
"""
import math

# Blanks within this many mm of each other count as the same diameter. Two
# dish ends nominally identical can differ in the last decimal through the
# development formula, and treating them as different would throw away the
# grid layout for no reason.
SAME_D_TOL = 0.5


def _pitch(d, gap):
    return d + gap


def inline_layout(d, gap, width, length, edge):
    """Rows straight across. Centres one pitch apart in both directions."""
    r, p = d / 2.0, _pitch(d, gap)
    pts, y = [], edge + r
    while y + r <= length - edge + 1e-9:
        x = edge + r
        while x + r <= width - edge + 1e-9:
            pts.append((x, y))
            x += p
        y += p
    return pts


def stagger_layout(d, gap, width, length, edge):
    """
    Alternate rows shifted by pitch/2, rows sqrt(3)/2 * pitch apart.

    That spacing is what keeps a shifted circle exactly one pitch from its
    neighbours in the row above: sqrt((pitch/2)^2 + (pitch*sqrt3/2)^2) = pitch.
    """
    r, p = d / 2.0, _pitch(d, gap)
    row_gap = p * math.sqrt(3) / 2.0
    pts, y, i = [], edge + r, 0
    while y + r <= length - edge + 1e-9:
        x = edge + r + (p / 2.0 if i % 2 else 0.0)
        while x + r <= width - edge + 1e-9:
            pts.append((x, y))
            x += p
        y += row_gap
        i += 1
    return pts


def check_layout(placed, gap, width, length, edge=0.0):
    """
    Ground truth. `placed` is [(x, y, d), ...].

    Returns (ok, reason). Every layout leaving this module goes through here.
    """
    for x, y, d in placed:
        r = d / 2.0
        if (x - r < edge - 1e-6 or y - r < edge - 1e-6
                or x + r > width - edge + 1e-6 or y + r > length - edge + 1e-6):
            return False, f"a \u00d8{d:.1f} blank falls outside the plate"
    n = len(placed)
    for i in range(n):
        xi, yi, di = placed[i]
        for j in range(i + 1, n):
            xj, yj, dj = placed[j]
            need = (di + dj) / 2.0 + gap
            if math.hypot(xi - xj, yi - yj) < need - 1e-6:
                return False, (f"two blanks overlap "
                               f"(centres {math.hypot(xi-xj, yi-yj):.1f} mm apart, "
                               f"need {need:.1f})")
    return True, ""


def best_uniform(d, gap, width, length, edge):
    """
    Best validated layout for one diameter on one plate.

    Both plate orientations are tried: which side you call the width changes
    how many rows fit, and the difference is routinely 20-50%.
    """
    best, best_name = [], "none"
    for w, l, swapped in ((width, length, False), (length, width, True)):
        for name, fn in (("inline", inline_layout), ("staggered", stagger_layout)):
            pts = fn(d, gap, w, l, edge)
            placed = [(x, y, d) for x, y in pts]
            ok, _ = check_layout(placed, gap, w, l, edge)
            if not ok or len(placed) <= len(best):
                continue
            # Report in the plate's own orientation.
            best = ([(y, x, d) for x, y, d in placed] if swapped else placed)
            best_name = name + (" (rotated)" if swapped else "")
    return best, best_name


# --------------------------------------------------------------------------
# Shapes are (kind, ...) so circles and rectangles can share one plate:
#     ("circle", cx, cy, d)
#     ("rect",   cx, cy, w, h)
# A rectangle is axis-aligned about its centre. Both are described by their
# CENTRE so the collision tests read the same way.
# --------------------------------------------------------------------------

def _clear(a, b, gap):
    """True when two placed shapes are at least `gap` apart."""
    if a[0] == "circle" and b[0] == "circle":
        _, ax, ay, ad = a
        _, bx, by, bd = b
        return math.hypot(ax - bx, ay - by) >= (ad + bd) / 2.0 + gap - 1e-9

    if a[0] == "rect" and b[0] == "rect":
        _, ax, ay, aw, ah = a
        _, bx, by, bw, bh = b
        return (abs(ax - bx) >= (aw + bw) / 2.0 + gap - 1e-9 or
                abs(ay - by) >= (ah + bh) / 2.0 + gap - 1e-9)

    # circle against rectangle: closest point on the rectangle to the centre.
    # This is what lets a small plate sit in the gap between two discs - the
    # bounding-box packer could never see that space at all.
    c, rct = (a, b) if a[0] == "circle" else (b, a)
    _, cx, cy, cd = c
    _, rx, ry, rw, rh = rct
    nx = max(rx - rw / 2.0, min(cx, rx + rw / 2.0))
    ny = max(ry - rh / 2.0, min(cy, ry + rh / 2.0))
    return math.hypot(cx - nx, cy - ny) >= cd / 2.0 + gap - 1e-9


def _inside(sh, width, length, edge):
    if sh[0] == "circle":
        _, x, y, d = sh
        r = d / 2.0
        return (x - r >= edge - 1e-9 and y - r >= edge - 1e-9 and
                x + r <= width - edge + 1e-9 and y + r <= length - edge + 1e-9)
    _, x, y, w, h = sh
    return (x - w / 2.0 >= edge - 1e-9 and y - h / 2.0 >= edge - 1e-9 and
            x + w / 2.0 <= width - edge + 1e-9 and y + h / 2.0 <= length - edge + 1e-9)


def shape_area(sh):
    if sh[0] == "circle":
        return math.pi * (sh[3] / 2.0) ** 2
    return sh[3] * sh[4]


def _fits(x, y, d, placed, gap, width, length, edge):
    r = d / 2.0
    if (x - r < edge or y - r < edge
            or x + r > width - edge or y + r > length - edge):
        return False
    for px, py, pd in placed:
        if math.hypot(x - px, y - py) < (d + pd) / 2.0 + gap - 1e-9:
            return False
    return True


def pack_mixed(diams, gap, width, length, edge, step=None):
    """
    Different diameters on one plate: largest first, lowest-then-leftmost.

    Large blanks placed first because a big circle dropped into a gap left by
    small ones rarely fits, while the reverse usually does. Candidate points
    come from a coarse grid; for circles that is enough, and it avoids the
    tangency bookkeeping a full no-fit-polygon search would need.
    """
    placed = []
    order = sorted(range(len(diams)), key=lambda i: -diams[i])
    step = step or max(5.0, min(diams) / 8.0)
    for idx in order:
        d = diams[idx]
        r = d / 2.0
        spot = None
        y = edge + r
        while y + r <= length - edge + 1e-9 and spot is None:
            x = edge + r
            while x + r <= width - edge + 1e-9:
                if _fits(x, y, d, placed, gap, width, length, edge):
                    spot = (x, y)
                    break
                x += step
            y += step
        if spot:
            placed.append((spot[0], spot[1], d))
    return placed


def nest(diameters, gap, width, length, edge=0.0):
    """
    Fit as many of `diameters` as possible onto ONE plate.

    Returns {"placed": [(x, y, d)...], "layout": name, "left": [d...]}.
    `left` is whatever did not fit and needs another plate.
    """
    if not diameters:
        return {"placed": [], "layout": "empty", "left": []}

    uniform = max(diameters) - min(diameters) <= SAME_D_TOL
    if uniform:
        d = sum(diameters) / len(diameters)
        pts, name = best_uniform(d, gap, width, length, edge)
        use = pts[:len(diameters)]
        return {"placed": use, "layout": name,
                "left": [d] * max(0, len(diameters) - len(use))}

    placed = pack_mixed(list(diameters), gap, width, length, edge)
    ok, why = check_layout(placed, gap, width, length, edge)
    if not ok:                      # never hand back something unverified
        return {"placed": [], "layout": f"failed: {why}", "left": list(diameters)}

    used = sorted((p[2] for p in placed), reverse=True)
    left = sorted(diameters, reverse=True)
    for d in used:
        if d in left:
            left.remove(d)
    return {"placed": placed, "layout": "mixed (largest first)", "left": left}


def nest_all(diameters, gap, width, length, edge=0.0):
    """Fill plate after plate until everything is placed. Returns a list."""
    plates, remaining, guard = [], list(diameters), 0
    while remaining and guard < 500:
        guard += 1
        res = nest(remaining, gap, width, length, edge)
        if not res["placed"]:
            # Nothing fits at all — oversize. Stop rather than loop.
            break
        plates.append(res)
        remaining = res["left"]
    return plates, remaining


def pack_shapes(specs, gap, width, length, edge=0.0, step=None, rotate=True):
    """
    Nest circles and rectangles together on one plate.

    `specs` is [("circle", d) | ("rect", w, h), ...]. Largest area first, then
    lowest-leftmost. Rectangles may be tried rotated 90 degrees.

    The point of putting both through one packer is the gap between two discs:
    it is real usable steel, and a bounding-box packer cannot see it because
    each disc has already claimed its whole square.

    Returns (placed_shapes, indices_that_did_not_fit).
    """
    order = sorted(range(len(specs)), key=lambda i: -shape_area(
        (specs[i][0], 0, 0) + tuple(specs[i][1:])))
    placed, left = [], []
    smallest = min((s[1] for s in specs), default=100.0)
    step = step or max(4.0, smallest / 10.0)

    for idx in order:
        spec = specs[idx]
        forms = ([("circle", spec[1])] if spec[0] == "circle"
                 else ([("rect", spec[1], spec[2])] +
                       ([("rect", spec[2], spec[1])]
                        if rotate and spec[1] != spec[2] else [])))
        done = False
        for form in forms:
            y = edge + (form[1] if form[0] == "circle" else form[2]) / 2.0
            while y <= length - edge and not done:
                x = edge + (form[1] if form[0] == "circle" else form[1]) / 2.0
                while x <= width - edge:
                    cand = ((form[0], x, y, form[1]) if form[0] == "circle"
                            else (form[0], x, y, form[1], form[2]))
                    if _inside(cand, width, length, edge) and all(
                            _clear(cand, q, gap) for q in placed):
                        placed.append(cand)
                        done = True
                        break
                    x += step
                y += step
            if done:
                break
        if not done:
            left.append(idx)
    return placed, left


def check_shapes(placed, gap, width, length, edge=0.0):
    """Ground truth for a mixed layout. Same role as check_layout()."""
    for sh in placed:
        if not _inside(sh, width, length, edge):
            return False, "a blank falls outside the plate"
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            if not _clear(placed[i], placed[j], gap):
                return False, "two blanks overlap"
    return True, ""



# ===========================================================================
# TRUE-SHAPE NESTING
#
# Shapes carry their real geometry and are described by their CENTRE:
#     ("circle", cx, cy, d)
#     ("ring",   cx, cy, od, bore)
#     ("rect",   cx, cy, w, h)
#
# A RING IS A CONTAINER, not just an obstacle. The disc inside its bore is
# cut out anyway and today goes in the skip - a 24" slip-on throws away 46.8 kg
# at 20 mm. Anything that fits entirely inside `bore/2 - gap` can be nested
# there for free.
#
# THREE PASSES, in this order and for a reason:
#   1. uniform circles on the proven hex/inline grid - a generic greedy pass
#      packs them worse, so the good layout is laid down first and kept
#   2. everything else into the space left over, true shape against true shape
#   3. ring bores filled last, once the rings themselves have positions
# ===========================================================================

def _corners(x, y, w, h):
    return ((x - w/2, y - h/2), (x + w/2, y - h/2),
            (x + w/2, y + h/2), (x - w/2, y + h/2))


def _half_h(sh):
    """Half the shape's vertical extent, for the row prefilter."""
    if sh[0] == "rect":
        return sh[4] / 2.0
    return sh[3] / 2.0


def _outer_r(sh):
    """Radius of the smallest circle containing the shape."""
    if sh[0] == "circle":
        return sh[3] / 2.0
    if sh[0] == "ring":
        return sh[3] / 2.0
    return math.hypot(sh[3], sh[4]) / 2.0


def fits_in_bore(inner, ring, gap):
    """True when `inner` sits wholly inside `ring`'s bore, clear of the metal."""
    if ring[0] != "ring":
        return False
    if ring[4] >= ring[3]:
        # A bore at least as wide as the outside is not a ring, it is a typo.
        # Left unguarded it makes the packer treat the whole plate as free
        # space inside that "bore" - blanks stack on each other and get pushed
        # off the edge, and the layout is thrown away. Refuse instead.
        return False
    limit = ring[4] / 2.0 - gap
    if limit <= 0:
        return False
    cx, cy = ring[1], ring[2]
    if inner[0] in ("circle", "ring"):
        return math.hypot(inner[1] - cx, inner[2] - cy) + _outer_r(inner) <= limit + 1e-9
    return all(math.hypot(px - cx, py - cy) <= limit + 1e-9
               for px, py in _corners(inner[1], inner[2], inner[3], inner[4]))


def _sep_outside(a, b, gap):
    """Separation test treating both shapes as solid, ignoring any bore."""
    ka, kb = a[0], b[0]
    if ka in ("circle", "ring") and kb in ("circle", "ring"):
        return math.hypot(a[1]-b[1], a[2]-b[2]) >= _outer_r(a) + _outer_r(b) + gap - 1e-9
    if ka == "rect" and kb == "rect":
        return (abs(a[1]-b[1]) >= (a[3]+b[3])/2 + gap - 1e-9 or
                abs(a[2]-b[2]) >= (a[4]+b[4])/2 + gap - 1e-9)
    c, r = (a, b) if ka in ("circle", "ring") else (b, a)
    nx = max(r[1]-r[3]/2, min(c[1], r[1]+r[3]/2))
    ny = max(r[2]-r[4]/2, min(c[2], r[2]+r[4]/2))
    return math.hypot(c[1]-nx, c[2]-ny) >= _outer_r(c) + gap - 1e-9


def _clear(a, b, gap):
    """
    Two shapes coexist if they are apart OR one is nested in the other's bore.

    The bore test only runs when a ring is actually involved. It used to run
    on every pair: two calls that immediately returned False for the other
    99% of comparisons, half a million of them on a single job.
    """
    if a[0] == "ring" or b[0] == "ring":
        if fits_in_bore(a, b, gap) or fits_in_bore(b, a, gap):
            return True
    return _sep_outside(a, b, gap)


def _inside(sh, width, length, edge):
    r = _outer_r(sh)
    if sh[0] == "rect":
        return (sh[1]-sh[3]/2 >= edge-1e-9 and sh[2]-sh[4]/2 >= edge-1e-9 and
                sh[1]+sh[3]/2 <= width-edge+1e-9 and sh[2]+sh[4]/2 <= length-edge+1e-9)
    return (sh[1]-r >= edge-1e-9 and sh[2]-r >= edge-1e-9 and
            sh[1]+r <= width-edge+1e-9 and sh[2]+r <= length-edge+1e-9)


def shape_area(sh):
    """Material the shape actually uses. A ring's bore is NOT its material."""
    if sh[0] == "circle":
        return math.pi * (sh[3]/2)**2
    if sh[0] == "ring":
        return math.pi * ((sh[3]/2)**2 - (sh[4]/2)**2)
    return sh[3] * sh[4]


def check_shapes(placed, gap, width, length, edge=0.0):
    """Ground truth. Nothing leaves this module without passing it."""
    for sh in placed:
        if not _inside(sh, width, length, edge):
            return False, f"a {sh[0]} falls outside the plate"
    for i in range(len(placed)):
        for j in range(i+1, len(placed)):
            if not _clear(placed[i], placed[j], gap):
                return False, f"a {placed[i][0]} and a {placed[j][0]} overlap"
    return True, ""


def _spec_shape(spec, x, y):
    k = spec[0]
    if k == "circle":
        return ("circle", x, y, spec[1])
    if k == "ring":
        return ("ring", x, y, spec[1], spec[2])
    return ("rect", x, y, spec[1], spec[2])


def _forms(spec, rotate=True):
    """Orientations worth trying. Only rectangles have any."""
    if spec[0] != "rect" or not rotate or spec[1] == spec[2]:
        return [spec]
    return [spec, ("rect", spec[2], spec[1])]


def _clear_x(cand, q, gap):
    """
    Smallest x at which `cand`, held at its current y, gets past `q`.

    WHY THIS EXISTS
        The scan below used to step across the whole plate a few millimetres
        at a time and re-test every placed shape at every stop. On a plate of
        20-odd blanks that is millions of distance tests, and three plates of
        mixed work took the better part of fifteen seconds.

        When a candidate is blocked by `q`, every position to the left of the
        point where it clears `q` is blocked by `q` too - the y is fixed, so
        the only way past is horizontally. So instead of inching forward, jump
        straight to that point. The scan then costs one test per obstacle per
        row instead of one per grid cell, and the answer is identical: no
        position that could have fitted is ever skipped.

    Returns the x to jump to, or None when no horizontal move clears `q`
    (the caller falls back to a plain step).
    """
    kc, kq = cand[0], q[0]
    cx, cy = cand[1], cand[2]

    if kq in ("circle", "ring"):
        rq = _outer_r(q)
        if kc in ("circle", "ring"):
            need = _outer_r(cand) + rq + gap
            dy = cy - q[2]
            if abs(dy) >= need:
                return None
            return q[1] + math.sqrt(max(need * need - dy * dy, 0.0))
        # rect candidate against a disc: closest point on the rect to centre
        w, h = cand[3], cand[4]
        need = rq + gap
        ny = max(cy - h / 2.0, min(q[2], cy + h / 2.0))
        dy = q[2] - ny
        if abs(dy) >= need:
            return None
        return q[1] + math.sqrt(max(need * need - dy * dy, 0.0)) + w / 2.0

    # q is a rectangle
    qw, qh = q[3], q[4]
    if kc in ("circle", "ring"):
        rc = _outer_r(cand)
        need = rc + gap
        ny = max(q[2] - qh / 2.0, min(cy, q[2] + qh / 2.0))
        dy = cy - ny
        if abs(dy) >= need:
            return None
        return q[1] + qw / 2.0 + math.sqrt(max(need * need - dy * dy, 0.0))

    w, h = cand[3], cand[4]
    if abs(cy - q[2]) >= (h + qh) / 2.0 + gap:
        return None
    return q[1] + (w + qw) / 2.0 + gap


def _gap_points(placed, limit=48):
    """
    Positions in the holes between discs, which a plain grid finds only by
    luck and only if its step happens to land there.

    Two families, and both are needed:

      pair midpoints    on a square grid the hole between four discs sits
                        exactly halfway along the diagonal of the outer two,
                        so the midpoint of a diagonal pair IS the hole centre
      triangle centroids a staggered grid has three-disc holes instead, and
                        their centres are not the midpoint of anything

    Only mutually close discs are paired up - the midpoint of two discs at
    opposite ends of the plate is open sheet the grid scan would have found
    anyway, and pairing everything with everything is O(n^2) of noise.
    """
    circ = [p for p in placed if p[0] in ("circle", "ring")][:limit]
    n = len(circ)
    if n < 2:
        return []
    near, pts = [], []
    for i in range(n):
        xi, yi, ri = circ[i][1], circ[i][2], _outer_r(circ[i])
        for j in range(i + 1, n):
            xj, yj, rj = circ[j][1], circ[j][2], _outer_r(circ[j])
            if math.hypot(xi - xj, yi - yj) <= 2.3 * (ri + rj):
                near.append((i, j))
                pts.append(((xi + xj) / 2.0, (yi + yj) / 2.0))
    adj = {}
    for i, j in near:
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    for i, j in near:
        for k in adj.get(i, ()) & adj.get(j, ()):
            if k > j:
                pts.append(((circ[i][1] + circ[j][1] + circ[k][1]) / 3.0,
                            (circ[i][2] + circ[j][2] + circ[k][2]) / 3.0))
    return pts


def _place_in_bore(spec, ring, placed, gap, step, rotate=True, resume=None):
    """
    Fit `spec` anywhere inside `ring`'s bore, not just dead centre.

    This used to try the bore centre and nothing else, which meant exactly one
    part could ever go inside a ring: the second one was offered the same
    point, found the first already sitting there, and was sent out to open
    plate. A 300 mm bore holding one 80 mm block was throwing away the rest of
    a disc that gets cut out regardless.

    Two things keep the scan cheap:

      near      only blanks close enough to reach into the bore can block a
                candidate confined to it, and that set is worked out once per
                call rather than once per position
      resume    the same monotonicity the open-plate scan relies on - nothing
                leaves the plate, so a spot that was taken stays taken, and
                the next identical block starts where the last one landed
                instead of re-walking the bore from the edge
    """
    limit = ring[4] / 2.0 - gap
    if limit <= 0:
        return None
    if resume is None:
        resume = {}
    rid = id(ring)
    near = [q for q in placed
            if q is not ring
            and math.hypot(q[1] - ring[1], q[2] - ring[2]) <= limit + _outer_r(q) + gap]

    for form in _forms(spec, rotate):
        key = ("bore", form, rid)
        st = resume.get(key)
        if st == "done":
            continue
        half_w = form[1] / 2 if form[0] == "rect" else _outer_r(("circle", 0, 0, form[1]))
        half_h = form[2] / 2 if form[0] == "rect" else half_w
        if half_w > limit or half_h > limit:
            resume[key] = "done"
            continue
        y, x_first = st if st else (ring[2] - limit + half_h, ring[1] - limit + half_w)
        while y <= ring[2] + limit - half_h + 1e-9:
            x, x_first = (x_first if x_first is not None
                          else ring[1] - limit + half_w), None
            while x <= ring[1] + limit - half_w + 1e-9:
                cand = _spec_shape(form, x, y)
                if fits_in_bore(cand, ring, gap) and all(
                        _clear(cand, q, gap) for q in near):
                    resume[key] = (y, x)
                    return cand
                x += step
            y += step
        resume[key] = "done"
    return None


def _try_place(spec, placed, gap, width, length, edge, step, rotate=True,
               hints=None, resume=None):
    """
    `resume` carries scan state between calls on the SAME plate.

    Nothing is ever removed from `placed` while a plate is being filled, so a
    position that was rejected once is rejected for good. That makes the scan
    monotone, and it means the tenth identical pad does not have to re-walk
    the nine-tenths of the plate the first nine already proved full - it picks
    up where the last one succeeded. Same layout, a fraction of the work.

    Pass a fresh dict per plate. Passing one across plates would be wrong,
    because an empty plate is not more full than the last one.
    """
    if resume is None:
        resume = {}

    # A bore first, always. The disc inside a ring is cut out regardless, so a
    # part that fits there is free plate - it must never be given open sheet
    # while an empty bore is sitting on the same plate.
    for ring in (p for p in placed if p[0] == "ring"):
        got = _place_in_bore(spec, ring, placed, gap, step, rotate, resume)
        if got:
            return got

    # Hole centres before open sheet. A part dropped into the waste between
    # four discs costs no extra plate at all, so it must be offered that
    # space before it is offered clean steel further along - otherwise the
    # hole stays scrap and the plate has to grow to take the part.
    for hx, hy in (hints or ()):
        for form in _forms(spec, rotate):
            cand = _spec_shape(form, hx, hy)
            if _inside(cand, width, length, edge) and all(
                    _clear(cand, q, gap) for q in placed):
                return cand

    for form in _forms(spec, rotate):
        if resume.get(form) == "done":
            continue
        half_w = form[1]/2 if form[0] == "rect" else _outer_r(("circle",0,0,form[1]))
        half_h = form[2]/2 if form[0] == "rect" else half_w
        mark = resume.get(form)
        y, x_first = mark if mark else (edge + half_h, edge + half_w)
        while y <= length - edge + 1e-9:
            # Only shapes whose vertical extent reaches this row can possibly
            # block it, and that does not change as x moves. Working it out
            # once per row instead of once per candidate position is the
            # difference between testing every blank on the plate a few
            # thousand times and testing a handful.
            lo, hi = y - half_h - gap, y + half_h + gap
            row_shapes = [q for q in placed
                          if (q[2] - _half_h(q)) <= hi and (q[2] + _half_h(q)) >= lo]
            x, x_first = (x_first if x_first is not None else edge + half_w), None
            while x <= width - edge + 1e-9:
                cand = _spec_shape(form, x, y)
                nxt = x + step
                blocked = False
                for q in row_shapes:
                    if _clear(cand, q, gap):
                        continue
                    blocked = True
                    jump = _clear_x(cand, q, gap)
                    if jump is not None and jump > nxt:
                        nxt = jump
                if not blocked and _inside(cand, width, length, edge):
                    resume[form] = (y, x)
                    return cand
                x = nxt
            y += step
        resume[form] = "done"
    return None


def nest_plate(specs, gap, width, length, edge=0.0, step=None):
    """
    Nest every shape it can onto ONE plate.

    `specs` is [("circle", d) | ("ring", od, bore) | ("rect", w, h), ...].

    TWO ORDERINGS ARE TRIED, because neither wins on its own:

      circles first  the hexagonal grid is optimal for equal discs, but laid
                     down first it can swallow the whole plate and leave a
                     large rectangle homeless
      big first      places the awkward shapes while there is room, then fits
                     discs around them - better on mixed work, worse on a
                     plate of nothing but discs

    Whichever places more material wins. Both are validated.

    Returns (placed, leftover_indices, origin) where origin[k] is the index in
    `specs` that placed[k] came from. The two lists are built together and
    stay in step; deriving one from the other afterwards silently mismatches
    parts, because `placed` is in placement order and the index list is not.
    """
    if not specs:
        return [], [], []
    best = None
    for mode in ("circles_first", "big_first"):
        got = _nest_once(specs, gap, width, length, edge, step, mode)
        placed = got[0]
        # Rank by AREA placed, not by count: fitting one large rectangle is
        # worth more than three small discs, because whatever is left over
        # has to go on another plate.
        #
        # Bores break the tie. A part nested in a bore costs no plate at all,
        # so a layout that uses one is strictly better than an identical
        # layout that does not - and without this the two orderings tie and
        # the bore-using one gets thrown away.
        nested = sum(1 for a in placed for b in placed
                     if a is not b and b[0] == "ring" and fits_in_bore(a, b, gap))
        score = (sum(shape_area(x) for x in placed), nested, len(placed))
        if best is None or score > best[0]:
            best = (score, got)
    return best[1]


def _nest_once(specs, gap, width, length, edge, step, mode):
    placed, origin, taken = [], [], set()
    resume = {}                 # scan state for THIS plate only
    sizes = [max(s[1:]) for s in specs]
    step = step or max(4.0, min(sizes) / 10.0)

    def put(i, shape):
        placed.append(shape)
        origin.append(i)
        taken.add(i)

    # --- big / awkward shapes first, if that is the mode ------------------
    if mode == "big_first":
        first = sorted((i for i, sp in enumerate(specs) if sp[0] != "circle"),
                       key=lambda i: -shape_area(_spec_shape(specs[i], 0, 0)))
        for i in first:
            got = _try_place(specs[i], placed, gap, width, length, edge, step,
                             resume=resume)
            if got:
                put(i, got)

    # --- uniform circles keep the optimal grid ---------------------------
    circ = [i for i, s in enumerate(specs) if s[0] == "circle" and i not in taken]
    if circ and mode == "circles_first":
        ds = [specs[i][1] for i in circ]
        if max(ds) - min(ds) <= SAME_D_TOL:
            pts, _name = best_uniform(sum(ds)/len(ds), gap, width, length, edge)
            for i, (px, py, pd) in zip(circ, pts):
                put(i, ("circle", px, py, pd))
    elif circ:
        # big_first mode: discs go in around whatever is already down, so the
        # grid cannot be used and each one is placed on its merits.
        for i in sorted(circ, key=lambda i: -specs[i][1]):
            got = _try_place(specs[i], placed, gap, width, length, edge, step,
                             resume=resume)
            if got:
                put(i, got)

    # --- pass 2: everything else, largest first --------------------------
    # Smallest last is deliberate: a small plate offered the holes between
    # discs before the big shapes have positions would take open sheet the
    # big one then cannot use.
    rest = sorted((i for i in range(len(specs)) if i not in taken),
                  key=lambda i: -shape_area(_spec_shape(specs[i], 0, 0)))
    hints = _gap_points(placed)
    # A shape that does not fit will not fit the next one of its kind either,
    # unless something has been placed in between - nothing else on the plate
    # has moved. Twenty identical pads that cannot fit used to cost twenty
    # full scans of the plate; now they cost one. The set is dropped the
    # moment anything lands, because that changes the plate.
    hopeless = set()
    for i in rest:
        if specs[i] in hopeless:
            continue
        got = _try_place(specs[i], placed, gap, width, length, edge, step,
                         hints=hints, resume=resume)
        if got:
            put(i, got)
            hopeless.clear()
            if got[0] in ("circle", "ring"):
                hints = _gap_points(placed)
        else:
            hopeless.add(specs[i])

    # --- pass 3: fill the ring bores -------------------------------------
    rings = [p for p in placed if p[0] == "ring"]
    if rings:
        # Smallest first here, the opposite of every other pass. A bore is a
        # fixed pocket of free plate: filling it with small parts packs more of
        # them in, and anything too big for it was going to need open plate
        # anyway. Every spare part is offered every bore, so two or three
        # blocks can share one ring.
        spare = sorted((i for i in range(len(specs)) if i not in taken),
                       key=lambda i: shape_area(_spec_shape(specs[i], 0, 0)))
        for i in spare:
            for ring in rings:
                cand = _place_in_bore(specs[i], ring, placed, gap, step,
                                      resume=resume)
                if cand:
                    put(i, cand)
                    break

    left = [i for i in range(len(specs)) if i not in taken]
    ok, why = check_shapes(placed, gap, width, length, edge)
    if not ok:
        # Never hand back an invalid plate. A layout that merely looks
        # plausible is worse than none, because it becomes a cutting plan.
        return [], list(range(len(specs))), []
    return placed, left, origin


def nest_shapes_all(specs, gap, width, length, edge=0.0):
    """
    Fill plate after plate. Returns (plates, indices_that_never_fitted).

    Each plate is {"placed": [...], "keys": [...]} with keys[k] the index in
    `specs` of placed[k] - same order, one for one, so the caller can tell
    which part each outline on the plate actually is.
    """
    plates, pending, guard = [], list(range(len(specs))), 0
    while pending and guard < 500:
        guard += 1
        sub = [specs[i] for i in pending]
        placed, left, origin = nest_plate(sub, gap, width, length, edge)
        if not placed:
            break
        plates.append({"placed": placed,
                       "keys": [pending[i] for i in origin]})
        pending = [pending[i] for i in left]
    return plates, pending


def utilisation(placed, width, length):
    """Fraction of the plate that becomes part material, bores excluded."""
    if not (width and length):
        return 0.0
    tot = 0.0
    for sh in placed:
        tot += shape_area(sh) if isinstance(sh, tuple) and isinstance(sh[0], str) \
            else math.pi * (sh[2] / 2.0) ** 2
    return tot / (width * length) * 100.0
