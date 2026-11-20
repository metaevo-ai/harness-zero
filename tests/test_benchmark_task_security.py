"""Ensure public benchmark tasks cannot bypass their verifiers."""

from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("root", "artifact", "network_key", "network_value"),
    [
        (ROOT / "data" / "uspto", "/app/answer.txt", "network_mode", "no-network"),
        (ROOT / "data" / "spreadsheetbench-verified", "/app/output", "allow_internet", False),
    ],
)
def test_public_benchmark_tasks_isolate_verifier(
    root: Path, artifact: str, network_key: str, network_value: object
):
    configs = sorted(root.glob("*/task.toml"))
    assert configs
    for path in configs:
        config = tomllib.loads(path.read_text())
        assert config["verifier"]["environment_mode"] == "separate", path
        assert config["environment"][network_key] == network_value, path
        assert {item["source"] for item in config["artifacts"]} == {artifact}, path
