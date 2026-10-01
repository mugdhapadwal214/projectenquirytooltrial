"""
pipe_database.py — lookups against the ANSI B36.10 / B36.19 pipe chart.

The JSON is built once by build_pipe_database.py; this module only reads it.
Kept separate from the flange lookups because the two standards are unrelated
and their tables have nothing in common beyond the words "nominal pipe size".

MASS AND MATERIAL
    kg/m in the dataset is for carbon steel at 7850 kg/m3. `mass_per_m` rescales
    it by the density of the material actually chosen, so a stainless or
    aluminium pipe of the same size and schedule comes out at the right weight
    rather than silently being priced as steel.
"""
try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

import json
import os

_DB = None
_FILE = _dp("pipe_schedules.json")


def db():
    global _DB
    if _DB is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                _DB = json.load(f)
        except (OSError, ValueError):
            _DB = {"sizes": [], "_density_basis_kg_m3": 7850.0}
    return _DB


def _sizes():
    return db().get("sizes", [])


def nps_list():
    """Every nominal size in the chart, in published order."""
    return [s["nps"] for s in _sizes()]


def row(nps):
    for s in _sizes():
        if str(s["nps"]) == str(nps):
            return s
    return None


def schedules(nps):
    """
    Schedules published for this size, ordered lightest wall first — which is
    also cheapest first, and the order someone scanning the list expects.
    """
    s = row(nps)
    if not s:
        return []
    return sorted(s["schedules"].keys(), key=lambda k: s["schedules"][k]["t_mm"])


def detail(nps, schedule, material_density=None):
    """Full dimensions for one size/schedule, or None if not published."""
    s = row(nps)
    if not s:
        return None
    w = s["schedules"].get(str(schedule))
    if not w:
        return None

    kg_m = w["kg_m"]
    basis = db().get("_density_basis_kg_m3", 7850.0)
    if material_density and basis:
        # Same geometry, different metal — scale rather than mis-price it.
        kg_m = kg_m * float(material_density) / float(basis)

    return {
        "nps": s["nps"],
        "dn": s.get("dn"),
        "schedule": str(schedule),
        "od_mm": s["od_mm"],
        "od_in": s["od_in"],
        "t_mm": w["t_mm"],
        "id_mm": w["id_mm"],
        "kg_m": round(kg_m, 4),
        "standard": w.get("standard", ""),
    }


def corrections():
    return db().get("corrections", [])
