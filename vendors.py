"""
vendors.py — the vendor master.

Fields follow the Master Vendor Data Sheet: company block, up to three
contacts, and commercial details.

WHY VENDORS ARE CATEGORISED
    An RFQ goes to the suppliers who can actually fulfil it. A plate mill has
    no use for a machining enquiry. Each vendor therefore carries one or more
    supply categories, and the RFQ page raises one document per vendor in the
    chosen category — three raw-material vendors gives three RFQs, each
    addressed to that vendor by name.

WHY THE CATEGORY LIST IS DATA, NOT CODE
    Categories live in the same JSON file. Today it is raw material,
    machining, heat treatment, surface treatment and bought-out components;
    tomorrow it will be something else, and that should not need a rebuild.
"""
import datetime
import json
import os
import threading

try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

_LOCK = threading.Lock()

DEFAULT_CATEGORIES = [
    "Raw Material",
    "Machining",
    "Heat Treatment",
    "Surface Treatment",
    "Bought-out Components",
    "Fabrication",
    "Other",
]

# Blank record. Every field the data sheet asks for, so nothing is silently
# dropped when a vendor is saved.
BLANK = {
    "company": {"name": "", "city": "", "address": "", "products": "",
                "website": "", "portfolio": ""},
    "contacts": [
        {"label": "Primary", "name": "", "email": "", "phone": ""},
        {"label": "Secondary", "name": "", "email": "", "phone": ""},
        {"label": "Tertiary", "name": "", "email": "", "phone": ""},
    ],
    "categories": [],
    "gst": "",
    "payment_terms": "",
    "notes": "",
}


# ── Where the vendors live ──────────────────────────────────────────────────
# The vendor master is the one in the Project Enquiry tool (accounts/vendors.json),
# so a vendor added anywhere — Store, Project Expense, a bank statement sub-head —
# is available to the RFQ here, and there is only one list to maintain.
try:
    from enquiry_link import accounts as _accounts
except ImportError:                            # standalone estimator: own file
    def _accounts(): return None

class _Global:
    """The enquiry tool's vendor master, loaded on first use."""
    def __getattr__(self, item):
        mod = _accounts()
        if mod is None:
            raise AttributeError(item)
        return getattr(mod, item)
    def __bool__(self):
        return _accounts() is not None

_global = _Global()
_global = _global if bool(_global) else None


def _file():
    return _dp("vendors.json")


def _blank_store():
    return {"next_id": 1, "categories": list(DEFAULT_CATEGORIES), "vendors": []}


def _load():
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("next_id", 1)
        d.setdefault("vendors", [])
        d.setdefault("categories", list(DEFAULT_CATEGORIES))
        return d
    except (OSError, ValueError):
        return _blank_store()


def _save(d):
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _file())          # atomic — a crash cannot truncate it


def _to_rm(v):
    """Global vendor record -> the shape this app works in."""
    return {
        "id": v.get("id", ""),
        "company": {"name": v.get("name", ""), "city": v.get("city", ""),
                    "address": v.get("address", ""), "products": v.get("products", ""),
                    "website": v.get("website", ""), "portfolio": v.get("portfolio", "")},
        "contacts": [{"label": lab,
                      "name": (v.get("contacts") or [{}, {}, {}])[i].get("name", "") if i < len(v.get("contacts") or []) else "",
                      "email": (v.get("contacts") or [{}, {}, {}])[i].get("email", "") if i < len(v.get("contacts") or []) else "",
                      "phone": (v.get("contacts") or [{}, {}, {}])[i].get("phone", "") if i < len(v.get("contacts") or []) else ""}
                     for i, lab in enumerate(("Primary", "Secondary", "Tertiary"))],
        "categories": list(v.get("supplies") or []),
        "gst": v.get("gst", ""), "payment_terms": v.get("payment_terms", ""),
        "notes": v.get("notes", ""), "created": v.get("added", ""),
    }


def _to_global(payload):
    c = payload.get("company") or {}
    return {"name": c.get("name", ""), "city": c.get("city", ""), "address": c.get("address", ""),
            "products": c.get("products", ""), "website": c.get("website", ""),
            "portfolio": c.get("portfolio", ""), "supplies": payload.get("categories") or [],
            "contacts": [{"name": x.get("name", ""), "email": x.get("email", ""),
                          "phone": x.get("phone", "")} for x in (payload.get("contacts") or [])],
            "gst": payload.get("gst", ""), "payment_terms": payload.get("payment_terms", ""),
            "notes": payload.get("notes", "")}


def categories():
    if _global is not None:
        return list(_global.SUPPLY_TYPES)
    return _load().get("categories", list(DEFAULT_CATEGORIES))


def all_vendors(category=None):
    """Every vendor, or only those supplying one category."""
    if _global is not None:
        v = [_to_rm(x) for x in _global.vendors()]
    else:
        v = _load()["vendors"]
    if category:
        v = [x for x in v if category in (x.get("categories") or [])]
    return sorted(v, key=lambda x: (x.get("company", {}).get("name") or "").lower())


def get(vid):
    for v in all_vendors():
        if v["id"] == vid:
            return v
    return None


def _clean(payload):
    """Take only the fields we know, so a stray key cannot corrupt a record."""
    src_c = payload.get("company") or {}
    rec = {
        "company": {k: str(src_c.get(k, "") or "").strip() for k in BLANK["company"]},
        "contacts": [],
        "categories": [str(c).strip() for c in (payload.get("categories") or []) if str(c).strip()],
        "gst": str(payload.get("gst", "") or "").strip(),
        "payment_terms": str(payload.get("payment_terms", "") or "").strip(),
        "notes": str(payload.get("notes", "") or "").strip(),
    }
    src = payload.get("contacts") or []
    for i, label in enumerate(("Primary", "Secondary", "Tertiary")):
        c = src[i] if i < len(src) else {}
        rec["contacts"].append({"label": label,
                                "name": str((c or {}).get("name", "") or "").strip(),
                                "email": str((c or {}).get("email", "") or "").strip(),
                                "phone": str((c or {}).get("phone", "") or "").strip()})
    return rec


def add(payload):
    if _global is not None:
        return _to_rm(_global.add_vendor(_to_global(_clean(payload))))
    raise RuntimeError("The vendor list is maintained in the Project Enquiry tool.")


def update(vid, payload):
    if _global is not None:
        return _to_rm(_global.update_vendor(vid, _to_global(_clean(payload))))
    raise RuntimeError("The vendor list is maintained in the Project Enquiry tool.")


def delete(vid):
    if _global is not None:
        _global.remove_vendor(vid)
        return True
    return False


def by_category():
    out = {c: [] for c in categories()}
    for v in all_vendors():
        for c in v.get("categories") or []:
            out.setdefault(c, []).append(v)
    return out


def primary_contact(v):
    """First contact with a name, or an empty record."""
    for c in v.get("contacts") or []:
        if c.get("name") or c.get("email"):
            return c
    return {"name": "", "email": "", "phone": ""}
