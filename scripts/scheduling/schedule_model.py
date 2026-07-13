"""
Specialist schedule model with employment windows and productivity ramps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd

from scheduling.capacity_config import SHIFT_PATTERNS, load_capacity_config, productive_minutes_per_day
from scheduling.productivity_ramp import ramp_multiplier

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEDULES = PROJECT_ROOT / "data" / "reference" / "agent_schedules.csv"
FIVETRAN_SCHEDULES = PROJECT_ROOT / "data" / "raw" / "agent_schedules.csv"

# Fallback when roster has no explicit row in agent_schedules.csv
REGION_DEFAULT_SCHEDULE: dict[str, dict[str, str]] = {
    "EMEA": {"timezone": "Europe/Dublin", "shift_pattern": "M-F", "shift_start_local": "09:00"},
    "US": {"timezone": "America/New_York", "shift_pattern": "M-F", "shift_start_local": "09:00"},
    "APAC": {"timezone": "Asia/Singapore", "shift_pattern": "M-F", "shift_start_local": "09:00"},
}


@dataclass
class AgentSchedule:
    agent_id: int
    name: str
    support_group: str
    region: str
    tz: ZoneInfo
    shift_pattern: str
    shift_weekdays: frozenset[int]
    shift_start: time
    shift_duration: timedelta
    base_productive_minutes_daily: float
    start_date: date
    end_date: date | None

    next_available_utc: datetime | None = None
    productive_used_by_date: dict[date, float] = field(default_factory=dict)
    tickets_handled: int = 0

    def local_date(self, dt_utc: datetime) -> date:
        return dt_utc.astimezone(self.tz).date()

    def is_employed(self, dt_utc: datetime) -> bool:
        d = self.local_date(dt_utc)
        if self.start_date is not None and d < self.start_date:
            return False
        if self.end_date is not None and d > self.end_date:
            return False
        return True

    def effective_productive_minutes(self, local_day: date) -> float:
        if self.start_date is not None and local_day < self.start_date:
            return 0.0
        if self.end_date is not None and local_day > self.end_date:
            return 0.0
        mult = ramp_multiplier(self.support_group, self.start_date, local_day)
        return self.base_productive_minutes_daily * mult

    def is_on_shift(self, dt_utc: datetime) -> bool:
        if not self.is_employed(dt_utc):
            return False
        local = dt_utc.astimezone(self.tz)
        if local.weekday() not in self.shift_weekdays:
            return False
        start = local.replace(
            hour=self.shift_start.hour,
            minute=self.shift_start.minute,
            second=0,
            microsecond=0,
        )
        end = start + self.shift_duration
        return start <= local < end

    def productive_remaining(self, local_day: date) -> float:
        budget = self.effective_productive_minutes(local_day)
        used = self.productive_used_by_date.get(local_day, 0.0)
        return max(0.0, budget - used)

    def consume_productive(self, local_day: date, minutes: float) -> None:
        self.productive_used_by_date[local_day] = (
            self.productive_used_by_date.get(local_day, 0.0) + minutes
        )


def resolve_schedules_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    if env := os.environ.get("AGENT_SCHEDULES_PATH"):
        return Path(env)
    if FIVETRAN_SCHEDULES.exists():
        return FIVETRAN_SCHEDULES
    return DEFAULT_SCHEDULES


def _parse_shift_start(value: str) -> time:
    parts = str(value).strip().split(":")
    return time(int(parts[0]), int(parts[1]))


def _parse_date(value: str | None) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", ""}:
        return None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date()


def load_agent_schedules(
    roster: list[dict],
    schedules_path: Path | None = None,
    config: dict | None = None,
) -> list[AgentSchedule]:
    """
    Build schedules for roster agents with employment windows.

    Skips agents marked active=false with no end_date (not staffed for work).
    Agents with end_date remain for historical coverage through that day.
    """
    cfg = config or load_capacity_config()
    path = resolve_schedules_path(schedules_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent schedules not found: {path}")

    sched_df = pd.read_csv(path, dtype={"agent_id": "Int64"})
    sched_df.columns = [c.strip().lower() for c in sched_df.columns]
    sched_map = {int(r.agent_id): r for r in sched_df.itertuples(index=False)}

    shift_hours = float(cfg.get("shift_duration_hours", 9))
    duration = timedelta(hours=shift_hours)

    agents: list[AgentSchedule] = []
    for r in roster:
        # Inactive with no offboard date → excluded from capacity & assignment
        if not r.get("active", True) and not r.get("end_date"):
            continue

        aid = int(r["id"])
        if aid in sched_map:
            row = sched_map[aid]
            pattern = str(row.shift_pattern).strip().upper()
            region = str(getattr(row, "region", r.get("region", "EMEA"))).strip().upper()
            tz_name = str(row.timezone).strip()
            shift_start = _parse_shift_start(row.shift_start_local)
        else:
            region = str(r.get("region", "EMEA")).strip().upper()
            defaults = REGION_DEFAULT_SCHEDULE.get(region, REGION_DEFAULT_SCHEDULE["EMEA"])
            pattern = defaults["shift_pattern"]
            tz_name = defaults["timezone"]
            shift_start = _parse_shift_start(defaults["shift_start_local"])

        if pattern not in SHIFT_PATTERNS:
            raise ValueError(f"Unknown shift pattern '{pattern}' for agent {aid}")

        group = str(r["group"])
        start_date = _parse_date(r["start_date"])
        end_date = _parse_date(r.get("end_date"))
        if start_date is None:
            continue

        agents.append(
            AgentSchedule(
                agent_id=aid,
                name=str(r["name"]),
                support_group=group,
                region=region,
                tz=ZoneInfo(tz_name),
                shift_pattern=pattern,
                shift_weekdays=SHIFT_PATTERNS[pattern],
                shift_start=shift_start,
                shift_duration=duration,
                base_productive_minutes_daily=productive_minutes_per_day(group, cfg),
                start_date=start_date,
                end_date=end_date,
            )
        )
    return agents


def next_shift_start_utc(agent: AgentSchedule, after_utc: datetime) -> datetime | None:
    """Next shift start at or after after_utc; None if agent is offboarded or no shift in range."""
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)

    local = after_utc.astimezone(agent.tz)
    if agent.end_date is not None and local.date() > agent.end_date:
        return None

    search_from = max(local.date(), agent.start_date)
    if agent.end_date is not None:
        search_until = agent.end_date
    else:
        search_until = search_from + timedelta(days=90)

    day = search_from
    while day <= search_until:
        if day.weekday() in agent.shift_weekdays:
            start_local = datetime.combine(day, agent.shift_start, tzinfo=agent.tz)
            end_local = start_local + agent.shift_duration
            if day == local.date():
                if local >= end_local:
                    day += timedelta(days=1)
                    continue
                if local > start_local:
                    return local.astimezone(timezone.utc)
            return start_local.astimezone(timezone.utc)
        day += timedelta(days=1)
    return None


def iter_shift_slices_utc(
    agent: AgentSchedule, start_utc: datetime, end_utc: datetime
) -> Iterator[tuple[datetime, datetime]]:
    cursor = start_utc
    if cursor.tzinfo is None:
        cursor = cursor.replace(tzinfo=timezone.utc)

    while cursor < end_utc:
        if agent.end_date is not None and agent.local_date(cursor) > agent.end_date:
            break
        if agent.local_date(cursor) < agent.start_date:
            nxt = next_shift_start_utc(agent, cursor)
            if nxt is None or nxt >= end_utc:
                break
            cursor = nxt
            continue
        if not agent.is_on_shift(cursor):
            nxt = next_shift_start_utc(agent, cursor + timedelta(minutes=1))
            if nxt is None or nxt >= end_utc:
                break
            cursor = nxt
            continue

        local = cursor.astimezone(agent.tz)
        day = local.date()
        shift_start = datetime.combine(day, agent.shift_start, tzinfo=agent.tz)
        shift_end = shift_start + agent.shift_duration
        slice_start = max(cursor, shift_start.astimezone(timezone.utc))
        slice_end = min(end_utc, shift_end.astimezone(timezone.utc))
        if slice_end > slice_start:
            yield slice_start, slice_end
        cursor = slice_end


def schedule_work_completion(
    agent: AgentSchedule,
    work_start_utc: datetime,
    handle_minutes: float,
) -> datetime:
    if work_start_utc.tzinfo is None:
        work_start_utc = work_start_utc.replace(tzinfo=timezone.utc)

    remaining = handle_minutes
    cursor = work_start_utc

    if not agent.is_on_shift(cursor):
        nxt = next_shift_start_utc(agent, cursor)
        if nxt is None:
            return cursor
        cursor = nxt

    safety = 0
    while remaining > 0 and safety < 5000:
        safety += 1
        if not agent.is_employed(cursor):
            nxt = next_shift_start_utc(agent, cursor + timedelta(days=1))
            if nxt is None:
                break
            cursor = nxt
            continue

        local_day = agent.local_date(cursor)
        prod_left = agent.productive_remaining(local_day)

        if prod_left <= 0:
            nxt = next_shift_start_utc(agent, cursor + timedelta(days=1))
            if nxt is None:
                break
            cursor = nxt
            continue

        if not agent.is_on_shift(cursor):
            nxt = next_shift_start_utc(agent, cursor)
            if nxt is None:
                break
            cursor = nxt
            continue

        local = cursor.astimezone(agent.tz)
        shift_start = datetime.combine(local.date(), agent.shift_start, tzinfo=agent.tz)
        shift_end = shift_start + agent.shift_duration
        slice_end = min(
            shift_end.astimezone(timezone.utc),
            cursor + timedelta(minutes=prod_left),
        )
        slice_minutes = (slice_end - cursor).total_seconds() / 60.0
        if slice_minutes <= 0:
            nxt = next_shift_start_utc(agent, cursor + timedelta(minutes=1))
            if nxt is None:
                break
            cursor = nxt
            continue

        use = min(remaining, slice_minutes, prod_left)
        if use <= 0:
            nxt = next_shift_start_utc(agent, cursor + timedelta(minutes=15))
            if nxt is None:
                break
            cursor = nxt
            continue

        agent.consume_productive(local_day, use)
        remaining -= use
        cursor = cursor + timedelta(minutes=use)

    return cursor
