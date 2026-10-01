"""
database.py — Reference data for the RM Estimator.

This module holds the raw reference data used by calculator.py. Nothing
here is calculated — it's the equivalent of the "Tables" sheet in the
NSWC-11 Excel workbooks: static values that calculator.py looks up.

SOURCE / JUSTIFICATION
-----------------------
MATERIALS (density, kg/m3):
    Standard textbook/engineering-handbook density values for common
    metals. These are physical constants — they don't change with time
    or region, so they're hardcoded rather than fetched from anywhere.
    Values used:
        Mild Steel IS2062         7850 kg/m3
        Stainless Steel SS 304    7930 kg/m3
        Stainless Steel SS 304L   7930 kg/m3
        Stainless Steel SS 316    7980 kg/m3
        Stainless Steel SS 316L   7980 kg/m3
        Stainless Steel SS 440C   7700 kg/m3
        Aluminium AA6061          2700 kg/m3
        Titanium Ti 6AL4V         4430 kg/m3
        Lead                      11340 kg/m3
        

MATERIAL RATE
    Rate is no longer held here. It is entered per part in the input
    table (Rs/kg), because different parts on the same plate can be
    bought at different prices, and buy prices move often enough that
    hardcoding them would be misleading.

CUTTING PROCESS
    Removed. The earlier version carried a kerf-loss allowance per
    cutting process (laser / waterjet) as a percentage of plate weight.
    Those were placeholder figures rather than measured shop data, and
    this tool now estimates raw material only — process losses belong in
    a costing tool, not here. The only cutting-related allowance that
    remains is the physical gap left between adjacent blanks, which is
    geometry, not process: see CUT_GAP_FACTOR in calculator.py.
"""
try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

import json
import os
from pathlib import Path


# --------------------------------------------------------------- materials --
#
# The material master now lives in data/materials.json, not in this file.
#
# Two reasons. It carries far more than density — composition limits,
# mechanical properties, certification class — all of which get printed in the
# RFQ, and none of which belongs in source code. And it means the purchase team
# can correct a composition without editing Python or rebuilding the exe.
#
# Density is still the only field the calculations touch. Everything else is
# reference data for the RFQ.
#
# The material NAME is the key used everywhere else — parts, offcut stock, the
# job ledger. Renaming one leaves those pointing at nothing, which is why
# app.py checks for stale names at startup.

_MATERIALS_FILE = _dp("materials.json")
_MAT_DB = None


def material_db():
    """The whole material master, loaded once."""
    global _MAT_DB
    if _MAT_DB is None:
        try:
            with open(_MATERIALS_FILE, "r", encoding="utf-8") as f:
                _MAT_DB = json.load(f)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Could not read {_MATERIALS_FILE}: {exc}. The app cannot "
                f"calculate a weight without material densities."
            ) from exc
    return _MAT_DB


def material_records():
    """One record per material, keyed by name."""
    return {m["name"].strip(): m for m in material_db().get("materials", [])}


def material_info(name):
    """Full record for one material, or None. Used by the RFQ."""
    return material_records().get(str(name).strip())


# Densities, in the shape the calculator has always expected. Keys are
# stripped: a stray trailing space is invisible in the file but makes the name
# a different string everywhere else, and nothing would ever match it.
MATERIALS = {name: float(rec["density"])
             for name, rec in material_records().items()}


def get_density(material: str) -> float:
    """Return density (kg/m3) for a material name. Raises KeyError if unknown."""
    return MATERIALS[material]


# Common stock plate sizes (width x length, mm). The user can type any size in
# the UI; these are just the quick picks. Sizes vary by mill and by material,
# so treat this as a convenience list, not a specification.
STANDARD_PLATES = [
    {"label": "1250 \u00d7 2500", "width": 1250, "length": 2500},
    {"label": "1500 \u00d7 3000", "width": 1500, "length": 3000},
    {"label": "2000 \u00d7 6000", "width": 2000, "length": 6000},
]

# ============================================================
# FLANGE DATABASE
# ============================================================

# Location of flange_database.json
BASE_DIR = Path(__file__).parent

FLANGE_DATABASE_FILE = (
    BASE_DIR / "flange_database.json"
)


# Load flange database
with open(
    FLANGE_DATABASE_FILE,
    "r",
    encoding="utf-8"
) as file:

    FLANGE_DATABASE = json.load(file)

# ============================================================
# GET ONE FLANGE
# ============================================================

def get_flange_from_database(
    pressure_class,
    nps,
    flange_type
):
    
    """
    Find a flange using:

    Pressure Class
    NPS
    Flange Type
    """

    pressure_class = int(
        pressure_class
    )

    nps = str(nps).strip()

    flange_type = str(
        flange_type
    ).strip()


    for flange in FLANGE_DATABASE["flanges"]:

        if (
            flange["pressure_class"]
            == pressure_class

            and

            flange["nps"]
            == nps

            and

            flange["flange_type"]
            == flange_type
        ):

            return flange


    return None

# ============================================================
# GET AVAILABLE PRESSURE CLASSES
# ============================================================

def get_flange_classes():
    """
    Return all pressure classes available
    in the flange database.
    """

    classes = set()

    for flange in FLANGE_DATABASE["flanges"]:

        classes.add(
            flange["pressure_class"]
        )

    return sorted(classes)


# ============================================================
# GET AVAILABLE NPS SIZES
# ============================================================

def get_flange_nps(pressure_class):
    """
    Return all NPS sizes available for
    the selected pressure class.
    """

    pressure_class = int(
        pressure_class
    )

    nps_values = set()

    for flange in FLANGE_DATABASE["flanges"]:

        if (
            flange["pressure_class"]
            == pressure_class
        ):

            nps_values.add(
                flange["nps"]
            )

    return sorted(
        nps_values,
        key=str
    )


# ============================================================
# GET AVAILABLE FLANGE TYPES
# ============================================================

def get_flange_types(
    pressure_class,
    nps
):
    """
    Return all flange types available for
    a selected pressure class and NPS.
    """

    pressure_class = int(
        pressure_class
    )

    nps = str(nps).strip()

    flange_types = set()

    for flange in FLANGE_DATABASE["flanges"]:

        if (
            flange["pressure_class"]
            == pressure_class

            and

            flange["nps"]
            == nps
        ):

            flange_types.add(
                flange["flange_type"]
            )

    return sorted(
        flange_types
    )

