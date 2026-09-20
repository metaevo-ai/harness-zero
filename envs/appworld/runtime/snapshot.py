"""Harbor collect hook, executed in the world service after the student is stopped."""

import json
import os
import shutil
import urllib.request
from pathlib import Path

task_id = os.environ["APPWORLD_TASK_ID"]
# The server is single-threaded. This waits for any preceding API mutation and save.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:8000/status", timeout=300) as response:
    status = json.load(response)
source = Path("/opt/appworld/experiments/outputs/rollout/tasks") / task_id / "dbs"
target = Path("/export/appworld")
target.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, target / "dbs", dirs_exist_ok=True)
(target / "metadata.json").write_text(json.dumps({"task_id": task_id, **status}))
