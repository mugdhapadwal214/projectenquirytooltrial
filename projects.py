"""
projects.py — the project store.

WHAT A PROJECT IS
    Everything the user typed for one job or enquiry: the parts on every tab,
    the plate and cutting settings, and the ledger of what has been booked.
    Opening a project restores all of it exactly as it was left.

WHAT A PROJECT IS NOT
    It does not own the offcut rack. The rack is deliberately GLOBAL, shared
    by every project, because that is how a real stores works — a remnant left
    over from last month's job is available for this month's, and locking it
    inside the project that created it would strand usable steel. Each remnant
    still records which job produced it, so the trail is intact.

LAYOUT
    data/projects.json        index — id, name, client, dates, counts
    data/projects/P-0001.json full state for one project
    data/remnants.json        the shared rack, outside all of this

    One file per project rather than one big file, so a project can be copied,
    backed up or sent to someone on its own, and so a corrupt write can only
    ever damage one of them.

SAVE FILES
    export()/import_file() move a project in and out as a single self-contained
    .rmp document — the same idea as the .rcp files in the reliability app.
    Everything needed to reopen the project travels inside it, so it can be
    e-mailed or archived without the rest of the installation.
"""
try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

import datetime
import json
import os
import re
import threading

_LOCK = threading.Lock()
_INDEX = _dp("projects.json")
_STORE = os.path.dirname(_dp("projects", "x"))

FILE_KIND = "rm-estimator-project"
FILE_VERSION = 1


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _blank_index():
    return {"next_id": 1, "projects": []}


def _read_index():
    try:
        with open(_INDEX, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("next_id", 1)
        d.setdefault("projects", [])
        return d
    except (OSError, ValueError):
        return _blank_index()


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)       # atomic: a crash can't leave a half-written file


def _state_path(pid):
    # The id is generated here, never user input, but a project can be imported
    # from a file someone else made — so the name is still checked before it is
    # used to build a path.
    if not re.fullmatch(r"P-\d{4,}", str(pid)):
        raise ValueError(f"Bad project id: {pid!r}")
    return os.path.join(_STORE, f"{pid}.json")


def _blank_state():
    return {"rows_by_type": {}, "settings": {}, "jobs": [], "next_job_id": 1}


# ------------------------------------------------------------------ index --

def all_projects():
    """Index entries only — enough to list them, without loading every state."""
    return _read_index()["projects"]


def create(name, client="", notes="", number=""):
    """
    `id` is the app's own internal key and never changes. `number` is the
    user's own project number — the one on their paperwork — and is what gets
    shown and printed on the RFQ. Keeping them separate means the user can use
    whatever numbering their office already runs on without it having to be
    unique to this app or safe to put in a filename.
    """
    with _LOCK:
        idx = _read_index()
        pid = f"P-{idx['next_id']:04d}"
        entry = {
            "id": pid,
            "number": (number or "").strip(),
            "name": (name or "Untitled project").strip(),
            "client": (client or "").strip(),
            "notes": (notes or "").strip(),
            "created": _now(),
            "modified": _now(),
            "parts": 0,
            "jobs": 0,
        }
        idx["next_id"] += 1
        idx["projects"].append(entry)
        _write_json(_INDEX, idx)
        _write_json(_state_path(pid), _blank_state())
        return entry


def rename(pid, name=None, client=None, notes=None, number=None):
    with _LOCK:
        idx = _read_index()
        for p in idx["projects"]:
            if p["id"] == pid:
                if name is not None:
                    p["name"] = name.strip() or p["name"]
                if client is not None:
                    p["client"] = client.strip()
                if notes is not None:
                    p["notes"] = notes.strip()
                if number is not None:
                    p["number"] = number.strip()
                p["modified"] = _now()
                _write_json(_INDEX, idx)
                return p
        return None


def delete(pid):
    with _LOCK:
        idx = _read_index()
        before = len(idx["projects"])
        idx["projects"] = [p for p in idx["projects"] if p["id"] != pid]
        if len(idx["projects"]) == before:
            return False
        _write_json(_INDEX, idx)
        try:
            os.remove(_state_path(pid))
        except OSError:
            pass
        return True


