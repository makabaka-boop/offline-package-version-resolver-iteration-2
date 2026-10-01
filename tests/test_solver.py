import itertools
import random

import pytest

from resolver import parse_request, solve


def brute_force(payload: dict):
    """Enumerate every (installed-or-absent) version combination."""
    catalog = parse_request(payload)
    best = None
    best_key = None

    choices = [(name, (0,) + catalog.versions[name]) for name in catalog.ordered_names]
    for values in itertools.product(*(options for _, options in choices)):
        candidate = {
            name: version for (name, _), version in zip(choices, values) if version != 0
        }
        if not is_complete_solution(catalog, candidate):
            continue

        changed = 0
        for name, version in candidate.items():
            if catalog.installed.get(name) != version:
                changed += 1
        for name in catalog.installed:
            if name not in candidate:
                changed += 1

        vector = tuple(candidate.get(name, 0) for name in catalog.ordered_names)
        key = (changed, len(candidate), tuple(-x for x in vector))
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best


def is_complete_solution(catalog, candidate):
    # Exact closure: start from roots and follow dependency edges; the reached
    # set must equal the candidate package set.
    reached: set[str] = set()
    stack = list(catalog.root)
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        actual = candidate.get(name)
        if actual is None or actual not in catalog.versions[name]:
            return False
        if name in catalog.root:
            lower, upper = catalog.root[name]
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
    # Mutual exclusion: a declared conflict pair must not be installed together.
    for (name_a, version_a), (name_b, version_b) in catalog.conflicts:
        if candidate.get(name_a) == version_a and candidate.get(name_b) == version_b:
            return False
    return True


def solve_payload(payload):
    catalog = parse_request(payload)
    result = solve(catalog)
    return None if result is None else dict(sorted(result.items()))


def payload_from_generated(packages, root, installed, conflicts=None):
    payload = {"packages": packages, "root": root, "installed": installed}
    if conflicts is not None:
        payload["conflicts"] = conflicts
    return payload


def make_random_payload(
    seed: int, package_count: int, max_versions: int, conflict_count: int = 0
) -> dict:
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
            # Dependency density is deliberately moderate to produce both
            # closures, cycles, and packages absent from some solutions.
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
            # Include versions absent from the catalog to test stale installs.
            installed[name] = rng.randint(1, max_versions + 1)

    conflicts = None
    if conflict_count and len(names) >= 2:
        conflicts = []
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

    return payload_from_generated(packages, root, installed, conflicts)


@pytest.mark.parametrize("seed", range(60))
def test_small_random_catalogs_match_full_enumeration(seed):
    payload = make_random_payload(seed, package_count=3, max_versions=3)
    expected = brute_force(payload)
    actual = solve_payload(payload)
    assert actual == (None if expected is None else dict(sorted(expected.items())))


@pytest.mark.parametrize("seed", range(30))
def test_medium_random_catalogs_match_full_enumeration(seed):
    # Five packages with up to four versions gives at most 5**5 = 3125 vectors.
    payload = make_random_payload(seed + 1000, package_count=5, max_versions=4)
    expected = brute_force(payload)
    actual = solve_payload(payload)
    assert actual == (None if expected is None else dict(sorted(expected.items())))


@pytest.mark.parametrize("seed", range(60))
def test_small_random_catalogs_with_conflicts_match_full_enumeration(seed):
    payload = make_random_payload(
        seed + 2000, package_count=3, max_versions=3, conflict_count=3
    )
    expected = brute_force(payload)
    actual = solve_payload(payload)
    assert actual == (None if expected is None else dict(sorted(expected.items())))


@pytest.mark.parametrize("seed", range(30))
def test_medium_random_catalogs_with_conflicts_match_full_enumeration(seed):
    payload = make_random_payload(
        seed + 3000, package_count=5, max_versions=4, conflict_count=6
    )
    expected = brute_force(payload)
    actual = solve_payload(payload)
    assert actual == (None if expected is None else dict(sorted(expected.items())))


