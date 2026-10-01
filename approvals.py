"""
approvals.py — sign-off on an RFQ before it can be issued.

THE CHAIN
    An RFQ is submitted by whoever prepared it and then passes through the
    designations in users.DESIGNATIONS order: Project Engineer, Team Lead,
    Director. Sequential, not parallel - each
    should not be signing off commercials the engineer has not yet checked,
    and a manager approving before either is a rubber stamp.

    Set CHAIN_MODE = "parallel" below if that is wrong for your office; the
    rest of the module already handles both.

WHY A FROZEN SNAPSHOT
    The request stores a copy of exactly what was submitted - the material
    schedule, the vendor list, the totals. Approval attaches to THAT, not to
    the live project.

    Without this the whole thing is theatre: a director signs off, someone
    changes a thickness, and the RFQ that goes out is not the one that was
    approved. Any edit to the project after submission marks the request
    STALE, and it has to be resubmitted and re-approved.

WHAT IS NOT DECIDED HERE
    Whether a rejection at step 4 should return to step 1 or to step 3. It
    currently returns to the submitter and clears every approval, because a
    rejection usually means the scope changed. Change `reject` if your office
    works differently.
"""
import datetime
import hashlib
import json
import os
import threading

try:
    from .runtime_paths import data_path as _dp
    from .users import DESIGNATIONS
except ImportError:
    from runtime_paths import data_path as _dp
    from users import DESIGNATIONS

_LOCK = threading.Lock()

# The review chain. Three levels, matching users.DESIGNATIONS exactly.
#
# Purchaser and Manager remain valid designations a person can hold - they sign
# in and use the app - they simply do not sit on the RFQ approval chain. Keeping
# the chain separate from the designation list is what makes that possible;
# deriving one from the other would force every role to be an approver.
CHAIN = ["Project Engineer", "Team Lead", "Director"]
CHAIN_MODE = "sequential"          # or "parallel"

PENDING, APPROVED, REJECTED, STALE, ISSUED = (
    "pending", "approved", "rejected", "stale", "issued")


def _file():
    return _dp("approvals.json")


def _load():
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("next_id", 1)
        d.setdefault("requests", [])
        return d
    except (OSError, ValueError):
        return {"next_id": 1, "requests": []}


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _file())


def fingerprint(payload):
    """
    Stable hash of what is being approved.

    Used to notice that the project has changed under an open request. Sorted
    keys so an unrelated reordering does not look like an edit.
    """
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def all_requests(project_id=None):
    rs = _load()["requests"]
    if project_id:
        rs = [r for r in rs if r["project_id"] == project_id]
    return sorted(rs, key=lambda r: r["created"], reverse=True)


def get(rid):
    for r in _load()["requests"]:
        if r["id"] == rid:
            return r
    return None


def open_request_for(project_id):
    """The one live request for a project, if any."""
    for r in all_requests(project_id):
        if r["status"] in (PENDING, STALE):
            return r
    return None


def submit(project, items, vendors_list, submitted_by, note=""):
    """Open an approval request. Raises ValueError if one is already open."""
    if not items:
        raise ValueError("Nothing to approve - there is no raw material on "
                         "this project yet.")
    if not vendors_list:
        raise ValueError("Select at least one vendor before sending for approval.")

    with _LOCK:
        d = _load()
        for r in d["requests"]:
            if r["project_id"] == project["id"] and r["status"] in (PENDING, STALE):
                raise ValueError(f"{r['id']} is already open for this project. "
                                 f"Withdraw it before submitting another.")

        snapshot = {
            "items": items,
            "vendors": [{"id": v["id"],
                         "name": (v.get("company") or {}).get("name", ""),
                         "email": _vendor_email(v)} for v in vendors_list],
            "total_kg": round(sum(float(i.get("weight_kg") or 0) for i in items), 2),
        }
        rec = {
            "id": f"AP-{d['next_id']:04d}",
            "project_id": project["id"],
            "project_number": project.get("number") or project["id"],
            "project_name": project.get("name", ""),
            "submitted_by": submitted_by,
            "note": note.strip(),
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": PENDING,
            "snapshot": snapshot,
            "fingerprint": fingerprint(snapshot),
            "steps": [{"designation": role, "state": PENDING, "by": None,
                       "at": None, "comment": ""} for role in CHAIN],
            "history": [{"at": datetime.datetime.now().isoformat(timespec="seconds"),
                         "who": submitted_by.get("name", ""),
                         "role": submitted_by.get("designation", ""),
                         "action": "submitted", "comment": note.strip()}],
        }
        d["next_id"] += 1
        d["requests"].append(rec)
        _save(d)
        return rec


