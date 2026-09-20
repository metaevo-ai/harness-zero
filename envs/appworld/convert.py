#!/usr/bin/env python3
"""Generate self-contained Harbor tasks using the isolated AppWorld services."""

import json
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parents[1] / "data"
OUTPUT = DATA / "appworld-harbor"
SPLITS = ("train", "dev", "test_normal")

TASK_CONFIG = '''schema_version = "1.4"

[[artifacts]]
source = "/export/appworld"
destination = "appworld"
service = "world"

[metadata]
category = "multi-app-workflow"
tags = ["appworld", "{split}"]

[agent]
timeout_sec = 3600.0

[environment]
docker_image = "ahd-appworld-client:v2"
workdir = "/workdir"

[environment.healthcheck]
command = "appworld task && appworld exec --json </dev/null"
timeout_sec = 15.0
start_period_sec = 60.0

[verifier]
environment_mode = "separate"
timeout_sec = 900.0

[[verifier.collect]]
service = "world"
command = "python /opt/appworld_env/snapshot.py"
timeout_sec = 360.0

[verifier.environment]
build_timeout_sec = 600.0
'''

COMPOSE = '''services:
  main:
    image: ahd-appworld-client:v2
    command: ["appworld", "serve"]
    cap_drop: [ALL]
    # Harbor uploads files with host ownership; the agent must chmod its CLI wrapper.
    cap_add: [FOWNER]
    security_opt: ["no-new-privileges:true"]
    networks: [appworld]
    depends_on:
      world:
        condition: service_healthy
  world:
    image: ahd-appworld-world:v2
    command: ["python", "/opt/appworld_env/server.py"]
    environment:
      APPWORLD_TASK_ID: "{task_id}"
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    tmpfs:
      - /tmp
      - /opt/appworld/experiments
    volumes:
      - world-export:/export
    networks: [appworld]
    healthcheck:
      test: ["CMD", "curl", "--noproxy", "*", "-fsS", "http://127.0.0.1:8000/health"]
      interval: 2s
      timeout: 2s
      retries: 60
networks:
  appworld:
    internal: true
    # Internal networks otherwise still expose services on the host bridge IP.
    driver_opts:
      com.docker.network.bridge.inhibit_ipv4: "true"
volumes:
  world-export:
'''

WORKING_NOTES = '''

## Working notes

The AppWorld task is already initialized. Its database runs in a separate
service. Use the following CLI from bash (available with or without a harness):

```bash
appworld docs supervisor
appworld docs supervisor show_account_passwords
appworld exec <<'PY'
print(apis.supervisor.show_profile())
print(apis.supervisor.show_account_passwords())
PY
```

`apis` and `requester` are already defined. `import apis` and
`from apis import spotify` also work and use the same public API proxies. Python
variables and app state persist across `appworld exec` calls. Standard Python
imports are supported. Print values you want to inspect. On an exception,
earlier mutations remain applied and earlier printed output is preserved.
Python has a 100-second execution deadline (override with --timeout, max 120).
A timed-out worker is stopped and its Python variables reset; app state remains.
Read affected entities before retrying, since an API in flight may still finish.

`appworld docs <app>` lists endpoints; add the endpoint name for its complete
parameters and response schemas. `appworld task` shows the fixed task id and
instruction. `appworld status` reports whether the supervisor marked the task
complete; it does not report grading results. Use the `execute` tool for all these CLI commands.

Credentials come from `apis.supervisor.show_account_passwords()` (a list keyed
by `account_name`) and `apis.supervisor.show_profile()` (email and phone number).
The simulated date comes from the phone app, not the system clock.

Finish with `apis.supervisor.complete_task(answer=...)` if the instruction
requests an answer, otherwise `apis.supervisor.complete_task()`. Only submit
again if you need to correct your previous submission; a new submission
replaces the previous answer. Completion status does not mean the task passed.

Only public app APIs affect the graded world. Local files are your workspace;
the verifier evaluates the state collected directly from the AppWorld service.
'''


def main():
    runtime_hash = hashlib.sha256()
    for path in sorted((ROOT / "runtime").glob("*.py")):
        runtime_hash.update(path.name.encode())
        runtime_hash.update(path.read_bytes())
    runtime_version = runtime_hash.hexdigest()
    train, test = [], []
    for split in SPLITS:
        ids = (DATA / "datasets" / f"{split}.txt").read_text().split()
        for task_id in ids:
            src = DATA / "tasks" / task_id
            dst = OUTPUT / "tasks" / task_id
            # Only generated task directories are replaced; source data is never moved.
            if dst.exists():
                shutil.rmtree(dst)
            (dst / "environment").mkdir(parents=True)
            (dst / "tests").mkdir()
            (dst/'environment/appworld-role.json').write_text('{"role":"agent"}\n')
            (dst/'tests/appworld-role.json').write_text('{"role":"verifier"}\n')
            (dst / "task.toml").write_text(TASK_CONFIG.format(split=split))
            (dst / "environment/docker-compose.yaml").write_text(COMPOSE.format(task_id=task_id))
            instruction = json.loads((src / "specs.json").read_text())["instruction"]
            (dst / "instruction.md").write_text(instruction.rstrip() + WORKING_NOTES)
            (dst / "tests/test.sh").write_text(
                "#!/bin/bash\nset -eu\n"
                f"python /opt/appworld_env/grade.py {task_id}\n")
            (dst / "tests/test.sh").chmod(0o755)
            (dst / "tests/Dockerfile").write_text(
                f"# Runtime source SHA256: {runtime_version}\n"
                "FROM ahd-appworld-verifier:v2\nCOPY . /tests/\n"
                "RUN chmod +x /tests/test.sh\n")
            shutil.copytree(src / "ground_truth", dst / "tests/ground_truth",
                            ignore=shutil.ignore_patterns("__pycache__"))
            compiled = src / "ground_truth/compiled_solution.py"
            if compiled.is_file():
                (dst / "solution").mkdir()
                (dst / "solution/solve.py").write_text(
                    compiled.read_text() + "\nsolution(apis, requester)\n")
                (dst / "solution/solve.sh").write_text(
                    "#!/bin/bash\nset -eu\nappworld exec --file /solution/solve.py\n")
                (dst / "solution/solve.sh").chmod(0o755)
            (test if split == "test_normal" else train).append(task_id)
    for filename, ids in (("split_train147.txt", train), ("split_test168.txt", test),
                          ("task_list.txt", train + test)):
        (OUTPUT / filename).write_text("\n".join(ids) + "\n")
    assert len(train) == 147 and len(test) == 168
    print(f"Generated {len(train) + len(test)} tasks in {OUTPUT / 'tasks'}")


if __name__ == "__main__":
    main()
