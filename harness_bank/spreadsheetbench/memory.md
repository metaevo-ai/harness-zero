# Accumulated failure patterns — SpreadsheetBench xlsx manipulation

The task: manipulate an .xlsx workbook per a forum-style instruction whose
message carries five fields (`### instruction`, `### spreadsheet_path`,
`### instruction_type`, `### answer_position`, `### output_path`). The
deliverable is the input workbook edited in place and saved to
`output_path`; the grader recalculates both files with LibreOffice and
compares cell values inside `answer_position`. Most failed runs are process
failures, not spreadsheet-knowledge failures. Intervene as soon as a
pattern is recognizable.

1. **Explore-first skipped.** The student starts writing code before
   exploring the workbook (sheets, headers, dimensions, worked examples).
   Intervene: replace the opening with a bounded exploration action — list
   sheets and dimensions, print headers and nearby labels, dump any
   pre-filled cells inside the answer range. NOTE: the student's sandbox
   has NO harness files — nothing exists under /opt/ahd/harness, so never
   send the student to read skill or memory paths there. You are the
   carrier of the workflow: embed the relevant checklist items in your
   replacements (as part of the command or the visible content) instead of
   pointing at files.
2. **Workbook rebuilt.** Delivery via a fresh `Workbook()` or
   `pandas.to_excel()` drops sheet names, extra sheets, and formulas; the
   grader answers "worksheet not found" even when the values are right.
   Intervene: output = `shutil.copy(INPUT, OUTPUT)` then openpyxl edits, or
   `load_workbook(INPUT)` → modify → `wb.save(OUTPUT)`; afterwards reload
   and assert sheet names and header rows match the input.
3. **Worked examples overwritten.** The student writes its own rule across
   the whole answer range and clobbers pre-filled example cells that pin
   the rule, format, and order. Intervene: examples are the spec — infer
   the rule FROM them (a rule contradicting an example is wrong, not the
   example), write only into blank cells, and before finishing diff output
   vs input inside the answer range: every pre-filled cell byte-identical
   unless the instruction explicitly requires changing it.
4. **Formula strings delivered.** Answer cells holding strings starting
   with `=` can never be verified in the sandbox (no LibreOffice) and
   openpyxl writes no cached value. Intervene: literal computed values are
   the default; "write a formula / SUMIFS / VBA / macro" specifies the
   logic, not the deliverable format. Code and explanations never go into
   cells — only into the final summary.
5. **Coordinate drift.** pandas positional indexing (its header handling
   shifts every row by one), `chr(64 + n)` column math (breaks past Z:
   AZ=52, BE=57), open range ends. Intervene: `ws["B3"]` and
   `column_index_from_string`, closed intervals with both endpoints, and
   the answer_position boundary wins over the instruction's start-cell
   wording.
6. **Self-consistent "verification".** The student prints its own output
   and eyeballs it against its own intent — that proves consistency, not
   correctness, and a wrong rule is usually self-consistent too. Intervene:
   verification must be assertions against independent recomputation and
   the worked examples. Stop-and-fix signals to watch for: `=` strings in
   the answer range; `data_only=True` returning None; a conditional column
   where every row got the same value; a negative date difference; zero
   lookup hit rate; text where numbers belong; a rule that writes zero net
   cells; output contradicting numbers the instruction itself quotes
   ("a total of 9,480" is a unit test, not background).
7. **Prose instead of workbook.** Forum-worded tasks ("How can I ...",
   "could you advise") answered with advice, or imperative tasks
   (delete / filter / insert / sort / fill) "answered" with an answer range
   flooded by repeated prose words (Yes/No) or explanation text in cells.
   Intervene: the deliverable is always the actually-modified workbook.
8. **"Reasonable" instead of mechanical.** The grader diffs against the
   mechanical output of the reference formula or macro, not the reasonable
   output. Intervene when the student: repairs more than the complained
   aspect (keep the original return column, ranges, and structure; replay
   the old formula's cached values as fixtures for everything else);
   leaves lookup misses blank instead of the literal `#N/A`; skips rows
   that "look like headers" (transforms apply blindly); reorders, trims, or
   drops empty slots from concatenations (derived fragments stay byte-exact
   substrings of their source).
9. **Layout blindness.** Block structure, repeated headers, blank masks,
   and anchor position are part of the answer: "put X from I3" means the
   first cell lands exactly at I3, header row included. Intervene when the
   student invents summary rows or headers the examples don't show, fills
   the right values into the wrong block geometry, or clears rows instead
   of deleting them (`ws.delete_rows` must shrink `ws.max_row`; formulas
   left dangling onto emptied rows are rewritten as literal values).
10. **Trial-and-error loops.** The same exception signature three or more
    times in a row — a third of the turn budget once went to a single
    ValueError whose fix was documented library behavior. Intervene:
    replace with a docs action — have the student inspect the relevant
    library source or documentation (`python3 -c "import openpyxl.utils, inspect; print(inspect.getsource(...))"`),
    or embed the documented fix directly in your replacement — then one
    changed attempt.
11. **No-op verification tail.** A correct deliverable already saved, then
    the student re-verifies without changing anything until the 40-turn
    wall kills the trial unsubmitted. Intervene once the trajectory passes
    ~25 turns with a file on disk: one reload-and-assert pass, then finish
    immediately.
12. **Native semantics reinvented.** Self-invented sort keys, match modes,
    rounding, and trimming where Excel has defaults: lexicographic sort
    ('D 14' < 'D 6'), approximate LOOKUP (largest key ≤ target), no
    rounding or `.strip()` unless the instruction or a formula template
    shows it, format codes as output text spec ('DDD' → 'Mon', not
    'Monday'), and serial values near 1899-12/1900-01 read as durations
    (1900-01-11 encodes 11 days — dropping the day part loses whole days).
