# spreadsheetbench_student harness bank

Student-side harness for SpreadsheetBench Verified 400 (`data/spreadsheetbench-verified`),
loaded into the Harness-Zero miniswe via the `student_harness_dir` agent kwarg.

## Layout

- `skills/<name>/SKILL.md` — deepagents-native skills (progressive disclosure:
  index in system prompt, student reads full text via `execute cat`).
  Seeded with `spreadsheet-manipulation`, the same workflow/pitfall catalog as
  the teacher-side bank, in native frontmatter format.
- `memory.md` — primed into the system prompt (deepagents MemoryMiddleware).
- `tools/*.py` — prebuilt tools loaded next to `execute`; each exposes `make_tool(backend)`.
- `middlewares/*.py` — student-side middlewares; each exposes `make_middleware()`.
- `manifest.json` — the enable list; components are switched on/off here.
- `registry.py` — factory registration + catalog; the loader's single entry point.

Evolution process: `.agents/skills/student-harness-evolve/SKILL.md`.
Later converted to teacher-side components under `harness_bank/spreadsheetbench/`.