def test_simple_root_selects_highest_compatible_version():
    payload = {
        "packages": {"a": {"1": {}, "2": {}}},
        "root": {"a": [1, 2]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 2}


def test_minimize_installed_changes_before_version_maximization():
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"b": [1, 1]}},
                "2": {"dependencies": {"b": [2, 2]}},
            },
            "b": {"1": {}, "2": {}},
        },
        "root": {"a": [1, 2]},
        "installed": {"a": 1, "b": 1},
    }
    assert solve_payload(payload) == {"a": 1, "b": 1}


def test_change_count_is_primary_over_smaller_closure():
    # a=1 plus x is a zero-change two-package closure.  Upgrading to a=2 would
    # make the one-package closure, but it changes a and removes x (two
    # changes).  A smaller closure therefore cannot beat fewer changes.
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"x": [1, 1]}},
                "2": {},
            },
            "x": {"1": {}},
            "b": {"1": {}},
        },
        "root": {"a": [1, 2]},
        "installed": {"a": 1, "x": 1},
    }
    assert solve_payload(payload) == {"a": 1, "x": 1}


def test_smaller_package_count_wins_at_equal_change_cost():
    # Fresh catalog: both feasible plans add two packages (equal changes), but
    # a=2 reaches only b, whereas a=1 reaches both x and y.
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"x": [1, 1], "y": [1, 1]}},
                "2": {"dependencies": {"b": [1, 1]}},
            },
            "x": {"1": {}},
            "y": {"1": {}},
            "b": {"1": {}},
        },
        "root": {"a": [1, 2]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 2, "b": 1}


def test_conflicting_closed_intervals_are_unresolvable():
    payload = {
        "packages": {
            "a": {"1": {"dependencies": {"b": [1, 2]}}},
            "b": {"1": {}, "2": {}, "3": {}},
            "c": {"1": {"dependencies": {"b": [3, 3]}}},
        },
        "root": {"a": [1, 1], "c": [1, 1]},
        "installed": {},
    }
    assert solve_payload(payload) is None


def test_self_cycle_is_accepted_when_interval_matches():
    payload = {
        "packages": {
            "a": {"1": {"dependencies": {"a": [1, 1]}}, "2": {}}
        },
        "root": {"a": [1, 2]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 2}


def test_self_cycle_can_force_a_specific_version():
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"a": [2, 2]}},
                "2": {"dependencies": {"a": [2, 2]}},
            }
        },
        "root": {"a": [1, 2]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 2}


def test_mutual_dependency_cycle_is_resolved():
    payload = {
        "packages": {
            "a": {"1": {"dependencies": {"b": [1, 2]}}},
            "b": {"1": {"dependencies": {"a": [1, 1]}}, "2": {}},
        },
        "root": {"a": [1, 1]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 1, "b": 2}


def test_package_not_in_reachable_closure_is_removed():
    payload = {
        "packages": {
            "a": {"1": {}},
            "b": {"1": {}},
        },
        "root": {"a": [1, 1]},
        "installed": {"a": 1, "b": 1},
    }
    # Removal of b costs one; a solution containing b is illegal by exact
    # closure, so there is no way to retain it.
    assert solve_payload(payload) == {"a": 1}


def test_stale_installed_version_counts_as_a_change():
    payload = {
        "packages": {"a": {"2": {}, "3": {}}},
        "root": {"a": [2, 3]},
        "installed": {"a": 1},
    }
    assert solve_payload(payload) == {"a": 3}


def test_lexicographic_vector_uses_complete_catalog_order():
    # A solution with higher a loses under the package-count tie-breaker; this
    # case has equal count, and b precedes x in catalog order.
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"b": [1, 1], "x": [1, 1]}},
                "2": {"dependencies": {"b": [2, 2], "x": [1, 1]}},
            },
            "b": {"1": {}, "2": {}},
            "x": {"1": {}},
        },
        "root": {"a": [1, 2]},
        "installed": {},
    }
    assert solve_payload(payload) == {"a": 2, "b": 2, "x": 1}