def entry(pid):
    for p in _read_index()["projects"]:
        if p["id"] == pid:
            return p
    return None


# ------------------------------------------------------------------ state --

def load_state(pid):
    try:
        with open(_state_path(pid), "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return _blank_state()
    b = _blank_state()
    b.update(d)
    return b


def save_state(pid, state):
    """
    Store the working state and refresh the index counters.

    Called on every change, debounced by the browser — so nothing is lost if
    the window closes, which is the whole reason projects exist rather than
    living in the page.
    """
    with _LOCK:
        cur = load_state(pid)
        # Whitelisted on purpose, so a stray key from the page cannot end up in
        # the file. Anything new the browser sends MUST be added here or it is
        # silently dropped on every save — which is exactly what happened to
        # the BOM until it was added.
        # Everything the page sends and expects back. Each of the last five was
        # being written by the browser and dropped here without a sound, so
        # status activities, assignments, plate size choices and the last
        # layout all vanished the moment a project was reopened - and the
        # status board then reported a finished job as never started.
        for k in ("rows_by_type", "settings", "jobs", "next_job_id", "bom",
                  "activities", "auto_meta", "plate_overrides",
                  "last_result", "last_payload_rows"):
            if k in state:
                cur[k] = state[k]
        _write_json(_state_path(pid), cur)

        idx = _read_index()
        for p in idx["projects"]:
            if p["id"] == pid:
                p["modified"] = _now()
                p["parts"] = sum(len(v) for v in (cur.get("rows_by_type") or {}).values())
                p["jobs"] = len(cur.get("jobs") or [])
                break
        _write_json(_INDEX, idx)
        return cur


# ------------------------------------------------------------- save files --

def export(pid):
    """One self-contained document: index entry plus full state."""
    e = entry(pid)
    if not e:
        return None
    return {
        "kind": FILE_KIND,
        "version": FILE_VERSION,
        "exported": _now(),
        "project": e,
        "state": load_state(pid),
    }


def import_file(doc, new_name=None):
    """
    Bring a .rmp file back in as a NEW project.

    Deliberately never overwrites an existing project: the file may have come
    from another machine where the ids mean something different, and silently
    replacing someone's work would be far worse than making a duplicate they
    can delete.
    """
    if not isinstance(doc, dict) or doc.get("kind") != FILE_KIND:
        raise ValueError("Not a project file — expected a .rmp saved by this app.")
    if int(doc.get("version", 0)) > FILE_VERSION:
        raise ValueError(
            f"That file was saved by a newer version (v{doc.get('version')}). "
            f"This build understands up to v{FILE_VERSION}.")

    src = doc.get("project") or {}
    e = create(new_name or src.get("name") or "Imported project",
               src.get("client", ""), src.get("notes", ""),
               src.get("number", ""))
    save_state(e["id"], doc.get("state") or _blank_state())
    return e


# -------------------------------------------------------------- job ledger --

def add_job(pid, source, lines, totals):
    """Append a booked job to THIS project's ledger."""
    with _LOCK:
        st = load_state(pid)
        rec = {
            "id": f"J-{st.get('next_job_id', 1):04d}",
            "booked": _now(),
            "source": source or "job",
            "lines": lines or [],
            "totals": totals or {},
        }
        st["next_job_id"] = st.get("next_job_id", 1) + 1
        st.setdefault("jobs", []).append(rec)
        _write_json(_state_path(pid), st)

        idx = _read_index()
        for p in idx["projects"]:
            if p["id"] == pid:
                p["jobs"] = len(st["jobs"])
                p["modified"] = _now()
                break
        _write_json(_INDEX, idx)
        return rec


def jobs(pid):
    return load_state(pid).get("jobs", [])


def drop_jobs(pid, job_ids):
    """
    Remove superseded jobs from the ledger.

    WHY THIS IS NEEDED
        summary() adds up EVERY job in the project. So when parts that were
        already booked to J-0001 are reopened and recalculated, committing the
        result used to append J-0002 containing those same parts - and the
        project then claimed to have made both. Do it once more and the same
        drum shell is counted three times, with the plate cost, the raw
        material weight and therefore the RFQ all inflated to match.

        A recalculation of the same parts REPLACES the job they came from. It
        does not add to it.
    """
    ids = {str(i) for i in (job_ids or []) if i}
    if not ids:
        return []
    with _LOCK:
        st = load_state(pid)
        keep, dropped = [], []
        for j in st.get("jobs", []):
            (dropped if j.get("id") in ids else keep).append(j)
        if not dropped:
            return []
        st["jobs"] = keep
        _write_json(_state_path(pid), st)

        idx = _read_index()
        for pr in idx["projects"]:
            if pr["id"] == pid:
                pr["jobs"] = len(keep)
                pr["modified"] = _now()
                break
        _write_json(_INDEX, idx)
        return [j.get("id") for j in dropped]


def clear_jobs(pid):
    """
    Set the project's job history aside. Returns what it did.

    TWO THINGS THIS DELIBERATELY DOES NOT DO.

    It does not reset `next_job_id`. The old version set it back to 1, so the
    next job booked was J-0001 again - colliding with archived RFQs, with the
    `_booked` stamps still sitting on parts rows, and with the `from` on every
    offcut harvested by the original J-0001. Two different jobs with one id,
    and nothing to tell them apart afterwards. Job numbers are now permanent:
    they are only ever handed out once.

    It does not delete. The jobs are moved to `cleared_jobs` with a timestamp,
    so a history wiped by accident can still be read back out of the project
    file. There is no undo button, but the data is there to build one.

    It also does not touch inventory, and that is a real limitation rather
    than a decision: offcuts those jobs consumed stay consumed, and offcuts
    they produced stay in the rack. Physical steel does not un-cut itself
    because a record was deleted. The caller is told the counts so it can say
    so plainly.
    """
    with _LOCK:
        st = load_state(pid)
        cleared = list(st.get("jobs", []))
        if not cleared:
            return {"cleared": 0, "next_job_id": int(st.get("next_job_id") or 1),
                    "job_ids": []}

        st.setdefault("cleared_jobs", []).append(
            {"at": _now(), "jobs": cleared})
        st["jobs"] = []
        # next_job_id is NOT reset. See above.
        _write_json(_state_path(pid), st)

        idx = _read_index()
        for pr in idx["projects"]:
            if pr["id"] == pid:
                pr["jobs"] = 0
                pr["modified"] = _now()
                break
        _write_json(_INDEX, idx)

        return {"cleared": len(cleared),
                "next_job_id": int(st.get("next_job_id") or 1),
                "job_ids": [j.get("id") for j in cleared]}


def summary(pid):
    """Running totals across every booked job in this project."""
    made = bought = 0
    plate_cost = cut_cost = purchase_cost = weight = 0.0
    plates = 0
    by_type = {}

    for j in jobs(pid):
        t = j.get("totals", {})
        plate_cost += float(t.get("plate_cost") or 0)
        cut_cost += float(t.get("cut_cost") or 0)
        purchase_cost += float(t.get("purchase_cost") or 0)
        weight += float(t.get("plate_weight_kg") or 0)
        plates += int(t.get("plates_bought") or 0)
        for ln in j.get("lines", []):
            q = int(ln.get("qty") or 0)
            k = ln.get("component_type") or "item"
            e = by_type.setdefault(k, {"made": 0, "bought": 0, "cost": 0.0})
            if ln.get("procurement") == "purchased":
                bought += q
                e["bought"] += q
                e["cost"] += float(ln.get("purchase_cost") or 0)
            else:
                made += q
                e["made"] += q
                e["cost"] += (float(ln.get("material_cost") or 0)
                              + float(ln.get("cut_cost") or 0))

    parts_cost = sum(e["cost"] for e in by_type.values())
    return {
        "jobs": len(jobs(pid)),
        "parts_cost": parts_cost,
        "made_qty": made, "bought_qty": bought, "total_qty": made + bought,
        "plate_cost": plate_cost, "cut_cost": cut_cost,
        "purchase_cost": purchase_cost,
        "grand_total": plate_cost + cut_cost + purchase_cost,
        "plate_weight_kg": weight, "plates_bought": plates,
        "by_type": by_type,
    }
