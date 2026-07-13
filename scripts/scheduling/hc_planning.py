"""
Headcount planning from weekly inbound volume.

Sizing model (matches 3-business-day SLA — clear weekly demand, not each hour):

  tickets_per_agent_day = productive_minutes_per_day / p50_handle_minutes
                        # productive_minutes already includes unplanned shrinkage

  tickets_per_agent_week = tickets_per_agent_day
                         × working_days_per_week
                         × planning_target_utilization

  required_hc = (weekly_inbound × solve_goal) / tickets_per_agent_week

  scheduled_hc = agent_hours_in_period / (shift_hours × working_days_in_period)

  hc_gap = scheduled_hc − required_hc   # negative ⇒ understaffed
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scheduling.capacity_config import (
    load_capacity_config,
    p50_handle_minutes,
    productive_minutes_per_day,
)
from scheduling.hourly_schedule import TIERS

# Fallbacks if YAML keys are absent
DEFAULT_WORKING_DAYS_PER_WEEK = 5.0
DEFAULT_TARGET_UTILIZATION = 0.85
DEFAULT_SOLVE_GOAL = 1.0
DEFAULT_SLA_BUSINESS_DAYS = 3.0
DEFAULT_CLEAR_HORIZON_HOURS = 8.0  # legacy alias for dashboard imports


def planning_params(config: dict | None = None) -> dict[str, float]:
    cfg = config or load_capacity_config()
    return {
        "working_days_per_week": float(
            cfg.get("planning_working_days_per_week", DEFAULT_WORKING_DAYS_PER_WEEK)
        ),
        "target_utilization": float(
            cfg.get("planning_target_utilization", DEFAULT_TARGET_UTILIZATION)
        ),
        "solve_goal": float(cfg.get("planning_solve_goal", DEFAULT_SOLVE_GOAL)),
        "sla_business_days": float(
            cfg.get("planning_sla_business_days", DEFAULT_SLA_BUSINESS_DAYS)
        ),
        "shift_hours": float(cfg.get("shift_duration_hours", 9)),
    }


def tickets_per_agent_day(tier: str, config: dict | None = None) -> float:
    """Fully-ramped tickets one agent can solve in a productive day (post-shrinkage)."""
    cfg = config or load_capacity_config()
    mins = productive_minutes_per_day(tier, cfg)
    p50 = p50_handle_minutes(tier, cfg)
    if p50 <= 0:
        return 0.0
    return mins / p50


def tickets_per_agent_week(tier: str, config: dict | None = None) -> float:
    """Weekly ticket throughput for one FTE at the planning utilization target."""
    cfg = config or load_capacity_config()
    params = planning_params(cfg)
    return (
        tickets_per_agent_day(tier, cfg)
        * params["working_days_per_week"]
        * params["target_utilization"]
    )


def required_hc_for_volume(
    inbound_tickets: float,
    tier: str,
    *,
    working_days: float,
    config: dict | None = None,
) -> float:
    """
    HC required to solve inbound volume over `working_days` calendar workdays.

    Scales the weekly FTE model: capacity ∝ working_days.
    """
    cfg = config or load_capacity_config()
    params = planning_params(cfg)
    tpd = tickets_per_agent_day(tier, cfg)
    if tpd <= 0 or working_days <= 0:
        return 0.0
    capacity_per_hc = tpd * working_days * params["target_utilization"]
    demand = float(inbound_tickets) * params["solve_goal"]
    return demand / capacity_per_hc


def _period_key(ts: pd.Series, grain: str) -> pd.Series:
    s = pd.to_datetime(ts, utc=True)
    if grain == "day":
        return s.dt.floor("D")
    if grain == "week":
        return s.dt.to_period("W-MON").dt.start_time.dt.tz_localize("UTC")
    if grain == "month":
        return s.dt.to_period("M").dt.start_time.dt.tz_localize("UTC")
    if grain == "quarter":
        return s.dt.to_period("Q").dt.start_time.dt.tz_localize("UTC")
    if grain == "year":
        return s.dt.to_period("Y").dt.start_time.dt.tz_localize("UTC")
    raise ValueError(f"Unknown grain: {grain}")


def _working_days_in_period(period_start: pd.Timestamp, grain: str, params: dict[str, float]) -> float:
    """Approximate paid working days covered by the period."""
    wd = params["working_days_per_week"]
    if grain == "day":
        # Count weekdays only
        ts = pd.Timestamp(period_start)
        return 1.0 if ts.dayofweek < 5 else 0.0
    if grain == "week":
        return wd
    if grain == "month":
        return wd * (52.0 / 12.0)  # ~4.33 weeks
    if grain == "quarter":
        return wd * (52.0 / 4.0)  # 13 weeks
    if grain == "year":
        return wd * 52.0
    return wd


def build_hc_planning_grain(
    backlog: pd.DataFrame,
    grain: str,
    *,
    config: dict | None = None,
) -> pd.DataFrame:
    """
    Aggregate backlog_hourly to `grain` × tier and compute required vs scheduled HC.

    Primary signal: inbound ticket volume in the period (weekly-first model).
    """
    if backlog.empty:
        return pd.DataFrame()

    cfg = config or load_capacity_config()
    params = planning_params(cfg)

    df = backlog.copy()
    df["period_start_utc"] = pd.to_datetime(df["period_start_utc"], utc=True)
    df["period_start"] = _period_key(df["period_start_utc"], grain)

    rows: list[dict] = []
    for (period, tier), g in df.groupby(["period_start", "tier"], sort=True):
        inbound = float(g["tickets_inbound"].fillna(0).sum())
        solved = float(g["tickets_solved"].fillna(0).sum())
        backlog_end = float(g["backlog_end"].fillna(0).max())
        agent_hours = float(g["hc"].fillna(0).sum())
        ticket_cap = float(g["hrly_ticket_capacity"].fillna(0).sum())
        peak_scheduled = float(g["hc"].fillna(0).max())
        hours = int(len(g))

        working_days = _working_days_in_period(period, grain, params)
        tpd = tickets_per_agent_day(str(tier), cfg)
        tpw = tickets_per_agent_week(str(tier), cfg)

        required = required_hc_for_volume(inbound, str(tier), working_days=working_days, config=cfg)
        # FTE on roster/schedule: convert agent-hours → HC
        scheduled = (
            agent_hours / (params["shift_hours"] * working_days) if working_days > 0 else 0.0
        )
        gap = scheduled - required

        rows.append(
            {
                "period_start": period,
                "tier": tier,
                "period_grain": grain,
                "tickets_inbound": inbound,
                "tickets_solved": solved,
                "backlog_end": backlog_end,
                "working_days": working_days,
                "tickets_per_agent_day": tpd,
                "tickets_per_agent_week": tpw,
                "agent_hours_scheduled": agent_hours,
                "hrly_ticket_capacity": ticket_cap,
                "scheduled_hc": scheduled,
                "scheduled_hc_peak": peak_scheduled,
                "required_hc": required,
                "hc_gap": gap,
                # Aliases kept for dashboard compatibility during transition
                "scheduled_hc_avg": scheduled,
                "required_hc_avg": required,
                "hc_gap_avg": gap,
                "required_hc_peak": required,
                "fte_gap": gap,
                "hours": hours,
                "coverage_pct": (scheduled / required) if required > 0 else np.nan,
                "utilization_pct": (solved / ticket_cap) if ticket_cap > 0 else np.nan,
                "solve_rate": (solved / inbound) if inbound > 0 else np.nan,
                "target_utilization": params["target_utilization"],
                "solve_goal": params["solve_goal"],
                "sla_business_days": params["sla_business_days"],
                "staffing_status": (
                    "under" if gap < -0.25 else ("over" if gap > 0.25 else "balanced")
                ),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["period_start", "tier"]).reset_index(drop=True)


def build_hc_planning_marts(
    backlog: pd.DataFrame,
    *,
    target_utilization: float | None = None,
    clear_horizon_hours: float | None = None,  # unused; kept for call-site compat
    config: dict | None = None,
) -> dict[str, pd.DataFrame]:
    """Build day/week/month/quarter/year HC planning marts from backlog_hourly."""
    cfg = dict(config or load_capacity_config())
    if target_utilization is not None:
        cfg["planning_target_utilization"] = float(target_utilization)

    return {
        grain: build_hc_planning_grain(backlog, grain, config=cfg)
        for grain in ("day", "week", "month", "quarter", "year")
    }


def save_hc_planning_marts(
    marts: dict[str, pd.DataFrame],
    output_dir: Path,
) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for grain, df in marts.items():
        if df is None or df.empty:
            continue
        path = output_dir / f"mart_hc_planning_{grain}.parquet"
        df.to_parquet(path, index=False)
        df.to_csv(output_dir / f"mart_hc_planning_{grain}.csv", index=False)
        written.append(path)
    return written


def throughput_reference_table(config: dict | None = None) -> pd.DataFrame:
    """Per-tier throughput assumptions for dashboards / docs."""
    cfg = config or load_capacity_config()
    params = planning_params(cfg)
    rows = []
    for tier in TIERS:
        tpd = tickets_per_agent_day(tier, cfg)
        rows.append(
            {
                "tier": tier,
                "productive_minutes_day": productive_minutes_per_day(tier, cfg),
                "p50_handle_minutes": p50_handle_minutes(tier, cfg),
                "tickets_per_agent_day": round(tpd, 2),
                "tickets_per_agent_week": round(tickets_per_agent_week(tier, cfg), 2),
                "working_days_per_week": params["working_days_per_week"],
                "target_utilization": params["target_utilization"],
            }
        )
    return pd.DataFrame(rows)
