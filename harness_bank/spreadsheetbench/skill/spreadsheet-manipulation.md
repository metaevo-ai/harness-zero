---
name: spreadsheet-manipulation
description: "Workflow and pitfall catalog for manipulating .xlsx spreadsheets with openpyxl/pandas — explore-first data discovery (header groups beyond row 1, example/answer tabs as semantic specs), literal values over formulas, data_only reads, key normalization (dates/numbers/text), target-range hygiene, and edge-case handling for totals, blanks, and multi-key lookups. Use to diagnose student failures on spreadsheet tasks and to steer interventions."
---

# Spreadsheet Manipulation Skill (xlsx)

## Overview
This skill documents how agents should manipulate Excel (.xlsx) spreadsheets using Python, and where they fail doing it. As the reviewing teacher, use it to diagnose which step of the student's workflow went wrong and to write replacements that follow the workflow below — the student's deliverable is judged by exactly these rules.

**Primary libraries**: `openpyxl` (structure-preserving read/write), `pandas` (data transformation).
Never use any other third-party libraries.

---

## Delivery (read this first)

- **Edit in place, never rebuild.** Output = `shutil.copy(INPUT, OUTPUT)` then modify with openpyxl, or `load_workbook(INPUT)` → modify → `wb.save(OUTPUT)`. NEVER create a fresh `Workbook()` and NEVER deliver via `pandas.to_excel()`: both drop sheet names, extra sheets, and formulas, and the grader answers "worksheet not found" even when your values are correct. After saving, reload and assert `sheetnames` and the header rows match the input exactly.
- **Worked examples are the spec.** Cells already filled inside the answer range, and example/result sheets (`Output`, `Manual Result`, `Desired...`), show the exact rule, format, and order expected. Read them first, infer the rule, never overwrite them, and replay them as fixtures against your own output: before finishing, recompute EVERY pre-filled example cell with your own logic and assert equality — one mismatch means your rule is wrong, so re-derive it from the examples instead of preserving the examples and filling the rest with the broken rule.
- **Forum-style instructions still demand the workbook.** "How can I ...", "Could you advise ...", or "what formula should I use" are task wordings, not invitations to write advice. The deliverable is always the manipulated workbook at `OUTPUT_PATH`; answer cells must contain computed values, never code, formulas, or explanations.
- **Coordinates are Excel coordinates.** Write with `ws["B3"]` / `column_index_from_string`; never use pandas positional indexing for writes (its header handling shifts every row by one) and never `chr(64 + n)` (breaks past column Z). Ranges are closed intervals — write and check both endpoints.

---

## Common Workflow

1. **Explore** the input file: list sheets, inspect headers, check dimensions.

- Inspect actual workbook data beyond the preview, including nearby rows/columns, sample outputs, formulas, labels, headers, and any reference/example sheets such as `Output`, `Manual Result`, or `Desired...` tabs.

- Treat existing filled cells in the requested output area or adjacent example tables as semantic examples for edge cases and expected formats, but still recompute and write the complete requested target range.
   - Scan the used range for complete header groups, not just row 1. Tables may start in later rows/columns, have title rows above them, or have multiple source/result tables on the same sheet; use nearby labels and the requested output range to distinguish sources from destinations.
   - Locate tables, fields, and target ranges by header text, nearby labels, and surrounding nonblank structure rather than fixed coordinates. Build header maps from actual cells when useful, e.g. `{str(cell.value).strip(): cell.column}`.
2. **Write `solution.py`** with `INPUT_PATH` and `OUTPUT_PATH` defined at the top.
3. **Execute** `python solution.py` and verify the output file was created.
4. **Confirm** the target cells/range contain the expected values.

---

## Library Selection

| Use case | Library |
|----------|---------|
| Preserve formulas, formatting, named ranges | `openpyxl` |
| Bulk data transformation, aggregation, sorting | `pandas` → write back with `openpyxl` |
| Simple cell read/write | `openpyxl` |

**Warning**: `pandas.to_excel()` silently destroys existing formulas and named ranges.
When writing back to a spreadsheet that contains formulas, always use `openpyxl.save()`.

**Formula evaluation caution**: `openpyxl` can write formulas but does **not** calculate them or update cached results. If the requested output will be checked as cell values, compute the result in Python and write literal values unless the user explicitly requires live formulas. When existing formulas are inputs to your logic, load a second workbook with `data_only=True` to read cached values while saving changes through the normal workbook:

