"""
inventory.py — remnant (offcut) stock.

THE IDEA
------------------------------------------------------------------
When a job opens a 1250 x 2500 plate and uses a third of it, the rest is
not scrap — it is a large clean rectangle sitting against the wall. The
next job that needs the same material and thickness should cut from that
before anyone buys new plate.

This module does three things:

  1. HARVEST   Work out which rectangles are actually left over from a
               nested plate, and which of those are big enough to be
               worth keeping.
  2. STORE     Keep them in data/remnants.json — plain text, offline,
               editable by hand if the storeman moves something.
  3. REUSE     Offer them back to the next job, largest first, before
               any new plate is costed.

HARVESTING: WHICH RECTANGLES ARE REAL
------------------------------------------------------------------
Shelf packing leaves offcut in two clearly-shaped places:

    +--------------------------------+
    | part part part |  shelf tail   |   <- tail of each shelf
    +----------------+---------------+
    | part part |     shelf tail     |
    +--------------------------------+
    |                                |
    |        bottom remainder        |   <- everything below the last shelf
    |                                |
    +--------------------------------+

The bottom remainder is usually the big one and the one worth keeping.
Shelf tails are narrower and often too thin to be useful, which is what
the minimum size filter is for.

What is NOT harvested is the material between and around the blanks
inside a shelf — the corners around a circle, the gaps between parts.
That is genuinely unusable and is correctly left as scrap. Reporting it
as "free area" but not as a remnant is the honest distinction.

COSTING
------------------------------------------------------------------
Remnant material has already been paid for by the job that opened the
plate. Charging the next job full rate for it would bill the same steel
twice. So by default a remnant is issued at zero material cost and the
saving is reported. Set `remnant_charge_pct` above 0 if your accounts
would rather carry remnants at a transfer price.

Cutting cost still applies to remnants — the torch does not care where
the plate came from.
"""
try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp


import datetime as _dt
import json
import os
import threading

_LOCK = threading.Lock()

# A remnant has to be worth walking to the rack for.
DEFAULT_MIN_SIDE_MM = 200.0
DEFAULT_MIN_AREA_M2 = 0.10


# ── Where the stock lives ───────────────────────────────────────────────────
# The offcut stock is no longer a file of this module. It is the Raw Material
# list of the Store section in the Project Enquiry tool, so that a plate left
# over from an estimate and a plate the storeman entered by hand are one list,
# and both are offered back to the next job.
try:
    from enquiry_link import store as _store_mod
    _store = _store_mod()
except ImportError:                           # standalone estimator: fall back to the old file
    _store = None


def _path():
    return _dp("remnants.json")


def _blank():
    return {"next_id": 1, "remnants": []}


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _file_load() -> dict:
    p = _path()
    if not os.path.exists(p):
        return _blank()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("next_id", 1)
        d.setdefault("remnants", [])
        return d
    except (ValueError, OSError):
        return _blank()


def _file_save(data: dict) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _from_store():
    """Store rows -> remnant records. A row with a thickness and both sides is
    usable stock; quantity greater than one becomes that many pieces."""
    out = []
    for i, r in enumerate(_store.raw_rows()):
        if str(r.get("status", "")).lower() == "used":
            continue
        thk, w, l = _num(r.get("thk")), _num(r.get("width")), _num(r.get("length"))
        if not (thk and w and l):
            continue
        qty = int(_num(r.get("qty")) or 1)
        for c in range(max(1, qty)):
            out.append({
                "id": f"S-{i + 1}" + (f".{c + 1}" if qty > 1 else ""),
                "row": i,
                "material": r.get("material", ""),
                "thickness": thk, "width": round(w, 1), "length": round(l, 1),
                "area_m2": round(w * l / 1e6, 4),
                "source": r.get("source") or r.get("location", ""),
                "created": str(r.get("added", ""))[:10],
                "status": "available",
            })
    return out


