---
name: student-harness-evolve
description: Generic workflow for evolving a shared student harness -- for a benchmark's Harbor-format train task set, take bare miniswe rollouts as the baseline, handle only the tasks the previous round got wrong, use a swarm (default 10 coder subagents) to analyze rollout trajectories in parallel, have the main agent write the shared student harness (tools / middleware / skills / memory) under `harness_bank/<domain>/`, and iterate for 3 rounds with "miniswe eval rollouts loading that harness" as the evaluator. The evolved artifact can later be rewritten into a teacher-side harness.
---

# Shared Student Harness Evolution Guide

## What this workflow does

For a benchmark's train task set, evolve **one** deliverable: a **student-side harness shared by all tasks**, kept in a per-benchmark bank (`harness_bank/<domain>/`):

- `tools/` -- prebuilt tools (real StructuredTools loaded into miniswe alongside `execute`);
- `middlewares/` -- student-side langchain AgentMiddleware;
- `skills/<name>/SKILL.md` + `memory.md` -- skill and memory material, uploaded into the sandbox and exposed to the student via deepagents' SkillsMiddleware / MemoryMiddleware.

**The evaluator** is a bare miniswe rollout with the harness loaded (`harness_zero.harness:HarnessZeroMinisweAgent`, teacher passthrough, i.e. no teacher intervention; the bank is mounted via `student_harness_dir`). It measures "does this student harness help the model itself". A campaign runs **3 rounds** on the rhythm: "select the tasks the previous round got wrong -> parallel swarm analysis -> main agent writes the bank -> eval rollout on exactly those failed tasks -> attribution". Tasks already solved count as "the harness is good enough for them" and are not rolled out again in later rounds.

## Campaign parameters (set at each instantiation)

| Parameter | Meaning |
|---|---|
| `<DOMAIN>` | Domain name (determines the bank path `harness_bank/<domain>/`; for the student side, prefer a `<benchmark>_student` suffix to distinguish it from teacher-side banks) |
| `<TASKS>` | Harbor task path + task-list file (e.g. `-p data/spreadsheetbench-verified`, task list derived from `data/spreadsheetbench-verified/splits.json`) |
| `<MODEL>` | Evaluation model (e.g. `openrouter:qwen/qwen3.5-9b`, reasoning explicitly enabled) |
| `<BASELINE_JOBS>` | Job directory of the baseline (bare, no harness) rollout; the input for round-1 failure analysis. If it does not exist, run the baseline first |
| `<N_AGENTS>` | Swarm concurrency, at most 10 subagents at a time; all of the round's failed tasks are distributed among these subagents, with no per-subagent task cap |

## Bank structure and integration

