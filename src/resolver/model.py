"""Request model and strict JSON validation.

The accepted request shape is::

    {
      "packages": {
        "name": {
          "1": {"dependencies": {"other": [">=1", "<=3"]}},
          "2": {}
        }
      },
      "root": {"name": [">=1", "<=2"]},
      "installed": {"name": 2},
      "conflicts": [[["name_a", 1], ["name_b", 2]]]
    }

Closed intervals are encoded by their inclusive lower and upper bound.
Each conflict entry pairs the exact versions of two different packages that
must never be installed together; the constraint is bidirectional and does
not create any dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .errors import InputError

# dependencies[package][version] = {dependency_name: (lower, upper)}
Dependencies = dict[str, dict[int, dict[str, tuple[int, int]]]]

# conflicts[i] = ((package_a, version_a), (package_b, version_b))
Conflicts = tuple[tuple[tuple[str, int], tuple[str, int]], ...]


@dataclass(frozen=True)
class Catalog:
    versions: dict[str, tuple[int, ...]]
    dependencies: Dependencies
    root: dict[str, tuple[int, int]]
    installed: dict[str, int]
    conflicts: Conflicts = field(default_factory=tuple)

    @property
    def ordered_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.versions))


def parse_request(raw: str | bytes) -> Catalog:
    """Parse and validate a resolver JSON document."""
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise InputError(f"invalid JSON: {exc.msg}") from exc
    elif isinstance(raw, dict):
        # Programmatic callers may construct the request directly.  Duplicate
        # keys are impossible in a Python mapping.
        document = raw
    else:
        raise InputError("request must be a JSON object")
    return _validate_document(document)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _validate_document(document: Any) -> Catalog:
    if not isinstance(document, dict):
        raise InputError("request must be a JSON object")
    _expect_exact_keys(
        document,
        {"packages", "root", "installed"},
        "request",
        optional_keys={"conflicts"},
    )

    packages = _require_object(document["packages"], "packages")
    if not packages:
        raise InputError("packages must contain at least one package")
    if len(packages) > 10:
        raise InputError("packages may contain at most 10 unique packages")

    dependencies: Dependencies = {}
    versions_by_name: dict[str, tuple[int, ...]] = {}
    for name, version_map in packages.items():
        _validate_package_name(name)
        if name in versions_by_name:
            # JSON duplicate keys are already rejected; this remains defensive.
            raise InputError(f"duplicate package {name!r}")
        versions, deps = _validate_package(name, version_map)
        versions_by_name[name] = versions
        dependencies[name] = deps

    root_raw = _require_object(document["root"], "root")
    root: dict[str, tuple[int, int]] = {}
    for name, interval in root_raw.items():
        _validate_package_name(name)
        if name not in versions_by_name:
            raise InputError(f"root requirement {name!r} is outside the catalog")
        if name in root:
            raise InputError(f"duplicate root requirement {name!r}")
        root[name] = _validate_interval(interval, f"root requirement {name!r}")

    installed_raw = _require_object(document["installed"], "installed")
    installed: dict[str, int] = {}
    for name, version in installed_raw.items():
        _validate_package_name(name)
        if name not in versions_by_name:
            raise InputError(f"installed package {name!r} is outside the catalog")
        if name in installed:
            raise InputError(f"duplicate installed package {name!r}")
        installed[name] = _validate_positive_version(
            version, f"installed version for {name!r}"
        )

    # Package names and interval values are now known valid.  Do this after the
    # rest of the structural validation so e.g. a malformed interval cannot be
    # hidden by an out-of-catalog dependency error.
    for package_name, by_version in dependencies.items():
        for version, package_dependencies in by_version.items():
            for dependency_name in package_dependencies:
                if dependency_name not in versions_by_name:
                    location = f"{package_name} version {version}"
                    raise InputError(
                        f"dependency {dependency_name!r} from {location} is outside "
                        "the catalog"
                    )

    conflicts = _validate_conflicts(document.get("conflicts", []), versions_by_name)

    return Catalog(
        versions=versions_by_name,
        dependencies=dependencies,
        root=root,
        installed=installed,
        conflicts=conflicts,
    )


def _validate_package(
    name: str, value: Any
) -> tuple[tuple[int, ...], dict[int, dict[str, tuple[int, int]]]]:
    source = _require_object(value, f"package {name!r}")
    if not source:
        raise InputError(f"package {name!r} must contain at least one version")
    if len(source) > 4:
        raise InputError(f"package {name!r} may contain at most four versions")

    numeric_versions: set[int] = set()
    dependencies: dict[int, dict[str, tuple[int, int]]] = {}
    for version_text, body in source.items():
        if not version_text.isdigit():
            raise InputError(f"version key {version_text!r} must be a positive integer")
        version = int(version_text)
        if version < 1 or str(version) != version_text:
            raise InputError(f"version key {version_text!r} must be a positive integer")
        if version in numeric_versions:
            raise InputError(f"duplicate version {version} for package {name!r}")

        body_object = _require_object(body, f"package {name!r} version {version}")
        _expect_exact_keys(
            body_object, {"dependencies"}, f"package {name!r} version {version}", optional=True
        )
        deps_value = body_object.get("dependencies", {})
        deps_object = _require_object(
            deps_value, f"dependencies of {name!r} version {version}"
        )

        parsed_deps: dict[str, tuple[int, int]] = {}
        for dep_name, interval in deps_object.items():
            _validate_package_name(dep_name)
            if dep_name in parsed_deps:
                raise InputError(
                    f"duplicate dependency {dep_name!r} from {name!r} version {version}"
                )
            parsed_deps[dep_name] = _validate_interval(
                interval, f"dependency {dep_name!r} from {name!r} version {version}"
            )

        numeric_versions.add(version)
        dependencies[version] = parsed_deps

    return tuple(sorted(numeric_versions)), dependencies


def _validate_conflicts(
    value: Any, versions_by_name: dict[str, tuple[int, ...]]
) -> Conflicts:
    if not isinstance(value, list):
        raise InputError("conflicts must be a JSON array")

    seen: set[frozenset[tuple[str, int]]] = set()
    conflicts: list[tuple[tuple[str, int], tuple[str, int]]] = []
    for index, entry in enumerate(value):
        location = f"conflicts entry {index}"
        if not isinstance(entry, list) or len(entry) != 2:
            raise InputError(
                f"{location} must be a two-element array of [package, version] pairs"
            )

        pair: list[tuple[str, int]] = []
        for endpoint in entry:
            if not isinstance(endpoint, list) or len(endpoint) != 2:
                raise InputError(
                    f"{location} endpoints must be [package, version] pairs"
                )
            name, version = endpoint
            _validate_package_name(name)
            pair.append(
                (
                    name,
                    _validate_positive_version(
                        version, f"conflict version for {name!r}"
                    ),
                )
            )

        (name_a, version_a), (name_b, version_b) = pair
        if name_a == name_b:
            raise InputError(f"{location} must name two different packages")
        for name, version in pair:
            if name not in versions_by_name:
                raise InputError(f"conflict package {name!r} is outside the catalog")
            if version not in versions_by_name[name]:
                raise InputError(
                    f"conflict version {version} for {name!r} is outside the catalog"
                )

        # The exclusion is symmetric, so an entry and its reverse (or an exact
        # repeat) describe the same pair and are rejected as duplicates.
        key = frozenset(pair)
        if key in seen:
            raise InputError(
                f"duplicate conflict between {name_a!r} version {version_a} and "
                f"{name_b!r} version {version_b}"
            )
        seen.add(key)
        conflicts.append((pair[0], pair[1]))

    return tuple(conflicts)


def _validate_package_name(value: Any) -> None:
    if not isinstance(value, str):
        raise InputError(f"package name {value!r} must be a string")
    if not value:
        raise InputError("package names must not be empty")
    if not all(0x20 < ord(char) < 0x7F for char in value):
        raise InputError(f"package name {value!r} must contain only visible ASCII")


def _validate_interval(value: Any, location: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise InputError(f"{location} must be a two-element [lower, upper] interval")
    lower = _validate_bound(value[0], f"lower bound of {location}")
    upper = _validate_bound(value[1], f"upper bound of {location}")
    if lower > upper:
        raise InputError(f"{location} has lower bound greater than upper bound")
    return lower, upper


def _validate_bound(value: Any, location: str) -> int:
    # bool is a subclass of int but is not meaningful as a version number.
    if not isinstance(value, int) or isinstance(value, bool):
        raise InputError(f"{location} must be an integer")
    if value < 1:
        raise InputError(f"{location} must be positive")
    return value


def _validate_positive_version(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InputError(f"{location} must be an integer")
    if value < 1:
        raise InputError(f"{location} must be positive")
    return value


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{location} must be a JSON object")
    return value


def _expect_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    location: str,
    optional: bool = False,
    optional_keys: set[str] | frozenset[str] = frozenset(),
) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected - optional_keys
    if missing and not optional:
        raise InputError(f"{location} is missing key(s): {', '.join(sorted(missing))}")
    if unknown:
        raise InputError(f"{location} contains unknown key(s): {', '.join(sorted(unknown))}")