def load() -> dict:
    """Kept for callers that want the whole stock in the old shape."""
    if _store is None:
        return _file_load()
    return {"next_id": 1, "remnants": _from_store()}


def save(data: dict) -> None:
    if _store is None:
        _file_save(data)
    # with the Store in charge there is nothing to write back wholesale


def available(material: str = None, thickness: float = None) -> list:
    """Stock in the Store, largest first; optionally only what fits this job."""
    out = load()["remnants"]
    out = [r for r in out if r.get("status", "available") == "available"]
    if material is not None:
        out = [r for r in out if str(r["material"]).strip().lower() == str(material).strip().lower()]
    if thickness is not None:
        out = [r for r in out if abs(float(r["thickness"]) - float(thickness)) < 1e-9]
    return sorted(out, key=lambda r: r["width"] * r["length"], reverse=True)


def add(items: list, source: str = "") -> list:
    """Put harvested rectangles into the Store as raw material."""
    if _store is None:
        with _LOCK:
            d = _file_load()
            today = _dt.date.today().isoformat()
            added = []
            for it in items:
                rid = f"R-{d['next_id']:04d}"
                d["next_id"] += 1
                rec = {"id": rid, "material": it["material"], "thickness": float(it["thickness"]),
                       "width": round(float(it["width"]), 1), "length": round(float(it["length"]), 1),
                       "area_m2": round(float(it["width"]) * float(it["length"]) / 1e6, 4),
                       "source": source or it.get("source", ""), "created": today,
                       "status": "available"}
                d["remnants"].append(rec); added.append(rec)
            _file_save(d)
            return added
    with _LOCK:
        rows = _store.raw_rows()
        added = []
        for it in items:
            rec = {
                "material": str(it.get("material", "")), "form": "Offcut",
                "thk": str(it.get("thickness", "")), "length": str(round(float(it.get("length") or 0), 1)),
                "width": str(round(float(it.get("width") or 0), 1)), "qty": "1", "unit": "Nos",
                "location": "", "source": source or it.get("source", ""), "vendor": "",
                "rate": "", "remarks": "from Project Estimation",
                "added": _dt.datetime.now().isoformat(timespec="seconds"),
            }
            # Twenty-six identical offcuts are one line of stock with a quantity
            # of 26, not twenty-six lines the storeman has to read one by one.
            same = None
            for r in rows:
                if (str(r.get("material", "")) == rec["material"] and str(r.get("thk", "")) == rec["thk"]
                        and str(r.get("length", "")) == rec["length"]
                        and str(r.get("width", "")) == rec["width"]
                        and str(r.get("source", "")) == rec["source"]
                        and str(r.get("status", "")).lower() != "used"):
                    same = r
                    break
            if same is not None:
                same["qty"] = str(int(_num(same.get("qty")) or 1) + 1)
                same["added"] = rec["added"]
                added.append(same)
            else:
                rows.append(rec)
                added.append(rec)
        _store.write_raw_rows(rows)
    return added


def _row_of(rid):
    for r in _from_store():
        if r["id"] == rid:
            return r
    return None


def remove(rid: str) -> bool:
    if _store is None:
        with _LOCK:
            d = _file_load()
            n = len(d["remnants"])
            d["remnants"] = [r for r in d["remnants"] if r["id"] != rid]
            _file_save(d)
            return len(d["remnants"]) < n
    rec = _row_of(rid)
    if rec is None:
        return False
    with _LOCK:
        rows = _store.raw_rows()
        i = rec["row"]
        if i >= len(rows):
            return False
        qty = int(_num(rows[i].get("qty")) or 1)
        if qty > 1:
            rows[i]["qty"] = str(qty - 1)                  # one piece of several
        else:
            rows.pop(i)
        _store.write_raw_rows(rows)
    return True


def consume(rid: str, leftover: list = None, source: str = "") -> None:
    """A piece has been cut: take it out of the Store, put anything still
    usable back as a smaller piece."""
    remove(rid)
    if leftover:
        add(leftover, source=source or f"offcut of {rid}")


