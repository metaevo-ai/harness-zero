---
name: teacher-harness-adapt
description: "Generic workflow for harness adaptation (Harness-Zero paper, Appendix B) -- rewrite an evolved student-side harness h* (`harness_bank/<domain>_student/`) into the private reference harness K (`harness_bank/<domain>/`) used by the harnessing teacher agent: tools become action recipes, student middleware becomes review middleware, skills become review guidance, memory becomes failure patterns, plus an optional domain prompt. Use when creating a teacher-side bank for a new domain or re-adapting after the student bank evolves."
---

# Harness Adaptation Guide (student-side h* -> teacher-side K)

## What this workflow does

For one domain, produce **one** deliverable: a **teacher-side reference harness K** at `harness_bank/<domain>/`, adapted from the evolved student-side bank `harness_bank/<domain>_student/` (produced by the `student-harness-evolve` skill). K does not act on the environment and is never shown to the student; the harnessing teacher agent reads it to decide PASS/REPLACE on each unexecuted student proposal and to construct student-native replacements.

The adaptation **preserves what each component does while changing its audience and enforcement point** (paper Table 6):

| Student-side h* (acts around the student) | Teacher-side K (advises the harnessing agent) |
|---|---|
| `tools/*.py` -- StructuredTool in the student's action space | `tool/<name>.md` -- **action recipe**: how to construct an equivalent action the student can execute through h |
| `middlewares/*.py` -- inspects state / blocks the loop | `middlewares/*.py` -- **review middleware**: detects the condition from the proposal + visible trajectory, privately hints the teacher |
| `skills/<name>/SKILL.md` -- workflow instructions for the student | `skill/<name>.md` -- **review guidance**: diagnose the current step; construct a student-native replacement when needed |
| `memory.md` -- recurring failures and practices | `memory.md` -- **failure patterns**: each failure becomes a recognizable review condition + targeted intervention |
| (nothing) | `teacher_prompt.md` -- optional **domain prompt**: the domain's review policy, appended to the teacher's system prompt |

