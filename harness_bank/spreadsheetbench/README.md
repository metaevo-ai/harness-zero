# spreadsheetbench harness bank

Teacher-side components for SpreadsheetBench Verified 400 (`data/spreadsheetbench-verified`),
transcribed from the evolved student-side bank (`harness_bank/spreadsheetbench_student`,
three evolution rounds on the 300-task train split). Content stays aligned with the
student bank; only the framing is teacher-facing.

## Contents

- `memory.md` — accumulated student failure patterns with interventions
  (skill-first, in-place edit, worked examples as spec, literal values, coordinate
  discipline, assertion verification, mechanical ground truth, loop/budget discipline).
- `skill/spreadsheet-manipulation.md` — xlsx manipulation workflow and pitfall catalog;
  reference for diagnosing which workflow step the student got wrong.
- `tool/prefilled_diff.md` — JIT tool: diff input vs output inside the answer range and
  report changed pre-filled literal cells. Deploy before the student finishes.
- `tool/answer_range_check.md` — JIT tool: mechanical health check of the answer range
  (empty range, formula strings, numbers-as-text, error literals, constant fills).
- `middlewares/` — teacher-side candidate hints, loaded via
  `harness_bank.spreadsheetbench.middlewares:build_teacher_middlewares`:
  `stall` (empty/intent-only candidates), `error_loop` (same exception 3x),
  `turn_budget` (25/35-turn reminders), `finish_guard` (delivery evidence check).

The train/test split ships in `data/spreadsheetbench-verified/splits.json` (300 train / 100 test).