```
harness_bank/<domain>/
|-- registry.py        # single entry point; API contract below
|-- manifest.json      # enablement manifest: tools/middlewares/skills lists + memory switch; toggle components only here
|-- memory.md          # accumulated checklist clauses (English, no source attribution, grouped by topic)
|-- tools/             # *.py, each exposing make_tool(backend) -> StructuredTool
|-- middlewares/       # *.py, each exposing make_middleware() -> AgentMiddleware
|-- skills/            # <name>/SKILL.md, deepagents skill format (progressive disclosure)
`-- README.md          # bank description and provenance
```

- **Registry API contract** (consumed by the loader under exactly these names): `TOOL_FACTORIES` / `MIDDLEWARE_FACTORIES` (dict, name -> `"module:function"` import string, which the loader resolves into a factory). `catalog_text()` (component listing rendering) is only for logs and audits; the loader does not consume it. Component docstrings are the catalog source.
- **Loading semantics** (the contract of `student_harness_dir`, implemented in `src/harness_zero/student_harness.py` / `src/harness_zero/student.py`):
  1. tools are appended to miniswe's tools list (after `execute`);
  2. the `skills/` directory and memory.md are uploaded into the sandbox (`/opt/ahd/harness/skills/`, `/opt/ahd/harness/memory.md`): skills are progressively disclosed through deepagents' `SkillsMiddleware` (only the index goes into the system prompt; the student reads full text via `execute cat`), and memory.md is injected into the system prompt through `MemoryMiddleware`; SkillsMiddleware and MemoryMiddleware run **before** the review middleware so the teacher sees the same context as the student;
  3. the bank's middleware is appended after the review middleware, followed by `StudentRequestCaptureMiddleware` (records the final model request, for audit);
  4. **bank bytes are hashed into `bank_sha256`** -- the rollout log writes `student_harness_bank.txt` recording the mounted bank's hash, for provenance only, with no assertions;
  5. when `student_harness_dir` is not passed, behavior is exactly the status quo (bare compatible).
- **The student core is not evolvable**: the `execute` tool, the student.md base prompt, the review middleware, the turn limit, and the tool_call adapter are fixed layers; evolution happens only in bank content.

## Per-round workflow

### Phase 0: determine this round's failed-task set

- Round 1: select tasks whose reward did not pass from the `<BASELINE_JOBS>` results (if the baseline does not exist, first run bare rollouts over the train task list with `<MODEL>`).
- Rounds 2/3: select tasks whose reward did not pass from the previous round's harnessed eval rollout.
- This round's swarm, bank edits, and eval rollout cover only these failed tasks. Tasks already solved leave the campaign.
- If the failed-task set is empty, end the campaign early; completed rounds still count as valid results.

### Phase 1: swarm (at most 10 coder subagents at a time; analyze and propose changes only)

Launch with AgentSwarm, item = this round's failed-task group. Each subagent does two things for its assigned tasks:

1. **Failure analysis -> `evolution_runs/<domain>/round<N>/notes/<TASK>.md`**
   - Round 1: read the task's bare trajectory in `<BASELINE_JOBS>` (`<job>/<TASK>__*/agent/trajectory.json`, `llm_calls.jsonl`, `verifier/`), and understand how the agent worked and the failure class (insufficient exploration / mechanism misused / miscalculation / deliverable missing or misplaced / missing verification discipline).
   - Rounds 2/3: read the previous round's harnessed trajectory and attribute per the Attribution checklist section.
2. **Propose bank changes**: per Evolution discipline, decide whether the failure should be addressed by generalizing an existing component, adding a new component, or editing prompt/memory material; write the proposal and its rationale in the notes (it must argue "this helps a class of tasks"); do not write to `harness_bank/` directly.

Subagent forbidden zone: do not modify `harness_bank/`, `src/`, or `data/`; do not run rollouts; do not perform git operations.

**WARNING -- component hard rule:** middleware hooks run on the harbor event loop; sandbox probing may only use `await backend.aexecute(...)`; never call the synchronous `backend.execute(...)` inside an async hook -- internally it is `run_coroutine_threadsafe(...).result()`, which deadlocks the entire event loop when called on the loop thread (0% CPU, all trials frozen, even trial-level timeouts cannot fire). Tool function bodies run on worker threads and are not subject to this restriction; message-level checks in middleware are always the safest choice.

### Phase 2: main-agent write-up and acceptance

1. **Consolidated writing**: read all of the round's notes, resolve duplicate or conflicting proposals, and have the main agent uniformly modify the bank (tools / middleware / skills / memory.md / manifest.json).
2. **Reconciliation**: grep every reference name in manifest.json against the registered names in the registry; stale references must be zeroed out (otherwise loading fails outright).
3. **Smoke test**: import smoke (`from harness_bank.<domain>.registry import ...` + render the catalog); `load_student_harness(bank_dir)` resolves successfully (all manifest references hit, `skills/<name>/SKILL.md` files all present); `pytest tests/ -q` shows no regressions.
4. **Eval rollout**: submit a rollout of bare miniswe with the bank loaded, using this round's failed-task IDs as the exact include set (teacher passthrough, same model as the baseline, job name marks the round, such as `<domain>-evol-round<N>`). Before the campaign starts, review the plan with the user once: the full train task list, the failure-selection criterion, the number of rounds, the fixed model and hyperparameters, and a directly executable command template; after user confirmation, later rounds proceed automatically under the confirmed rules without asking again each round. Re-review whenever the model, dataset, selection criterion, or hyperparameters change.

Reading results: the reward distribution in `runs/<job>/result.json`; per-trial details in `<job>/<trial>/{agent,verifier}`. Immediately after the rollout finishes, generate the next round's failed-task list and record the tasks that passed this round and left the campaign.

## Evolution discipline

1. **The lib is the only layer.** Shared mechanism -> bank; specific to one task -> do not write it, accept the residual failure. The one-sentence test: is this failure "shared by a class of tasks" or "specific to this one task" -- shared -> bank; specific -> give up on that task, and never write task-specific content (concrete cell addresses, concrete answers, steps unique to one task, concrete file names) into any shared component, skill, or memory. This is both overfitting prevention and the foundation of the claim that "the harness is generic".
2. **Generalize before adding.** If attribution points to a mechanism the bank already has but that does not quite fit -> generalize and improve the existing component (near-duplicate variants are forbidden); if it truly does not exist -> write a new component (first ask yourself "would this be useful on other tasks"; only if generic does it enter bank + manifest + registry).
3. **Prompt material is also a shared asset.** For behavioral failures (finishing without verification, acting before reading the task, writing the deliverable to the wrong place), prefer editing the corresponding `skills/<name>/SKILL.md` or appending generic clauses to memory.md (English, no source attribution, merged into the matching topic section); do not write task-specific exhortations.
4. **Anti-bloat discipline.** Near-duplicate proposals are deduplicated by the main agent during consolidation; component survival is adjudicated by evaluator performance -- components with no evidence of benefit for two consecutive rounds are moved out of the manifest (files kept for traceability).
5. **Traces are evidence.** Every change must point back to a trajectory attribution in the notes; components that "feel like they should help" are not allowed.

## Attribution checklist (round >=2, go through in order, record in notes)

1. **Did the harness load?** -> In the trial log, confirm the bank_sha256 in `student_harness_bank.txt` matches the current bank and that the skills index appears in the system prompt; if not mounted: manifest/registry reconciliation or a loader problem.
2. **Did the student use the evolved tools?** -> Search `llm_calls.jsonl` for the evolved tools' names; if unused: the tool description is not discoverable or the skills index in the system prompt gives no guidance -- fix descriptions / material rather than adding new tools.
3. **Did the middleware fire?** -> If it fired but did not help: the clauses are not actionable enough -- rewrite abstract principles into verbatim rules; if it did not fire: the hook condition does not match the actual trajectory shape.
4. **Is the student dying on mechanics?** (hangs / malformed tool calls / validation loops / repeatedly retrying a failing tool) -> mechanics problem, go back to Evolution discipline and fix the bank.
5. **Dead-trial classification**: infra (does not count) vs turn wall (non-convergence, attribution 2/3/4) vs verifier failure (real failure -- read the verifier details and distinguish "wrong value / wrong location / missing deliverable / wrong format").

## Rounds and acceptance

- Run 3 rounds in total; end early if the failed-task set empties. Record per round: number of input failed tasks, number of tasks that passed this round and left, number of remaining failed tasks, mean reward (vs the bare baseline on the same task set), component additions/changes, recurring failure patterns, and the composition of abnormal trials (infra deaths vs real failures).
- Low scores in round 1 are expected (the v1 artifact comes from static analysis alone, with no attribution iterations); watch the trend, not the absolute value.
- Final delivery after all rounds: the reward curve, the bank's final-state inventory (manifest + component catalog), a map of failure patterns, open issues (including the list of abandoned task-specific residual failures), and suggested material for rewriting into a teacher-side harness.