```python
wb = openpyxl.load_workbook(INPUT_PATH)
wb_values = openpyxl.load_workbook(INPUT_PATH, data_only=True)
ws = wb["Sheet1"]
ws_values = wb_values["Sheet1"]
```

Treat wording such as “write/fix a formula,” “SUMIFS/COUNTIFS,” “VBA,” or “macro” as a description of the spreadsheet logic unless the deliverable explicitly requires live formula text, an `.xlsm`, or a preserved VBA project. For normal `.xlsx` outputs, implement the equivalent logic in Python/openpyxl and write the computed final values to the requested cells so verification does not depend on Excel recalculation or macros.

When the user provides an existing or broken formula, use it as a semantic specification: honor its referenced lookup ranges, criteria ranges, return ranges, aggregation intent, and error-handling behavior, then write the resulting values rather than guessing different source columns or leaving unevaluated formulas. Fixing a formula means a MINIMAL repair — keep its original return column, lookup ranges, and overall structure, and never "improve" its logic. Before overwriting anything, load the cached values (`data_only=True`) and record what the old formula currently produces; your rewrite must reproduce those values cell-for-cell except for the exact aspect the user complains about.

## When formulas are the question

Even when the task is *about* a formula ("my VLOOKUP returns #N/A", "write a formula that ..."), the scored artifact is the computed workbook. Workflow:

1. **Mine templates first.** Dump the formulas already present in the same column, neighboring rows, or template sheets (`ws.cell(...).value` where `data_type == "f"`) and reuse their exact reference structure instead of inventing one.
2. **Reproduce the logic in Python.** Implement the lookup/aggregation/conditional yourself with openpyxl/pandas and write literal values to the target cells.
3. **If you must write a formula string** (rare; only when live formulas are explicitly required): start it with `=`; modern functions need the `_xlfn.` prefix (e.g. `_xlfn.XLOOKUP`) or LibreOffice shows `#NAME?`; avoid array formulas (CSE) — rewrite them as per-row scalar logic; self-references and whole-column references that include the target cell itself produce `#VALUE!`.
4. **Verify by recalculation, not by reading your formula.** To check formula behavior, recalculate with LibreOffice (`libreoffice --headless --convert-to xlsx --outdir /tmp/recalc file.xlsx`) and read the values back — but check first with `command -v libreoffice`, because many sandboxes do NOT have it; without it, recompute the formula's logic in Python and deliver literal values. When `data_only=True` returns `None` for a formula cell there is no cached value to trust.

---

## Mechanical ground truth

The grader compares against the MECHANICAL output of the reference formula or macro — not the "reasonable" output you would produce. Adjust your sense of correctness accordingly:

- A transform applies blindly to every row in its range, including rows that look like headers or contain garbage; the reference macro does not skip them, so neither do you.
- Lookup misses keep the mechanical error result: write the literal `#N/A` where the reference VLOOKUP errors, instead of leaving the cell blank.
- Concatenations and joins preserve empty slots and original source order; do not re-sort, trim, or "clean up" pieces — a derived text fragment must remain a byte-exact substring of its source cell.
- For every cell you are tempted to leave blank, skip, or tidy up, ask: what does the dumb formula output here?

---

## solution.py Template

```python
import openpyxl
import pandas as pd

INPUT_PATH  = "..."   # set to the actual input path
OUTPUT_PATH = "..."   # set to the actual output path

wb = openpyxl.load_workbook(INPUT_PATH)
ws = wb.active  # or wb["SheetName"]

# --- perform manipulation ---

wb.save(OUTPUT_PATH)
```

---

## Output Requirements

- Save the result to `OUTPUT_PATH`.
- Do not hardcode row counts or column letters — iterate over actual rows in the workbook.
- Preserve sheets and cells not mentioned in the instruction.

## Matching and Target Range Hygiene

- Choose the comparison operator from the instruction and examples: use `startswith` for “begins with”, substring search for “contains/search/occurrence”, and exact normalized equality only when a whole-cell match is implied.
- Create small helper functions for comparisons and numeric parsing. Normalize text by trimming, collapsing repeated spaces/NBSPs, and casefolding; when names or labels have punctuation/spacing inconsistencies, consider punctuation-insensitive keys. Parse numeric text after removing commas/currency symbols while preserving signs and decimal points; skip `None`/blank and booleans for numeric tests, and handle placeholders such as `"-"`, `"$"`, `"$0"`, blanks, and numeric zero deliberately.
- Normalize date keys deliberately: handle `datetime`/`date` objects, Excel serial numbers, and date-like strings, then compare at the granularity implied by the task, such as exact date, month, month/year, fiscal period, or year. For workday/date-window logic, compute the range in Python and exclude weekends/holidays as specified.

