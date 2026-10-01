"""
rfq_archive.py — every RFQ ever issued, kept and findable.

WHY THIS EXISTS
    An RFQ is a commercial document. Months later somebody asks what was
    quoted to which vendor, at what date, against which project — and if the
    only copy was whatever the user chose in a Save dialog, the answer depends
    on whether that person still works here and how they name folders.

    So the app keeps its own copy, always, in a folder it controls, and writes
    a line in a log. Where the *user* saves their copy is a separate question
    and does not affect this one. A cancelled Save dialog must not lose the
    record.

WHAT IS RECORDED
    RFQ number, project, vendor, date, total weight, and the archive path.
    Enough to find a document without opening any of them.
"""
import datetime
import json
import os
import shutil
import threading

try:
    from . import filesave
except ImportError:
    import filesave

_LOCK = threading.Lock()


def _log_file():
    return os.path.join(filesave.default_dir("data"), "rfq_log.json")


def archive_dir():
    return filesave.default_dir("rfq_archive")


def _load():
    try:
        with open(_log_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(rows):
    tmp = _log_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, _log_file())          # never a half-written log


def record(src_path, rfq_no, project_id, project_number, vendor, kind="RM",
           weight_kg=0.0, extra=None):
    """
    Copy a freshly written RFQ into the archive and log it.

    The copy is taken here rather than trusting the caller's path to survive.
    Returns the log entry.
    """
    stamp = datetime.datetime.now()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(rfq_no))
    name = f"{stamp:%Y%m%d_%H%M%S}_{safe}.docx"
    dest = os.path.join(archive_dir(), name)
    try:
        shutil.copyfile(src_path, dest)
    except OSError:
        dest = src_path                    # better a pointer than no record

    entry = {
        "id": f"{stamp:%Y%m%d%H%M%S}-{safe}",
        "rfq_no": rfq_no,
        "kind": kind,
        "project_id": project_id,
        "project_number": project_number,
        "vendor": vendor,
        "weight_kg": round(float(weight_kg or 0), 2),
        "issued_at": stamp.isoformat(timespec="seconds"),
        "file": os.path.basename(dest),
        "path": dest,
    }
    if extra:
        entry.update(extra)

    with _LOCK:
        rows = _load()
        rows.append(entry)
        _save(rows)
    return entry


def all(project_id=None):
    """Newest first. Filtered to one project when asked."""
    rows = _load()
    if project_id:
        rows = [r for r in rows if r.get("project_id") == project_id]
    return sorted(rows, key=lambda r: r.get("issued_at", ""), reverse=True)


def get(entry_id):
    for r in _load():
        if r.get("id") == entry_id:
            return r
    return None
