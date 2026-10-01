"""
build_material_reference.py — a reviewable sheet of material data.

WHY THIS IS A REVIEW DOCUMENT AND NOT A DATABASE
    These figures are transcribed from published standards, but they are NOT
    yet wired into the app. That is deliberate: composition and property limits
    vary by grade, product form, thickness and heat treatment condition, and a
    wrong figure here becomes a wrong figure on a purchase order.

    Every row therefore carries the standard it came from, so it can be checked
    against the actual spec rather than taken on trust. Confirm it, correct it,
    then it goes in.

TWO THINGS THAT NEED YOUR DECISION, MARKED IN THE SHEET
    1. IS 2062 is a family, not a grade. E250 alone has sub-grades A, B, BR, C
       with different impact requirements, and the app currently just says
       "Mild Steel IS2062". Which grade do you actually buy?
    2. Several properties depend on section thickness — IS 2062 yield drops
       above 20 mm, 6061-T6 differs by product form. The sheet lists the common
       case and says so; if you work outside it, the values change.
"""
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "Material_Reference_for_review.xlsx")

NAVY = "1F3864"
HDR = PatternFill("solid", fgColor=NAVY)
SUB = PatternFill("solid", fgColor="D9E2F3")
WARN = PatternFill("solid", fgColor="FFF4E0")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
F = "Arial"

# ---------------------------------------------------------------- data ----
# material, standard, grade/condition, C, Mn, Si, P, S, Cr, Ni, Mo, other
COMPOSITION = [
    ("Mild Steel IS2062", "IS 2062:2011", "E250 Gr. B",
     "0.22 max", "1.50 max", "0.40 max", "0.045 max", "0.045 max",
     "\u2014", "\u2014", "\u2014", "C+Mn/6 \u2264 0.42 (CE)"),
    ("Stainless Steel 304", "ASTM A240 / UNS S30400", "Annealed",
     "0.07 max", "2.00 max", "0.75 max", "0.045 max", "0.030 max",
     "17.5\u201319.5", "8.0\u201310.5", "\u2014", "N 0.10 max"),
    ("Stainless Steel 304L", "ASTM A240 / UNS S30403", "Annealed",
     "0.030 max", "2.00 max", "0.75 max", "0.045 max", "0.030 max",
     "17.5\u201319.5", "8.0\u201312.0", "\u2014", "N 0.10 max"),
    ("Stainless Steel 316", "ASTM A240 / UNS S31600", "Annealed",
     "0.08 max", "2.00 max", "0.75 max", "0.045 max", "0.030 max",
     "16.0\u201318.0", "10.0\u201314.0", "2.00\u20133.00", "N 0.10 max"),
    ("Stainless Steel 316L", "ASTM A240 / UNS S31603", "Annealed",
     "0.030 max", "2.00 max", "0.75 max", "0.045 max", "0.030 max",
     "16.0\u201318.0", "10.0\u201314.0", "2.00\u20133.00", "N 0.10 max"),
    ("Stainless Steel 440C", "ASTM A276 / UNS S44004", "Annealed / hardened",
     "0.95\u20131.20", "1.00 max", "1.00 max", "0.040 max", "0.030 max",
     "16.0\u201318.0", "\u2014", "0.75 max", "martensitic"),
    ("Aluminium AA6061", "ASTM B209 / EN 573", "T6 / T651",
     "\u2014", "0.15 max", "0.40\u20130.8", "\u2014", "\u2014",
     "0.04\u20130.35", "\u2014", "\u2014", "Mg 0.8\u20131.2, Cu 0.15\u20130.40, Fe 0.7 max"),
    ("Titanium Ti 6AL4V", "ASTM B265 Grade 5", "Annealed",
     "0.08 max", "\u2014", "\u2014", "\u2014", "\u2014",
     "\u2014", "\u2014", "\u2014", "Al 5.5\u20136.75, V 3.5\u20134.5, Fe 0.40 max, O 0.20 max"),
    ("Lead", "ASTM B29 (99.94% Pb)", "Commercially pure",
     "\u2014", "\u2014", "\u2014", "\u2014", "\u2014",
     "\u2014", "\u2014", "\u2014", "Pb 99.94 min"),
]

