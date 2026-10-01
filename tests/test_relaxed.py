"""Tests for the explicitly enabled root-requirement relaxation mode.

The "oracle" independently enumerates every revocation set in the canonical
order (fewest roots first, then lexicographic name list) and, for the first
feasible set, enumerates every package/version combination to find the closure
optimal under (fewest changes, fewest packages, largest version vector).  This
cross-checks the revocation set, the closure and the three objectives
separately, alongside isolation and stale/cycle/conflict behaviour.
"""

import itertools
import json
import random

from resolver import parse_request, solve, solve_relaxed


# --------------------------------------------------------------------------- #
# Independent reference implementation
# --------------------------------------------------------------------------- #


def is_complete(catalog, candidate, roots):
    """Exact closure check for an arbitrary retained-root mapping."""
    reached: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        actual = candidate.get(name)
        if actual is None or actual not in catalog.versions[name]:
            return False
        if name in roots:
            lower, upper = roots[name]
            if not lower <= actual <= upper:
                return False
        reached.add(name)
        for dependency, (dep_lower, dep_upper) in catalog.dependencies[name][
            actual
        ].items():
            dep_actual = candidate.get(dependency)
            if dep_actual is None or not dep_lower <= dep_actual <= dep_upper:
                return False
            stack.append(dependency)
    if reached != set(candidate):
        return False
    for (name_a, version_a), (name_b, version_b) in catalog.conflicts:
        if candidate.get(name_a) == version_a and candidate.get(name_b) == version_b:
            return False
    return True


def complete_selections(catalog, roots):
    choices = [(name, (0,) + catalog.versions[name]) for name in catalog.ordered_names]
    for values in itertools.product(*(options for _, options in choices)):
        candidate = {
            name: version
            for (name, _), version in zip(choices, values)
            if version != 0
        }
        if is_complete(catalog, candidate, roots):
            yield candidate


def objective_key(catalog, candidate):
    changed = 0
    for name, version in candidate.items():
        if catalog.installed.get(name) != version:
            changed += 1
    for name in catalog.installed:
        if name not in candidate:
            changed += 1
    vector = tuple(candidate.get(name, 0) for name in catalog.ordered_names)
    return changed, len(candidate), tuple(-x for x in vector)


def retained_roots(catalog, revoked):
    revoked_set = set(revoked)
    return {
        name: catalog.root[name]
        for name in sorted(catalog.root)
        if name not in revoked_set
    }


def relaxed_oracle(catalog):
    """Return (revoked_tuple, best_selection) following the canonical order."""
    root_names = sorted(catalog.root)
    for size in range(len(root_names) + 1):
        for revoked in itertools.combinations(root_names, size):
            roots = retained_roots(catalog, revoked)
            best = None
            best_key = None
            for candidate in complete_selections(catalog, roots):
                key = objective_key(catalog, candidate)
                if best_key is None or key < best_key:
                    best_key = key
                    best = candidate
            if best is not None:
                return tuple(revoked), best
    return None


def expected_changes(catalog, selection):
    changes = []
    for name in sorted(set(selection) | set(catalog.installed)):
        old = catalog.installed.get(name)
        new = selection.get(name)
        if old == new:
            continue
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        else:
            kind = "changed"
        changes.append((name, old, new, kind))
    return changes


def has_any_solution(catalog, roots):
    for _ in complete_selections(catalog, roots):
        return True
    return False


def assert_matches_oracle(payload):
    """Run the real solver and assert every documented property."""
    catalog = parse_request(payload)
    oracle = relaxed_oracle(catalog)
    assert oracle is not None  # revoking every root always yields the closure
    expected_revoked, expected_selection = oracle

    plan = solve_relaxed(catalog)
    assert plan is not None
    assert tuple(plan.revoked) == expected_revoked
    assert dict(plan.selection) == dict(expected_selection)

    actual_changes = [(c.name, c.old, c.new, c.kind) for c in plan.changes]
    assert actual_changes == expected_changes(catalog, plan.selection)

    # Re-adding any single revoked requirement to the retained set must again
    # be infeasible: the concession is provably necessary.
    roots = retained_roots(catalog, plan.revoked)
    for name in plan.revoked:
        re_added = dict(roots)
        re_added[name] = catalog.root[name]
        assert not has_any_solution(catalog, re_added)

    return plan


# --------------------------------------------------------------------------- #
# Random small-directory enumeration
# --------------------------------------------------------------------------- #


