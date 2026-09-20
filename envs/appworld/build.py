#!/usr/bin/env python3
"""Prepare an allowlisted context and build all AppWorld images from this directory."""

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parents[1] / "data"


def prepare(context: Path) -> None:
    context.mkdir(parents=True, exist_ok=True)
    public = context / "public_data"
    if public.exists():
        shutil.rmtree(public)
    public.mkdir()
    for name in ("api_docs", "base_dbs"):
        shutil.copytree(DATA / name, public / name)
    shutil.copy2(DATA / "version.txt", public / "version.txt")
    for split in ("train", "dev", "test_normal"):
        for task_id in (DATA / "datasets" / f"{split}.txt").read_text().split():
            src = DATA / "tasks" / task_id
            dst = public / "tasks" / task_id
            dst.mkdir(parents=True)
            shutil.copy2(src / "specs.json", dst / "specs.json")
            shutil.copytree(src / "dbs", dst / "dbs")
    assert not list(public.rglob("ground_truth"))
    assert not list(public.rglob("answer.json"))
    assert not list(public.rglob("*.py"))
    shutil.copy2(ROOT / "Dockerfile", context / "Dockerfile")
    shutil.copytree(ROOT / "runtime", context / "runtime", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument('--image-tag', default='v2')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', args.image_tag):
        parser.error('Invalid Docker image tag')
    context = DATA / "appworld-build"
    prepare(context)
    print(f"Prepared clean context: {context}", flush=True)
    if not args.prepare_only:
        for target in ("client", "world", "verifier"):
            subprocess.run(["docker", "build", "--builder", "default", "--target", target, "-t",
                            f"ahd-appworld-{target}:{args.image_tag}", str(context)], check=True)


if __name__ == "__main__":
    main()
