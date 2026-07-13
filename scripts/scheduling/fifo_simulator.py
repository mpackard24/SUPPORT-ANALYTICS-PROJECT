"""
FIFO ticket assignment simulation (optimized for large ticket volumes).

Simulates realistic completion up to the current UTC time using roster schedules,
capacity config, and productivity ramps. Tickets that cannot be completed by the
simulation horizon remain open with empty completion fields.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from scheduling.capacity_config import load_capacity_config
from scheduling.schedule_model import (
    AgentSchedule,
    load_agent_schedules,
    next_shift_start_utc,
    schedule_work_completion,
)


@dataclass
class AssignmentEvent:
    ticket_id: int
    support_group: str
    region: str
    agent_id: int
    agent_name: str
    created_at: datetime
    queue_entered_at: datetime
    work_started_at: datetime
    solved_at: datetime | None
    queue_wait_minutes: float
    handle_minutes: float
    first_response_hours: float
    resolution_hours: float | None
    completed: bool


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def simulation_now() -> datetime:
    return datetime.now(timezone.utc)


def sample_handle_minutes(support_group: str, rng: np.random.Generator, cfg: dict) -> float:
    p50 = float(cfg["solve_time_p50_minutes"][support_group])
    sigma = float(cfg.get("solve_time_lognormal_sigma", 0.45))
    cap = float(cfg.get("solve_time_max_minutes", 240))
    return float(min(cap, rng.lognormal(np.log(p50), sigma)))


def _synthesize_post_solve(rng: np.random.Generator) -> tuple[int, int, bool]:
    reopen_count = int(rng.choice([0, 1, 2, 3], p=[0.82, 0.12, 0.04, 0.02]))
    satisfaction = int(rng.choice([1, 2, 3, 4, 5], p=[0.04, 0.06, 0.12, 0.33, 0.45]))
    return satisfaction, reopen_count, reopen_count > 0


def _agent_can_start(agent: AgentSchedule, not_before: datetime) -> datetime | None:
    if not agent.is_employed(not_before):
        return next_shift_start_utc(agent, not_before)

    start = not_before
    if agent.next_available_utc is not None:
        start = max(start, _utc(agent.next_available_utc))

    if not agent.is_on_shift(start):
        start = next_shift_start_utc(agent, start)
        if start is None:
            return None

    for _ in range(60):
        if start is None:
            return None
        if agent.productive_remaining(agent.local_date(start)) > 0:
            return start
        start = next_shift_start_utc(agent, start + timedelta(days=1))
    return start


def _assign_one(
    team: str,
    team_agents: dict[str, list[AgentSchedule]],
    team_queues: dict[str, deque[int]],
    ticket_meta: dict[int, dict[str, Any]],
    events: list[AssignmentEvent],
    rng: np.random.Generator,
    cfg: dict,
    sim_end: datetime,
) -> bool:
    if not team_queues[team]:
        return False

    agents = team_agents.get(team, [])
    if not agents:
        return False

    head_id = team_queues[team][0]
    meta = ticket_meta[head_id]
    not_before = meta["queue_entered_at"]

    best_agent: AgentSchedule | None = None
    best_start: datetime | None = None
    for agent in agents:
        if not agent.is_employed(not_before):
            continue
        start = _agent_can_start(agent, not_before)
        if start is None:
            continue
        if best_start is None or start < best_start:
            best_agent = agent
            best_start = start

    if best_agent is None or best_start is None:
        return False

    work_start = _utc(best_start)
    # Cannot start after the simulation horizon
    if work_start > sim_end:
        return False

    handle_min = sample_handle_minutes(team, rng, cfg)
    solved_at = schedule_work_completion(best_agent, work_start, handle_min)
    completed = solved_at <= sim_end

    best_agent.next_available_utc = solved_at
    best_agent.tickets_handled += 1

    created = meta["created_at"]
    queue_wait = (work_start - created).total_seconds() / 60.0

    auto_ack_pct = float(cfg.get("first_response_auto_ack_pct", 0.08))
    auto_ack_max = float(cfg.get("first_response_auto_ack_max_minutes", 3))
    if rng.random() < auto_ack_pct:
        first_response_hours = float(rng.uniform(0.5, auto_ack_max)) / 60.0
    else:
        first_response_hours = max(0.0, (work_start - created).total_seconds() / 3600.0)

    resolution_hours = (
        (solved_at - created).total_seconds() / 3600.0 if completed else None
    )

    events.append(
        AssignmentEvent(
            ticket_id=head_id,
            support_group=team,
            region=best_agent.region,
            agent_id=best_agent.agent_id,
            agent_name=best_agent.name,
            created_at=created,
            queue_entered_at=meta["queue_entered_at"],
            work_started_at=work_start,
            solved_at=solved_at if completed else None,
            queue_wait_minutes=queue_wait,
            handle_minutes=handle_min,
            first_response_hours=first_response_hours,
            resolution_hours=resolution_hours,
            completed=completed,
        )
    )
    team_queues[team].popleft()
    return True


def simulate_fifo(
    tickets_df: pd.DataFrame,
    roster: list[dict],
    *,
    schedules_path=None,
    config: dict | None = None,
    seed: int = 42,
    simulation_end: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[AgentSchedule]]:
    cfg = config or load_capacity_config()
    rng = np.random.default_rng(seed)
    sim_end = _utc(simulation_end or simulation_now())

    agents = load_agent_schedules(roster, schedules_path=schedules_path, config=cfg)
    for a in agents:
        a.next_available_utc = None
        a.productive_used_by_date = {}
        a.tickets_handled = 0

    team_agents: dict[str, list[AgentSchedule]] = defaultdict(list)
    for a in agents:
        team_agents[a.support_group].append(a)

    df = tickets_df.copy()
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df[df["created_at"] <= sim_end].sort_values("created_at").reset_index(drop=True)

    team_queues: dict[str, deque[int]] = defaultdict(deque)
    ticket_meta: dict[int, dict[str, Any]] = {}
    events: list[AssignmentEvent] = []

    for row in df.itertuples(index=False):
        tid = int(row.ticket_id)
        team = str(row.support_group)
        created = _utc(row.created_at.to_pydatetime())
        if created > sim_end:
            continue
        ticket_meta[tid] = {
            "support_group": team,
            "created_at": created,
            "queue_entered_at": created,
        }
        team_queues[team].append(tid)

        while _assign_one(team, team_agents, team_queues, ticket_meta, events, rng, cfg, sim_end):
            pass

    # Final drain: keep assigning any remaining queue work that can still start
    progressed = True
    while progressed:
        progressed = False
        for team in list(team_queues.keys()):
            while _assign_one(team, team_agents, team_queues, ticket_meta, events, rng, cfg, sim_end):
                progressed = True

    events_df = pd.DataFrame([e.__dict__ for e in events])

    sim = df.copy()
    if not events_df.empty:
        ev = events_df.rename(
            columns={
                "agent_id": "assignee_id",
                "agent_name": "assignee_name",
                "region": "assignee_region",
                "first_response_hours": "first_response_time_hours",
                "resolution_hours": "resolution_time_hours",
            }
        )
        completed_mask = ev["completed"].fillna(False).astype(bool)
        ev["solved_at"] = pd.to_datetime(ev["solved_at"], utc=True)
        ev["work_started_at"] = pd.to_datetime(ev["work_started_at"], utc=True)
        ev["updated_at"] = pd.Series(pd.NaT, index=ev.index, dtype="datetime64[ns, UTC]")
        n_done = int(completed_mask.sum())
        if n_done:
            ev.loc[completed_mask, "updated_at"] = (
                ev.loc[completed_mask, "solved_at"]
                + pd.to_timedelta(rng.integers(15, 120, size=n_done), unit="m")
            )
        ratings = [_synthesize_post_solve(rng) for _ in range(len(ev))]
        ev["satisfaction_rating"] = [
            r[0] if done else pd.NA for r, done in zip(ratings, completed_mask)
        ]
        ev["reopen_count"] = [r[1] if done else 0 for r, done in zip(ratings, completed_mask)]
        ev["reopened"] = [r[2] if done else False for r, done in zip(ratings, completed_mask)]
        ev["status"] = np.where(completed_mask, "solved", "open")
        # Assigned (work started) even when still open at horizon — not unassigned backlog
        ev["in_backlog"] = False

        completion = ev[
            [
                "ticket_id",
                "assignee_id",
                "assignee_name",
                "assignee_region",
                "work_started_at",
                "solved_at",
                "updated_at",
                "queue_wait_minutes",
                "handle_minutes",
                "first_response_time_hours",
                "resolution_time_hours",
                "status",
                "in_backlog",
                "satisfaction_rating",
                "reopen_count",
                "reopened",
            ]
        ]
        sim = sim.merge(completion, on="ticket_id", how="left", suffixes=("", "_sim"))
        for col in completion.columns:
            if col == "ticket_id":
                continue
            sim_col = f"{col}_sim"
            if sim_col in sim.columns:
                sim[col] = sim[sim_col].combine_first(sim.get(col))
                sim.drop(columns=[sim_col], inplace=True)

    for col, default in (
        ("status", "open"),
        ("in_backlog", True),
        ("reopened", False),
        ("reopen_count", 0),
    ):
        if col not in sim.columns:
            sim[col] = default
        else:
            sim[col] = sim[col].fillna(default)

    for col in (
        "assignee_id",
        "assignee_name",
        "assignee_region",
        "work_started_at",
        "solved_at",
        "updated_at",
        "queue_wait_minutes",
        "handle_minutes",
        "first_response_time_hours",
        "resolution_time_hours",
        "satisfaction_rating",
    ):
        if col not in sim.columns:
            sim[col] = np.nan

    sim["status"] = sim["status"].fillna("open")
    sim["in_backlog"] = sim["in_backlog"].fillna(True)
    sim["reopened"] = sim["reopened"].fillna(False)
    sim["reopen_count"] = sim["reopen_count"].fillna(0)

    # Legacy aliases for dashboards not yet migrated
    if "assignee_id" in sim.columns:
        sim["sim_assignee_id"] = sim["assignee_id"]
        sim["sim_assignee_name"] = sim["assignee_name"]
        sim["sim_assignee_region"] = sim["assignee_region"]
        sim["sim_work_started_at"] = sim["work_started_at"]
        sim["sim_solved_at"] = sim["solved_at"]
        sim["sim_queue_wait_minutes"] = sim["queue_wait_minutes"]
        sim["sim_handle_minutes"] = sim["handle_minutes"]
        sim["sim_first_response_time_hours"] = sim["first_response_time_hours"]
        sim["sim_resolution_time_hours"] = sim["resolution_time_hours"]
        sim["sim_status"] = sim["status"]
        sim["sim_in_backlog"] = sim["in_backlog"]

    sim.attrs["simulation_end"] = sim_end
    sim.attrs["backlog_unassigned"] = sum(len(q) for q in team_queues.values())

    return sim, events_df, agents
