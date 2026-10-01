import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMD = [sys.executable, "-m", "resolver"]


def run_cli(payload, tmp_path: Path, *extra_args):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        CMD + [*extra_args, str(request_path)],
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


def test_relaxed_mode_accepts_explicit_long_option_name(tmp_path):
    payload = {
        "packages": {"a": {"1": {}}},
        "root": {"a": [2, 2]},
        "installed": {},
    }
    result = run_cli(payload, tmp_path, "--relax-root-requirements")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["revoked_root_requirements"] == [
        {"package": "a", "interval": [2, 2]}
    ]
    assert report["installation"] == {}


def test_relaxed_mode_prints_ordinary_selection_without_concession(tmp_path):
    payload = {
        "packages": {"a": {"1": {}, "2": {}}},
        "root": {"a": [1, 2]},
        "installed": {"a": 2},
    }
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '{"a": 2}\n'


def test_relaxed_mode_reports_revoked_roots_installation_and_changes(tmp_path):
    payload = {
        "packages": {
            "a": {"1": {}},
            "b": {"1": {}},
            "c": {"1": {}},
        },
        "root": {"a": [1, 1], "b": [1, 1], "c": [1, 1]},
        "installed": {"a": 1, "c": 9},
        "conflicts": [[["a", 1], ["b", 1]], [["a", 1], ["c", 1]]],
    }
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "revoked_root_requirements": [{"package": "a", "interval": [1, 1]}],
        "installation": {"b": 1, "c": 1},
        "changes": [
            {"package": "a", "action": "removed", "before": 1, "after": None},
            {"package": "b", "action": "added", "before": None, "after": 1},
            {"package": "c", "action": "changed", "before": 9, "after": 1},
        ],
    }


def test_relaxed_mode_can_output_empty_installation(tmp_path):
    payload = {
        "packages": {"a": {"1": {}}},
        "root": {"a": [2, 2]},
        "installed": {"a": 1},
    }
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 0
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["revoked_root_requirements"] == [
        {"package": "a", "interval": [2, 2]}
    ]
    assert report["installation"] == {}
    assert report["changes"] == [
        {"package": "a", "action": "removed", "before": 1, "after": None}
    ]


def test_relaxed_mode_does_not_turn_format_error_into_unresolvable(tmp_path):
    request_path = tmp_path / "bad.json"
    request_path.write_text("{not json", encoding="utf-8")
    result = subprocess.run(
        CMD + ["--relax-roots", str(request_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
