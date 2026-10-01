import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMD = [sys.executable, "-m", "resolver"]


def run_cli(payload, tmp_path: Path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        CMD + [str(request_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )


def test_cli_outputs_json_selection(tmp_path):
    payload = {
        "packages": {"a": {"1": {}, "2": {}}},
        "root": {"a": [1, 2]},
        "installed": {},
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"a": 2}


def test_cli_outputs_unresolvable_sentinel(tmp_path):
    payload = {
        "packages": {"a": {"1": {"dependencies": {"a": [2, 2]}}, "2": {}}},
        "root": {"a": [1, 1]},
        "installed": {},
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "UNRESOLVABLE"
    assert result.stderr == ""


def test_cli_rejects_bad_json_with_nonzero_exit(tmp_path):
    request_path = tmp_path / "bad.json"
    request_path.write_text("{not json", encoding="utf-8")
    result = subprocess.run(
        CMD + [str(request_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")


def test_cli_resolves_request_with_conflicts(tmp_path):
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {},
        "conflicts": [[["a", 2], ["b", 2]]],
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"a": 2, "b": 1}


def test_cli_conflicts_can_force_unresolvable(tmp_path):
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "UNRESOLVABLE"
    assert result.stderr == ""


def test_cli_rejects_invalid_conflicts_with_empty_stdout(tmp_path):
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 9]]],
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")


def test_cli_request_without_conflicts_is_byte_compatible(tmp_path):
    payload = {
        "packages": {"a": {"1": {}, "2": {}}},
        "root": {"a": [1, 2]},
        "installed": {},
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '{"a": 2}\n'
