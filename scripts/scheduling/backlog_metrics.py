"""
Hourly backlog dataset — volume, completions, and queue depth by tier.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scheduling.hourly_schedule import LA_TZ, TIERS, format_period_la

COMPLETION_COLS = ("solved_at", "sim_solved_at")


def _solved_col(df: pd.DataFrame) -> str | None:
    for c in COMPLETION_COLS:
        if c in df.columns:
            return c
    return None


def build_hourly_backlog(
    tickets_df: pd.DataFrame,
    staffing: pd.DataFrame | None = None,
    *,
    end_utc: datetime | None = None,
) -> pd.DataFrame:
    """Hourly backlog mart keyed by LA period and tier."""
    if tickets_df.empty:
        return pd.DataFrame()

    t = tickets_df.copy()
    t["created_at"] = pd.to_datetime(t["created_at"], utc=True)
    solved_col = _solved_col(t)
    if solved_col:
        t[solved_col] = pd.to_datetime(t[solved_col], utc=True)
    has_assignee = "assignee_id" in t.columns

    end_ts = pd.Timestamp(end_utc or datetime.now(timezone.utc)).floor("h")
    start = t["created_at"].min().floor("h")
    hours_utc = pd.date_range(start, end_ts, freq="h", tz="UTC")

    cap_lookup: dict[tuple[str, str], float] = {}
    hc_lookup: dict[tuple[str, str], int] = {}
    if staffing is not None and not staffing.empty:
        st = staffing.copy()
        st["period_start_utc"] = pd.to_datetime(st["period_start_utc"], utc=True)
        for row in st.itertuples(index=False):
            key = (pd.Timestamp(row.period_start_utc).isoformat(), row.tier)
            cap_lookup[key] = float(row.hrly_ticket_capacity)
            hc_lookup[key] = int(row.hc)

    rows: list[dict] = []
    for tier in TIERS:
        tier_t = t[t["support_group"] == tier]
        created_hourly = tier_t.groupby(tier_t["created_at"].dt.floor("h")).size()

        if solved_col:
            solved_t = tier_t[tier_t[solved_col].notna()]
            solved_hourly = solved_t.groupby(solved_t[solved_col].dt.floor("h")).size()
        else:
            solved_hourly = pd.Series(dtype=int)

        # Unassigned = created by hour-end with no assignee (FIFO queue / open)
        if has_assignee:
            unassigned_created = tier_t[tier_t["assignee_id"].isna()]["created_at"]
        elif solved_col:
            unassigned_created = tier_t[tier_t[solved_col].isna()]["created_at"]
        else:
            unassigned_created = tier_t["created_at"]
        unassigned_created = pd.to_datetime(unassigned_created, utc=True)

        cum_created = 0
        cum_solved = 0
        for hour_utc in hours_utc:
            inbound = int(created_hourly.get(hour_utc, 0))
            solved_h = int(solved_hourly.get(hour_utc, 0))
            cum_created += inbound
            cum_solved += solved_h
            backlog = cum_created - cum_solved
            hour_end = hour_utc + pd.Timedelta(hours=1)
            unassigned = int((unassigned_created < hour_end).sum())

            hour_la = hour_utc.astimezone(LA_TZ)
            key = (hour_utc.isoformat(), tier)
            rows.append(
                {
                    "period_la": format_period_la(hour_la.to_pydatetime()),
                    "period_start_utc": hour_utc,
                    "tier": tier,
                    "tickets_inbound": inbound,
                    "tickets_solved": solved_h,
                    "backlog_end": int(backlog),
                    "backlog_unassigned": unassigned,
                    "hc": hc_lookup.get(key, 0),
                    "hrly_ticket_capacity": cap_lookup.get(key, 0.0),
                    "capacity_gap": round(cap_lookup.get(key, 0.0) - solved_h, 2),
                }
            )

    out = pd.DataFrame(rows)
    out["utilization_pct"] = np.where(
        out["hrly_ticket_capacity"] > 0,
        out["tickets_solved"] / out["hrly_ticket_capacity"],
        np.nan,
    )
    return out
