"""Local entry points for rollout planning, datum building, and SFT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_zero.rollout import build_rollout_plan, load_task_ids


REPO_ROOT = Path(__file__).resolve().parents[2]
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-zero")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-sft")
    build.add_argument("--trials", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--reward-threshold", type=float, default=1.0)

    rollout = sub.add_parser("plan-rollout")
    rollout.add_argument("--dataset", type=Path, required=True)
    rollout.add_argument("--components", type=Path, required=True)
    rollout.add_argument("--teacher-middleware-factory")
    rollout.add_argument("--tasks-file", type=Path, required=True)
    rollout.add_argument("--attempts", type=int, required=True)
    rollout.add_argument("--concurrency", type=int, required=True)
    rollout.add_argument("--student-model", required=True)
    rollout.add_argument("--student-base-url")
    rollout.add_argument("--student-reasoning-prefill", choices=("none", "think"), default="none")
    rollout.add_argument("--student-azure-api", choices=("deepseek", "responses"), default="deepseek")
    rollout.add_argument("--student-reasoning-effort")
    rollout.add_argument(
        "--student-reasoning-enabled",
        action=argparse.BooleanOptionalAction,
    )
    rollout.add_argument(
        "--teacher-provider",
        choices=("openai", "openrouter", "azure", "azure_openai", "passthrough"),
        required=True,
    )
    rollout.add_argument("--teacher-model", required=True)
    rollout.add_argument(
        "--teacher-reasoning-effort",
        required=True,
    )
    rollout.add_argument("--job-name", required=True)
    rollout.add_argument("--output-dir", type=Path, required=True)
    rollout.add_argument(
        "--max-replacements-per-trial",
        type=int,
        default=5,
    )
    rollout.add_argument("--student-max-turns", type=int, default=40)
    rollout.add_argument("--max-retries", type=int, default=2)
    rollout.add_argument("--student-harness-dir")
    rollout.add_argument("--agent-timeout-multiplier", type=float)
    rollout.add_argument("--teacher-prompt-suffix")
    rollout.add_argument("--teacher-prompt-path")
    rollout.add_argument("--teacher-oracle-answer-dir")

    train = sub.add_parser("train-sft")
    train.add_argument("--data", type=Path, required=True)
    train.add_argument("--base-model", required=True)
    train.add_argument("--renderer", choices=("qwen3_5",), required=True)
    train.add_argument("--rank", type=int, required=True)
    train.add_argument("--peak-learning-rate", type=float, required=True)
    train.add_argument("--final-learning-rate", type=float, required=True)
    train.add_argument("--warmup-ratio", type=float, required=True)
    train.add_argument("--epochs", type=int, required=True)
    train.add_argument("--checkpoint-epochs", type=float, nargs="+", default=None)
    train.add_argument("--batch-size", type=int, required=True)
    train.add_argument("--max-length", type=int, required=True)
    train.add_argument("--seed", type=int, required=True)
    train.add_argument("--run-name", required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument("--resume-from")
    train.add_argument("--reviewed", action="store_true")
    train.add_argument("--plan-only", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "build-sft":
        from harness_zero.sft import build_sft_file, discover_trial_stores

        trial_dirs = discover_trial_stores(args.trials)
        stats = build_sft_file(
            trial_dirs,
            args.output,
            reward_threshold=args.reward_threshold,
        )
        print(json.dumps(stats))
        return
    if args.command == "plan-rollout":
        plan = build_rollout_plan(
            repo_root=REPO_ROOT,
            dataset=args.dataset,
            components_dir=args.components,
            teacher_middleware_factory=args.teacher_middleware_factory,
            tasks=load_task_ids(args.tasks_file),
            attempts=args.attempts,
            concurrency=args.concurrency,
            student_model=args.student_model,
            student_base_url=args.student_base_url,
            student_reasoning_prefill=args.student_reasoning_prefill,
            student_azure_api=args.student_azure_api,
            student_reasoning_effort=args.student_reasoning_effort,
            student_reasoning_enabled=args.student_reasoning_enabled,
            teacher_provider=args.teacher_provider,
            teacher_model=args.teacher_model,
            teacher_reasoning_effort=args.teacher_reasoning_effort,
            job_name=args.job_name,
            output_dir=args.output_dir,
            max_replacements_per_trial=args.max_replacements_per_trial,
            student_max_turns=args.student_max_turns,
            max_retries=args.max_retries,
            student_harness_dir=args.student_harness_dir,
            agent_timeout_multiplier=args.agent_timeout_multiplier,
            teacher_prompt_suffix=args.teacher_prompt_suffix,
            teacher_prompt_path=args.teacher_prompt_path,
            teacher_oracle_answer_dir=args.teacher_oracle_answer_dir,
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    rows = [
        json.loads(line)
        for line in args.data.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    plan = {
        "data": str(args.data),
        "examples": len(rows),
        "tasks": len({row.get("task_id") for row in rows}),
        "base_model": args.base_model,
        "renderer": args.renderer,
        "rank": args.rank,
        "peak_learning_rate": args.peak_learning_rate,
        "final_learning_rate": args.final_learning_rate,
        "warmup_ratio": args.warmup_ratio,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "max_length": args.max_length,
        "seed": args.seed,
        "run_name": args.run_name,
        "output_dir": str(args.output_dir),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan_only:
        return
    if not args.reviewed:
        raise SystemExit(
            "refusing external Tinker submission: show this plan and full command "
            "to the user, then rerun with --reviewed after approval"
        )
    from harness_zero.train import train_positive_sft

    print(
        json.dumps(
            train_positive_sft(
                data_path=args.data,
                base_model=args.base_model,
                renderer_name=args.renderer,
                rank=args.rank,
                peak_learning_rate=args.peak_learning_rate,
                final_learning_rate=args.final_learning_rate,
                warmup_ratio=args.warmup_ratio,
                epochs=args.epochs,
                batch_size=args.batch_size,
                max_length=args.max_length,
                seed=args.seed,
                run_name=args.run_name,
                output_dir=args.output_dir,
                checkpoint_epochs=(
                    tuple(args.checkpoint_epochs)
                    if args.checkpoint_epochs
                    else (0.5, 1.0, 2.0, 3.0)
                ),
                resume_from=args.resume_from,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