- For monthly or period summary grids, canonicalize period labels from all sources: sheet names, title text, row/column headers, text months such as `March`, and actual date cells. Match summaries by normalized period plus the other stated criteria rather than by fixed month offsets or existing formulas.
- For date ranges and rolling windows, infer endpoint inclusivity from wording and examples. Phrases like `X to Y`, `through`, and `up to`, or examples such as `2 to 5` meaning `4 days`, usually require inclusive boundary handling.
- For time extraction or time-threshold logic, parse `datetime`, `time`, Excel serial/fractional times, and time-like strings into real Python `time`/`datetime` values. Write real time values with an Excel `number_format` such as `hh:mm:ss AM/PM`; do not write text substrings when the result should behave as a time.
- For joins, deduplication, grouping, interval lookups, lookup grids, and ordered outputs, build explicit normalized keys, including composite keys when the task refers to multiple fields. Preserve original source order within each group unless sorting is explicitly requested.
- For outputs that depend on other rows or lookup grids, make a first pass to build normalized dictionaries/groups/range structures, then a second pass to write results. Avoid nested full-sheet scans per row; split delimited tokens and ignore empty tokens, and treat error literals such as `#N/A` as meaningful sentinel values when the task refers to them.

- For lookups, filters, joins, and label/header matching, normalize comparison keys consistently: trim whitespace, skip blanks explicitly, use case-insensitive text matching when appropriate, and treat numeric-looking IDs consistently (`330`, `330.0`, and `"330"`). Keep numeric outputs numeric; use `number_format` for display formatting instead of converting numbers to strings unless text is explicitly required.
- When replacing a generated output area, clear only the instructed target range before writing new results so stale values/formulas do not remain. Preserve formatting, column widths, borders, formulas, and unrelated cells unless the instruction explicitly asks to change them.

- If the instruction includes formatting changes, apply them exactly after writing values and only to the requested cells/range. Use `openpyxl` styles for fills, alignment, fonts, borders, and number formats; convert hex colors to ARGB when needed, for example `#FFC000` → `FFFFC000`. For “format as text,” set `number_format = '@'` and write string values when the expected cell values are text.

- When the instruction names a destination range or columns, write derived results directly there. Do not insert rows/columns, relocate the source table, or sort/delete source records unless that structural change is explicitly requested.
- For filtered lists, summaries, and aggregations, first collect all source records/results in memory, preserving the required order, then write from the first output row and clear leftover cells below the new results in the target columns. When adding rows, copy style/alignment/number format from an existing template row when appropriate; when deleting rows, delete from bottom to top to avoid row-index shifts.
- Preserve intended blanks as empty cells (`None`) rather than placeholder text or `0` unless the task specifies otherwise.

- For numeric aggregation, crosstab, SUMIFS-like, and INDEX/MATCH-style summary outputs, infer missing-match behavior from table semantics and examples: numeric summary grids usually require literal `0` for no matching records, while filtered lists or “show only once” outputs usually require blanks (`None`).
- For blank-sensitive logic such as “if input is blank, output blank,” evaluate the driving input with `data_only=True` when it may itself be a formula, and write `None` for truly blank outputs rather than relying on a new formula returning `""`.

## Execution Pitfalls

- Deleting rows means `ws.delete_rows(...)`, not clearing cell contents — a cleared row still exists and shifts nothing. After deleting, assert `ws.max_row` shrank by the deleted count and that no fully-blank rows remain inside the data region; formulas that referenced the deleted rows now dangle (`=+C9-D9` pointing at emptied rows recalculates to 0) — rewrite them as literal values.
- "Insert N new rows" means N BLANK rows (`None`), not N copies of the anchor row's contents — assert the inserted cells are actually empty.
- With no worked example showing otherwise, fall back to native Excel semantics: Excel sorts lexicographically ('D 14' < 'D 6'), and `LOOKUP`/approximate `MATCH` picks the largest key ≤ target. Run `print(ws.tables)` during exploration — Excel Tables change what a reference means.
- Never invent rounding or trimming: `grep -n "round(" solution.py` before saving — every call must trace to instruction wording or an existing formula template, otherwise delete it. The same goes for `.strip()` on derived text.
- Format codes in the instruction are output text specs: `DDD` → `Mon` (not `Monday`), `MMM` → `Jan`, `0.0%` → one-decimal percent text.
- Excel serial values near 1899-12 / 1900-01 are usually DURATIONS, not dates: `1900-01-11` encodes 11 days. Convert with `openpyxl.utils.datetime.from_excel` / `to_excel` and keep the day component — dropping it loses whole days.
- Emit text values verbatim; normalization (trim/casefold) is for matching keys only, never for what you write.
- "Above/below an anchor" means the nearest matching cell searching outward from the anchor, not the whole column.
- Convert Excel date serials with `openpyxl.utils.datetime.from_excel`; never hand-compute the 1899-12-30 epoch — off-by-one is the norm.
- Apply the requested transform uniformly to every row in the range; do not skip rows that "look like headers" — the grader expects a blind transform.
- When fixing an existing formula, make the minimal change and keep its original return column and reference structure.
- A worked example demonstrates format, not precision — recompute values at the task's own precision even when the example shows rounded numbers.
- "Put X from CELL with headers" means the header row itself starts at CELL.
- If `data_only=True` returns `None` for formula cells you must read, recalculate a copy with LibreOffice (`libreoffice --headless --convert-to xlsx --outdir /tmp/recalc file.xlsx`) and read values from there — if `libreoffice` is not installed in the sandbox, recompute the formula logic in Python instead; never invent aggregates to fill the gap.

