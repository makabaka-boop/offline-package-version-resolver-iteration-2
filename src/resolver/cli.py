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
from .solver import solve


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve an offline package catalog")
    parser.add_argument(
        "request",
        nargs="?",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON request file (defaults to standard input)",
    )
    args = parser.parse_args(argv)

    try:
        raw = args.request.read()
        catalog = parse_request(raw)
    except (OSError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    selection = solve(catalog)
    if selection is None:
        print("UNRESOLVABLE")
        return 0

    # Re-encoding preserves deterministic key order and emits true JSON,
    # rather than Python's single-quoted representation.
    print(json.dumps({name: selection[name] for name in catalog.ordered_names if name in selection}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
