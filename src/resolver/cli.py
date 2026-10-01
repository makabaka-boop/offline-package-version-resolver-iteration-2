"""Command-line entry point.

Usage::

    python -m resolver < request.json
    python -m resolver request.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .errors import InputError
from .model import parse_request
from .solver import RootRelaxation, solve, solve_relaxed_roots


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
        "--relax-root-requirements",
        "--relax-roots",
        dest="relax_root_requirements",
        action="store_true",
        help=(
            "if the exact request is unresolvable, temporarily revoke the "
            "minimum complete root requirements"
        ),
    )
    args = parser.parse_args(argv)

    try:
        raw = args.request.read()
        catalog = parse_request(raw)
    except (OSError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.relax_root_requirements:
        relaxation = solve_relaxed_roots(catalog)
        if relaxation.revoked_requirements:
            print(format_relaxation(relaxation))
        else:
            print(format_selection(catalog, relaxation.selection))
        return 0

    selection = solve(catalog)
    if selection is None:
        print("UNRESOLVABLE")
        return 0

    # Re-encoding preserves deterministic key order and emits true JSON,
    # rather than Python's single-quoted representation.
    print(format_selection(catalog, selection))
    return 0


def format_selection(catalog, selection) -> str:
    return json.dumps(
        {name: selection[name] for name in catalog.ordered_names if name in selection}
    )


def format_relaxation(relaxation: RootRelaxation) -> str:
    report = {
        "revoked_root_requirements": [
            {
                "package": name,
                "interval": list(relaxation.revoked_requirements[name]),
            }
            for name in relaxation.revoked_roots
        ],
        "installation": {
            name: relaxation.selection[name]
            for name in sorted(relaxation.selection)
        },
        "changes": [
            {
                "package": change.name,
                "action": change.action,
                "before": change.before,
                "after": change.after,
            }
            for change in sorted(relaxation.changes, key=lambda change: change.name)
        ],
    }
    return json.dumps(report, sort_keys=False)


if __name__ == "__main__":
    raise SystemExit(main())
