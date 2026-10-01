"""Offline package dependency resolver."""

from .errors import InputError
from .model import Catalog, parse_request
from .solver import (
    Change,
    RelaxedPlan,
    compute_changes,
    solve,
    solve_relaxed,
)

__all__ = [
    "Catalog",
    "Change",
    "InputError",
    "RelaxedPlan",
    "compute_changes",
    "parse_request",
    "solve",
    "solve_relaxed",
]
