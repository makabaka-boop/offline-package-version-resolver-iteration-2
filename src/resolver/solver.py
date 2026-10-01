"""Exact backtracking solver for the small offline package catalog."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from itertools import combinations
from typing import Optional

from .model import Catalog

Selection = dict[str, int]


@dataclass(frozen=True)
class Change:
    """A single installed package's difference from ``installed``.

    ``kind`` is ``"added"`` (absent before, present after), ``"removed"``
    (present before, absent after) or ``"changed"`` (present at both times but
    a different version).
    """

    name: str
    old: Optional[int]
    new: Optional[int]
    kind: str


@dataclass(frozen=True)
class RelaxedPlan:
    """Result of root-requirement relaxation.

    ``revoked`` names the whole root requirements temporarily dropped; the
    retained requirements are solved with the ordinary objectives.  Nothing in
    dependencies, conflicts or installed versions is ever rewritten.
    """

    revoked: tuple[str, ...]
    selection: Selection
    changes: tuple[Change, ...]


def compute_changes(
    selection: Selection, installed: dict[str, int]
) -> tuple[Change, ...]:
    """Describe every package that differs from the installed state."""
    changes: list[Change] = []
    for name in sorted(set(selection) | set(installed)):
        old = installed.get(name)
        new = selection.get(name)
        if old == new:
            continue
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        else:
            kind = "changed"
        changes.append(Change(name=name, old=old, new=new, kind=kind))
    return tuple(changes)


def solve(catalog: Catalog) -> Optional[Selection]:
    """Return the best installable selection, or ``None`` if none exists.

    A selection is valid when every root is selected, every selected version's
    dependency interval contains the selected dependency, no declared conflict
    pair is installed together, and no package outside that reachable closure
    is present.
    """
    return _solve(catalog, catalog.root)


def solve_relaxed(catalog: Catalog) -> Optional[RelaxedPlan]:
    """Relax an infeasible request by temporarily revoking whole roots only.

    Candidate revocation sets are examined in order of:

    1. fewest revoked root requirements, then
    2. lexicographically smallest list of revoked package names.

    For the first (hence best under that order) set whose retained roots admit
    a complete closure, the ordinary solver returns that closure under the
    unchanged objectives (fewest changes, then fewest packages, then the
    lexicographically largest version vector).

    Each candidate is solved from a fresh propagation state, so constraint
    propagation and branching never leak between root sets.  Revoking every
    root always admits the empty closure, so the only ``None`` result would be
    a malformed catalog, which validation rejects before solving.
    """
    root_names = sorted(catalog.root)
    for size in range(len(root_names) + 1):
        # ``combinations`` yields tuples in lexicographic order when its input
        # iterable is sorted, so the first feasible set already wins the
        # tie-break without comparing closures across distinct revocations.
        for revoked in combinations(root_names, size):
            revoked_set = set(revoked)
            retained = {
                name: catalog.root[name]
                for name in root_names
                if name not in revoked_set
            }
            selection = _solve(catalog, retained)
            if selection is not None:
                return RelaxedPlan(
                    revoked=tuple(revoked),
                    selection=selection,
                    changes=compute_changes(selection, catalog.installed),
                )
    return None


def _solve(
    catalog: Catalog, roots: dict[str, tuple[int, int]]
) -> Optional[Selection]:
    """Solve for a specific set of retained root requirements.

    All mutable search state is created locally for this call, which keeps
    searches over different retained-root sets fully isolated.
    """
    names = catalog.ordered_names
    versions = {name: catalog.versions[name] for name in names}
    installed = catalog.installed
    deps = catalog.dependencies

    # Bidirectional mutual exclusion between exact (package, version) pairs.
    conflict_partners: dict[tuple[str, int], set[tuple[str, int]]] = {}
    for (name_a, version_a), (name_b, version_b) in catalog.conflicts:
        conflict_partners.setdefault((name_a, version_a), set()).add((name_b, version_b))
        conflict_partners.setdefault((name_b, version_b), set()).add((name_a, version_a))

    # name -> closed (lower, upper) bounds accumulated from roots/edges
    bounds: dict[str, tuple[int, int]] = dict(roots)
    selected: Selection = {}
    best: Optional[Selection] = None
    best_change = 1 << 30
    best_count = 1 << 30
    best_vector: Optional[tuple[int, ...]] = None

    def max_in_bounds(name: str, lower: int, upper: int) -> int:
        options = versions[name]
        pos = bisect_right(options, upper) - 1
        return options[pos] if pos >= 0 and options[pos] >= lower else 0

    def forced_change_lower_bound() -> int:
        """Changes unavoidable for the packages currently required."""
        changes = 0
        for name, (lower, upper) in bounds.items():
            if name in selected:
                if installed.get(name) != selected[name]:
                    changes += 1
            else:
                kept = installed.get(name)
                can_keep = (
                    kept is not None
                    and lower <= kept <= upper
                    # A kept version must actually still exist in the catalog.
                    and bisect_right(versions[name], kept) > 0
                    and versions[name][bisect_right(versions[name], kept) - 1] == kept
                )
                if not can_keep:
                    changes += 1
        # An installed package not required yet may later be added by an edge or
        # be removed, so it contributes no safe lower bound here.
        return changes

    def lexicographic_upper_bound() -> tuple[int, ...]:
        # Optimistic vector: pending packages take their highest still-possible
        # value, not-yet-required packages their catalog maximum.  It never
        # under-estimates a descendant, so a vector <= best cannot improve.
        result = []
        for name in names:
            if name in selected:
                result.append(selected[name])
            elif name in bounds:
                lower, upper = bounds[name]
                result.append(max_in_bounds(name, lower, upper))
            else:
                result.append(versions[name][-1])
        return tuple(result)

    def record(candidate: Selection) -> None:
        nonlocal best, best_change, best_count, best_vector
        changed = 0
        for name, version in candidate.items():
            if installed.get(name) != version:
                changed += 1
        for name in installed:
            if name not in candidate:
                changed += 1

        count = len(candidate)
        vector = tuple(candidate.get(name, 0) for name in names)
        if (
            best_vector is None
            or changed < best_change
            or (changed == best_change and count < best_count)
            or (changed == best_change and count == best_count and vector > best_vector)
        ):
            best, best_change, best_count, best_vector = (
                dict(candidate),
                changed,
                count,
                vector,
            )

    def dfs() -> None:
        change_lb = forced_change_lower_bound()
        count_lb = len(bounds)
        if change_lb > best_change:
            return
        if change_lb == best_change:
            if count_lb > best_count:
                return
            if (
                count_lb == best_count
                and best_vector is not None
                and lexicographic_upper_bound() <= best_vector
            ):
                return

        pending = [name for name in names if name in bounds and name not in selected]
        if not pending:
            record(selected)
            return

        # Most constrained package first; ties stay name-sorted for determinism.
        name = min(
            pending,
            key=lambda candidate: (
                sum(
                    bounds[candidate][0] <= version <= bounds[candidate][1]
                    for version in versions[candidate]
                ),
                candidate,
            ),
        )
        lower, upper = bounds[name]

        # Try larger versions first: under equal change/count, the first
        # feasible solution is already lexicographically maximal and makes
        # branch-and-bound pruning effective.  Exhaustive search still keeps
        # the objective correct regardless of this ordering.
        for version in reversed(versions[name]):
            if not lower <= version <= upper:
                continue

            partners = conflict_partners.get((name, version))
            if partners is not None and any(
                selected.get(other_name) == other_version
                for other_name, other_version in partners
            ):
                # Known-incompatible with an already selected version.  Every
                # conflicting pair is caught when its second endpoint is
                # selected, and backtracking undoes the selection as usual.
                continue

            selected[name] = version
            undo: list[tuple[str, Optional[tuple[int, int]]]] = []
            consistent = True

            for dependency, (dep_lower, dep_upper) in deps[name][version].items():
                old_bounds = bounds.get(dependency)
                if old_bounds is None:
                    next_bounds = (dep_lower, dep_upper)
                else:
                    next_bounds = (
                        max(old_bounds[0], dep_lower),
                        min(old_bounds[1], dep_upper),
                    )

                if next_bounds[0] > next_bounds[1]:
                    consistent = False
                elif dependency in selected and not (
                    next_bounds[0] <= selected[dependency] <= next_bounds[1]
                ):
                    consistent = False

                if old_bounds != next_bounds:
                    undo.append((dependency, old_bounds))
                    if consistent:
                        bounds[dependency] = next_bounds

                if not consistent:
                    break

            if consistent:
                dfs()

            for dependency, old_bounds in undo:
                if old_bounds is None:
                    del bounds[dependency]
                else:
                    bounds[dependency] = old_bounds
            del selected[name]

    # Reject a root whose interval does not intersect its catalog versions.
    for name, (lower, upper) in bounds.items():
        if max_in_bounds(name, lower, upper) == 0:
            return None

    dfs()
    return best