The mapping is **not one-to-one**: one student guard can split into several review checks (USPTO's `answer_guard` became `answer-file` + `enumeration` + `canonical-form` + `completeness`); a sandbox-probing guard becomes a trajectory-evidence condition plus an action recipe when the correction itself must run in the environment. Record every split/merge/drop decision and its reason in the teacher bank's `README.md`.

## Parameters (set at each instantiation)

| Parameter | Meaning |
|---|---|
| `<DOMAIN>` | Domain name; teacher bank path is `harness_bank/<domain>/` |
| `<STUDENT_BANK>` | Source bank, default `harness_bank/<domain>_student/` |
| `<TASKS>` / models | A few train task IDs + student/teacher model routes for the optional reviewed smoke rollout |

## How K is consumed (the integration contract)

Read these files before writing anything; they define the exact contract:

1. **Readable components** -- `src/harness_zero/store.py` (`TrialStore._copy_components`): per trial, `index.json` + every file it lists + `teacher_prompt.md` are copied into the teacher workspace under `/components/`. A listed path that is missing or escapes the bank is a **hard rollout failure**. Only kinds `memory` / `skill` / `tool` are listed; ids follow `kind:name`.
2. **Teacher session** -- `src/harness_zero/teacher.py`: the teacher is a deepagents agent with read-only filesystem tools (`ls`, `read_file`, `glob`, `grep`) rooted at its workspace, plus one `submit_review` tool. The base system prompt (`src/harness_zero/prompts/harness_teacher.md`) already covers: read `/components/index.json` first, replacement style (student first-person voice, minimal change, at most one `execute` call), the replacement budget, and the submission format. **Do not repeat these in domain content.**
3. **Domain prompt** -- `teacher_prompt.md` is appended verbatim after the base system prompt (not listed in `index.json`).
4. **Review middleware** -- loaded via `--teacher-middleware-factory harness_bank.<domain>.middlewares:build_teacher_middlewares` (import string `module:function`, resolved by `importlib` in `src/harness_zero/harness.py`). Each middleware subclasses `TeacherCandidateMiddleware` (`src/harness_zero/teacher_guidance.py`) and implements `hint(candidate) -> TeacherHint | None`. A non-None hint is appended to the current `Student update` message under `## Triggered middleware guidance` with `### \`<component_id>\``, `Evidence:`, `Hint:` (deduped per update by component_id).
5. **The candidate** -- `src/harness_zero/review.py`: `candidate.original` is an `AssistantResponse` (`reasoning`, `content`, `tool_call` = `{name: "execute", command}` or `None` for a finish); `candidate.context` is the student-visible message dicts; `candidate.candidate_issue` flags malformed proposals (a PASS would preserve the issue).
6. **The submission** -- PASS: replacement null. REPLACE: a complete `AssistantResponse` (reasoning + content + at most one `execute` call; null `tool_call` = final answer), plus `components_used` (ids from your content) and a <=500-char private reason.
7. **Leak filters** -- `src/harness_zero/dataset_utils.py`: accepted responses referencing `/components/`, `submit_review`, `components_used`, `harness-zero-review`, or reviewer-perspective reasoning ("the student's proposal", "PASS this response") are rejected from SFT data. Nothing teacher-private may appear in replacement-facing text.

## Bank structure

```
harness_bank/<domain>/
|-- index.json          # readable-component index: id (kind:name), kind, path, one-line summary
|-- teacher_prompt.md   # optional domain review rules (appended to the teacher system prompt)
|-- memory.md           # failure patterns: trigger / evidence / intervention / do-not-trigger
|-- skill/<name>.md     # review guidance (keeps YAML frontmatter: name, description)
|-- tool/<name>.md      # action recipes: student-native bash reference implementations
|-- middlewares/        # review middleware; __init__.py exposes build_teacher_middlewares()
`-- README.md           # provenance + content inventory + split/merge/drop decisions
```

## Adaptation discipline

1. **Review-time checkability.** Every review-middleware condition must be decidable from the candidate and the student-visible trajectory alone. The teacher has **no sandbox access by design** -- otherwise the behavior cannot be internalized by the student. A student-side sandbox probe becomes either an *evidence requirement* in the hint instruction ("PASS only when the trajectory shows X after its last write") or an *action recipe* the teacher deploys through the student's own `execute` (the check and its result then enter the student-visible trajectory -- this grounding is what makes the trajectory trainable).
2. **Fail-open.** Hint only on confident matches; parse errors, missing fields, and ambiguity return `None`. The teacher model sees the full trajectory and makes the final call; a noisy hint costs replacement budget and review quality.
3. **Message-level purity.** `hint()` is a pure synchronous function over the candidate -- no I/O, no backend, no async. There is no event-loop escape hatch teacher-side; anything that needs environment evidence belongs in an action recipe.
4. **Hints tell the teacher what a PASS requires.** Write `evidence` as the concrete match ("finish candidate; no write under `/app/output/` appears in assistant messages") and `instruction` as the actionable rule ("REPLACE with the verification action now; finish only after the evidence exists"), naming the relevant recipe id when one applies.
5. **K is private; replacements are student-voice training data.** Component files may discuss the student in third person, but every instruction that shapes a replacement must lead to a response the student could have produced from its visible context: no task-specific answers, no verifier or hidden-reference knowledge, no mention of teacher/review/components/training, no `/components/` paths.
6. **Same anti-overfit bar as evolution.** Nothing task-specific (concrete answers, cell addresses, file names unique to one task) enters any K component. K encodes conditions and corrective behavior for a *class* of tasks.
7. **Do not repeat the base prompt.** Replacement style, budget accounting, submission format, and generic review rules live in `prompts/harness_teacher.md`. Domain content adds only what is domain-specific.

## Per-component recipes

### `memory.md` (memory -> failure patterns)

Rewrite the student-side checklist clauses into numbered patterns, each with **Trigger** (the recognizable review condition), **Evidence** (what in the trajectory confirms it), **Intervention** (the targeted correction), and **Do not trigger when** (the false-positive guard). Open with a scope header: teacher-side guidance from student-visible failures; contains no task-specific answers; the student never sees this file. Keep the domain knowledge -- drop the imperative student-voice ("never eyeball-parse" -> "Trigger: the student hand-transcribes a SMILES without an RDKit parse").

### `skill/<name>.md` (skill -> review guidance)

Keep the YAML frontmatter (`name`, `description`). Reframe the workflow for the teacher: a short `Teacher use` section stating when to apply it (diagnose which workflow step the current proposal got wrong) and how to realize a missing step as the student's next native action; then keep the working procedure itself as the diagnostic reference. Preserve every domain-specific condition and pitfall; change only the audience.

### `tool/<name>.md` (tool -> action recipe)

Format (see `harness_bank/uspto/tool/verify_answer.md`): title, blockquote one-liner, when to deploy, then the **reference implementation as student-native bash** -- a `cat > .agent-tools/<name>.py <<'PY' ... PY` heredoc plus its invocation, runnable through the student's `execute` tool -- then what a PASS requires and how to read the diagnostics (which checks are contract failures vs warnings, what the check does NOT prove). Port the student tool's logic; do not import or reference the student-side Python module.

### `teacher_prompt.md` (domain prompt)

Domain review rules only: the **required trajectory shape** (the stepwise SOP every accepted trajectory in this domain must read as), **what must be spoken** (domain knowledge stated as reusable general rules in the reasoning, never unexplained conclusions, never references to tools/files/components), and the **review policy** (REPLACE on SOP-form violations even when the proposal might accidentally score -- the trajectory is training data; prefer several small step-shaped replacements over one large rewrite; PASS sound derivations and intervene at the first breaking step).

### `middlewares/` (student middleware -> review middleware)

Template:

```python
"""Teacher-side <check> -- <the failure it catches and why it scores>.

Observed failure mode ... Student-side this was <what it did>; on the
teacher side the sandbox is unreachable, so the check looks for evidence
in the student-visible trajectory instead.
"""

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class Teacher<Name>Middleware(TeacherCandidateMiddleware):
    name = "teacher-<name>"

    def hint(self, candidate) -> TeacherHint | None:
        proposal = candidate.original  # reasoning / content / tool_call
        # condition over the proposal and candidate.context; fail open
        if not <condition>:
            return None
        return TeacherHint(
            component_id="middleware:<name>",
            evidence="<concrete match>",
            instruction="<what a PASS requires / what to REPLACE with>",
        )
```

Register every middleware in `middlewares/__init__.py`'s `build_teacher_middlewares()` (order = injection order). When scanning `candidate.context` for evidence, match assistant messages only -- a checklist the student merely *read* in a tool result is not evidence the student *ran* the check. Middleware ids (`middleware:<name>`) are not listed in `index.json` but appear in the teacher's `components_used`; keep them stable.

## Workflow

### Phase 0: read the inputs

- The full student bank: `registry.py`, `manifest.json`, `memory.md`, `skills/`, `tools/`, `middlewares/` (each docstring records the observed failure it came from -- that is your adaptation source).
- The target harness h: `src/harness_zero/prompts/student.md` (one `execute` tool, final answer without a tool call) -- every recipe and replacement must be expressible in exactly this action space.
- The teacher base prompt `src/harness_zero/prompts/harness_teacher.md` and the contract files listed above.
- The canonical examples `harness_bank/uspto/` + `harness_bank/uspto_student/` and `harness_bank/spreadsheetbench/` + `harness_bank/spreadsheetbench_student/`.

### Phase 1: adaptation plan

Inventory every student-bank component and decide its fate: transform (to which K kind), split (into which checks), merge, or drop (why it is not review-time checkable or not worth a check). Write the plan as a mapping table into the teacher bank's `README.md` skeleton before writing content.

### Phase 2: write content components

`memory.md`, `skill/*.md`, `tool/*.md`, `teacher_prompt.md` per the recipes above, then `index.json` listing exactly the memory/skill/tool files with one-line summaries (the summary is what the teacher sees when deciding what to read -- make it name the situations the component covers).

### Phase 3: write review middleware

One file per check, the `__init__.py` factory, mirroring the plan.

### Phase 4: reconcile and smoke

```bash
PYTHONPATH=src:. python - <<'PY'
import json, pathlib
from harness_bank.<domain>.middlewares import build_teacher_middlewares
mws = build_teacher_middlewares()
bank = pathlib.Path("harness_bank/<domain>")
for c in json.loads((bank / "index.json").read_text())["components"]:
    assert (bank / c["path"]).is_file(), c
print(len(mws), "middlewares;", "index paths OK")
PY
PYTHONPATH=src pytest tests/ -q
```

Every `index.json` path must resolve (a miss kills every rollout at trial setup) and the factory must import cleanly.

### Phase 5: reviewed smoke rollout (recommended, with user-confirmed model routes)

Run 2-5 train tasks through the full reviewed pipeline (see the README quickstart): `--components harness_bank/<domain> --teacher-middleware-factory harness_bank.<domain>.middlewares:build_teacher_middlewares`. Then inspect, per trial under `runs/<job>/<trial>/trial/`:

- `teacher_session.jsonl` -- did the teacher read `/components/index.json` early; did hints fire on the intended candidates?
- `trajectory.jsonl` review entries -- PASS/REPLACE ratio, `components_used` (do the ids exist?), replacement validity under h (at most one `execute`; student first-person reasoning; no `/components/` or reviewer-voice leaks).
- `teacher_llm_calls.jsonl` + `result.json` -- cost and reward vs the bare baseline on the same tasks.

## Iteration attribution (after the smoke rollout)

1. **Teacher never reads a component** -> the `index.json` summary does not name the situations it covers; fix summaries, not content volume.
2. **Hint never fires** -> the condition does not match real proposal shapes; re-read actual candidates in `trajectory.jsonl` and relax/narrow the condition, keeping fail-open.
3. **Hint fires but is ignored or misused** -> the instruction is not actionable; rewrite it as "PASS requires X / REPLACE with Y", naming the recipe id to deploy.
4. **Replacements leak reviewer voice or private paths** -> the domain prompt's replacement discipline is too weak; strengthen "form matters" and the must-be-spoken rules in `teacher_prompt.md` (never patch individual trajectories).
5. **Over-replacement (budget burned early)** -> the domain policy invites style rewrites; restate that only substantive corrections justify the budget.

## Acceptance

Deliver: the complete teacher bank (all files above), the `README.md` with the component mapping and every split/merge/drop decision, smoke-test output (factory import + index reconciliation + `pytest tests/ -q` green), and -- if the smoke rollout ran -- per-trial review statistics (hint fire counts, PASS/REPLACE ratio, components_used coverage, reward vs baseline) plus the list of open issues for the next adaptation round.
