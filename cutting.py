"""
cutting.py — cut length, pierce count and cutting cost.

WHY THIS IS SEPARATE FROM MATERIAL COST
------------------------------------------------------------------
Material cost scales with AREA — how much plate the blank occupies.
Cutting cost scales with PERIMETER — how far the torch travels.

These two behave completely differently. Ten small discs and one large
disc of the same total area cost roughly the same in material, but the
ten small ones cost far more to cut, because there is far more edge.
Folding cutting into a per-kg rate hides that, which is why it is
calculated separately here.

COSTING BASIS
------------------------------------------------------------------
    cost = cut length (m) x rate per metre
         + number of pierces x charge per pierce

Two terms because they are two different things. Length is the torch
travelling; a pierce is it punching through the plate before it can
start, which is where most of the wear and the dross come from. A part
with a bore costs two pierces, not one, however short its cut path.

WHAT IS DELIBERATELY NOT MODELLED
------------------------------------------------------------------
Machine time. A time estimate needs a feed rate per material and
thickness, and that varies with machine power, assist gas and nozzle.
Carrying invented feed rates is worse than carrying none, because the
resulting minutes look authoritative in a quotation while being wrong.
Add it back only with measured shop data.

Also not modelled: lead-in / lead-out moves, rapid traverse between
parts, common-line cutting, and corner deceleration. The figure here is
the theoretical cut path, which is the floor — a real job sits above it.
"""

import math

MM_TO_M = 1e-3


def perimeter_mm(shape: dict) -> float:
    """Total cut path for one blank, mm. A ring is cut twice — bore and OD."""
    kind = shape["kind"]
    if kind == "circle":
        return math.pi * shape["d"]
    if kind == "ring":
        di = shape.get("di") or 0
        return math.pi * shape["d"] + (math.pi * di if di else 0.0)
    if kind == "rect":
        return 2 * (shape["l"] + shape["w"])
    raise ValueError(f"Unknown shape kind: {kind}")


def pierces(shape: dict) -> int:
    """
    Number of times the torch has to punch through the plate — one per closed
    contour. A ring is two: the outside profile and the bore.
    """
    if shape["kind"] == "ring":
        return 2 if (shape.get("di") or 0) else 1
    return 1


def cut_cost(length_mm: float, n_pierce: int, cfg: dict) -> float:
    """Rs. Length at the per-metre rate, plus a fixed charge per pierce."""
    return (length_mm * MM_TO_M * float(cfg.get("rate_per_m", 0) or 0)
            + n_pierce * float(cfg.get("pierce_charge", 0) or 0))


def summarise(shape: dict, qty: int, cfg: dict) -> dict:
    """Everything cutting-related for one part line."""
    per = perimeter_mm(shape)
    pc = pierces(shape)
    total = per * qty
    n_pierce = pc * qty
    return {
        "perimeter_mm": per,
        "pierces_each": pc,
        "cut_length_mm": total,
        "cut_length_m": total * MM_TO_M,
        "pierces": n_pierce,
        "cut_cost": cut_cost(total, n_pierce, cfg),
    }
