# answer_range_check

> Mechanical health check of the deliverable's answer range: empty range, formula strings, numbers-as-text, error literals, degenerate constant fills.

Use this before the student finishes, after the output workbook exists. The observed losses: values computed but written to the wrong sheet/column (the scored range stays empty); strings starting with `=` delivered as answers (the sandbox has no LibreOffice, so they can never be verified); numbers/times written as text ('8.75', '22:00:00') where the grader compares typed values; broken-formula error text in cells; and conditional columns collapsing to one constant. `#N/A` is NOT flagged — lookup-miss tasks legitimately deliver it. Have the student create the script once and run it with the task's `output_path` and `answer_position`; every FAIL line must be fixed or explicitly justified by the instruction and worked examples before finishing.

```bash
mkdir -p .agent-tools
cat > .agent-tools/answer_range_check.py <<'PY'
import re, sys
import openpyxl
from openpyxl.utils import range_boundaries, get_column_letter

outp, rng = sys.argv[1], sys.argv[2]
sheet = None
if "!" in rng:
    sheet, rng = rng.split("!", 1)
    sheet = sheet.strip("'")
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*[AP]M)?$", re.I)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
ERRORS = {"#VALUE!", "#NAME?", "#REF!", "#DIV/0!", "#NULL!", "#NUM!"}
wb = openpyxl.load_workbook(outp)
ws = wb[sheet] if sheet else wb.active
min_c, min_r, max_c, max_r = range_boundaries(rng)
nonempty, formulas, numstrings, errlits = [], [], [], []
values = set()
for row in range(min_r, max_r + 1):
    for col in range(min_c, max_c + 1):
        c = ws.cell(row=row, column=col)
        coord = f"{get_column_letter(col)}{row}"
        v = c.value
        if v is None:
            continue
        nonempty.append(coord)
        values.add(repr(v))
        if isinstance(v, str):
            if v.startswith("="):
                formulas.append(coord)
            elif v in ERRORS:
                errlits.append(coord)
            elif NUMERIC.match(v.strip()) or TIME.match(v.strip()) or DATE.match(v.strip()):
                numstrings.append(coord)
failures = 0
def check(ok, label):
    global failures
    failures += not ok
    print(("PASS " if ok else "FAIL ") + label)
check(bool(nonempty), f"answer range {rng} is non-empty")
check(not formulas, f"no formula strings ({len(formulas)}: {formulas[:5]}) — deliver literal computed values")
check(not errlits, f"no error literals ({len(errlits)}: {errlits[:5]})")
check(len(numstrings) < 2, f"numbers/times not written as text ({len(numstrings)}: {numstrings[:5]})")
if len(nonempty) >= 6:
    check(len(values) > 1, f"answer range not a single constant across all {len(nonempty)} cells (unless examples show a constant fill)")
raise SystemExit(bool(failures))
PY
python3 .agent-tools/answer_range_check.py OUTPUT_XLSX ANSWER_RANGE
```
