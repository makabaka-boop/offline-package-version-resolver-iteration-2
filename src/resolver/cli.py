"""Command-line entry point.

Usage::

    python -m resolver < request.json
    python -m resolver request.json
    python -m resolver --relax-roots request.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .errors import InputError
from .model import Catalog, parse_request
from .solver import RelaxedPlan, solve, solve_relaxed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve an offline package catalog")
    parser.add_argument(
        "request",
        nargs="?",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON request file (defaults to standard input)",
    )
    parser.add_argument(
        "--relax-roots",
        action="store_true",
        help=(
            "when the roots are jointly infeasible, temporarily revoke whole "
            "root requirements (fewest first) instead of reporting UNRESOLVABLE"
        ),
    )
    args = parser.parse_args(argv)

    try:
        raw = args.request.read()
        catalog = parse_request(raw)
    except (OSError, InputError) as exc:
        # A malformed document is a contract error, never an infeasibility: it
        # must not be wrapped as UNRESOLVABLE and no partial plan is printed.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.relax_roots:
        plan = solve_relaxed(catalog)
        if plan is None:
            # Defensive only: revoking every root yields the empty closure, so a
            # validated catalog always produces a plan.
            print("UNRESOLVABLE")
            return 0
        print(json.dumps(render_relaxed(catalog, plan), sort_keys=True))
        return 0

    selection = solve(catalog)
    if selection is None:
        print("UNRESOLVABLE")
        return 0

    # Re-encoding preserves deterministic key order and emits true JSON,
    # rather than Python's single-quoted representation.
    print(json.dumps({name: selection[name] for name in catalog.ordered_names if name in selection}))
    return 0


def render_relaxed(catalog: Catalog, plan: RelaxedPlan) -> dict:
    """Render a relaxed plan as the documented JSON object.

    Top-level keys: ``revoked`` (root package names), ``selection`` (final
    install set, name -> version) and ``changes`` relative to ``installed``.
    """
    selection = {
        name: plan.selection[name]
        for name in catalog.ordered_names
        if name in plan.selection
    }
    changes = [
        {"name": change.name, "from": change.old, "to": change.new, "kind": change.kind}
        for change in plan.changes
    ]
    return {
        "revoked": list(plan.revoked),
        "selection": selection,
        "changes": changes,
    }


if __name__ == "__main__":
    raise SystemExit(main())
