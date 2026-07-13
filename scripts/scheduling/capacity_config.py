"""
Load capacity configuration from data/reference/capacity_config.yaml.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "data" / "reference" / "capacity_config.yaml"

# Five-day shift patterns (Python weekday: Mon=0 … Sun=6)
SHIFT_PATTERNS: dict[str, frozenset[int]] = {
    "M-F": frozenset({0, 1, 2, 3, 4}),
    "M-TH": frozenset({0, 1, 2, 3}),
    "T-SAT": frozenset({1, 2, 3, 4, 5}),
    "T-FRI": frozenset({1, 2, 3, 4}),
    "SUN-TH": frozenset({6, 0, 1, 2, 3}),
    "W-SUN": frozenset({2, 3, 4, 5, 6}),
}


@lru_cache(maxsize=1)
def load_capacity_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(f"Capacity config not found: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def unplanned_shrinkage_pct(config: dict | None = None) -> float:
    cfg = config or load_capacity_config()
    return float(cfg.get("unplanned_shrinkage_pct", 0.05))


def productive_minutes_per_day(support_group: str, config: dict | None = None) -> float:
    """
    Ticket-solving minutes available per agent per shift day.

    Applies unplanned_shrinkage_pct so FIFO budgets match staffing capacity.
    """
    cfg = config or load_capacity_config()
    hours = float(cfg["productive_hours_per_day"][support_group])
    shrinkage = unplanned_shrinkage_pct(cfg)
    return hours * 60.0 * (1.0 - shrinkage)


def p50_handle_minutes(support_group: str, config: dict | None = None) -> float:
    cfg = config or load_capacity_config()
    return float(cfg["solve_time_p50_minutes"][support_group])


def hourly_ticket_capacity_per_agent(
    support_group: str,
    *,
    ramp_multiplier: float = 1.0,
    config: dict | None = None,
) -> float:
    """
    Expected tickets an agent can complete in one scheduled hour.

    (productive_minutes_per_day / 60 / shift_hours) * (60 / p50_minutes) * ramp

    Shrinkage is applied once inside productive_minutes_per_day.
    """
    cfg = config or load_capacity_config()
    productive_hrs = productive_minutes_per_day(support_group, cfg) / 60.0
    shift_hrs = float(cfg.get("shift_duration_hours", 9))
    p50 = float(cfg["solve_time_p50_minutes"][support_group])
    return (productive_hrs / shift_hrs) * (60.0 / p50) * ramp_multiplier
