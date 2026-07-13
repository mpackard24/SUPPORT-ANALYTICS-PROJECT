"""
Build hourly (and roll-up) capacity / utilization / backlog metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scheduling.schedule_model import AgentSchedule, iter_shift_slices_utc


def _to_offset(freq: str):
    alias = {"H": "h", "D": "D", "W": "W", "M": "ME"}.get(freq, freq)
    return pd.tseries.frequencies.to_offset(alias)


def _period_starts(series: pd.Series, freq: str) -> pd.Series:
    s = pd.to_datetime(series, utc=True)
    offset = _to_offset(freq)
    if freq in ("h", "H"):
        return s.dt.floor("h")
    return s.dt.to_period(offset).dt.start_time.dt.tz_localize("UTC")


def build_hourly_capacity(
    tickets_sim: pd.DataFrame,
    events_df: pd.DataFrame,
    agents: list[AgentSchedule],
    *,
    freq: str = "h",
) -> pd.DataFrame:
    """Period-level metrics: capacity, utilization, inbound, backlog, over/under."""
    if tickets_sim.empty:
        return pd.DataFrame()

    offset = _to_offset(freq)
    t = tickets_sim.copy()
    t["created_at"] = pd.to_datetime(t["created_at"], utc=True)
    solved_series = t["sim_solved_at"] if "sim_solved_at" in t.columns else t.get("solved_at")
    t["sim_solved_at"] = pd.to_datetime(solved_series, utc=True)

    tmin = t["created_at"].min()
    tmax = max(t["created_at"].max(), t["sim_solved_at"].max(skipna=True) or tmin)

    # Periods with activity (sparse — avoids iterating every hour in multi-year ranges)
    active: set[pd.Timestamp] = set(_period_starts(t["created_at"], freq))
    if not events_df.empty:
        ev_tmp = events_df.copy()
        ev_tmp["work_started_at"] = pd.to_datetime(ev_tmp["work_started_at"], utc=True)
        active |= set(_period_starts(ev_tmp["work_started_at"], freq))

    if not active:
        return pd.DataFrame()

    periods = sorted(active)

    # --- Scheduled capacity by team + region ---
    cap_rows: list[dict] = []
    for period_start in periods:
        period_end = period_start + offset
        ps = period_start.to_pydatetime()
        pe = period_end.to_pydatetime()
        for agent in agents:
            shift_minutes = agent.shift_duration.total_seconds() / 60.0
            if shift_minutes <= 0:
                continue
            overlap = 0.0
            for slice_start, slice_end in iter_shift_slices_utc(agent, ps, pe):
                mins = (slice_end - slice_start).total_seconds() / 60.0
                local_day = agent.local_date(slice_start)
                effective_daily = agent.effective_productive_minutes(local_day)
                rate = effective_daily / shift_minutes
                overlap += mins * rate
            if overlap > 0:
                cap_rows.append(
                    {
                        "period_start": period_start,
                        "support_group": agent.support_group,
                        "region": agent.region,
                        "agent_id": agent.agent_id,
                        "capacity_minutes": overlap,
                    }
                )

    cap_detail = pd.DataFrame(cap_rows)
    if cap_detail.empty:
        region_cap = pd.DataFrame(
            columns=["period_start", "support_group", "region", "capacity_minutes", "agents_scheduled"]
        )
    else:
        region_cap = (
            cap_detail.groupby(["period_start", "support_group", "region"], as_index=False)
            .agg(capacity_minutes=("capacity_minutes", "sum"), agents_scheduled=("agent_id", "nunique"))
        )

    # --- Utilization from assignment events ---
    util_rows: list[dict] = []
    if events_df is not None and not events_df.empty:
        ev = events_df.copy()
        ev["work_started_at"] = pd.to_datetime(ev["work_started_at"], utc=True)
        ev["solved_at"] = pd.to_datetime(ev["solved_at"], utc=True)
        for row in ev.itertuples(index=False):
            span = max(
                (row.solved_at - row.work_started_at).total_seconds() / 60.0,
                row.handle_minutes,
            )
            cur = pd.Timestamp(row.work_started_at).floor("h")
            if freq != "h":
                cur = _period_starts(pd.Series([row.work_started_at]), freq).iloc[0]
            end_ts = row.solved_at
            while cur < end_ts:
                nxt = cur + offset
                seg_start = max(cur, row.work_started_at)
                seg_end = min(nxt, end_ts)
                seg_mins = (seg_end - seg_start).total_seconds() / 60.0
                if seg_mins > 0:
                    prorated = row.handle_minutes * (seg_mins / span)
                    util_rows.append(
                        {
                            "period_start": cur,
                            "support_group": row.support_group,
                            "region": row.region,
                            "utilized_minutes": prorated,
                            "tickets_completed": 1 if seg_end >= row.solved_at else 0,
                        }
                    )
                cur = nxt

    util_detail = pd.DataFrame(util_rows)
    if util_detail.empty:
        region_util = pd.DataFrame(
            columns=["period_start", "support_group", "region", "utilized_minutes", "tickets_completed"]
        )
    else:
        region_util = (
            util_detail.groupby(["period_start", "support_group", "region"], as_index=False)
            .agg(utilized_minutes=("utilized_minutes", "sum"), tickets_completed=("tickets_completed", "sum"))
        )

    mart = region_cap.merge(region_util, on=["period_start", "support_group", "region"], how="outer")

    # Team-level inbound + backlog for active periods
    team_rows: list[dict] = []
    for period_start in periods:
        period_end = period_start + offset
        created_mask = (t["created_at"] >= period_start) & (t["created_at"] < period_end)
        inbound = t.loc[created_mask].groupby("support_group").size()
        backlog_mask = (t["created_at"] < period_end) & (
            t["sim_solved_at"].isna() | (t["sim_solved_at"] >= period_end)
        )
        backlog = t.loc[backlog_mask].groupby("support_group").size()
        groups = set(inbound.index) | set(backlog.index)
        for grp in groups:
            team_rows.append(
                {
                    "period_start": period_start,
                    "support_group": grp,
                    "region": "ALL",
                    "tickets_inbound": int(inbound.get(grp, 0)),
                    "backlog_end": int(backlog.get(grp, 0)),
                }
            )

    team_df = pd.DataFrame(team_rows)

    # Team totals for capacity/util
    if not mart.empty:
        team_cap = (
            mart.groupby(["period_start", "support_group"], as_index=False)
            .agg(
                capacity_minutes=("capacity_minutes", "sum"),
                utilized_minutes=("utilized_minutes", "sum"),
                tickets_completed=("tickets_completed", "sum"),
                agents_scheduled=("agents_scheduled", "sum"),
            )
        )
        team_cap["region"] = "ALL"
        mart = pd.concat([mart, team_cap], ignore_index=True)

    mart = mart.merge(
        team_df,
        on=["period_start", "support_group", "region"],
        how="left",
    )

    for col in (
        "capacity_minutes",
        "utilized_minutes",
        "tickets_completed",
        "tickets_inbound",
        "backlog_end",
        "agents_scheduled",
    ):
        if col in mart.columns:
            mart[col] = mart[col].fillna(0)

    mart["utilization_pct"] = np.where(
        mart["capacity_minutes"] > 0,
        mart["utilized_minutes"] / mart["capacity_minutes"],
        np.nan,
    )
    mart["capacity_delta_minutes"] = mart["utilized_minutes"] - mart["capacity_minutes"]
    mart["over_under"] = np.where(
        mart["capacity_delta_minutes"] > 5,
        "over",
        np.where(mart["capacity_delta_minutes"] < -5, "under", "balanced"),
    )
    mart["period_grain"] = freq
    return mart.sort_values(["period_start", "support_group", "region"]).reset_index(drop=True)


def rollup_mart(mart: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Roll up an hourly mart to day / week / month."""
    if mart.empty:
        return mart
    df = mart[mart["region"] != "ALL"].copy() if (mart["region"] == "ALL").any() else mart.copy()
    df["period_start"] = pd.to_datetime(df["period_start"], utc=True)
    df["rollup_start"] = _period_starts(df["period_start"], freq)

    agg = (
        df.groupby(["rollup_start", "support_group", "region"], as_index=False)
        .agg(
            capacity_minutes=("capacity_minutes", "sum"),
            utilized_minutes=("utilized_minutes", "sum"),
            tickets_completed=("tickets_completed", "sum"),
            agents_scheduled=("agents_scheduled", "max"),
        )
    )
    agg["utilization_pct"] = np.where(
        agg["capacity_minutes"] > 0,
        agg["utilized_minutes"] / agg["capacity_minutes"],
        np.nan,
    )
    agg["capacity_delta_minutes"] = agg["utilized_minutes"] - agg["capacity_minutes"]
    agg["over_under"] = np.where(
        agg["capacity_delta_minutes"] > 5,
        "over",
        np.where(agg["capacity_delta_minutes"] < -5, "under", "balanced"),
    )
    agg["period_grain"] = freq
    return agg.rename(columns={"rollup_start": "period_start"})
