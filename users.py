"""
users.py — accounts and sign-in for the local build.

SCOPE
    This is the offline version: a JSON file beside the app, so the sign-in
    flow, the designations and the guarded routes can be tried out before any
    of it goes online. It is NOT the multi-user design. When the app moves to a
    server this file is replaced by a database table, and sessions will need
    HTTPS and a shared secret across instances.

    Two things are nonetheless done properly here, because getting them wrong
    is a habit that survives a rewrite:

      * Passwords are never stored. Only a PBKDF2-HMAC-SHA256 hash with a
        per-user random salt, 200k iterations.
      * Comparison is constant-time, so a wrong password cannot be narrowed
        down by timing.

    Email is stored as the login identifier only. There is no verification
    step - deliberately, for now.

DESIGNATIONS
    The three roles. The order here IS the review order, so the approval
    sequence reads it straight from the list rather than hardcoding names in
    two places. approvals.CHAIN must stay identical to this list.
"""
import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading

try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

_LOCK = threading.Lock()

# Order matters: this is the review sequence.
DESIGNATIONS = [
    "Project Engineer",
    "Team Lead",
    "Director",
]

_ALGO = "sha256"
_ITERATIONS = 200_000


def _file():
    return _dp("users.json")


def _blank():
    return {"users": []}


def _load():
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("users", [])
        return d
    except (OSError, ValueError):
        return _blank()


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _file())          # atomic - a crash cannot truncate it


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"),
                             salt.encode("utf-8"), _ITERATIONS)
    return salt, dk.hex()


def _norm(email):
    return (email or "").strip().lower()


def get(email):
    e = _norm(email)
    for u in _load()["users"]:
        if u["email"] == e:
            return u
    return None


def all_users():
    return [{k: v for k, v in u.items() if k not in ("salt", "hash")}
            for u in _load()["users"]]


def count():
    return len(_load()["users"])


def create(email, password, name, designation):
    """Register an account. Raises ValueError with a message fit to show."""
    e = _norm(email)
    if not e or "@" not in e:
        raise ValueError("Enter a valid email address")
    if len(password or "") < 6:
        raise ValueError("Password must be at least 6 characters")
    if not (name or "").strip():
        raise ValueError("Enter your name")
    if designation not in DESIGNATIONS:
        raise ValueError("Choose a designation")

    with _LOCK:
        d = _load()
        if any(u["email"] == e for u in d["users"]):
            raise ValueError("An account already exists for that email")
        salt, h = hash_password(password)
        rec = {
            "email": e,
            "name": name.strip(),
            "designation": designation,
            "salt": salt,
            "hash": h,
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        d["users"].append(rec)
        _save(d)
    return {k: v for k, v in rec.items() if k not in ("salt", "hash")}


def verify(email, password):
    """Return the user record on success, or None. Never says which half failed."""
    u = get(email)
    if not u:
        # Hash anyway, so a missing account and a wrong password take the same
        # time and cannot be told apart from the outside.
        hash_password(password or "")
        return None
    _, h = hash_password(password or "", u["salt"])
    if not hmac.compare_digest(h, u["hash"]):
        return None
    return {k: v for k, v in u.items() if k not in ("salt", "hash")}


def set_password(email, password):
    if len(password or "") < 6:
        raise ValueError("Password must be at least 6 characters")
    with _LOCK:
        d = _load()
        for u in d["users"]:
            if u["email"] == _norm(email):
                u["salt"], u["hash"] = hash_password(password)
                _save(d)
                return True
    return False


def delete(email):
    with _LOCK:
        d = _load()
        before = len(d["users"])
        d["users"] = [u for u in d["users"] if u["email"] != _norm(email)]
        if len(d["users"]) != before:
            _save(d)
            return True
    return False