def make_random_payload(seed, package_count, max_versions, conflict_count=0):
    rng = random.Random(seed)
    names = [f"p{i}" for i in range(package_count)]
    packages = {}
    catalog_versions = {}
    for name in names:
        versions = list(range(1, rng.randint(1, max_versions) + 1))
        catalog_versions[name] = versions
        package = {}
        for version in versions:
            deps = {}
            for other in names:
                if other != name and rng.random() < 0.28:
                    dep_version = rng.randint(1, max_versions)
                    width = rng.randint(0, 2)
                    deps[other] = [
                        max(1, dep_version - width),
                        dep_version + width,
                    ]
                elif other == name and rng.random() < 0.12:
                    deps[name] = [1, rng.randint(1, max_versions)]
            package[str(version)] = {"dependencies": deps}
        packages[name] = package

    root_count = rng.randint(1, package_count)
    root_names = rng.sample(names, root_count)
    root = {}
    for name in root_names:
        wanted = rng.randint(1, max_versions)
        width = rng.randint(0, 1)
        root[name] = [max(1, wanted - width), wanted + width]

    installed = {}
    for name in names:
        if rng.random() < 0.55:
            installed[name] = rng.randint(1, max_versions + 1)  # stale possible

    conflicts = []
    if conflict_count and len(names) >= 2:
        seen = set()
        attempts = 0
        while len(conflicts) < conflict_count and attempts < 200:
            attempts += 1
            name_a, name_b = rng.sample(names, 2)
            version_a = rng.choice(catalog_versions[name_a])
            version_b = rng.choice(catalog_versions[name_b])
            key = frozenset({(name_a, version_a), (name_b, version_b)})
            if key in seen:
                continue
            seen.add(key)
            conflicts.append([[name_a, version_a], [name_b, version_b]])

    payload = {"packages": packages, "root": root, "installed": installed}
    if conflicts:
        payload["conflicts"] = conflicts
    return payload


def force_one_impossible_root(payload):
    """Rewrite one root interval to a version absent from the catalog.

    That root alone is then infeasible, deterministically forcing a non-empty
    revocation set while its package may still be reached via dependencies.
    """
    payload = json.loads(json.dumps(payload))
    name = sorted(payload["root"])[0]
    max_version = max(int(v) for v in payload["packages"][name])
    payload["root"][name] = [max_version + 10, max_version + 10]
    return payload


def test_small_random_catalogs_match_enumeration():
    nonempty_revocations = 0
    for seed in range(40):
        payload = make_random_payload(seed, 3, 3, conflict_count=3)
        if seed % 2 == 0:
            payload = force_one_impossible_root(payload)
        plan = assert_matches_oracle(payload)
        if plan.revoked:
            nonempty_revocations += 1
    assert nonempty_revocations >= 15


def test_medium_random_catalogs_match_enumeration():
    nonempty_revocations = 0
    for seed in range(12):
        payload = make_random_payload(seed + 5000, 5, 4, conflict_count=6)
        if seed % 2 == 1:
            payload = force_one_impossible_root(payload)
        plan = assert_matches_oracle(payload)
        if plan.revoked:
            nonempty_revocations += 1
    assert nonempty_revocations >= 4


def test_random_catalogs_without_conflicts_match_enumeration():
    # Interval-only infeasibility (roots vs dependency edges, incl. cycles).
    saw_revocation = False
    for seed in range(30):
        payload = make_random_payload(seed + 9000, 3, 3, conflict_count=0)
        plan = assert_matches_oracle(payload)
        saw_revocation = saw_revocation or bool(plan.revoked)
    assert saw_revocation


# --------------------------------------------------------------------------- #
# Deterministic behaviour
# --------------------------------------------------------------------------- #


def test_lexicographically_smallest_single_revocation_wins():
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("a",)
    assert dict(plan.selection) == {"b": 1}
    assert [(c.name, c.kind) for c in plan.changes] == [("b", "added")]


