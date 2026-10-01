"""CLI behaviour for the root-requirement relaxation mode."""

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
        CMD + list(extra_args) + [str(request_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )


CONFLICTING_ROOTS = {
    "packages": {"a": {"1": {}}, "b": {"1": {}}},
    "root": {"a": [1, 1], "b": [1, 1]},
    "installed": {},
    "conflicts": [[["a", 1], ["b", 1]]],
}


def test_relax_roots_reports_revocation_and_selection(tmp_path):
    result = run_cli(CONFLICTING_ROOTS, tmp_path, "--relax-roots")
    assert result.returncode == 0
    assert result.stderr == ""
    document = json.loads(result.stdout)
    assert document["revoked"] == ["a"]
    assert document["selection"] == {"b": 1}
    assert document["changes"] == [
        {"name": "b", "from": None, "to": 1, "kind": "added"}
    ]


def test_relax_roots_flag_works_before_and_after_path(tmp_path):
    after = run_cli(CONFLICTING_ROOTS, tmp_path, "--relax-roots")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(CONFLICTING_ROOTS), encoding="utf-8")
    before = subprocess.run(
        CMD + [str(request_path), "--relax-roots"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert after.returncode == before.returncode == 0
    assert after.stdout == before.stdout


def test_relax_roots_on_solvable_request_revokes_nothing(tmp_path):
    # a is installed and kept; absent b is added at its top version.
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {"a": 1},
    }
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["revoked"] == []
    assert document["selection"] == {"a": 1, "b": 2}
    assert document["changes"] == [
        {"name": "b", "from": None, "to": 2, "kind": "added"}
    ]


def test_relax_roots_uses_empty_closure_when_all_roots_revoked(tmp_path):
    # Root interval cannot match any catalog version, so it must be revoked;
    # the empty closure then removes the installed package.
    payload = {
        "packages": {"a": {"1": {}}},
        "root": {"a": [5, 5]},
        "installed": {"a": 1},
    }
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["revoked"] == ["a"]
    assert document["selection"] == {}
    assert document["changes"] == [
        {"name": "a", "from": 1, "to": None, "kind": "removed"}
    ]


def test_relax_roots_format_error_still_exits_two_without_plan(tmp_path):
    payload = dict(CONFLICTING_ROOTS, bogus=1)
    result = run_cli(payload, tmp_path, "--relax-roots")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")


def test_relax_roots_malformed_json_is_not_unresolvable(tmp_path):
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
    assert "UNRESOLVABLE" not in result.stderr
    assert result.stderr.startswith("error: ")


def test_without_flag_unresolvable_output_is_byte_compatible(tmp_path):
    result = run_cli(CONFLICTING_ROOTS, tmp_path)
    assert result.returncode == 0
    assert result.stdout == "UNRESOLVABLE\n"
    assert result.stderr == ""


def test_without_flag_success_output_is_byte_compatible(tmp_path):
    payload = {
        "packages": {"a": {"1": {}, "2": {}}},
        "root": {"a": [1, 2]},
        "installed": {},
    }
    result = run_cli(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout == '{"a": 2}\n'
    assert result.stderr == ""
