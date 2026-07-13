"""
Productivity ramp for new specialists — configurable via productivity_ramp.yaml.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAMP = PROJECT_ROOT / "data" / "reference" / "productivity_ramp.yaml"


@lru_cache(maxsize=1)
def load_ramp_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_RAMP
    if not cfg_path.exists():
        raise FileNotFoundError(f"Productivity ramp config not found: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def tenure_days(start: date, on_day: date) -> int:
    return max(0, (on_day - start).days)


def ramp_multiplier(support_group: str, start: date, on_day: date, config: dict | None = None) -> float:
    """
    Return 0.0 if before start; 1.0 when fully ramped; gradual climb in between.

    Uses team-specific ramp length from config.
    """
    if on_day < start:
        return 0.0

    cfg = config or load_ramp_config()
    ramp_days = int(cfg["ramp_days_to_full_productivity"][support_group])
    min_mult = float(cfg.get("min_ramp_multiplier", 0.35))
    curve = str(cfg.get("ramp_curve", "linear")).lower()
    days = tenure_days(start, on_day)

    if days >= ramp_days:
        return 1.0

    progress = days / ramp_days if ramp_days > 0 else 1.0
    if curve == "smooth":
        progress = progress**0.5

    return min_mult + (1.0 - min_mult) * progress
