"""
Read a designer's BOM spreadsheet into BOM lines.

WHY THIS IS FORGIVING ABOUT LAYOUT
    The sheet was not written for this app. It was written by whoever drew the
    assembly, so the header will not be on row 1, the quantity column will be
    called Qty or Nos or No. of, and the type column may not exist at all. A
    parser that demanded an exact layout would be refused by nearly every real
    file and the feature would go unused.

WHY IT NEVER GUESSES QUIETLY
    Anything it could not place is reported back by row number and by the text
    it actually found. A BOM line that silently arrives with the wrong type
    becomes a wrong blank, a wrong nest and a wrong price, and nobody looks at
    the BOM again to find out why. Better to import the line with the type left
    blank and say so than to pick one and be wrong.
"""
import io
import re

import openpyxl


# Component types this app can cost. Kept as a function rather than a constant
# so a type added to meta.py is importable the same day, with no second list to
# forget to update.
def _known_types():
    try:
        from .meta import COMPONENT_SCHEMAS
    except ImportError:
        from meta import COMPONENT_SCHEMAS
    return list(COMPONENT_SCHEMAS.keys())


SUB = "__sub"

# What a designer might call each type. Matched on the whole cell first, then
# as a word inside it, so "Dished End (Torispherical)" still lands on Dish End.
_TYPE_WORDS = {
    "Dish End":  ["dish end", "dished end", "dish-end", "dishend", "head",
                  "torispherical", "ellipsoidal", "hemispherical", "d end"],
    "Shell":     ["shell", "cylinder", "cylindrical shell", "can"],
    "Pipe":      ["pipe", "tube", "nozzle pipe"],
    "Flange":    ["flange", "blind flange", "slip on", "weld neck", "wnrf", "sorf"],
    "Plate":     ["plate", "sheet", "pad", "gusset", "bracket", "blank"],
    "Round Bar": ["round bar", "roundbar", "bar", "rod", "disc", "disk", "boss"],
    "Ring":      ["ring", "annulus", "backing ring", "reinforcement ring"],
    "Circular Plate": ["circular plate", "round plate", "disc", "disk",
                       "blank disc", "cover plate"],
    "Custom":    ["custom", "misc", "other"],
    SUB:         ["sub assembly", "sub-assembly", "subassembly", "sub assy",
                  "sub-assy", "subassy", "assembly", "assy", "weldment"],
}

_HEAD_WORDS = {
    "part_no":   ["part no", "part number", "part_no", "partno", "item no",
                  "item code", "drawing no", "drg no", "sr no", "sl no", "s no"],
    "part_name": ["part name", "name", "description", "component", "item",
                  "item description", "particulars", "part description"],
    "qty":       ["qty", "quantity", "nos", "no of", "no. of", "count", "pcs"],
    "thk":       ["thk", "thickness", "thick", "t (mm)", "t mm", "plate thk"],
    "type":      ["type", "component type", "part type", "category", "class"],
    "od":        ["od", "o d", "outer dia", "outer diameter", "outside dia",
                  "outside diameter", "dia", "diameter", "\u00f8"],
    "id":        ["id", "i d", "inner dia", "inner diameter", "inside dia",
                  "inside diameter", "bore"],
    "length":    ["length", "len", "l (mm)", "l mm"],
    "width":     ["width", "wid", "w (mm)", "w mm", "breadth"],
    "processes": ["process", "processes", "manufacturing process",
                  "mfg process", "operations", "operation"],
}

_TRUE = {"x", "xx", "y", "yes", "1", "1.0", "true", "t", "✓", "✔", "*", "ok"}


def _norm(v):
    """Cell text, squashed to something comparable."""
    if v is None:
        return ""
    s = str(v).replace("\u00a0", " ").strip().lower()
    return re.sub(r"[\s._\-/]+", " ", s).strip()


def _head_key(text):
    n = _norm(text)
    if not n:
        return None
    for key, words in _HEAD_WORDS.items():
        if n in words:
            return key
    for key, words in _HEAD_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", n) for w in words):
            return key
    return None


def _match_type(text, known):
    """
    Canonical component type for whatever the designer wrote.

    Returns (type, matched). `matched` False means the cell had text but none
    of it named a type this app knows - the caller reports that rather than
    choosing one.
    """
    n = _norm(text)
    if not n:
        return "", False
    for t in known:
        if n == _norm(t):
            return t, True
    for t, words in _TYPE_WORDS.items():
        if t != SUB and t not in known:
            continue
        if n in words:
            return t, True
    # Longest word first, so "round bar" wins over "bar" and "dish end" over
    # "head" when a cell happens to contain both.
    for t, words in sorted(
            ((t, w) for t, w in _TYPE_WORDS.items() if t == SUB or t in known),
            key=lambda kv: -max(len(w) for w in kv[1])):
        for w in sorted(words, key=len, reverse=True):
            if re.search(rf"\b{re.escape(w)}\b", n):
                return t, True
    return "", False


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _find_header(ws, scan_rows=40):
    """
    The header row is the one that names the most columns we recognise.

    Scanning rather than assuming row 1: real BOM sheets open with a title
    block, a drawing number and a blank line or two.
    """
    best, best_score, best_map = None, 0, {}
    for r in range(1, min(scan_rows, ws.max_row or 1) + 1):
        cols, letters = {}, {}
        for c in range(1, min(ws.max_column or 1, 60) + 1):
            raw = ws.cell(row=r, column=c).value
            key = _head_key(raw)
            if key and key not in cols:
                cols[key] = c
            n = _norm(raw)
            if len(n) == 1 and n.isalpha():
                letters[n.upper()] = c
        score = len(cols) + (1 if len(letters) >= 3 else 0)
        if "part_name" not in cols and "part_no" not in cols:
            score = 0                     # a row of stray letters is not a header
        if score > best_score:
            best, best_score, best_map = r, score, {"cols": cols, "letters": letters}
    if best_score < 2:
        return None, {}
    return best, best_map


