#!/usr/bin/env python3
"""
Run FIFO assignment simulation and build capacity / backlog marts.

Usage (from project root):
    python scripts/run_simulation.py

Reads:
    data/generated/zendesk_dummy_tickets.csv  (intake only)
    data/processed/hourly_staffing_schedule.parquet
    data/reference/agent_roster.csv + agent_schedules.csv
    data/reference/capacity_config.yaml + productivity_ramp.yaml

Writes:
    data/processed/tickets_simulated.csv
    data/processed/simulation_events.csv
    data/processed/backlog_hourly.parquet (+ .csv)
    data/processed/mart_hourly_capacity.parquet (+ .csv)
    data/processed/mart_daily_capacity.parquet (+ .csv)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_roster import load_agent_roster, resolve_roster_path, roster_to_export_df
from scheduling.backlog_metrics import build_hourly_backlog
from scheduling.fifo_simulator import simulate_fifo, simulation_now
from scheduling.hc_planning import build_hc_planning_marts, save_hc_planning_marts
from scheduling.hourly_metrics import build_hourly_capacity, rollup_mart
from scheduling.hourly_schedule import TIERS, build_schedule_from_roster, save_hourly_staffing_schedule

PROCESSED = PROJECT_ROOT / "data" / "processed"
TICKETS_IN = PROJECT_ROOT / "data" / "generated" / "zendesk_dummy_tickets.csv"
AGENTS_OUT = PROJECT_ROOT / "data" / "generated" / "zendesk_dummy_agents.csv"
STAFFING_PATH = PROCESSED / "hourly_staffing_schedule.parquet"


def _ensure_staffing(
    roster: list[dict],
    first_ticket_utc: datetime,
    output_dir: Path,
) -> pd.DataFrame:
    """Always rebuild hourly staffing from current roster/schedules."""
    staffing = build_schedule_from_roster(roster, first_ticket_utc=first_ticket_utc)
    save_hourly_staffing_schedule(staffing, output_dir)
    return staffing


def run_simulation(
    *,
    tickets_path: Path | None = None,
    output_dir: Path | None = None,
    seed: int = 42,
    quiet: bool = False,
    simulation_end: datetime | None = None,
    force_rebuild_staffing: bool = False,  # kept for CLI compat; staffing always rebuilds
) -> dict[str, Any]:
    tickets_in = Path(tickets_path or TICKETS_IN)
    out_dir = Path(output_dir or PROCESSED)
    sim_end = simulation_end or simulation_now()

    if not tickets_in.exists():
        raise FileNotFoundError(f"Run generate_tickets first: {tickets_in}")

    if not quiet:
        print("=" * 70)
        print("Support Capacity Simulation — FIFO assignment")
        print("=" * 70)

    roster_path = resolve_roster_path()
    roster = load_agent_roster(active_only=False)
    employed = [a for a in roster if a.get("start_date")]

    # Fail fast if schedules are missing for roster agents (common edit mistake)
    from scheduling.schedule_model import resolve_schedules_path

    schedules_path = resolve_schedules_path()
    sched_ids = set(
        int(x)
        for x in pd.read_csv(schedules_path, dtype={"agent_id": "Int64"})["agent_id"].dropna()
    )
    missing_sched = sorted(
        int(a["id"]) for a in roster if a.get("start_date") and int(a["id"]) not in sched_ids
    )
    if missing_sched:
        raise ValueError(
            f"Roster agents missing from {schedules_path.name}: {missing_sched}. "
            "Add matching schedule rows before simulating."
        )

    if not quiet:
        print(f"Roster       : {roster_path} ({len(employed)} agents with start dates)")
        print(f"Schedules    : {schedules_path}")
        for tier in TIERS:
            n = sum(1 for a in roster if a.get("group") == tier)
            print(f"  {tier:<20}: {n} roster rows")
        print(f"Sim horizon  : through {sim_end.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    tickets = pd.read_csv(tickets_in, low_memory=False)
    tickets["created_at"] = pd.to_datetime(tickets["created_at"], utc=True)
    if not quiet:
        print(f"Tickets      : {len(tickets):,} intake")

    first_ticket = tickets["created_at"].min().to_pydatetime()
    staffing = _ensure_staffing(roster, first_ticket, out_dir)

    # Keep modeling-layer agent snapshot in sync with roster (even without --generate)
    AGENTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    roster_to_export_df(roster).to_csv(AGENTS_OUT, index=False)

    if not quiet:
        print("Staffing     : max HC / hour by tier")
        for tier in TIERS:
            tier_hc = staffing[(staffing["tier"] == tier) & (staffing["hc"] > 0)]["hc"].max()
            print(f"  {tier:<20}: {int(tier_hc) if pd.notna(tier_hc) else 0}")
        print(f"Agents export: {AGENTS_OUT.relative_to(PROJECT_ROOT)} ({len(roster)} rows)")

    t0 = time.perf_counter()
    sim, events, agents = simulate_fifo(tickets, roster, seed=seed, simulation_end=sim_end)
    elapsed = time.perf_counter() - t0

    solved = int((sim["status"] == "solved").sum())
    backlog = int(sim["in_backlog"].sum())
    unassigned = int(sim.attrs.get("backlog_unassigned", 0))

    if not quiet:
        print(f"Simulated    : {elapsed:.1f}s")
        print(f"  Assigned   : {len(events):,}")
        print(f"  Solved     : {solved:,}")
        print(f"  Backlog    : {backlog:,} (queue remaining: {unassigned})")

    out_dir.mkdir(parents=True, exist_ok=True)

    sim_path = out_dir / "tickets_simulated.csv"
    sim.to_csv(sim_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

    events_path = out_dir / "simulation_events.csv"
    events.to_csv(events_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

    backlog_df = build_hourly_backlog(sim, staffing, end_utc=sim_end)
    backlog_path = out_dir / "backlog_hourly.parquet"
    backlog_df.to_parquet(backlog_path, index=False)
    backlog_df.to_csv(out_dir / "backlog_hourly.csv", index=False)

    hc_marts = build_hc_planning_marts(backlog_df)
    hc_paths = save_hc_planning_marts(hc_marts, out_dir)

    hourly = build_hourly_capacity(sim, events, agents, freq="h")
    daily = rollup_mart(hourly, "D")

    hourly_path = out_dir / "mart_hourly_capacity.parquet"
    daily_path = out_dir / "mart_daily_capacity.parquet"
    hourly.to_parquet(hourly_path, index=False)
    hourly.to_csv(out_dir / "mart_hourly_capacity.csv", index=False)
    daily.to_parquet(daily_path, index=False)
    daily.to_csv(out_dir / "mart_daily_capacity.csv", index=False)

    if not quiet:
        print(f"\nOutputs → {out_dir}/")
        print("  tickets_simulated.csv")
        print("  simulation_events.csv")
        print("  backlog_hourly.parquet")
        for p in hc_paths:
            print(f"  {p.name}")
        print("  mart_hourly_capacity.parquet")
        print("  mart_daily_capacity.parquet")
        print("=" * 70)

    return {
        "tickets": len(tickets),
        "assigned": len(events),
        "solved": solved,
        "backlog": backlog,
        "elapsed_s": elapsed,
        "sim_path": sim_path,
        "events_path": events_path,
        "backlog_path": backlog_path,
        "hourly_path": hourly_path,
        "daily_path": daily_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FIFO capacity simulation")
    parser.add_argument(
        "--force-rebuild-staffing",
        action="store_true",
        help="Rebuild hourly_staffing_schedule from roster even if file exists",
    )
    args = parser.parse_args()
    run_simulation(force_rebuild_staffing=args.force_rebuild_staffing)


if __name__ == "__main__":
    main()
