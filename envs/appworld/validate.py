#!/usr/bin/env python3
"""Run oracle and adversarial Harbor fixtures without sampling a model."""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DATA = REPO / "data"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harbor", default="harbor", help="Harbor executable")
    parser.add_argument('--image-tag', default='v2')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', args.image_tag):
        parser.error('Invalid Docker image tag')
    image_ids = {}
    for target, files in {"client": ["client.py"], "world": ["server.py", "snapshot.py"],
                          "verifier": ["server.py", "snapshot.py", "grade.py"]}.items():
        image = f"ahd-appworld-{target}:{args.image_tag}"
        expected = {name: hashlib.sha256((ROOT / "runtime" / name).read_bytes()).hexdigest()
                    for name in files}
        probe = (
            "import hashlib,json; from pathlib import Path; "
            f"expected={expected!r}; "
            "actual={name:hashlib.sha256((Path('/opt/appworld_env')/name).read_bytes()).hexdigest() "
            "for name in expected}; assert actual==expected,(actual,expected); "
            "assert not list(Path('/opt/appworld').rglob('ground_truth')); "
            "assert not list(Path('/opt/appworld').rglob('answer.json')); "
            "assert not Path('/tests').exists()"
        )
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                        image, "-c", probe], check=True)
        image_ids[target] = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
    tasks = DATA / 'appworld-validation' / args.image_tag / 'tasks'
    tasks.mkdir(parents=True, exist_ok=True)
    cases = {"oracle_action": ("07b42fd_1", 1.0),
             "oracle_answer": ("287e338_3", 1.0),
             "oracle_date": ("2a163ab_3", 1.0),
             "attack": ("07b42fd_1", 0.0)}
    for name, (task_id, _) in cases.items():
        target = tasks / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(DATA / "appworld-harbor/tasks" / task_id, target)
        for relative in ['task.toml','environment/docker-compose.yaml','tests/Dockerfile']:
            path = target/relative
            text = path.read_text()
            for role in ['client','world','verifier']:
                text = text.replace(f'ahd-appworld-{role}:v2',f'ahd-appworld-{role}:{args.image_tag}')
            path.write_text(text)
        if name == "oracle_action":
            shutil.copy2(ROOT / "tests/timeout_probe.py", target / "solution/timeout_probe.py")
            shutil.copy2(ROOT / 'tests/http_input_probe.py', target/'solution/http_input_probe.py')
            shutil.copy2(ROOT / 'tests/network_probe.py', target/'solution/network_probe.py')
            addresses = json.loads(subprocess.check_output(['ip','-j','-4','address'],text=True))
            host_ips = sorted({a['local'] for interface in addresses for a in interface['addr_info']
                               if a['family']=='inet' and not a['local'].startswith('127.')})
            (target/'solution/host_ipv4.json').write_text(json.dumps(host_ips))
            script = target / "solution/solve.sh"
            script.write_text(script.read_text().replace(
                "appworld exec --file", "python /solution/network_probe.py\npython /solution/timeout_probe.py\npython /solution/http_input_probe.py\nappworld exec --file"))
        if name == "attack":
            shutil.rmtree(target / "solution")
            (target / "solution").mkdir()
            shutil.copy2(ROOT / "tests/attack.py", target / "solution/attack.py")
            script = target / "solution/solve.sh"
            script.write_text("#!/bin/bash\nset -eu\npython /solution/attack.py\n")
            script.chmod(0o755)
    job = datetime.datetime.now().strftime("%Y%m%d-appworld-validation-%H%M%S")
    # These images are local to this daemon; a configured remote builder cannot see them.
    env = {**os.environ, "BUILDX_BUILDER": "default", "COMPOSE_BAKE": "false",
           'PYTHONPATH':str(REPO)+os.pathsep+str(REPO/'src')}
    subprocess.run([args.harbor, "run", "--path", str(tasks), "--agent", "oracle",
                    "--env", "envs.appworld.harbor_environment:AppWorldDockerEnvironment",
                    "--n-attempts", "1", "--n-concurrent", "3",
                    "--job-name", job, "--jobs-dir", str(REPO / "runs"), "--yes"], check=True, env=env)
    results = {}
    job_dir = REPO / "runs" / job
    assert (job_dir / "result.json").is_file()
    for name, (_, expected) in cases.items():
        trials = list(job_dir.glob(name + "__*/result.json"))
        assert len(trials) == 1, (name, trials)
        trial = json.loads(trials[0].read_text())
        assert trial["exception_info"] is None, trial["exception_info"]
        reward = trial["verifier_result"]["rewards"]["reward"]
        assert reward == expected, (name, reward, expected)
        exit_code = trials[0].parent / "agent/exit-code.txt"
        # Harbor only writes this file for a nonzero oracle exit.
        assert not exit_code.exists() or exit_code.read_text().strip() == "0", exit_code
        oracle_log = (trials[0].parent / "agent/oracle.txt").read_text()
        if name == "attack":
            assert "PASS: private routes denied" in oracle_log, oracle_log
        if name == "oracle_action":
            assert "PASS: deadline stopped loop" in oracle_log, oracle_log
            assert 'PASS: absolute HTTP input deadline' in oracle_log, oracle_log
            assert 'PASS: host services unreachable' in oracle_log, oracle_log
        manifest = json.loads((trials[0].parent / "artifacts/manifest.json").read_text())
        world = next(entry for entry in manifest if entry.get("service") == "world")
        assert world["status"] not in ("failed", "missing", "empty"), world
        results[name] = {"reward": reward, "trial": str(trials[0].parent)}
    assert len(list((DATA / "appworld-harbor/tasks").iterdir())) == 732
    report = {"job": job, 'image_tag':args.image_tag, "image_ids": image_ids, "results": results,
              'environment_import_path':'envs.appworld.harbor_environment:AppWorldDockerEnvironment'}
    name = 'validation.json' if args.image_tag=='v2' else f'validation-{args.image_tag}.json'
    (ROOT / name).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
