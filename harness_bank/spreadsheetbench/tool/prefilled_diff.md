# prefilled_diff

> Diff input vs output workbook inside the answer range and report every pre-filled literal cell the output changed.

Use this before the student finishes, whenever the trajectory shows no input↔output comparison. The observed loss (repeated across three evolution rounds): the student writes its own rule across the whole answer range and silently overwrites pre-filled worked-example cells that pin the expected rule, format, and order — prose reminders do not stop it, a mechanical diff does. Formula cells are exempt (a formula-fix task legitimately rewrites those); only literal pre-filled values are compared. Have the student create the script once and run it with the task's `spreadsheet_path`, `output_path`, and `answer_position`; any reported cell must be restored (or explicitly justified by the instruction) before finishing.

```bash
mkdir -p .agent-tools
cat > .agent-tools/prefilled_diff.py <<'PY'
import json, sys
import openpyxl
from openpyxl.utils import range_boundaries, get_column_letter

inp, outp, rng = sys.argv[1], sys.argv[2], sys.argv[3]
sheet = None
if "!" in rng:
    sheet, rng = rng.split("!", 1)
    sheet = sheet.strip("'")
wb_in = openpyxl.load_workbook(inp)
wb_out = openpyxl.load_workbook(outp)
ws_in = wb_in[sheet] if sheet else wb_in.active
ws_out = wb_out[sheet] if sheet else wb_out.active
min_c, min_r, max_c, max_r = range_boundaries(rng)
bad = []
checked = 0
for row in range(min_r, max_r + 1):
    for col in range(min_c, max_c + 1):
        ci = ws_in.cell(row=row, column=col)
        if ci.value is None or ci.data_type == "f":
            continue
        checked += 1
        co = ws_out.cell(row=row, column=col)
        if co.value != ci.value:
            coord = f"{get_column_letter(col)}{row}"
            bad.append((coord, repr(ci.value)[:40], repr(co.value)[:40]))
print(f"compared {checked} pre-filled literal cells inside {rng}")
for coord, was, now in bad[:20]:
    print(f"FAIL {coord}: was {was}, now {now}")
print(f"{len(bad)} violation(s)" if bad else "PASS: all pre-filled cells preserved")
raise SystemExit(bool(bad))
PY
python3 .agent-tools/prefilled_diff.py INPUT_XLSX OUTPUT_XLSX ANSWER_RANGE
```