def test_revoked_root_package_remains_as_a_dependency():
    # Root a demands a=1 while b forces a=2: jointly infeasible.  Revoking a's
    # root keeps b, and a stays in the closure at version 2; the installed a=1
    # is changed rather than removed.  Dependencies are never rewritten.
    payload = {
        "packages": {
            "a": {"1": {}, "2": {}},
            "b": {"1": {"dependencies": {"a": [2, 2]}}},
        },
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {"a": 1},
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("a",)
    assert dict(plan.selection) == {"a": 2, "b": 1}
    assert [(c.name, c.old, c.new, c.kind) for c in plan.changes] == [
        ("a", 1, 2, "changed"),
        ("b", None, 1, "added"),
    ]


def test_lexicographic_order_yields_to_feasibility():
    # ("a",) sorts before ("z",) but a alone leaves the self-inconsistent z,
    # which is still infeasible; z must be the revoked root.
    payload = {
        "packages": {
            "a": {"1": {"dependencies": {"x": [1, 1]}}, "2": {}},
            "x": {"1": {}},
            "z": {"1": {"dependencies": {"z": [2, 2]}}},
        },
        "root": {"a": [1, 2], "z": [1, 1]},
        "installed": {"a": 1, "x": 1},
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("z",)
    # Fewest-changes objective within the retained roots keeps a=1,x=1.
    assert dict(plan.selection) == {"a": 1, "x": 1}
    assert plan.changes == ()


def test_version_vector_decides_within_retained_closure():
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"x": [1, 1]}},
                "2": {"dependencies": {"b": [1, 1]}},
            },
            "x": {"1": {}},
            "b": {"1": {}},
            "z": {"1": {"dependencies": {"z": [2, 2]}}},
        },
        "root": {"a": [1, 2], "z": [1, 1]},
        "installed": {},
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("z",)
    # Equal change count and package size; the lexicographically larger vector
    # (a=2,b=1) beats (a=1,x=1).
    assert dict(plan.selection) == {"a": 2, "b": 1}


def test_dependency_cycle_with_conflict_and_revocation():
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"b": [1, 2]}},
                "2": {"dependencies": {"b": [1, 2]}},
            },
            "b": {
                "1": {"dependencies": {"a": [1, 2]}},
                "2": {"dependencies": {"a": [1, 2]}},
            },
            "c": {"1": {"dependencies": {"c": [2, 2]}}},
        },
        "root": {"a": [1, 2], "b": [1, 2], "c": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 2], ["b", 2]]],
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("c",)
    assert dict(plan.selection) == {"a": 2, "b": 1}


def test_conflict_with_stale_installed_versions_forces_revocation():
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {"a": 9, "b": 9},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("a",)
    assert dict(plan.selection) == {"b": 1}
    assert [(c.name, c.old, c.new, c.kind) for c in plan.changes] == [
        ("a", 9, None, "removed"),
        ("b", 9, 1, "changed"),
    ]


def test_two_revocations_needed_for_pairwise_conflicts():
    payload = {
        "packages": {
            "a": {"1": {}},
            "b": {"1": {}},
            "c": {"1": {}},
        },
        "root": {"a": [1, 1], "b": [1, 1], "c": [1, 1]},
        "installed": {},
        "conflicts": [
            [["a", 1], ["b", 1]],
            [["a", 1], ["c", 1]],
            [["b", 1], ["c", 1]],
        ],
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ("a", "b")
    assert dict(plan.selection) == {"c": 1}


def test_empty_root_relaxes_to_empty_closure():
    payload = {
        "packages": {"a": {"1": {}}},
        "root": {},
        "installed": {"a": 1},
    }
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ()
    assert dict(plan.selection) == {}
    assert [(c.name, c.old, c.new, c.kind) for c in plan.changes] == [
        ("a", 1, None, "removed")
    ]


def test_solvable_request_relaxes_to_normal_solution():
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {"a": 1},
    }
    normal = solve(parse_request(payload))
    plan = assert_matches_oracle(payload)
    assert plan.revoked == ()
    assert dict(plan.selection) == dict(normal) == {"a": 1, "b": 2}
    assert [(c.name, c.kind) for c in plan.changes] == [("b", "added")]


def test_repeated_relaxation_does_not_leak_or_mutate_state():
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    catalog = parse_request(payload)
    assert solve(catalog) is None  # genuinely infeasible

    first = solve_relaxed(catalog)
    second = solve_relaxed(catalog)
    assert first.revoked == second.revoked == ("a",)
    assert dict(first.selection) == dict(second.selection) == {"b": 1}

    # The shared catalog (roots included) is untouched across isolated searches.
    assert catalog.root == {"a": (1, 1), "b": (1, 1)}
    assert solve(catalog) is None


def test_format_error_is_not_reported_as_unresolvable():
    # The conflict references an unknown version: a contract error regardless
    # of relaxation, surfaced as InputError rather than a plan/UNRESOLVABLE.
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 9]]],
    }
    from resolver import InputError

    try:
        parse_request(payload)
    except InputError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected InputError")
