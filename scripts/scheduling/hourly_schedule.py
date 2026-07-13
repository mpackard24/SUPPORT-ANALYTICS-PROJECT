"""
Build hourly staffing schedule by tier in America/Los_Angeles time.

Outputs one row per (hour, tier) from first ticket hour through current hour,
including zero-capacity hours.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scheduling.capacity_config import hourly_ticket_capacity_per_agent, load_capacity_config
from scheduling.productivity_ramp import ramp_multiplier
from scheduling.schedule_model import AgentSchedule, load_agent_schedules

LA_TZ = ZoneInfo("America/Los_Angeles")
TIERS = ["Tier 1", "Tier 2", "Enterprise", "Technical Quality"]


def format_period_la(ts_la: datetime) -> str:
    """MM-DD-YY-HH in Los Angeles local time."""
    return ts_la.strftime("%m-%d-%y-%H")


def _hour_range_la(start_utc: datetime, end_utc: datetime) -> list[datetime]:
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)

    cur_la = start_utc.astimezone(LA_TZ).replace(minute=0, second=0, microsecond=0)
    end_la = end_utc.astimezone(LA_TZ).replace(minute=0, second=0, microsecond=0)
    hours: list[datetime] = []
    while cur_la <= end_la:
        hours.append(cur_la)
        cur_la += timedelta(hours=1)
    return hours


def agent_on_shift_during_la_hour(agent: AgentSchedule, hour_start_la: datetime) -> bool:
    """True if agent is on shift for any part of the LA hour bucket."""
    if hour_start_la.tzinfo is None:
        hour_start_la = hour_start_la.replace(tzinfo=LA_TZ)
    mid_utc = (hour_start_la + timedelta(minutes=30)).astimezone(timezone.utc)
    return agent.is_on_shift(mid_utc)


def build_hourly_staffing_schedule(
    agents: list[AgentSchedule],
    *,
    start_utc: datetime,
    end_utc: datetime,
    config: dict | None = None,
) -> pd.DataFrame:
    cfg = config or load_capacity_config()
    hours_la = _hour_range_la(start_utc, end_utc)

    rows: list[dict] = []
    for hour_la in hours_la:
        hour_utc = hour_la.astimezone(timezone.utc)
        for tier in TIERS:
            hc = 0
            capacity = 0.0
            tier_agents = [a for a in agents if a.support_group == tier]
            for agent in tier_agents:
                if not agent_on_shift_during_la_hour(agent, hour_la):
                    continue
                local_day = agent.local_date(hour_utc)
                ramp = ramp_multiplier(agent.support_group, agent.start_date, local_day)
                hc += 1
                capacity += hourly_ticket_capacity_per_agent(
                    tier, ramp_multiplier=ramp, config=cfg
                )
            rows.append(
                {
                    "period_la": format_period_la(hour_la),
                    "period_start_utc": hour_utc,
                    "period_start_la": hour_la.isoformat(),
                    "tier": tier,
                    "hc": hc,
                    "hrly_ticket_capacity": round(capacity, 4),
                }
            )

    return pd.DataFrame(rows)


def build_schedule_from_roster(
    roster: list[dict],
    *,
    first_ticket_utc: datetime,
    end_utc: datetime | None = None,
    schedules_path: Path | None = None,
    config: dict | None = None,
) -> pd.DataFrame:
    cfg = config or load_capacity_config()
    end = end_utc or datetime.now(timezone.utc)
    if first_ticket_utc.tzinfo is None:
        first_ticket_utc = first_ticket_utc.replace(tzinfo=timezone.utc)
    start = first_ticket_utc.replace(minute=0, second=0, microsecond=0)
    agents = load_agent_schedules(roster, schedules_path=schedules_path, config=cfg)
    return build_hourly_staffing_schedule(agents, start_utc=start, end_utc=end, config=cfg)


def save_hourly_staffing_schedule(
    df: pd.DataFrame,
    output_dir: Path,
    *,
    basename: str = "hourly_staffing_schedule",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{basename}.parquet"
    csv_path = output_dir / f"{basename}.csv"
    df.to_parquet(parquet_path, index=False)
    df.to_csv(csv_path, index=False)
    return parquet_path, csv_path
