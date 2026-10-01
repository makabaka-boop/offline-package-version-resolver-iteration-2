"""Tiny pytest-compatible runner for environments without pytest installed.

It supports only the subset used by this repository: ``pytest.mark.parametrize``,
``pytest.raises``, plain test functions, and a ``tmp_path`` fixture.  The
canonical test command remains ``pytest`` (see Dockerfile.dev).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import traceback
import types


class Raises:
    def __init__(self, expected):
        self.expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"expected {self.expected.__name__}")
        return issubclass(exc_type, self.expected)


def parametrize(_argnames, argvalues):
    def decorator(function):
        function._parametrize = list(argvalues)
        return function

    return decorator


def load_module(path: pathlib.Path):
    name = f"_runner_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "src"))

    pytest = types.SimpleNamespace(
        mark=types.SimpleNamespace(parametrize=parametrize),
        raises=Raises,
    )
    sys.modules["pytest"] = pytest

    # Import conftest explicitly for its sys.path side effect.
    conftest = root / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("conftest", conftest)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    failures = 0
    count = 0
    for test_file in sorted((root / "tests").glob("test_*.py")):
        test_module = load_module(test_file)
        for attr_name in sorted(dir(test_module)):
            if not attr_name.startswith("test_"):
                continue
            function = getattr(test_module, attr_name)
            if not callable(function):
                continue
            cases = getattr(function, "_parametrize", None)
            if cases is None:
                cases = [()]
            for case in cases:
                if not isinstance(case, tuple):
                    case = (case,)
                count += 1
                needs_tmp = "tmp_path" in function.__code__.co_varnames
                try:
                    if needs_tmp:
                        with tempfile.TemporaryDirectory() as directory:
                            function(*case, tmp_path=pathlib.Path(directory))
                    else:
                        function(*case)
                except Exception:  # noqa: BLE001 - report every test failure
                    failures += 1
                    traceback.print_exc()
                    print(f"FAILED: {test_file.name}::{attr_name}{case}", file=sys.stderr)

    print(f"{count - failures} passed, {failures} failed, {count} total")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
