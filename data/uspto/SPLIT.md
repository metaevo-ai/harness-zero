# USPTO retrosynthesis task set

600 single-step retrosynthesis tasks: 500 train (`uspto-train-000..499`) and 100 disjoint test (`uspto-test-000..099`).

The reaction data derives from Schneider USPTO-50k (via `pingzhili/uspto-50k` on HuggingFace, `keep=true` subset). The 500-train split is balanced across the ten reaction classes: the 450 expansion tasks (`uspto-train-050..499`) were sampled uniformly at 45 per class (seed 42) and deduplicated by canonical product SMILES, both against the pre-existing tasks and within the expansion set. Products in each task were also canonicalized.

All `task.toml` files pin the prebuilt image `uspto-env:1.0`; its complete Dockerfile is embedded in each task's `environment/Dockerfile` (python 3.12 + rdkit + the subagent venv), so the image can be rebuilt from any task directory.
