"""
jobs.py — the job ledger behind the cumulative summary.

WHY THIS EXISTS
    Booking a job retires its offcut and marks its parts produced, which is
    correct — but it also means those parts vanish from the working table, and
    with them any record of what has actually been made. Over several batches
    you lose the one number an estimator most wants: how much of this project
    is done, and what did it cost.

    So every booking is appended here as a permanent line-by-line record. The
    summary page reads the ledger and shows the running total across all jobs,
    with the current un-booked estimate alongside it as pending.

WHY IT IS A FILE, NOT MEMORY
    The rack persists in data/remnants.json, so the history that produced it
    has to persist too. A summary that resets on refresh, while the offcut it
    created is still on the rack, would be worse than none — the two records
    would disagree.

MADE vs BOUGHT
    Each line carries `procurement`: "plate" for something cut from stock or
    offcut, "purchased" for a bought-in item. They are counted and costed
    separately all the way to the top line, because a plate estimator that
    folds a forged flange into "raw material" hides more than it shows.

STRUCTURE
    {
      "next_id": 3,
      "jobs": [
        {"id": "J-0001", "booked": "2026-08-22T09:15:02", "source": "Job ...",
         "lines": [ {component_type, part_no, label, spec, material,
                     thickness, qty, procurement, weight_kg, material_cost,
                     cut_cost, purchase_cost, source_plate}, ... ],
         "totals": {plate_cost, cut_cost, purchase_cost, total_cost,
                    plate_weight_kg, plates_bought, remnants_used,
                    remnants_racked}}
      ]
    }
"""
try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

import datetime
import json
import os
import threading

_LOCK = threading.Lock()
_FILE = _dp("jobs.json")


def _blank():
    return {"next_id": 1, "jobs": []}


def _load():
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("next_id", 1)
        d.setdefault("jobs", [])
        return d
    except (OSError, ValueError):
        return _blank()


def _save(d):
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _FILE)          # atomic, so a crash can't truncate the ledger


def all_jobs():
    return _load()["jobs"]


def add_job(source, lines, totals):
    """Append one booked job. Returns the stored record."""
    with _LOCK:
        d = _load()
        rec = {
            "id": f"J-{d['next_id']:04d}",
            "booked": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": source or "job",
            "lines": lines or [],
            "totals": totals or {},
        }
        d["next_id"] += 1
        d["jobs"].append(rec)
        _save(d)
        return rec


def remove(job_id):
    """
    Delete one job from the ledger.

    Note this does NOT put its offcut back on the rack or un-mark its parts —
    stock and history are separate records on purpose, and silently reversing
    a material movement because someone tidied a report would be worse than
    leaving the two to be reconciled by hand.
    """
    with _LOCK:
        d = _load()
        before = len(d["jobs"])
        d["jobs"] = [j for j in d["jobs"] if j["id"] != job_id]
        changed = len(d["jobs"]) != before
        if changed:
            _save(d)
        return changed


def clear():
    with _LOCK:
        _save(_blank())


def summary():
    """Running totals across every booked job, split made vs bought."""
    jobs = all_jobs()
    made_qty = bought_qty = 0
    plate_cost = cut_cost = purchase_cost = 0.0
    weight = 0.0
    plates = 0
    by_type = {}

    for j in jobs:
        t = j.get("totals", {})
        plate_cost += float(t.get("plate_cost") or 0)
        cut_cost += float(t.get("cut_cost") or 0)
        purchase_cost += float(t.get("purchase_cost") or 0)
        weight += float(t.get("plate_weight_kg") or 0)
        plates += int(t.get("plates_bought") or 0)
        for ln in j.get("lines", []):
            q = int(ln.get("qty") or 0)
            key = ln.get("component_type") or "item"
            e = by_type.setdefault(key, {"made": 0, "bought": 0, "cost": 0.0})
            if ln.get("procurement") == "purchased":
                bought_qty += q
                e["bought"] += q
                e["cost"] += float(ln.get("purchase_cost") or 0)
            else:
                made_qty += q
                e["made"] += q
                e["cost"] += (float(ln.get("material_cost") or 0)
                              + float(ln.get("cut_cost") or 0))

    # Two different bases, and they must not be added together:
    #   parts_cost  = each part's own bounding box of material + its cutting
    #   plate_cost  = the whole plates actually bought, offcut included
    # parts_cost is always the smaller. The gap is the offcut, which is real
    # money — so the headline total uses plate_cost, and the per-component
    # table is labelled as the parts basis rather than pretending to match.
    parts_cost = sum(e["cost"] for e in by_type.values())

    return {
        "jobs": len(jobs),
        "parts_cost": parts_cost,
        "made_qty": made_qty,
        "bought_qty": bought_qty,
        "total_qty": made_qty + bought_qty,
        "plate_cost": plate_cost,
        "cut_cost": cut_cost,
        "purchase_cost": purchase_cost,
        "grand_total": plate_cost + cut_cost + purchase_cost,
        "plate_weight_kg": weight,
        "plates_bought": plates,
        "by_type": by_type,
    }
