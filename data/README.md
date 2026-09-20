# Benchmark task sets

This directory contains the three Harbor task sets used in the Harness-Zero experiments, exactly as used for the reported runs. Each task directory follows the Harbor layout (`task.toml`, `instruction.md`, `environment/`, `tests/`, `solution/`).

## spreadsheetbench-verified/

400 real-world spreadsheet-manipulation tasks from [SpreadsheetBench](https://github.com/RUCKBReasoning/SpreadsheetBench) (verified subset; see also `RUCKBReasoning/spreadsheetbench-verified` on HuggingFace).

- `splits.json`: the disjoint train/test split used in the paper — **300 train** tasks (harness evolution + training-data collection) and **100 held-out test** tasks. The comment field records how the split was checked to be effectively random.
- All 400 task Dockerfiles do `FROM ssb-lo-base:1`; build that base image first with `docker build -t ssb-lo-base:1 -f data/spreadsheetbench-verified/base-image.Dockerfile .` (python 3.11 + libreoffice-calc + openpyxl/pandas). The input spreadsheets ship inside each task directory.

## appworld-harbor/

732 [AppWorld](https://github.com/StonyBrookNLP/appworld) tasks converted to Harbor by `envs/appworld/convert.py` (upstream: `appworld==0.1.3.post1`).

- `split_train147.txt`: official train + dev merged into the 147-task training split used in the paper.
- `split_test168.txt`: the 168 `test_normal` tasks used for held-out evaluation (grouped into 56 three-task scenarios; SGC metric).
- `split_challenge417.txt`: the 417 `test_challenge` tasks (not used in the paper).
- Images are built by `envs/appworld/build.py` (client / world / verifier, tag `v2`). AppWorld runs **must** use the dedicated agent and environment:
  `--agent envs.appworld.agent:AppWorldMinisweAgent --env envs.appworld.harbor_environment:AppWorldDockerEnvironment` (see `envs/appworld/README.md`).

## uspto/

600 single-step retrosynthesis tasks derived from the USPTO reaction corpus (Schneider USPTO-50k, via `pingzhili/uspto-50k` on HuggingFace; see `SPLIT.md` for provenance).

- 500 train tasks balanced across the 10 reaction classes, 100 disjoint test tasks.
- All tasks pin the prebuilt image `uspto-env:1.0`; its full Dockerfile is embedded in each task's `environment/Dockerfile` (python 3.12 + rdkit).