def _vendor_email(v):
    for c in v.get("contacts") or []:
        if c.get("email"):
            return c["email"]
    return ""


def next_step(rec):
    """The step waiting on someone, or None if the chain is complete."""
    if CHAIN_MODE == "parallel":
        for s in rec["steps"]:
            if s["state"] == PENDING:
                return s
        return None
    for s in rec["steps"]:
        if s["state"] == REJECTED:
            return None
        if s["state"] == PENDING:
            return s
    return None


def can_act(rec, designation):
    """Is it this designation's turn?"""
    if rec["status"] not in (PENDING,):
        return False
    s = next_step(rec)
    if not s:
        return False
    if CHAIN_MODE == "parallel":
        return any(x["designation"] == designation and x["state"] == PENDING
                   for x in rec["steps"])
    return s["designation"] == designation


def _touch(rec, who, action, comment=""):
    rec["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "who": who.get("name", ""), "role": who.get("designation", ""),
        "action": action, "comment": comment.strip(),
    })


def approve(rid, who, comment=""):
    with _LOCK:
        d = _load()
        for rec in d["requests"]:
            if rec["id"] != rid:
                continue
            role = who.get("designation")
            if not can_act(rec, role):
                raise ValueError(f"It is not the {role}'s turn on {rid}.")
            for s in rec["steps"]:
                if s["designation"] == role and s["state"] == PENDING:
                    s.update(state=APPROVED, by=who.get("name"),
                             at=datetime.datetime.now().isoformat(timespec="seconds"),
                             comment=comment.strip())
                    break
            _touch(rec, who, "approved", comment)
            if all(s["state"] == APPROVED for s in rec["steps"]):
                rec["status"] = APPROVED
                _touch(rec, who, "fully approved")
            _save(d)
            return rec
    raise ValueError(f"No request {rid}")


def reject(rid, who, comment=""):
    """
    Send it back. Every approval is cleared, because a rejection almost always
    means the scope changed - and an approval given against the old scope
    should not survive into the new one.
    """
    with _LOCK:
        d = _load()
        for rec in d["requests"]:
            if rec["id"] != rid:
                continue
            role = who.get("designation")
            if not can_act(rec, role):
                raise ValueError(f"It is not the {role}'s turn on {rid}.")
            for s in rec["steps"]:
                s.update(state=PENDING, by=None, at=None, comment="")
            rec["status"] = REJECTED
            _touch(rec, who, "rejected", comment)
            _save(d)
            return rec
    raise ValueError(f"No request {rid}")


def withdraw(rid, who):
    with _LOCK:
        d = _load()
        before = len(d["requests"])
        d["requests"] = [r for r in d["requests"] if r["id"] != rid]
        if len(d["requests"]) != before:
            _save(d)
            return True
    return False


def mark_issued(rid, who, sent_to):
    with _LOCK:
        d = _load()
        for rec in d["requests"]:
            if rec["id"] == rid:
                rec["status"] = ISSUED
                rec["issued_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                rec["issued_to"] = sent_to
                _touch(rec, who, "issued to vendors", ", ".join(sent_to))
                _save(d)
                return rec
    return None


def check_stale(rid, current_items):
    """
    Compare the live project against what was approved.

    Returns the record, flipped to STALE if they no longer match. This is the
    guard that stops an approved RFQ being issued for different figures.
    """
    with _LOCK:
        d = _load()
        for rec in d["requests"]:
            if rec["id"] != rid:
                continue
            live = dict(rec["snapshot"], items=current_items,
                        total_kg=round(sum(float(i.get("weight_kg") or 0)
                                           for i in current_items), 2))
            if fingerprint(live) != rec["fingerprint"] and rec["status"] in (PENDING, APPROVED):
                rec["status"] = STALE
                rec["history"].append({
                    "at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "who": "system", "role": "", "action": "marked stale",
                    "comment": "The project changed after submission. "
                               "Resubmit for approval.",
                })
                _save(d)
            return rec
    return None


def pending_for(designation):
    """Everything waiting on this designation, for the approvals page."""
    out = []
    for r in all_requests():
        if r["status"] == PENDING and can_act(r, designation):
            out.append(r)
    return out