def _codes_from_cell(v):
    return [p.strip().upper() for p in re.split(r"[,;/ ]+", str(v or "")) if p.strip()]


def parse(data, valid_codes=None):
    """
    Turn a BOM workbook into lines this app understands.

    `data` is bytes or a file-like object. Returns a dict with `lines` ready to
    drop into the BOM table, plus everything that could not be placed.
    """
    known = _known_types()
    valid_codes = {c.upper() for c in (valid_codes or [])}

    if isinstance(data, (bytes, bytearray)):
        data = io.BytesIO(data)
    try:
        wb = openpyxl.load_workbook(data, data_only=True)
    except Exception as exc:
        raise ValueError(
            "That file could not be opened as an Excel workbook. Save it as "
            f".xlsx and try again. ({exc.__class__.__name__})")

    # Whichever sheet actually holds a BOM, not necessarily the first one.
    sheet, header, cmap = None, None, {}
    for ws in wb.worksheets:
        r, m = _find_header(ws)
        if r:
            sheet, header, cmap = ws, r, m
            break
    if not sheet:
        raise ValueError(
            "No BOM table found. The sheet needs a header row naming at least "
            "a part name or part number column, plus one of quantity, "
            "thickness or type.")

    cols = cmap["cols"]
    letters = cmap["letters"] if valid_codes else {}
    letters = {k: v for k, v in letters.items() if k in valid_codes}

    lines, unknown_types, no_type, bad_qty, unknown_codes = [], [], [], [], []
    dim_type = []
    blanks = 0
    for r in range(header + 1, (sheet.max_row or header) + 1):
        def cell(key):
            c = cols.get(key)
            return sheet.cell(row=r, column=c).value if c else None

        part_no = str(cell("part_no") or "").strip()
        part_name = str(cell("part_name") or "").strip()
        raw_type = cell("type")
        raw_qty = cell("qty")
        raw_thk = cell("thk")

        if not any([part_no, part_name, raw_type, raw_qty, raw_thk]):
            blanks += 1
            if blanks >= 15:
                break
            continue
        blanks = 0

        # A row carrying only a part number is usually a section heading
        # ("SHELL ASSEMBLY"), not a line item.
        if not part_name and not raw_type and raw_qty is None and raw_thk is None:
            continue

        od = _num(cell("od"))
        bore = _num(cell("id"))
        ln = _num(cell("length"))
        wd = _num(cell("width"))

        ctype, matched = _match_type(raw_type, known)
        if not ctype and _norm(raw_type):
            unknown_types.append({"row": r, "text": str(raw_type).strip(),
                                  "part": part_name or part_no})
        elif not ctype:
            # No type column, or an empty cell. THE DIMENSIONS GIVEN ARE
            # THEMSELVES A STATEMENT OF SHAPE, and a more reliable one than a
            # part name: OD with a bore is a ring, OD on its own is a solid
            # disc, length with a width is a rectangular plate. Read those
            # before falling back to guessing from the name.
            shape_guess = ""
            if od and bore:
                shape_guess = "Ring"
            elif od and not bore:
                shape_guess = "Circular Plate"
            elif ln and wd:
                shape_guess = "Plate"
            if shape_guess and shape_guess in known:
                ctype = shape_guess
                dim_type.append({"row": r, "part": part_name or part_no,
                                 "guess": shape_guess,
                                 "why": ("OD and ID" if (od and bore)
                                         else "OD only" if od else "L and W")})
            else:
                guess, ok = _match_type(part_name, known)
                if ok:
                    ctype = guess
                    no_type.append({"row": r, "part": part_name or part_no,
                                    "guess": guess})
                else:
                    unknown_types.append({"row": r, "text": "(blank)",
                                          "part": part_name or part_no})

        qty = _num(raw_qty)
        if raw_qty not in (None, "") and qty is None:
            bad_qty.append({"row": r, "text": str(raw_qty).strip(),
                            "part": part_name or part_no})
        thk = _num(raw_thk)

        procs = []
        for code, c in letters.items():
            if _norm(sheet.cell(row=r, column=c).value) in _TRUE:
                procs.append(code)
        if cols.get("processes"):
            for code in _codes_from_cell(cell("processes")):
                if code in valid_codes:
                    if code not in procs:
                        procs.append(code)
                elif code:
                    unknown_codes.append({"row": r, "code": code,
                                          "part": part_name or part_no})

        # Whole numbers stay whole. A quantity shown as 2.0 in a table of
        # counts reads like a rounding artefact and invites a second look.
        def tidy(v):
            return int(v) if v is not None and float(v).is_integer() else v

        line = {"part_no": part_no, "part_name": part_name,
                "qty": tidy(qty) if qty is not None else 1,
                "thk": "" if thk is None else tidy(thk),
                "type": ctype, "processes": sorted(procs)}
        # Carry the dimensions through. "Enter details" then opens a row that
        # is already filled in, instead of making someone read them back off
        # the same spreadsheet they just imported.
        for key, val in (("od", od), ("id_", bore),
                         ("length", ln), ("width", wd)):
            if val is not None:
                line[key] = tidy(val)
        if ctype == SUB:
            line["children"] = []
        lines.append(line)

    return {
        "lines": lines,
        "sheet": sheet.title,
        "header_row": header,
        "columns": {k: sheet.cell(row=header, column=c).value for k, c in cols.items()},
        "process_columns": sorted(letters.keys()),
        "unknown_types": unknown_types,
        "guessed_types": no_type,
        "dimension_types": dim_type,
        "bad_qty": bad_qty,
        "unknown_codes": unknown_codes,
    }