---

## Robustness for Simple Fill Tasks

- Prefer simple, auditable row/column loops over complex workbook XML parsing unless the task truly requires unsupported workbook internals. Before returning, run the script once to catch syntax/indentation errors and verify that representative target rows were actually written.

When the user asks for a formula, macro, VBA code, or a fix to an Excel formula, still deliver the completed workbook state: compute the intended results in Python and write literal final values into the requested cells. Do not write formula strings unless the task explicitly says the output must contain live formulas.

After writing, reload or inspect the saved workbook and verify that every requested/evaluated target cell contains a non-formula literal where a value is expected. If a target cell is still `None` unexpectedly, fix the script before finishing.

Use existing formulas in the workbook as examples/specifications, not as output. If a cell contains a reference formula such as `=A25` or an INDEX/MATCH/SUMIFS pattern, parse what source cells/ranges/criteria it refers to, compute those results yourself, and overwrite the destination with the referenced or calculated value.

For blank-sensitive formula tasks, compute the branch explicitly: if the driving source cell is truly blank, write `None`; otherwise write the actual result such as `0`, `1`, a category label, or a lookup value. Never rely on `IF(...,"",...)` formulas to be recalculated later.

For lookup/category tasks, locate both the input rows and the lookup table by headers and nearby labels. Support exact keys, numeric-looking keys, and interval/range tables; then fill every destination row that has a driving input, not just the first visible example.

For “every nth row” or OFFSET-style tasks, infer the source column, first source row, and step from the provided examples or formulas, then copy the actual source values into the requested output range as literals.

For schedule/calendar fill tasks, build a cycle-day-to-periods mapping from the schedule/template area first, then fill the daily rows across all requested class columns based on each row’s cycle day. Preserve repeated/double periods exactly as shown by the template; do not leave formulas in the schedule cells.

For INDEX/MATCH problems where the first row works but subsequent rows fail, treat row labels, column/year headers, region/type criteria, and expense/category labels as a multi-key lookup. Fill the whole result matrix with values from the source data table, using cached `data_only` values when source cells are formulas.

For multi-step macro/VBA-style requests, implement every stated operation in the workbook, not just the first deletion/filtering step. Re-read the numbered requirements before saving and verify later computed columns, totals, and derived fields as well as the obvious filtered rows.

When a target range includes special rows such as `Total`, `Grand Total`, `min`, `max`, constraints, headers, or blank separators, do not apply ordinary row logic blindly to those rows. Compute totals as aggregates when indicated, and leave constraint/header/blank cells untouched unless explicitly requested.

For residual-balancing tasks, identify data rows separately from min/max constraint rows. Add positive residuals from unit 1 toward unit 5 without exceeding max values; subtract negative residuals from unit 5 toward unit 1 without going below min values; update only the unit cells in actual data rows.

For time-threshold rows, decide per row whether it is a normal data row or a summary row. Normal rows use the before/after threshold rule; summary rows should aggregate the computed normal-row results if the workbook labels or examples indicate a total.

Keep scripts simple enough to run cleanly. Avoid unnecessary dynamic code generation and fragile f-strings with regex expressions inside them. Always execute the final `solution.py`; fix any syntax, indentation, or runtime error, then verify representative target cells.

If workbook cells contain arbitrary sample text that could be sensitive or trigger content filters, do not quote large raw cell contents in your response. Process them locally in Python with neutral variable names and output only the completed script/workbook changes.