AUTO_TAG = "auto"          # rows the estimator keeps in step with the latest calculation


def _auto_source(project_id):
    return f"{AUTO_TAG}:{project_id or 'no-project'}"


def sync_auto(project_id, items, note="") -> list:
    """Put the leftover of the CURRENT calculation into the Store, replacing what
    the previous calculation of the same project left there. Recalculating a job
    therefore never stacks up phantom offcut — the Store always shows what the
    latest numbers on screen actually leave over."""
    tag = _auto_source(project_id)
    if _store is None:
        return []
    with _LOCK:
        rows = [r for r in _store.raw_rows() if not str(r.get("source", "")).startswith(tag)]
        for it in items or []:
            rows.append({
                "material": str(it.get("material", "")), "form": "Offcut",
                "thk": str(it.get("thickness", "")),
                "length": str(round(float(it.get("length") or 0), 1)),
                "width": str(round(float(it.get("width") or 0), 1)),
                "qty": "1", "unit": "Nos", "location": "", "source": f"{tag} {note}".strip(),
                "vendor": "", "rate": "",
                "remarks": "leftover of the latest estimate — booked when the job is saved",
                "added": _dt.datetime.now().isoformat(timespec="seconds"),
            })
        _store.write_raw_rows(rows)
    return [r for r in _from_store() if str(r.get("source", "")).startswith(tag)]


def drop_auto(project_id) -> int:
    """Remove the provisional rows of a project — used when its job is booked and
    the same pieces come back as real stock."""
    if _store is None:
        return 0
    tag = _auto_source(project_id)
    with _LOCK:
        rows = _store.raw_rows()
        keep = [r for r in rows if not str(r.get("source", "")).startswith(tag)]
        _store.write_raw_rows(keep)
    return len(rows) - len(keep)


def clear() -> None:
    """Deliberately does nothing when the Store owns the stock — the estimator
    must never wipe the storeman's list."""
    if _store is None:
        _file_save(_blank())


# ---------------------------------------------------------------- harvest --

def free_rects(plate: dict, plate_w: float, plate_l: float, gap: float) -> list:
    """
    Rectangles left over on one shelf-packed plate.

    plate    : a layout entry from shelf_pack — {"shelves": [...], "items": [...]}
    plate_w  : the shelf/horizontal axis
    plate_l  : the stacking/vertical axis

    Returns [{"x","y","width","length"}], unfiltered.
    """
    # The packer now hands back the actual empty rectangles it was left with,
    # which is what should go on the rack. The shelf reading below stays for
    # layouts made by the older packing.
    if plate.get("free"):
        return [{"x": f["x"], "y": f["y"], "width": f["w"], "length": f["h"]}
                for f in plate["free"] if f["w"] > 0 and f["h"] > 0]
    out = []
    shelves = plate.get("shelves", [])
    for sh in shelves:
        tail = plate_w - sh["used"]
        if tail > 0:
            out.append({"x": sh["used"] + (gap if sh["used"] else 0),
                        "y": sh["y"],
                        "width": max(tail - (gap if sh["used"] else 0), 0.0),
                        "length": sh["h"]})
    used_h = sum(sh["h"] for sh in shelves) + gap * len(shelves)
    if plate_l - used_h > 0:
        out.append({"x": 0.0, "y": used_h,
                    "width": plate_w, "length": plate_l - used_h})
    return [r for r in out if r["width"] > 0 and r["length"] > 0]


def worth_keeping(rects: list, min_side=DEFAULT_MIN_SIDE_MM,
                  min_area_m2=DEFAULT_MIN_AREA_M2) -> list:
    """Filter harvested rectangles down to ones actually worth racking."""
    keep = []
    for r in rects:
        if min(r["width"], r["length"]) < min_side:
            continue
        if r["width"] * r["length"] / 1e6 < min_area_m2:
            continue
        keep.append(r)
    return sorted(keep, key=lambda r: r["width"] * r["length"], reverse=True)