def test_empty_root_is_a_valid_empty_closure_and_removes_everything():
    payload = {
        "packages": {"a": {"1": {}}},
        "root": {},
        "installed": {"a": 1},
    }
    assert solve_payload(payload) == {}


def test_conflict_forces_backtracking_to_next_best_version():
    # Without the conflict the optimum is a=2,b=2.  Excluding that exact pair
    # must not drop b (a root); the solver keeps a=2 and downgrades b instead.
    payload = {
        "packages": {
            "a": {"1": {}, "2": {}},
            "b": {"1": {}, "2": {}},
        },
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {},
        "conflicts": [[["a", 2], ["b", 2]]],
    }
    assert solve_payload(payload) == {"a": 2, "b": 1}


def test_conflict_backtracks_through_dependency_propagation():
    # a=2 forces b=2, which conflicts with the required c=1; the solver must
    # unwind that branch and fall back to a=1 (which forces b=1).
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"b": [1, 1]}},
                "2": {"dependencies": {"b": [2, 2]}},
            },
            "b": {"1": {}, "2": {}},
            "c": {"1": {}},
        },
        "root": {"a": [1, 2], "c": [1, 1]},
        "installed": {},
        "conflicts": [[["b", 2], ["c", 1]]],
    }
    assert solve_payload(payload) == {"a": 1, "b": 1, "c": 1}


def test_conflict_with_root_requirements_is_unresolvable():
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    assert solve_payload(payload) is None


def test_conflicts_can_eliminate_every_version_combination():
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}}},
        "root": {"a": [1, 2], "b": [1, 1]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]], [["a", 2], ["b", 1]]],
    }
    assert solve_payload(payload) is None


def test_conflict_does_not_pull_package_into_closure():
    # b is only mentioned by the conflict: it stays out of the closure, and an
    # installed b is removed rather than retained.
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}}},
        "root": {"a": [1, 1]},
        "installed": {"b": 1},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    assert solve_payload(payload) == {"a": 1}


def test_conflict_only_applies_to_the_exact_declared_versions():
    payload = {
        "packages": {"a": {"1": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 1], "b": [1, 2]},
        "installed": {},
        "conflicts": [[["a", 1], ["b", 1]]],
    }
    assert solve_payload(payload) == {"a": 1, "b": 2}


def test_conflict_inside_dependency_cycle():
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
        },
        "root": {"a": [1, 2]},
        "installed": {},
        "conflicts": [[["a", 2], ["b", 2]]],
    }
    assert solve_payload(payload) == {"a": 2, "b": 1}


def test_conflict_with_stale_installed_version():
    # Installed a=9 no longer exists in the catalog; the conflict only forbids
    # (a,2)-(b,1), so the stale install is upgraded to a=2 alongside b=2.
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {"a": 9},
        "conflicts": [[["a", 2], ["b", 1]]],
    }
    assert solve_payload(payload) == {"a": 2, "b": 2}


def test_conflict_respects_change_count_before_version_vector():
    # Keeping the installed (a,1)-(b,2) costs zero changes and is allowed; the
    # conflict only rules out (a,2)-(b,2), so no upgrade can win.
    payload = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {"a": 1, "b": 2},
        "conflicts": [[["a", 2], ["b", 2]]],
    }
    assert solve_payload(payload) == {"a": 1, "b": 2}


def test_empty_conflicts_list_behaves_like_omitted_conflicts():
    base = {
        "packages": {"a": {"1": {}, "2": {}}, "b": {"1": {}, "2": {}}},
        "root": {"a": [1, 2], "b": [1, 2]},
        "installed": {"a": 1},
    }
    with_empty = dict(base, conflicts=[])
    assert solve_payload(with_empty) == solve_payload(base) == {"a": 1, "b": 2}