# material, density, yield, tensile, elong, hardness, melting, notes
MECHANICAL = [
    ("Mild Steel IS2062", 7850, "250 min (\u226420 mm)", "410 min", "23 min",
     "\u2014", "~1500",
     "Yield falls to 240 (20\u201340 mm) and 230 (>40 mm). App uses 7850."),
    ("Stainless Steel 304", 7930, "205 min", "515 min", "40 min",
     "92 HRB max", "1400\u20131450", "Non-magnetic in annealed condition."),
    ("Stainless Steel 304L", 7930, "170 min", "485 min", "40 min",
     "92 HRB max", "1400\u20131450", "Low carbon \u2014 better as-welded corrosion resistance."),
    ("Stainless Steel 316", 7980, "205 min", "515 min", "40 min",
     "95 HRB max", "1375\u20131400", "Mo addition improves pitting resistance."),
    ("Stainless Steel 316L", 7980, "170 min", "485 min", "40 min",
     "95 HRB max", "1375\u20131400", "Low carbon variant of 316."),
    ("Stainless Steel 440C", 7700, "450 (annealed)", "760 (annealed)", "14 min",
     "58 HRC min hardened", "1370\u20131480",
     "App density corrected from 7980 to 7700 \u2014 please confirm."),
    ("Aluminium AA6061", 2700, "276 min (T6)", "310 min (T6)", "8\u201310 min",
     "95 HB", "580\u2013650", "T6 values; O temper is far softer."),
    ("Titanium Ti 6AL4V", 4430, "828 min", "895 min", "10 min",
     "36 HRC", "1600\u20131660", "Grade 5, annealed."),
    ("Lead", 11340, "~5\u201310", "12\u201317", "30\u201350",
     "4 HB", "327", "Very low strength; used for shielding, not structure."),
]

CERT = [
    ("EN 10204 2.1", "Declaration of compliance",
     "Manufacturer states compliance. No test results.", "Non-critical items"),
    ("EN 10204 2.2", "Test report",
     "Non-specific test results, not on the actual batch.", "General fabrication"),
    ("EN 10204 3.1", "Inspection certificate 3.1",
     "Test results on the ACTUAL batch supplied, signed by the manufacturer's "
     "independent inspection unit.", "Pressure parts \u2014 the usual requirement"),
    ("EN 10204 3.2", "Inspection certificate 3.2",
     "As 3.1 but countersigned by an independent inspector or the customer's "
     "representative.", "Critical / third-party witnessed"),
]

OPEN_QUESTIONS = [
    ("IS 2062 grade not specified",
     "The app says only 'Mild Steel IS2062'. IS 2062 covers E250 (A/B/BR/C), "
     "E300, E350, E410, E450 \u2014 different yield and impact requirements. "
     "Composition above assumes E250 Gr. B.",
     "Which grade(s) do you buy? Should the app list them separately?"),
    ("440C density corrected",
     "database.py had 7980 kg/m3, copied from the 316 row above it. The "
     "docstring in the same file said 7700. Published value for martensitic "
     "440C is 7650\u20137750.",
     "7700 is now in use \u2014 confirm or give your own figure."),
    ("Thickness dependence not modelled",
     "IS 2062 yield drops with section thickness; 6061 properties depend on "
     "temper and product form. The app carries one density per material and no "
     "properties at all.",
     "Do you need properties per thickness band, or is one row per material "
     "enough for an RFQ?"),
    ("Lead has no cutting data",
     "Lead is not laser cut. Its cutting cost will read as if it were.",
     "Should lead be excluded from cutting entirely?"),
    ("Composition is for the RFQ, not for calculation",
     "None of these figures affect any weight or cost. They exist to be "
     "printed in the RFQ material specification section.",
     "Confirm this is the intended use before it is wired in."),
]


def sheet(wb, title, headers, rows, widths, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HDR
        cell.font = Font(name=F, bold=True, color="FFFFFF", size=9.5)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border = BOX
    for r in rows:
        ws.append(list(r))
    for r in range(2, ws.max_row + 1):
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = BOX
            cell.font = Font(name=F, size=9.5)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return ws


def build():
    wb = Workbook()

    ws = sheet(wb, "Chemical composition",
               ["Material", "Standard", "Grade / condition", "C %", "Mn %",
                "Si %", "P %", "S %", "Cr %", "Ni %", "Mo %", "Other"],
               COMPOSITION,
               [22, 24, 18, 11, 10, 11, 11, 11, 13, 13, 11, 34], first=True)
    n = ws.max_row + 2
    ws.cell(row=n, column=1, value=(
        "All values are weight %, single figures are maxima. Transcribed from the "
        "standards named in column B for REVIEW \u2014 check against the actual "
        "spec before use. Nothing here affects any calculation in the app; it is "
        "for the RFQ material specification section."))
    ws.cell(row=n, column=1).font = Font(name=F, size=9, italic=True)
    ws.cell(row=n, column=1).fill = WARN

    sheet(wb, "Mechanical properties",
          ["Material", "Density kg/m3", "Yield MPa", "Tensile MPa",
           "Elongation %", "Hardness", "Melting C", "Notes"],
          MECHANICAL, [22, 14, 20, 16, 14, 20, 14, 52])

    sheet(wb, "Certification classes",
          ["Class", "Name", "What it means", "Typical use"],
          CERT, [16, 30, 60, 34])

    ws = sheet(wb, "Open questions",
               ["Item", "Detail", "Needs your decision"],
               OPEN_QUESTIONS, [30, 62, 46])
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=3).fill = WARN

    wb.save(OUT)
    print(f"Wrote {OUT}")
    print(f"  {len(COMPOSITION)} materials, {len(OPEN_QUESTIONS)} open questions")


if __name__ == "__main__":
    build()
