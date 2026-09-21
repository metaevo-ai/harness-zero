# AppWorld environment for Harness-Zero

AppWorld rollouts and evaluations **must** use the dedicated agent and environment in this directory — the generic `harness_zero.harness:HarnessZeroMinisweAgent` + default docker env produce invalid scores for AppWorld:

```bash
--agent envs.appworld.agent:AppWorldMinisweAgent \
--env envs.appworld.harbor_environment:AppWorldDockerEnvironment
```

## Why a dedicated agent

`AppWorldMinisweAgent` inserts an initialize step before the agent graph: it reads the real supervisor identity and app descriptions from the sandbox and builds 23 teaching messages (a bash adaptation of the official AppWorld ReAct demonstration, with the A/B/C/D app descriptions, a pre-filled user identity, and the current-task marker) — 24 messages with the system prompt on the first turn. The generic agent starts with only system + instruction (2 messages), which is off-distribution relative to the training data. `AppWorldDockerEnvironment` additionally stops the student container for host-side evidence capture before scoring.

## Layout

- `agent.py` — `AppWorldMinisweAgent` (subclass of `HarnessZeroMinisweAgent`).
- `harbor_environment.py` — `AppWorldDockerEnvironment`.
- `common_context.py` — first-turn context construction (23 teaching messages).
- `runtime/` — in-image AppWorld service stack (`server.py`, `client.py`, `grade.py`, `snapshot.py`); the verifier never sees ground truth inside the student container.
- `Dockerfile`, `build.py` — build the `ahd-appworld-{client,world,verifier}:v2` images from the official AppWorld data (`appworld==0.1.3.post1`). `build.py --prepare-only` stages the build context without ground-truth files (asserted).
- `convert.py` — convert official AppWorld tasks into the self-contained Harbor tasks in `data/appworld-harbor/` (train/dev/test_normal → 732 tasks).
- `prompts/` — the upstream ReAct demonstration (`upstream/`, see its LICENSE) and the adaptation notes/provenance.
- `validate.py` — run oracle and adversarial Harbor fixtures without sampling a model, to sanity-check the images (writes a `validation*.json` report).
- `collect_sft.py`, `build_sft.py` — the AppWorld SFT pipeline: collect reviewed rollouts, then audit/mask sessions into training datums.
- `teacher_compare.py` — freeze and run the bare / student-bank / teacher-bank comparison.
- `tests/` — local tests for the pieces above (no paid models, no Docker).

## Typical flow

```bash
# 1. Build images (needs the official AppWorld data)
python envs/appworld/build.py

# 2. Sanity-check the images without a model
python envs/appworld/validate.py --image-tag v2

# 3. Run a reviewed rollout over the 147-task training split
harness-zero run-rollout \
  --dataset data/appworld-harbor/tasks \
  --components harness_bank/appworld \
  --tasks-file data/appworld-harbor/split_train147.txt \
  ...  # student/teacher model flags as in the top-level README
```
