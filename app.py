#!/usr/bin/env python3
"""
Support Analytics — Streamlit Dashboard
=======================================
User Operations dashboard over synthetic Zendesk-style data transformed with dbt + DuckDB.

Run from project root (after `python scripts/run_pipeline.py --generate`):
    streamlit run app.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
sys.path.insert(0, str(PROJECT_ROOT))

from support_analytics import PRIORITY_WEIGHTS, processed_data_version
from theme import (
    ACCENT_DEFLECTION,
    ACCENT_SPECIALIST,
    ACCENT_VOLUME,
    AMBER,
    BORDER,
    CYAN,
    FONT_DISPLAY,
    GREEN,
    PINK,
    PURPLE,
    PURPLE_SOFT,
    SURFACE,
    TEAM_COLORS,
    TEXT,
    TEXT_DIM,
    TEXT_MUTED,
    apply_theme,
    filter_bar,
    page_header,
    section,
    style_fig as theme_style_fig,
    tab_lead,
)

SUPPORT_GROUPS = ["Tier 1", "Tier 2", "Enterprise", "Technical Quality"]
PRIORITIES = ["low", "normal", "high", "urgent"]

COLOR_SEQUENCE = [ACCENT_VOLUME, ACCENT_DEFLECTION, ACCENT_SPECIALIST, AMBER, CYAN, "#F9A8D4"]
GROUP_COLORS = TEAM_COLORS


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading processed data…")
def load_tickets(_data_version: str) -> pd.DataFrame:
    """Load ticket-level metrics and agent fields from the modeling layer."""
    metrics_path = PROCESSED / "int_ticket_metrics.parquet"
    # Prefer slim export (committed for Streamlit Cloud); fall back to full staging.
    staging_path = PROCESSED / "stg_tickets_app.parquet"
    if not staging_path.exists():
        staging_path = PROCESSED / "stg_tickets.parquet"

    if not metrics_path.exists() or not staging_path.exists():
        return pd.DataFrame()

    metrics = pd.read_parquet(metrics_path)
    staging = pd.read_parquet(
        staging_path,
        columns=["ticket_id", "assignee_id", "assignee_name", "channel", "language"],
    )
    df = metrics.merge(staging, on="ticket_id", how="left")

    for col in ("created_at", "created_date", "created_week", "created_month"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])

    return df


@st.cache_data(show_spinner=False)
def load_priority_mart(_data_version: str) -> pd.DataFrame:
    """Pre-computed category priority scores from the modeling layer."""
    path = PROCESSED / "mart_priority_scoring.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# Filtering & metrics helpers
# ---------------------------------------------------------------------------
def apply_filters(
    df: pd.DataFrame,
    start_date,
    end_date,
    groups: list[str],
    priorities: list[str],
    enterprise_only: bool,
) -> pd.DataFrame:
    """Apply global sidebar filters to ticket-level data."""
    if df.empty:
        return df

    mask = (
        (df["created_date"] >= pd.Timestamp(start_date))
        & (df["created_date"] <= pd.Timestamp(end_date))
        & (df["support_group"].isin(groups))
        & (df["priority"].isin(priorities))
    )
    if enterprise_only:
        mask &= df["is_enterprise"]

    return df.loc[mask].copy()


def kpi_block(df: pd.DataFrame) -> dict:
    """Compute headline KPIs for the filtered ticket set."""
    if df.empty:
        return {
            "total": 0,
            "pct_solved": 0.0,
            "avg_frt": None,
            "avg_res": None,
            "avg_csat": None,
            "pct_fr_sla": None,
            "pct_res_sla": None,
        }

    solved = df["is_solved"].fillna(False)
    fr_eligible = df["is_fr_sla_eligible"].fillna(False)
    res_eligible = df["is_res_sla_eligible"].fillna(False)

    return {
        "total": int(len(df)),
        "pct_solved": float(solved.mean()),
        "avg_frt": float(df["first_response_time_hours"].mean()),
        "avg_res": float(df.loc[solved, "resolution_time_hours"].mean())
        if solved.any()
        else None,
        "avg_csat": float(df.loc[df["has_csat"], "satisfaction_rating"].mean())
        if df["has_csat"].any()
        else None,
        "pct_fr_sla": float(df.loc[fr_eligible, "met_first_response_sla"].mean())
        if fr_eligible.any()
        else None,
        "pct_res_sla": float(df.loc[res_eligible, "met_resolution_sla"].mean())
        if res_eligible.any()
        else None,
    }


VOLUME_GRAIN_CONFIG = {
    "Daily": {
        "period": "D",
        "rolling": 7,
        "bar_name": "Daily volume",
        "rolling_name": "7-day rolling avg",
        "title": "Daily ticket volume",
    },
    "Weekly": {
        "period": "W-MON",
        "rolling": 4,
        "bar_name": "Weekly volume",
        "rolling_name": "4-week rolling avg",
        "title": "Weekly ticket volume",
    },
    "Monthly": {
        "period": "M",
        "rolling": 3,
        "bar_name": "Monthly volume",
        "rolling_name": "3-month rolling avg",
        "title": "Monthly ticket volume",
    },
    "Quarterly": {
        "period": "Q",
        "rolling": 4,
        "bar_name": "Quarterly volume",
        "rolling_name": "4-quarter rolling avg",
        "title": "Quarterly ticket volume",
    },
    "Yearly": {
        "period": "Y",
        "rolling": 2,
        "bar_name": "Yearly volume",
        "rolling_name": "2-year rolling avg",
        "title": "Yearly ticket volume",
    },
}


def daily_ops_series(df: pd.DataFrame) -> pd.DataFrame:
    """Daily volume, SLA attainment, and response metrics."""
    if df.empty:
        return pd.DataFrame()

    daily = (
        df.groupby("created_date", as_index=False)
        .agg(
            ticket_volume=("ticket_id", "count"),
            solved_volume=("is_solved", "sum"),
            avg_frt=("first_response_time_hours", "mean"),
            avg_res=("resolution_time_hours", "mean"),
            avg_csat=("satisfaction_rating", "mean"),
            fr_eligible=("is_fr_sla_eligible", "sum"),
            fr_met=("met_first_response_sla", "sum"),
            res_eligible=("is_res_sla_eligible", "sum"),
            res_met=("met_resolution_sla", "sum"),
            avg_fr_bh=("first_response_business_hours", "mean"),
            avg_res_bh=("resolution_business_hours", "mean"),
        )
        .sort_values("created_date")
    )
    daily["rolling_7d"] = daily["ticket_volume"].rolling(7, min_periods=1).mean()
    daily["pct_fr_sla"] = daily["fr_met"] / daily["fr_eligible"].replace(0, np.nan)
    daily["pct_res_sla"] = daily["res_met"] / daily["res_eligible"].replace(0, np.nan)
    daily["fr_breaches"] = daily["fr_eligible"] - daily["fr_met"]
    daily["res_breaches"] = daily["res_eligible"] - daily["res_met"]
    return daily


def period_bucket(series: pd.Series, grain: str) -> pd.Series:
    """Bucket timestamps to period starts for Daily / Weekly / Monthly / Quarterly / Yearly."""
    ts = pd.to_datetime(series)
    if grain == "Daily":
        return ts.dt.floor("D")
    return ts.dt.to_period(VOLUME_GRAIN_CONFIG[grain]["period"]).dt.start_time


def volume_ops_series(df: pd.DataFrame, grain: str = "Daily") -> pd.DataFrame:
    """Ticket volume aggregated to the selected grain with a matching rolling average."""
    if df.empty:
        return pd.DataFrame()

    cfg = VOLUME_GRAIN_CONFIG[grain]
    work = df[["ticket_id", "created_date"]].copy()
    work["period"] = period_bucket(work["created_date"], grain)

    out = (
        work.groupby("period", as_index=False)
        .agg(ticket_volume=("ticket_id", "count"))
        .sort_values("period")
    )
    out["rolling_avg"] = out["ticket_volume"].rolling(cfg["rolling"], min_periods=1).mean()
    return out


def sla_period_series(df: pd.DataFrame, grain: str = "Weekly") -> pd.DataFrame:
    """Roll SLA measures to the selected grain for trend charts."""
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["period"] = period_bucket(work["created_date"], grain)

    # Boolean SLA flags may arrive as object from parquet
    for col in ("met_first_response_sla", "met_resolution_sla", "is_fr_sla_eligible", "is_res_sla_eligible"):
        if col in work.columns:
            work[col] = work[col].fillna(False).astype(bool)

    agg = (
        work.groupby("period", as_index=False)
        .agg(
            ticket_volume=("ticket_id", "count"),
            fr_eligible=("is_fr_sla_eligible", "sum"),
            fr_met=("met_first_response_sla", "sum"),
            res_eligible=("is_res_sla_eligible", "sum"),
            res_met=("met_resolution_sla", "sum"),
            avg_fr_bh=("first_response_business_hours", "mean"),
            avg_res_bh=("resolution_business_hours", "mean"),
            avg_frt=("first_response_time_hours", "mean"),
            avg_res=("resolution_time_hours", "mean"),
        )
        .sort_values("period")
    )
    agg["pct_fr_sla"] = agg["fr_met"] / agg["fr_eligible"].replace(0, np.nan)
    agg["pct_res_sla"] = agg["res_met"] / agg["res_eligible"].replace(0, np.nan)
    agg["fr_breaches"] = agg["fr_eligible"] - agg["fr_met"]
    agg["res_breaches"] = agg["res_eligible"] - agg["res_met"]
    return agg


def sla_slice_series(df: pd.DataFrame, slice_col: str, grain: str = "Weekly") -> pd.DataFrame:
    """SLA attainment trends sliced by a dimension (support_group, priority, …)."""
    if df.empty or slice_col not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    work["period"] = period_bucket(work["created_date"], grain)

    for col in ("met_first_response_sla", "met_resolution_sla", "is_fr_sla_eligible", "is_res_sla_eligible"):
        if col in work.columns:
            work[col] = work[col].fillna(False).astype(bool)

    agg = (
        work.groupby(["period", slice_col], as_index=False)
        .agg(
            fr_eligible=("is_fr_sla_eligible", "sum"),
            fr_met=("met_first_response_sla", "sum"),
            res_eligible=("is_res_sla_eligible", "sum"),
            res_met=("met_resolution_sla", "sum"),
            avg_fr_bh=("first_response_business_hours", "mean"),
            avg_res_bh=("resolution_business_hours", "mean"),
            ticket_volume=("ticket_id", "count"),
        )
        .sort_values(["period", slice_col])
    )
    agg["pct_fr_sla"] = agg["fr_met"] / agg["fr_eligible"].replace(0, np.nan)
    agg["pct_res_sla"] = agg["res_met"] / agg["res_eligible"].replace(0, np.nan)
    return agg

def compute_priority_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recompute multi-signal priority scores on the filtered ticket set.

    Mirrors models/marts/mart_priority_scoring.py so sidebar filters stay
    consistent with the prioritization view.
    """
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("category", as_index=False).agg(
        ticket_count=("ticket_id", "count"),
        enterprise_ticket_count=("is_enterprise", "sum"),
        avg_support_cost_hours=("support_cost_hours", "mean"),
        avg_resolution_hours=("resolution_time_hours", "mean"),
        avg_reopen_count=("reopen_count", "mean"),
        avg_csat=("satisfaction_rating", "mean"),
        low_csat_rate=("is_low_csat", "mean"),
        high_urgency_rate=("is_high_urgency", "mean"),
    )

    total = g["ticket_count"].sum()
    g["frequency"] = g["ticket_count"] / total
    g["enterprise_share"] = g["enterprise_ticket_count"] / g["ticket_count"]
    g["sentiment_impact"] = 0.5 * g["low_csat_rate"].fillna(0) + 0.5 * g[
        "high_urgency_rate"
    ].fillna(0)

    def _norm(series: pd.Series) -> pd.Series:
        lo, hi = series.min(), series.max()
        if pd.isna(lo) or pd.isna(hi) or hi == lo:
            return pd.Series(0.0, index=series.index)
        return (series - lo) / (hi - lo)

    g["frequency_norm"] = _norm(g["frequency"])
    g["enterprise_impact_norm"] = _norm(g["enterprise_share"])
    g["support_cost_norm"] = _norm(g["avg_support_cost_hours"])
    g["sentiment_impact_norm"] = _norm(g["sentiment_impact"])

    g["score_from_frequency"] = 100 * PRIORITY_WEIGHTS["frequency"] * g["frequency_norm"]
    g["score_from_enterprise"] = (
        100 * PRIORITY_WEIGHTS["enterprise_impact"] * g["enterprise_impact_norm"]
    )
    g["score_from_cost"] = 100 * PRIORITY_WEIGHTS["support_cost"] * g["support_cost_norm"]
    g["score_from_sentiment"] = (
        100 * PRIORITY_WEIGHTS["sentiment_impact"] * g["sentiment_impact_norm"]
    )
    g["priority_score"] = (
        g["score_from_frequency"]
        + g["score_from_enterprise"]
        + g["score_from_cost"]
        + g["score_from_sentiment"]
    ).round(1)

    g = g.sort_values("priority_score", ascending=False).reset_index(drop=True)
    g["priority_rank"] = g.index + 1

    drivers = []
    for _, row in g.iterrows():
        parts = {
            "enterprise impact": row["score_from_enterprise"],
            "support cost": row["score_from_cost"],
            "sentiment / urgency": row["score_from_sentiment"],
            "ticket volume": row["score_from_frequency"],
        }
        top = max(parts, key=parts.get)
        drivers.append(top)
    g["top_driver"] = drivers
    return g


def fmt_hours(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    return f"{value:.1f}h"


def fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    return f"{value:.1%}"


def style_fig(fig: go.Figure, height: int = 360) -> go.Figure:
    return theme_style_fig(fig, height, hovermode="x unified")


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------
def most_recent_completed_saturday(today: date | None = None) -> date:
    """Latest Saturday whose calendar day has fully ended (before today if today is Saturday)."""
    today = today or date.today()
    days_since_sat = (today.weekday() - 5) % 7
    last_sat = today - timedelta(days=days_since_sat)
    if days_since_sat == 0:
        last_sat -= timedelta(days=7)
    return last_sat


def default_date_range(min_date: date, max_date: date) -> tuple[date, date]:
    """Jan 1 of the current year through the most recent completed Saturday, clamped to data."""
    year_start = date(date.today().year, 1, 1)
    start = max(min_date, year_start)
    end = min(max_date, most_recent_completed_saturday())
    if start > end:
        return min_date, max_date
    return start, end


def render_sidebar(df: pd.DataFrame) -> tuple:
    st.sidebar.title("Filters")
    st.sidebar.caption("Applied across every tab")

    min_date = df["created_date"].min().date()
    max_date = df["created_date"].max().date()
    default_start, default_end = default_date_range(min_date, max_date)

    date_range = st.sidebar.date_input(
        "Date range",
        value=(default_start, default_end),
        min_value=min_date,
        max_value=max_date,
        key="sidebar_date_range",
        help="Defaults to Jan 1 of this year through the most recent completed Saturday.",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, default_end

    groups = st.sidebar.multiselect(
        "Support group",
        options=SUPPORT_GROUPS,
        default=SUPPORT_GROUPS,
    )
    priorities = st.sidebar.multiselect(
        "Priority",
        options=PRIORITIES,
        default=PRIORITIES,
        format_func=lambda p: p.title(),
    )
    enterprise_only = st.sidebar.toggle(
        "Enterprise tickets only",
        value=False,
        help="Restricts all tabs except Enterprise View (which is always Enterprise).",
    )

    st.sidebar.divider()
    st.sidebar.markdown("**Demo path**")
    st.sidebar.caption(
        "Use **About** / **Dashboard** at the top.  \n"
        "1. **Operations** — volume & where work lands  \n"
        "2. **SLA Trends** — 3-business-day attainment  \n"
        "3. **Prioritization** — what Engineering should fix  \n"
        "4. **Capacity** — agent load & long-tail risk  \n"
        "5. **Enterprise** — strategic-account lens"
    )

    st.sidebar.divider()
    with st.sidebar.expander("About this dashboard", expanded=False):
        st.markdown(
            """
Synthetic Zendesk-style tickets → FIFO simulation → **dbt + DuckDB** models → this app.

**SLA:** Mon–Fri 09:00–17:00 America/Los_Angeles · fail after **3 business days** (24 bh).

**Enterprise:** tickets on the Enterprise support team.
            """
        )

    st.sidebar.markdown("**Data**")
    st.sidebar.caption(f"`{PROCESSED.relative_to(PROJECT_ROOT)}/`")
    st.sidebar.caption("`python scripts/run_pipeline.py`")

    return start_date, end_date, groups, priorities, enterprise_only


CSAT_GOAL = 4.0
SLA_GOAL = 0.99  # 99.0%


def sla_snapshot_metrics(df: pd.DataFrame) -> dict:
    """KPI block plus business-hours speeds and breach counts."""
    base = kpi_block(df)
    if df.empty:
        return {
            **base,
            "avg_fr_bh": None,
            "avg_res_bh": None,
            "fr_breaches": 0,
            "res_breaches": 0,
        }

    fr_elig = df["is_fr_sla_eligible"].fillna(False).astype(bool)
    res_elig = df["is_res_sla_eligible"].fillna(False).astype(bool)
    fr_met = df["met_first_response_sla"].fillna(False).astype(bool)
    res_met = df["met_resolution_sla"].fillna(False).astype(bool)
    return {
        **base,
        "avg_fr_bh": float(df["first_response_business_hours"].mean())
        if "first_response_business_hours" in df.columns
        else None,
        "avg_res_bh": float(df["resolution_business_hours"].mean())
        if "resolution_business_hours" in df.columns
        else None,
        "fr_breaches": int((fr_elig & ~fr_met).sum()),
        "res_breaches": int((res_elig & ~res_met).sum()),
    }


def period_starts(df: pd.DataFrame, grain: str) -> list:
    if df.empty:
        return []
    periods = period_bucket(df["created_date"], grain).dropna().unique()
    return sorted(pd.to_datetime(periods))


def tickets_in_period(df: pd.DataFrame, grain: str, period_start) -> pd.DataFrame:
    work = df.copy()
    work["_period"] = period_bucket(work["created_date"], grain)
    target = pd.Timestamp(period_start)
    return work.loc[work["_period"] == target].drop(columns=["_period"])


def format_period_label(period_ts, grain: str) -> str:
    ts = pd.Timestamp(period_ts)
    if grain == "Daily":
        return ts.strftime("%b %d, %Y")
    if grain == "Weekly":
        return f"Week of {ts.strftime('%b %d, %Y')}"
    if grain == "Monthly":
        return ts.strftime("%b %Y")
    if grain == "Quarterly":
        return f"Q{((ts.month - 1) // 3) + 1} {ts.year}"
    return str(ts.year)


def goal_delta_label(value: float | None, goal: float, *, as_pct: bool = False) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    diff = value - goal
    if as_pct:
        if abs(diff) < 5e-4:
            return f"● At {goal:.0%} goal"
        return f"{diff * 100:+.1f} pp vs {goal:.0%} goal"
    if abs(diff) < 0.005:
        return f"● At {goal:.1f} goal"
    return f"{diff:+.2f} vs {goal:.1f} goal"


def period_delta_label(
    current: float | None,
    prior: float | None,
    *,
    as_pct: bool = False,
    as_hours: bool = False,
    as_int: bool = False,
) -> str | None:
    if current is None or prior is None:
        return None
    if isinstance(current, float) and np.isnan(current):
        return None
    if isinstance(prior, float) and np.isnan(prior):
        return None
    diff = current - prior
    if as_pct:
        if abs(diff) < 5e-4:
            return "● 0 pp"
        return f"{diff * 100:+.1f} pp"
    if as_hours:
        if abs(diff) < 0.05:
            return "● 0.0h"
        return f"{diff:+.1f}h"
    if as_int:
        if abs(diff) < 0.5:
            return "● 0"
        return f"{diff:+,.0f}"
    if abs(diff) < 0.005:
        return "● 0.00"
    return f"{diff:+.2f}"


GRAIN_CHANGE_LABEL = {
    "Daily": "DoD",
    "Weekly": "WoW",
    "Monthly": "MoM",
    "Quarterly": "QoQ",
    "Yearly": "YoY",
}


def period_change_detail(
    current: float | None,
    prior: float | None,
    *,
    grain: str,
    higher_is_better: bool = True,
    as_pct: bool = False,
    as_hours: bool = False,
    as_int: bool = False,
) -> tuple[str | None, str]:
    """Return (label, tone) for period-over-period change. tone: good|bad|flat|muted."""
    tag = GRAIN_CHANGE_LABEL.get(grain, grain)
    if current is None or prior is None:
        return None, "muted"
    if isinstance(current, float) and np.isnan(current):
        return None, "muted"
    if isinstance(prior, float) and np.isnan(prior):
        return None, "muted"

    diff = float(current) - float(prior)
    if as_pct:
        flat = abs(diff) < 5e-4  # < 0.05 pp
        magnitude = f"{diff * 100:+.1f} pp"
    elif as_hours:
        flat = abs(diff) < 0.05
        magnitude = f"{diff:+.1f}h"
    elif as_int:
        flat = abs(diff) < 0.5
        magnitude = f"{diff:+,.0f}"
    else:
        flat = abs(diff) < 0.005
        magnitude = f"{diff:+.2f}"

    if flat:
        return f"● 0 {tag}", "flat"

    icon = "▲" if diff > 0 else "▼"
    improved = (diff > 0) if higher_is_better else (diff < 0)
    return f"{icon} {magnitude} {tag}", "good" if improved else "bad"


def goal_status_detail(
    value: float | None,
    goal: float,
    *,
    as_pct: bool = False,
) -> tuple[str | None, str]:
    """Return (label, tone) for goal comparison."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None, "muted"
    meets = value >= goal
    if as_pct:
        detail = f"{(value - goal) * 100:+.1f} pp vs {goal:.0%} goal"
    else:
        detail = f"{value - goal:+.2f} vs {goal:.1f} goal"
    if meets:
        return f"✓ At/above goal ({detail})", "good"
    return f"✗ Below goal ({detail})", "bad"


def render_dual_metric_card(
    container,
    label: str,
    value_str: str,
    *,
    grain: str,
    current: float | None = None,
    prior: float | None = None,
    higher_is_better: bool = True,
    as_pct: bool = False,
    as_hours: bool = False,
    as_int: bool = False,
    goal: float | None = None,
    goal_as_pct: bool = False,
) -> None:
    """Period card with optional goal line + WoW/MoM (etc.) change, including flat icon."""
    tone_color = {
        "good": GREEN,
        "bad": PINK,
        "flat": AMBER,
        "muted": TEXT_DIM,
    }
    lines: list[str] = []
    if goal is not None:
        goal_text, goal_tone = goal_status_detail(current, goal, as_pct=goal_as_pct)
        if goal_text:
            lines.append(
                f'<div style="font-size:0.78rem;font-weight:600;line-height:1.35;'
                f'margin-top:0.15rem;color:{tone_color[goal_tone]}">{goal_text}</div>'
            )

    change_text, change_tone = period_change_detail(
        current,
        prior,
        grain=grain,
        higher_is_better=higher_is_better,
        as_pct=as_pct,
        as_hours=as_hours,
        as_int=as_int,
    )
    if change_text:
        lines.append(
            f'<div style="font-size:0.78rem;font-weight:600;line-height:1.35;'
            f'margin-top:0.15rem;color:{tone_color[change_tone]}">{change_text}</div>'
        )
    elif prior is None:
        lines.append(
            f'<div style="font-size:0.78rem;font-weight:600;line-height:1.35;'
            f'margin-top:0.15rem;color:{TEXT_DIM}">No prior period</div>'
        )

    container.markdown(
        f"""
        <div style="
          background:{SURFACE};
          border:1px solid {BORDER};
          border-top:3px solid {PURPLE};
          border-radius:14px;
          padding:12px 14px;
          margin:0 0 0.65rem 0;
          box-shadow:0 10px 30px rgba(0,0,0,0.25);
          min-height:7.75rem;
          box-sizing:border-box;
        ">
          <div style="
            color:{TEXT_MUTED};
            font-size:0.72rem;
            letter-spacing:0.04em;
            text-transform:uppercase;
            font-weight:600;
            margin-bottom:0.25rem;
          ">{label}</div>
          <div style="
            color:{TEXT};
            font-family:{FONT_DISPLAY};
            font-size:1.55rem;
            font-weight:700;
            line-height:1.15;
            margin-bottom:0.45rem;
          ">{value_str}</div>
          {''.join(lines)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric(
    container,
    label: str,
    value_str: str,
    *,
    delta: str | None = None,
    higher_is_better: bool = True,
    help_text: str | None = None,
) -> None:
    """st.metric wrapper — green/red deltas (normal = up good, inverse = up bad)."""
    kwargs: dict = {"label": label, "value": value_str}
    if help_text:
        kwargs["help"] = help_text
    if delta:
        # Exact match to goal / zero change → flat amber treatment via off + label icon
        if delta.startswith("●") or " 0 " in f" {delta} ":
            kwargs["delta"] = delta
            kwargs["delta_color"] = "off"
        else:
            kwargs["delta"] = delta
            kwargs["delta_color"] = "normal" if higher_is_better else "inverse"
    else:
        kwargs["delta_color"] = "off"
    container.metric(**kwargs)


def render_kpi_row(kpis: dict, *, vs_goal: bool = True) -> None:
    """Full-period headline KPIs; CSAT uses the 4.0 goal when vs_goal=True."""
    c1, c2, c3, c4, c5 = st.columns(5)
    render_metric(c1, "Total Tickets", f"{kpis['total']:,}")
    render_metric(c2, "% Solved", fmt_pct(kpis["pct_solved"]))
    render_metric(c3, "Avg First Response", fmt_hours(kpis["avg_frt"]))
    render_metric(c4, "Avg Resolution", fmt_hours(kpis["avg_res"]))
    csat_delta = (
        goal_delta_label(kpis["avg_csat"], CSAT_GOAL) if vs_goal else None
    )
    render_metric(
        c5,
        "Avg CSAT",
        f"{kpis['avg_csat']:.2f}" if kpis["avg_csat"] is not None else "—",
        delta=csat_delta,
        higher_is_better=True,
        help_text=f"Goal ≥ {CSAT_GOAL:.1f}" if vs_goal else None,
    )


def render_sla_goal_row(kpis: dict, *, vs_goal: bool = True) -> None:
    """FR / Resolution SLA cards with 99% goal comparison."""
    s1, s2 = st.columns(2)
    fr_delta = (
        goal_delta_label(kpis["pct_fr_sla"], SLA_GOAL, as_pct=True) if vs_goal else None
    )
    res_delta = (
        goal_delta_label(kpis["pct_res_sla"], SLA_GOAL, as_pct=True) if vs_goal else None
    )
    render_metric(
        s1,
        "First Response SLA",
        fmt_pct(kpis["pct_fr_sla"]),
        delta=fr_delta,
        higher_is_better=True,
        help_text=f"Goal ≥ {SLA_GOAL:.0%} · within 3 business days (Mon–Fri 9–5 PT)",
    )
    render_metric(
        s2,
        "Resolution SLA",
        fmt_pct(kpis["pct_res_sla"]),
        delta=res_delta,
        higher_is_better=True,
        help_text=f"Goal ≥ {SLA_GOAL:.0%} · within 3 business days (Mon–Fri 9–5 PT)",
    )


def render_sla_snapshot_row(metrics: dict, *, vs_goal: bool = True) -> None:
    """Full-period SLA snapshot; SLA % cards compare to the 99% goal."""
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    render_metric(
        m1,
        "First response SLA",
        fmt_pct(metrics["pct_fr_sla"]),
        delta=goal_delta_label(metrics["pct_fr_sla"], SLA_GOAL, as_pct=True)
        if vs_goal
        else None,
        help_text=f"Goal ≥ {SLA_GOAL:.0%}",
    )
    render_metric(
        m2,
        "Resolution SLA",
        fmt_pct(metrics["pct_res_sla"]),
        delta=goal_delta_label(metrics["pct_res_sla"], SLA_GOAL, as_pct=True)
        if vs_goal
        else None,
        help_text=f"Goal ≥ {SLA_GOAL:.0%}",
    )
    render_metric(m3, "Avg FR (business h)", fmt_hours(metrics["avg_fr_bh"]))
    render_metric(m4, "Avg resolution (business h)", fmt_hours(metrics["avg_res_bh"]))
    render_metric(m5, "FR breaches", f"{metrics['fr_breaches']:,}")
    render_metric(m6, "Resolution breaches", f"{metrics['res_breaches']:,}")


def render_period_kpi_cards(
    current: dict,
    prior: dict | None,
    *,
    grain: str,
    current_label: str,
    prior_label: str | None,
) -> None:
    """Latest-grain KPI + SLA cards with goal status and WoW/MoM change."""
    tag = GRAIN_CHANGE_LABEL.get(grain, grain)
    caption = f"Latest {grain.lower()}: **{current_label}**"
    if prior_label:
        caption += f" · vs prior **{prior_label}** ({tag})"
    section(f"Latest {grain.lower()} KPIs", caption)

    c1, c2, c3, c4, c5 = st.columns(5)
    p = prior or {}
    render_dual_metric_card(
        c1,
        "Total Tickets",
        f"{current['total']:,}",
        grain=grain,
        current=float(current["total"]),
        prior=float(p["total"]) if prior is not None else None,
        as_int=True,
        higher_is_better=True,
    )
    render_dual_metric_card(
        c2,
        "% Solved",
        fmt_pct(current["pct_solved"]),
        grain=grain,
        current=current["pct_solved"],
        prior=p.get("pct_solved"),
        as_pct=True,
        higher_is_better=True,
    )
    render_dual_metric_card(
        c3,
        "Avg First Response",
        fmt_hours(current["avg_frt"]),
        grain=grain,
        current=current["avg_frt"],
        prior=p.get("avg_frt"),
        as_hours=True,
        higher_is_better=False,
    )
    render_dual_metric_card(
        c4,
        "Avg Resolution",
        fmt_hours(current["avg_res"]),
        grain=grain,
        current=current["avg_res"],
        prior=p.get("avg_res"),
        as_hours=True,
        higher_is_better=False,
    )
    render_dual_metric_card(
        c5,
        "Avg CSAT",
        f"{current['avg_csat']:.2f}" if current["avg_csat"] is not None else "—",
        grain=grain,
        current=current["avg_csat"],
        prior=p.get("avg_csat"),
        higher_is_better=True,
        goal=CSAT_GOAL,
    )

    s1, s2 = st.columns(2)
    render_dual_metric_card(
        s1,
        "First Response SLA",
        fmt_pct(current["pct_fr_sla"]),
        grain=grain,
        current=current["pct_fr_sla"],
        prior=p.get("pct_fr_sla"),
        as_pct=True,
        higher_is_better=True,
        goal=SLA_GOAL,
        goal_as_pct=True,
    )
    render_dual_metric_card(
        s2,
        "Resolution SLA",
        fmt_pct(current["pct_res_sla"]),
        grain=grain,
        current=current["pct_res_sla"],
        prior=p.get("pct_res_sla"),
        as_pct=True,
        higher_is_better=True,
        goal=SLA_GOAL,
        goal_as_pct=True,
    )


def render_period_sla_snapshot_cards(
    current: dict,
    prior: dict | None,
    *,
    grain: str,
    current_label: str,
    prior_label: str | None,
) -> None:
    """Latest-grain SLA snapshot with goal status and WoW/MoM change."""
    tag = GRAIN_CHANGE_LABEL.get(grain, grain)
    caption = f"Latest {grain.lower()}: **{current_label}**"
    if prior_label:
        caption += f" · vs prior **{prior_label}** ({tag})"
    section(f"Latest {grain.lower()} SLA snapshot", caption)

    p = prior or {}
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    render_dual_metric_card(
        m1,
        "First response SLA",
        fmt_pct(current["pct_fr_sla"]),
        grain=grain,
        current=current["pct_fr_sla"],
        prior=p.get("pct_fr_sla"),
        as_pct=True,
        higher_is_better=True,
        goal=SLA_GOAL,
        goal_as_pct=True,
    )
    render_dual_metric_card(
        m2,
        "Resolution SLA",
        fmt_pct(current["pct_res_sla"]),
        grain=grain,
        current=current["pct_res_sla"],
        prior=p.get("pct_res_sla"),
        as_pct=True,
        higher_is_better=True,
        goal=SLA_GOAL,
        goal_as_pct=True,
    )
    render_dual_metric_card(
        m3,
        "Avg FR (business h)",
        fmt_hours(current["avg_fr_bh"]),
        grain=grain,
        current=current["avg_fr_bh"],
        prior=p.get("avg_fr_bh"),
        as_hours=True,
        higher_is_better=False,
    )
    render_dual_metric_card(
        m4,
        "Avg resolution (business h)",
        fmt_hours(current["avg_res_bh"]),
        grain=grain,
        current=current["avg_res_bh"],
        prior=p.get("avg_res_bh"),
        as_hours=True,
        higher_is_better=False,
    )
    render_dual_metric_card(
        m5,
        "FR breaches",
        f"{current['fr_breaches']:,}",
        grain=grain,
        current=float(current["fr_breaches"]),
        prior=float(p["fr_breaches"]) if prior is not None else None,
        as_int=True,
        higher_is_better=False,
    )
    render_dual_metric_card(
        m6,
        "Resolution breaches",
        f"{current['res_breaches']:,}",
        grain=grain,
        current=float(current["res_breaches"]),
        prior=float(p["res_breaches"]) if prior is not None else None,
        as_int=True,
        higher_is_better=False,
    )


def latest_period_metrics(
    df: pd.DataFrame, grain: str, *, snapshot: bool = False
) -> tuple[dict | None, dict | None, str | None, str | None]:
    """Return (current_metrics, prior_metrics, current_label, prior_label)."""
    periods = period_starts(df, grain)
    if not periods:
        return None, None, None, None
    current_p = periods[-1]
    prior_p = periods[-2] if len(periods) > 1 else None
    builder = sla_snapshot_metrics if snapshot else kpi_block
    current = builder(tickets_in_period(df, grain, current_p))
    prior = builder(tickets_in_period(df, grain, prior_p)) if prior_p is not None else None
    return (
        current,
        prior,
        format_period_label(current_p, grain),
        format_period_label(prior_p, grain) if prior_p is not None else None,
    )


DEFAULT_TREND_GRAIN = "Weekly"
TREND_GRAIN_OPTIONS = list(VOLUME_GRAIN_CONFIG.keys())


def _on_trend_grain_change(source_key: str) -> None:
    """Keep Support Operations / SLA Trends / Enterprise View on the same grain."""
    st.session_state["trend_grain"] = st.session_state[source_key]


def render_trend_controls(*, widget_key: str, first: bool = False) -> str:
    """Shared trend grain control — one selection syncs across dashboard tabs."""
    if "trend_grain" not in st.session_state:
        st.session_state["trend_grain"] = DEFAULT_TREND_GRAIN

    # Align this tab's widget with the shared value before it renders.
    st.session_state[widget_key] = st.session_state["trend_grain"]

    section(
        "Trend controls",
        "Shared across Support Operations, SLA Trends, and Enterprise View.",
        first=first,
    )
    return st.radio(
        "Trend grain",
        options=TREND_GRAIN_OPTIONS,
        horizontal=True,
        key=widget_key,
        on_change=_on_trend_grain_change,
        args=(widget_key,),
        help="Aggregate time-series charts to the selected period. Shared across Operations, SLA Trends, and Enterprise.",
    )


def csat_period_series(df: pd.DataFrame, grain: str = "Weekly") -> pd.DataFrame:
    """Average CSAT rolled to the selected grain."""
    if df.empty or "satisfaction_rating" not in df.columns:
        return pd.DataFrame()
    work = df[["created_date", "satisfaction_rating"]].copy()
    work["period"] = period_bucket(work["created_date"], grain)
    return (
        work.groupby("period", as_index=False)
        .agg(avg_csat=("satisfaction_rating", "mean"))
        .sort_values("period")
    )


def tab_operations(df: pd.DataFrame) -> None:
    """Tab 1 — Support Operations Overview."""
    tab_lead(
        "Start here: headline health, inbound volume, and where tickets land by team."
    )

    kpis = kpi_block(df)
    section(
        "Headline KPIs",
        f"Filtered period · CSAT goal ≥ {CSAT_GOAL:.1f} · SLA goals ≥ {SLA_GOAL:.0%}",
        first=True,
    )
    render_kpi_row(kpis)
    render_sla_goal_row(kpis)

    if df.empty:
        st.info("No tickets match the current filters.")
        return

    grain = render_trend_controls(widget_key="ops_trend_grain")
    cur, prior, cur_label, prior_label = latest_period_metrics(df, grain)
    if cur is not None and cur_label is not None:
        render_period_kpi_cards(
            cur,
            prior,
            grain=grain,
            current_label=cur_label,
            prior_label=prior_label,
        )

    section(
        "Ticket volume",
        "Title and rolling average update with the shared trend grain.",
    )
    vol_cfg = VOLUME_GRAIN_CONFIG[grain]
    volume = volume_ops_series(df, grain)
    if volume.empty:
        st.info("No tickets match the current filters.")
        return

    fig_vol = go.Figure()
    fig_vol.add_trace(
        go.Bar(
            x=volume["period"],
            y=volume["ticket_volume"],
            name=vol_cfg["bar_name"],
            marker_color=ACCENT_VOLUME,
            opacity=0.85,
        )
    )
    fig_vol.add_trace(
        go.Scatter(
            x=volume["period"],
            y=volume["rolling_avg"],
            name=vol_cfg["rolling_name"],
            line=dict(color=CYAN, width=2.5),
        )
    )
    fig_vol.update_layout(
        title=vol_cfg["title"],
        yaxis_title="Tickets",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        barmode="overlay",
    )
    st.plotly_chart(style_fig(fig_vol, 380), width="stretch")
    st.caption("Trend grain is shared with **SLA Trends** and **Enterprise View**.")

    section("Where work lands", "Volume mix by support group and priority.")
    left, right = st.columns(2)

    by_group = (
        df.groupby("support_group", as_index=False)
        .agg(
            tickets=("ticket_id", "count"),
            avg_frt=("first_response_time_hours", "mean"),
            avg_res=("resolution_time_hours", "mean"),
            pct_solved=("is_solved", "mean"),
            avg_csat=("satisfaction_rating", "mean"),
        )
        .sort_values("tickets", ascending=False)
    )

    with left:
        total_tickets = int(by_group["tickets"].sum())
        fig_grp = px.pie(
            by_group,
            names="support_group",
            values="tickets",
            color="support_group",
            color_discrete_map=GROUP_COLORS,
            title="Ticket volume by support group",
        )
        fig_grp.update_traces(
            textposition="outside",
            texttemplate="%{label}<br>%{percent:.1%}<br>%{value:,}",
            textfont=dict(size=13, color=TEXT),
            hovertemplate=(
                "%{label}<br>%{value:,} tickets"
                f" ({total_tickets:,} total)"
                "<br>%{percent:.1%}<extra></extra>"
            ),
            hole=0.42,
            pull=[0.02] * len(by_group),
            marker=dict(line=dict(color=SURFACE, width=2)),
        )
        fig_grp.update_layout(
            showlegend=False,
            margin=dict(l=20, r=20, t=72, b=20),
            uniformtext_minsize=12,
            uniformtext_mode="hide",
        )
        st.plotly_chart(style_fig(fig_grp, 420), width="stretch")
        st.caption(f"Share of **{total_tickets:,}** tickets in the current filter.")

    with right:
        heat = (
            df.groupby(["support_group", "priority"], as_index=False)
            .size()
            .rename(columns={"size": "tickets"})
        )
        heat["priority"] = pd.Categorical(
            heat["priority"], categories=PRIORITIES, ordered=True
        )
        heat_pivot = heat.pivot(
            index="support_group", columns="priority", values="tickets"
        ).fillna(0)
        heat_pivot = heat_pivot.reindex(
            [g for g in SUPPORT_GROUPS if g in heat_pivot.index]
        )

        fig_heat = px.imshow(
            heat_pivot,
            text_auto=True,
            aspect="auto",
            color_continuous_scale="Purples",
            title="Volume heatmap: group × priority",
            labels=dict(color="Tickets"),
        )
        st.plotly_chart(style_fig(fig_heat, 340), width="stretch")

    section("Team snapshot", "Same filter — sortable table for talking points.")
    st.dataframe(
        by_group.assign(
            avg_frt=lambda d: d["avg_frt"].round(1),
            avg_res=lambda d: d["avg_res"].round(1),
            pct_solved=lambda d: (d["pct_solved"] * 100).round(1),
            avg_csat=lambda d: d["avg_csat"].round(2),
        ).rename(
            columns={
                "support_group": "Support Group",
                "tickets": "Tickets",
                "avg_frt": "Avg FRT (h)",
                "avg_res": "Avg Resolution (h)",
                "pct_solved": "% Solved",
                "avg_csat": "Avg CSAT",
            }
        ),
        width="stretch",
        hide_index=True,
    )


def tab_sla_trends(df: pd.DataFrame) -> None:
    """Dedicated SLA section — trends for all SLA measures."""
    tab_lead(
        "3-business-day SLA in PT business hours — attainment, speed, and where breaches cluster."
    )

    if df.empty:
        st.info("No tickets match the current filters.")
        return

    metrics = sla_snapshot_metrics(df)
    section(
        "SLA snapshot",
        f"Filtered period · SLA goals ≥ {SLA_GOAL:.0%}",
        first=True,
    )
    render_sla_snapshot_row(metrics)

    grain = render_trend_controls(widget_key="sla_trend_grain")
    cur, prior, cur_label, prior_label = latest_period_metrics(
        df, grain, snapshot=True
    )
    if cur is not None and cur_label is not None:
        render_period_sla_snapshot_cards(
            cur,
            prior,
            grain=grain,
            current_label=cur_label,
            prior_label=prior_label,
        )

    series = sla_period_series(df, grain=grain)
    if series.empty:
        st.info("No SLA-eligible tickets in this period.")
        return

    section(
        "SLA attainment over time",
        "Toggle the secondary axis between inbound volume and avg resolution time.",
    )
    secondary_metric = st.radio(
        "Secondary metric",
        options=["Ticket volume", "Avg resolution time"],
        index=0,
        horizontal=True,
        key="sla_att_secondary",
        help="Overlay ticket volume or average resolution time on the SLA attainment chart.",
    )
    show_volume = secondary_metric == "Ticket volume"

    fig_att = make_subplots(specs=[[{"secondary_y": True}]])
    fig_att.add_trace(
        go.Scatter(
            x=series["period"],
            y=series["pct_fr_sla"],
            name="First response SLA",
            line=dict(color="#059669", width=2.5),
            hovertemplate="%{y:.1%}<extra>FR SLA</extra>",
        ),
        secondary_y=False,
    )
    fig_att.add_trace(
        go.Scatter(
            x=series["period"],
            y=series["pct_res_sla"],
            name="Resolution SLA",
            line=dict(color="#D97706", width=2.5),
            hovertemplate="%{y:.1%}<extra>Res SLA</extra>",
        ),
        secondary_y=False,
    )
    if show_volume:
        fig_att.add_trace(
            go.Bar(
                x=series["period"],
                y=series["ticket_volume"],
                name="Ticket volume",
                marker_color="#E2E8F0",
                opacity=0.65,
                hovertemplate="%{y:,}<extra>Volume</extra>",
            ),
            secondary_y=True,
        )
        secondary_axis_title = "Tickets"
        att_title = f"SLA attainment over time ({grain.lower()}) — volume"
    else:
        fig_att.add_trace(
            go.Scatter(
                x=series["period"],
                y=series["avg_res_bh"],
                name="Avg resolution time",
                line=dict(color="#64748B", width=2.5, dash="dot"),
                hovertemplate="%{y:.1f}h<extra>Avg resolution</extra>",
            ),
            secondary_y=True,
        )
        secondary_axis_title = "Avg resolution (business h)"
        att_title = f"SLA attainment over time ({grain.lower()}) — resolution time"

    fig_att.add_hline(
        y=SLA_GOAL,
        line_dash="dot",
        line_color="#94A3B8",
        annotation_text=f"{SLA_GOAL:.0%} goal",
        annotation_position="top left",
        secondary_y=False,
    )
    fig_att.update_yaxes(title_text="% within SLA", tickformat=".0%", range=[0, 1.05], secondary_y=False)
    fig_att.update_yaxes(title_text=secondary_axis_title, secondary_y=True)
    fig_att.update_layout(
        title=att_title,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(style_fig(fig_att, 400), width="stretch")

    section(
        "Response & resolution speed",
        "Business hours count toward SLA; calendar hours show wall-clock experience.",
    )
    # --- Business hours + calendar hours ---
    left, right = st.columns(2)
    with left:
        fig_bh = go.Figure()
        fig_bh.add_trace(
            go.Scatter(
                x=series["period"],
                y=series["avg_fr_bh"],
                name="Avg FR business hours",
                line=dict(color="#059669", width=2),
            )
        )
        fig_bh.add_trace(
            go.Scatter(
                x=series["period"],
                y=series["avg_res_bh"],
                name="Avg resolution business hours",
                line=dict(color="#D97706", width=2),
            )
        )
        fig_bh.add_hline(
            y=24,
            line_dash="dash",
            line_color="#DC2626",
            annotation_text="SLA limit (24 bh)",
            annotation_position="top right",
        )
        fig_bh.update_layout(
            title="Average business hours to respond / resolve",
            yaxis_title="Business hours",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(style_fig(fig_bh, 360), width="stretch")

    with right:
        fig_cal = go.Figure()
        fig_cal.add_trace(
            go.Scatter(
                x=series["period"],
                y=series["avg_frt"],
                name="Avg FRT (calendar h)",
                line=dict(color="#2563EB", width=2),
            )
        )
        fig_cal.add_trace(
            go.Scatter(
                x=series["period"],
                y=series["avg_res"],
                name="Avg resolution (calendar h)",
                line=dict(color="#7C3AED", width=2),
            )
        )
        fig_cal.update_layout(
            title="Average calendar hours (wall-clock)",
            yaxis_title="Hours",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(style_fig(fig_cal, 360), width="stretch")

    section("SLA breaches", f"Tickets past the 24 business-hour target · {grain.lower()} grain.")
    # --- Breach volume ---
    fig_breach = go.Figure()
    fig_breach.add_trace(
        go.Bar(
            x=series["period"],
            y=series["fr_breaches"],
            name="First response breaches",
            marker_color="#F87171",
        )
    )
    fig_breach.add_trace(
        go.Bar(
            x=series["period"],
            y=series["res_breaches"],
            name="Resolution breaches",
            marker_color="#FB923C",
        )
    )
    fig_breach.update_layout(
        title=f"SLA breaches over time ({grain.lower()})",
        yaxis_title="Tickets past SLA",
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(style_fig(fig_breach, 360), width="stretch")

    # --- By support group ---
    section("By support group", "Team-level attainment and resolution speed.")
    by_group = sla_slice_series(df, "support_group", grain=grain)
    if not by_group.empty:
        g1, g2 = st.columns(2)
        with g1:
            fig_g_fr = px.line(
                by_group,
                x="period",
                y="pct_fr_sla",
                color="support_group",
                color_discrete_map=GROUP_COLORS,
                title="First response SLA by team",
                labels={"pct_fr_sla": "% within SLA", "period": "", "support_group": "Team"},
            )
            fig_g_fr.update_layout(yaxis_tickformat=".0%", legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(style_fig(fig_g_fr, 340), width="stretch")
        with g2:
            fig_g_res = px.line(
                by_group,
                x="period",
                y="pct_res_sla",
                color="support_group",
                color_discrete_map=GROUP_COLORS,
                title="Resolution SLA by team",
                labels={"pct_res_sla": "% within SLA", "period": "", "support_group": "Team"},
            )
            fig_g_res.update_layout(yaxis_tickformat=".0%", legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(style_fig(fig_g_res, 340), width="stretch")

        fig_g_bh = px.line(
            by_group,
            x="period",
            y="avg_res_bh",
            color="support_group",
            color_discrete_map=GROUP_COLORS,
            title="Avg resolution business hours by team",
            labels={"avg_res_bh": "Business hours", "period": "", "support_group": "Team"},
        )
        fig_g_bh.add_hline(y=24, line_dash="dash", line_color="#DC2626")
        fig_g_bh.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(style_fig(fig_g_bh, 340), width="stretch")

    # --- By priority ---
    section("By priority", "Whether urgency correlates with SLA risk.")
    by_pri = sla_slice_series(df, "priority", grain=grain)
    if not by_pri.empty:
        pri_colors = {
            "low": "#94A3B8",
            "normal": "#2563EB",
            "high": "#D97706",
            "urgent": "#DC2626",
        }
        p1, p2 = st.columns(2)
        with p1:
            fig_p_fr = px.line(
                by_pri,
                x="period",
                y="pct_fr_sla",
                color="priority",
                color_discrete_map=pri_colors,
                category_orders={"priority": PRIORITIES},
                title="First response SLA by priority",
                labels={"pct_fr_sla": "% within SLA", "period": "", "priority": "Priority"},
            )
            fig_p_fr.update_layout(yaxis_tickformat=".0%", legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(style_fig(fig_p_fr, 340), width="stretch")
        with p2:
            fig_p_res = px.line(
                by_pri,
                x="period",
                y="pct_res_sla",
                color="priority",
                color_discrete_map=pri_colors,
                category_orders={"priority": PRIORITIES},
                title="Resolution SLA by priority",
                labels={"pct_res_sla": "% within SLA", "period": "", "priority": "Priority"},
            )
            fig_p_res.update_layout(yaxis_tickformat=".0%", legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(style_fig(fig_p_res, 340), width="stretch")

    # --- Summary table for filtered period ---
    section("Period summary by team", "Totals for the sidebar date range (not trend grain).")
    summary = (
        df.assign(
            is_fr_sla_eligible=lambda d: d["is_fr_sla_eligible"].fillna(False).astype(bool),
            is_res_sla_eligible=lambda d: d["is_res_sla_eligible"].fillna(False).astype(bool),
            met_first_response_sla=lambda d: d["met_first_response_sla"].fillna(False).astype(bool),
            met_resolution_sla=lambda d: d["met_resolution_sla"].fillna(False).astype(bool),
        )
        .groupby("support_group", as_index=False)
        .agg(
            tickets=("ticket_id", "count"),
            fr_eligible=("is_fr_sla_eligible", "sum"),
            fr_met=("met_first_response_sla", "sum"),
            res_eligible=("is_res_sla_eligible", "sum"),
            res_met=("met_resolution_sla", "sum"),
            avg_fr_bh=("first_response_business_hours", "mean"),
            avg_res_bh=("resolution_business_hours", "mean"),
        )
    )
    summary["pct_fr_sla"] = summary["fr_met"] / summary["fr_eligible"].replace(0, np.nan)
    summary["pct_res_sla"] = summary["res_met"] / summary["res_eligible"].replace(0, np.nan)
    summary = summary.set_index("support_group").reindex(SUPPORT_GROUPS).dropna(how="all").reset_index()
    st.dataframe(
        summary.assign(
            pct_fr_sla=lambda d: (d["pct_fr_sla"] * 100).round(1),
            pct_res_sla=lambda d: (d["pct_res_sla"] * 100).round(1),
            avg_fr_bh=lambda d: d["avg_fr_bh"].round(1),
            avg_res_bh=lambda d: d["avg_res_bh"].round(1),
            fr_breaches=lambda d: (d["fr_eligible"] - d["fr_met"]).astype(int),
            res_breaches=lambda d: (d["res_eligible"] - d["res_met"]).astype(int),
        )[
            [
                "support_group",
                "tickets",
                "pct_fr_sla",
                "pct_res_sla",
                "avg_fr_bh",
                "avg_res_bh",
                "fr_breaches",
                "res_breaches",
            ]
        ].rename(
            columns={
                "support_group": "Team",
                "tickets": "Tickets",
                "pct_fr_sla": "FR SLA %",
                "pct_res_sla": "Res SLA %",
                "avg_fr_bh": "Avg FR (bh)",
                "avg_res_bh": "Avg Res (bh)",
                "fr_breaches": "FR breaches",
                "res_breaches": "Res breaches",
            }
        ),
        width="stretch",
        hide_index=True,
    )


def tab_prioritization(df: pd.DataFrame, priority_mart: pd.DataFrame) -> None:
    """Tab 2 — Issue Prioritization for Engineering."""
    tab_lead(
        "Which issue categories deserve Engineering and Product attention — and why."
    )

    with st.expander("How the priority score is calculated", expanded=False):
        st.markdown(
            f"""
This score ranks **issue categories** (not individual tickets) using four
min-max-normalized signals, then a weighted sum scaled to 0–100:

| Signal | Weight | Meaning |
|---|---:|---|
| **Frequency** | {PRIORITY_WEIGHTS['frequency']:.0%} | Share of all tickets in this category |
| **Enterprise impact** | {PRIORITY_WEIGHTS['enterprise_impact']:.0%} | Share of category tickets on the Enterprise team |
| **Support cost** | {PRIORITY_WEIGHTS['support_cost']:.0%} | Avg resolution time + reopen burden (hours-equivalent) |
| **Sentiment / impact** | {PRIORITY_WEIGHTS['sentiment_impact']:.0%} | Blend of low-CSAT rate and high-urgency rate |

```
priority_score = 100 × (
    {PRIORITY_WEIGHTS['frequency']} × frequency_norm
  + {PRIORITY_WEIGHTS['enterprise_impact']} × enterprise_impact_norm
  + {PRIORITY_WEIGHTS['support_cost']} × support_cost_norm
  + {PRIORITY_WEIGHTS['sentiment_impact']} × sentiment_impact_norm
)
```

Scores below are **recomputed on the current filter selection** so the ranking
stays consistent with the sidebar. The modeling-layer mart
(`mart_priority_scoring`) uses the same formula on the full dataset.
            """
        )

    scores = compute_priority_scores(df)
    if scores.empty:
        st.info("No tickets match the current filters.")
        return

    section("Category priority ranking", "Higher score → more attention warranted.", first=True)
    fig_rank = px.bar(
        scores.sort_values("priority_score"),
        x="priority_score",
        y="category",
        orientation="h",
        color="priority_score",
        color_continuous_scale="Tealgrn",
        title="Category priority ranking",
        labels={"priority_score": "Priority score", "category": ""},
        text="priority_score",
    )
    fig_rank.update_traces(textposition="outside")
    fig_rank.update_layout(coloraxis_showscale=False)
    st.plotly_chart(style_fig(fig_rank, 420), width="stretch")

    section("What drives the score", "Contribution of each signal to the 0–100 composite.")
    # Signal breakdown stacked bars
    signal_cols = [
        "score_from_frequency",
        "score_from_enterprise",
        "score_from_cost",
        "score_from_sentiment",
    ]
    signal_labels = {
        "score_from_frequency": "Frequency",
        "score_from_enterprise": "Enterprise impact",
        "score_from_cost": "Support cost",
        "score_from_sentiment": "Sentiment / impact",
    }
    melt = scores.melt(
        id_vars=["category", "priority_rank", "priority_score"],
        value_vars=signal_cols,
        var_name="signal",
        value_name="contribution",
    )
    melt["signal"] = melt["signal"].map(signal_labels)
    melt = melt.sort_values(["priority_rank", "signal"])

    fig_stack = px.bar(
        melt,
        x="category",
        y="contribution",
        color="signal",
        title="Score contribution by signal",
        labels={"contribution": "Points (of 100)", "category": "Category"},
        color_discrete_sequence=COLOR_SEQUENCE,
        category_orders={"category": scores["category"].tolist()},
    )
    fig_stack.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(style_fig(fig_stack, 400), width="stretch")

    # Ranked table
    display = scores[
        [
            "priority_rank",
            "category",
            "priority_score",
            "ticket_count",
            "frequency",
            "enterprise_share",
            "avg_support_cost_hours",
            "avg_csat",
            "low_csat_rate",
            "high_urgency_rate",
            "top_driver",
        ]
    ].copy()
    display["frequency"] = (display["frequency"] * 100).round(1)
    display["enterprise_share"] = (display["enterprise_share"] * 100).round(1)
    display["low_csat_rate"] = (display["low_csat_rate"] * 100).round(1)
    display["high_urgency_rate"] = (display["high_urgency_rate"] * 100).round(1)
    display["avg_support_cost_hours"] = display["avg_support_cost_hours"].round(1)
    display["avg_csat"] = display["avg_csat"].round(2)

    st.dataframe(
        display.rename(
            columns={
                "priority_rank": "Rank",
                "category": "Category",
                "priority_score": "Score",
                "ticket_count": "Tickets",
                "frequency": "Volume %",
                "enterprise_share": "Enterprise %",
                "avg_support_cost_hours": "Avg cost (h)",
                "avg_csat": "Avg CSAT",
                "low_csat_rate": "Low CSAT %",
                "high_urgency_rate": "High urgency %",
                "top_driver": "Top driver",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    # Category drill-down
    section("Category drill-down", "Pick a category to inspect signal mix and routing.")
    selected = st.selectbox(
        "Inspect a category",
        options=scores["category"].tolist(),
        index=0,
    )
    cat_df = df[df["category"] == selected]
    cat_row = scores[scores["category"] == selected].iloc[0]
    cat_kpis = kpi_block(cat_df)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Priority score", f"{cat_row['priority_score']:.1f}")
    m2.metric("Tickets", f"{cat_kpis['total']:,}")
    m3.metric("Avg resolution", fmt_hours(cat_kpis["avg_res"]))
    m4.metric("Avg CSAT", f"{cat_kpis['avg_csat']:.2f}" if cat_kpis["avg_csat"] else "—")
    m5.metric("Top driver", cat_row["top_driver"].title())

    d1, d2 = st.columns(2)
    with d1:
        # Signal radar / bar for selected category
        signal_df = pd.DataFrame(
            {
                "Signal": [
                    "Frequency",
                    "Enterprise impact",
                    "Support cost",
                    "Sentiment / impact",
                ],
                "Contribution": [
                    cat_row["score_from_frequency"],
                    cat_row["score_from_enterprise"],
                    cat_row["score_from_cost"],
                    cat_row["score_from_sentiment"],
                ],
            }
        )
        fig_sig = px.bar(
            signal_df,
            x="Signal",
            y="Contribution",
            title=f"Signal breakdown — {selected}",
            color="Signal",
            color_discrete_sequence=COLOR_SEQUENCE,
            text="Contribution",
        )
        fig_sig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
        fig_sig.update_layout(showlegend=False)
        st.plotly_chart(style_fig(fig_sig, 340), width="stretch")

    with d2:
        by_group = (
            cat_df.groupby("support_group", as_index=False)
            .size()
            .rename(columns={"size": "tickets"})
        )
        fig_cg = px.pie(
            by_group,
            names="support_group",
            values="tickets",
            title=f"Routing mix — {selected}",
            color="support_group",
            color_discrete_map=GROUP_COLORS,
            hole=0.45,
        )
        st.plotly_chart(style_fig(fig_cg, 340), width="stretch")

    # Optional: compare to full-history mart
    if not priority_mart.empty and selected in priority_mart["category"].values:
        mart_row = priority_mart[priority_mart["category"] == selected].iloc[0]
        st.caption(
            f"Full-history model rank for **{selected}**: "
            f"#{int(mart_row['priority_rank'])} "
            f"(score {mart_row['priority_score']:.1f}). "
            f"{mart_row['rationale']}"
        )


def tab_capacity(df: pd.DataFrame) -> None:
    """Tab 3 — Capacity & Team Performance."""
    tab_lead(
        "Resolution patterns, agent workload, and long-tail / reopen risk by team."
    )

    if df.empty:
        st.info("No tickets match the current filters.")
        return

    # Resolution time distribution by group
    section("Resolution patterns", "Distribution capped at p99 for readability.", first=True)
    solved = df[df["is_solved"] & df["resolution_time_hours"].notna()].copy()
    # Cap display at p99 for readability
    if not solved.empty:
        p99 = solved["resolution_time_hours"].quantile(0.99)
        solved_plot = solved[solved["resolution_time_hours"] <= p99]

        fig_dist = px.box(
            solved_plot,
            x="support_group",
            y="resolution_time_hours",
            color="support_group",
            color_discrete_map=GROUP_COLORS,
            title="Resolution time distribution by support group (≤ p99)",
            labels={
                "resolution_time_hours": "Resolution hours",
                "support_group": "Support group",
            },
            points=False,
        )
        fig_dist.update_layout(showlegend=False)
        st.plotly_chart(style_fig(fig_dist, 380), width="stretch")
    else:
        st.info("No solved tickets with resolution times in this filter.")

    # Agent workload
    section("Agent workload", "Top agents by ticket volume in the filtered period.")
    agent = (
        df.groupby(["assignee_name", "support_group"], as_index=False)
        .agg(
            tickets=("ticket_id", "count"),
            avg_res=("resolution_time_hours", "mean"),
            avg_frt=("first_response_time_hours", "mean"),
            reopen_rate=("reopen_count", lambda s: (s > 0).mean()),
            avg_csat=("satisfaction_rating", "mean"),
        )
        .sort_values("tickets", ascending=False)
    )

    top_n = st.slider("Show top N agents by volume", 5, 25, 12)
    agent_top = agent.head(top_n)

    fig_agent = px.bar(
        agent_top.sort_values("tickets"),
        x="tickets",
        y="assignee_name",
        color="support_group",
        orientation="h",
        color_discrete_map=GROUP_COLORS,
        title="Agent ticket volume",
        labels={"tickets": "Tickets", "assignee_name": "Agent"},
    )
    fig_agent.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(style_fig(fig_agent, 420), width="stretch")

    st.dataframe(
        agent_top.assign(
            avg_res=lambda d: d["avg_res"].round(1),
            avg_frt=lambda d: d["avg_frt"].round(1),
            reopen_rate=lambda d: (d["reopen_rate"] * 100).round(1),
            avg_csat=lambda d: d["avg_csat"].round(2),
        ).rename(
            columns={
                "assignee_name": "Agent",
                "support_group": "Group",
                "tickets": "Tickets",
                "avg_res": "Avg resolution (h)",
                "avg_frt": "Avg FRT (h)",
                "reopen_rate": "Reopen rate %",
                "avg_csat": "Avg CSAT",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    # Reopen rate + long-tail
    section(
        "Reopen rate & long-tail resolutions",
        "Past-SLA rate uses the 24 business-hour resolution target.",
    )
    c1, c2, c3 = st.columns(3)
    reopen_rate = (df["reopen_count"] > 0).mean()
    # Long-tail vs SLA: more than 3 business days (24 business hours)
    bh = df["resolution_business_hours"]
    past_sla = bh.notna() & (bh > 24)
    very_long = bh.notna() & (bh > 40)  # > 5 business days
    c1.metric("Tickets with ≥1 reopen", fmt_pct(reopen_rate))
    c2.metric("Resolved past SLA (>3 bd)", fmt_pct(past_sla.mean() if len(df) else None))
    c3.metric("Resolved > 5 business days", fmt_pct(very_long.mean() if len(df) else None))

    reopen_by_group = (
        df.groupby("support_group", as_index=False)
        .agg(
            reopen_rate=("reopen_count", lambda s: (s > 0).mean()),
            avg_reopens=("reopen_count", "mean"),
            long_tail_rate=(
                "resolution_business_hours",
                lambda s: (s > 24).mean(),
            ),
        )
        .sort_values("reopen_rate", ascending=False)
    )

    fig_reopen = make_subplots(specs=[[{"secondary_y": True}]])
    fig_reopen.add_trace(
        go.Bar(
            x=reopen_by_group["support_group"],
            y=reopen_by_group["reopen_rate"],
            name="Reopen rate",
            marker_color="#7C3AED",
        ),
        secondary_y=False,
    )
    fig_reopen.add_trace(
        go.Scatter(
            x=reopen_by_group["support_group"],
            y=reopen_by_group["long_tail_rate"],
            name="Past SLA rate (>3 bd)",
            mode="lines+markers",
            line=dict(color="#D97706", width=2.5),
        ),
        secondary_y=True,
    )
    fig_reopen.update_yaxes(title_text="Reopen rate", tickformat=".0%", secondary_y=False)
    fig_reopen.update_yaxes(
        title_text="Past-SLA rate", tickformat=".0%", secondary_y=True
    )
    fig_reopen.update_layout(title="Reopen vs past-SLA resolution by group")
    st.plotly_chart(style_fig(fig_reopen, 360), width="stretch")


def tab_enterprise(df_all_filtered_except_ent: pd.DataFrame) -> None:
    """Tab 4 — Enterprise-only customer-facing style view."""
    tab_lead(
        "Strategic-account lens — always scoped to the Enterprise support team "
        "(sidebar date & priority still apply)."
    )

    ent = df_all_filtered_except_ent[
        df_all_filtered_except_ent["is_enterprise"]
    ].copy()

    if ent.empty:
        st.warning(
            "No Enterprise tickets in the current filter selection. "
            "Include **Enterprise** in Support Group and widen the date range."
        )
        return

    section(
        "Enterprise KPIs",
        f"CSAT goal ≥ {CSAT_GOAL:.1f} · SLA goals ≥ {SLA_GOAL:.0%}",
        first=True,
    )
    kpis = kpi_block(ent)
    render_kpi_row(kpis)
    render_sla_goal_row(kpis)

    grain = render_trend_controls(widget_key="ent_trend_grain")
    cur, prior, cur_label, prior_label = latest_period_metrics(ent, grain)
    if cur is not None and cur_label is not None:
        render_period_kpi_cards(
            cur,
            prior,
            grain=grain,
            current_label=cur_label,
            prior_label=prior_label,
        )

    vol_cfg = VOLUME_GRAIN_CONFIG[grain]
    section(
        "Volume & quality trends",
        f"{grain} grain for the Enterprise segment — synced with Operations and SLA Trends.",
    )
    volume = volume_ops_series(ent, grain)
    sla = sla_period_series(ent, grain=grain)
    csat = csat_period_series(ent, grain=grain)
    if volume.empty:
        st.info("Not enough Enterprise history to plot trends.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=volume["period"],
            y=volume["ticket_volume"],
            name="Volume",
            fill="tozeroy",
            line=dict(color="#059669", width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=volume["period"],
            y=volume["rolling_avg"],
            name=vol_cfg["rolling_name"],
            line=dict(color="#064E3B", width=2, dash="dash"),
        )
    )
    fig.update_layout(
        title=f"Enterprise ticket volume ({grain.lower()})",
        yaxis_title="Tickets",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(style_fig(fig, 360), width="stretch")

    left, right = st.columns(2)
    with left:
        fig_csat = go.Figure()
        if not csat.empty:
            fig_csat.add_trace(
                go.Scatter(
                    x=csat["period"],
                    y=csat["avg_csat"],
                    mode="lines",
                    line=dict(color="#2563EB", width=2),
                    name="Avg CSAT",
                )
            )
        fig_csat.update_layout(
            title=f"Enterprise CSAT trend ({grain.lower()})",
            yaxis=dict(range=[1, 5]),
        )
        fig_csat.add_hline(
            y=CSAT_GOAL,
            line_dash="dot",
            line_color="#94A3B8",
            annotation_text=f"{CSAT_GOAL:.1f} goal",
            annotation_position="top left",
        )
        st.plotly_chart(style_fig(fig_csat, 320), width="stretch")

    with right:
        fig_sla = go.Figure()
        if not sla.empty:
            fig_sla.add_trace(
                go.Scatter(
                    x=sla["period"],
                    y=sla["pct_fr_sla"],
                    name="FR SLA",
                    line=dict(color="#059669"),
                )
            )
            fig_sla.add_trace(
                go.Scatter(
                    x=sla["period"],
                    y=sla["pct_res_sla"],
                    name="Resolution SLA",
                    line=dict(color="#D97706"),
                )
            )
        fig_sla.update_layout(
            title=f"Enterprise SLA attainment ({grain.lower()})",
            yaxis_tickformat=".0%",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        fig_sla.add_hline(
            y=SLA_GOAL,
            line_dash="dot",
            line_color="#94A3B8",
            annotation_text=f"{SLA_GOAL:.0%} goal",
            annotation_position="top left",
        )
        st.plotly_chart(style_fig(fig_sla, 320), width="stretch")

    section("Top enterprise issues", "Highest-volume categories · color encodes CSAT.")
    top_issues = (
        ent.groupby("category", as_index=False)
        .agg(
            tickets=("ticket_id", "count"),
            avg_res=("resolution_time_hours", "mean"),
            avg_csat=("satisfaction_rating", "mean"),
            pct_high_urgency=("is_high_urgency", "mean"),
            reopen_rate=("reopen_count", lambda s: (s > 0).mean()),
        )
        .sort_values("tickets", ascending=False)
    )

    fig_issues = px.bar(
        top_issues,
        x="category",
        y="tickets",
        color="avg_csat",
        color_continuous_scale="RdYlGn",
        title="Enterprise volume by category (color = avg CSAT)",
        labels={"tickets": "Tickets", "category": "Category", "avg_csat": "Avg CSAT"},
    )
    st.plotly_chart(style_fig(fig_issues, 380), width="stretch")

    st.dataframe(
        top_issues.assign(
            avg_res=lambda d: d["avg_res"].round(1),
            avg_csat=lambda d: d["avg_csat"].round(2),
            pct_high_urgency=lambda d: (d["pct_high_urgency"] * 100).round(1),
            reopen_rate=lambda d: (d["reopen_rate"] * 100).round(1),
        ).rename(
            columns={
                "category": "Category",
                "tickets": "Tickets",
                "avg_res": "Avg resolution (h)",
                "avg_csat": "Avg CSAT",
                "pct_high_urgency": "High urgency %",
                "reopen_rate": "Reopen rate %",
            }
        ),
        width="stretch",
        hide_index=True,
    )


REPO_URL = "https://github.com/mpackard24/SUPPORT-ANALYTICS-PROJECT"


def render_about_landing() -> None:
    """High-level project landing page for technical and non-technical readers."""
    st.markdown(
        """
        <div class="about-banner">
          <strong>Alpha demo</strong>
          <p>
            This is an early working prototype built with
            <strong style="color:#F4F7FB">Cursor</strong>
            to show end-to-end support analytics — from synthetic ticket creation
            through capacity simulation to an interactive operations dashboard.
            Expect rough edges: metrics and labels are illustrative, and not every
            data point has been scrubbed or production-hardened.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    section("What this project does", first=True)
    left, right = st.columns(2)
    with left:
        st.markdown(
            """
            <div class="hex-card purple">
              <h4>In plain terms</h4>
              <p>
                Support teams get more tickets than they can answer at once.
                This demo creates realistic ticket traffic, simulates how agents
                would work through that queue, then turns the results into charts
                leaders can use — volume, SLA risk, what to fix next, and where
                capacity is tight.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="hex-card green">
              <h4>Under the hood</h4>
              <p>
                A Python ticket generator feeds a FIFO solving simulation.
                <strong style="color:#F4F7FB">dbt + DuckDB</strong> models clean
                and aggregate the outputs; Streamlit serves the dashboard.
                “Vibe metrics” here means demo-friendly ops KPIs (CSAT, SLA,
                priority scores) — directional signals, not audited production KPIs.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    section(
        "How the pipeline works",
        "Four stages from synthetic intake to the views in this app.",
    )
    st.markdown(
        """
        <div class="about-flow">
          <div class="about-flow-step purple">
            <div class="step-num">01 · Generate</div>
            <h4>Ticket generator</h4>
            <p>
              Builds Zendesk-style intake: priorities, teams, channels, enterprise
              flags, and agent roster context — fully synthetic for the demo.
            </p>
          </div>
          <div class="about-flow-arrow">→</div>
          <div class="about-flow-step pink">
            <div class="step-num">02 · Simulate</div>
            <h4>Solving simulation</h4>
            <p>
              FIFO capacity simulation assigns and resolves work against staffing
              schedules so backlog, wait, and load behave like a real queue.
            </p>
          </div>
          <div class="about-flow-arrow">→</div>
          <div class="about-flow-step green">
            <div class="step-num">03 · Transform</div>
            <h4>dbt + DuckDB</h4>
            <p>
              Staging → intermediate metrics → marts. Business-hours SLA logic and
              Parquet exports power the Streamlit layer.
            </p>
          </div>
          <div class="about-flow-arrow">→</div>
          <div class="about-flow-step cyan">
            <div class="step-num">04 · Explore</div>
            <h4>Dashboard &amp; vibe metrics</h4>
            <p>
              Operations, SLA trends, issue prioritization, capacity, and an
              enterprise lens — interactive filters over the modeled outputs.
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    section(
        "What you can explore in the dashboard",
        "Switch to Dashboard above, then use the tabs.",
    )

    def _explore_card(accent: str, title: str, body: str) -> str:
        return f"""
        <div class="about-flow-step" style="border-top:3px solid {accent};margin:0 0 0.55rem 0;box-sizing:border-box">
          <div class="step-num" style="color:{accent}">Dashboard</div>
          <h4>{title}</h4>
          <p>{body}</p>
        </div>
        """

    explore_cards = [
        (PURPLE, "Support Operations", "Headline volume, team mix, and where work lands."),
        (PINK, "SLA Trends", "Business-hours first-response and resolution attainment."),
        (GREEN, "Issue Prioritization", "Category scores that surface what Engineering should tackle."),
        (CYAN, "Capacity &amp; Team", "Agent load, resolution patterns, and long-tail risk."),
        (AMBER, "Enterprise View", "Strategic-account segment: volume, quality, and top issues."),
    ]

    left, right = st.columns([1, 1], gap="medium")
    with left:
        for accent, title, body in explore_cards:
            st.markdown(_explore_card(accent, title, body), unsafe_allow_html=True)

    with right:
        st.markdown(
            f"""
            <div class="about-flow-step about-controls-panel" style="border-top:3px solid {PURPLE_SOFT}">
              <div class="step-num" style="color:{PURPLE_SOFT}">Controls</div>
              <h4>Sidebar filters</h4>
              <p>
                On the Dashboard view, the left sidebar scopes every chart the same way
                so tabs stay comparable. Change a control once — Operations, SLA,
                Prioritization, Capacity, and Enterprise all update together.
              </p>
              <div class="about-filter-stack">
                <div class="about-filter-item">
                  <strong>Date range</strong>
                  <span>Limits tickets by created date for the period you want to inspect. Use it to zoom into a spike week or compare quarters.</span>
                </div>
                <div class="about-filter-item">
                  <strong>Support groups</strong>
                  <span>Tier 1, Tier 2, Enterprise, and Technical Quality — include any mix to see how work is distributed across teams.</span>
                </div>
                <div class="about-filter-item">
                  <strong>Priority</strong>
                  <span>Low through urgent. Isolate high-severity queues when you want to stress-test SLA and capacity views.</span>
                </div>
                <div class="about-filter-item">
                  <strong>Enterprise toggle</strong>
                  <span>Focus on strategic-account tickets without rebuilding filters on every tab. Enterprise View always uses the Enterprise segment.</span>
                </div>
              </div>
              <p class="about-controls-footnote">
                Tip: start broad on Support Operations, then tighten filters as you move into SLA and Prioritization.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    section("Read this before treating numbers as truth")
    st.markdown(
        """
        - **Synthetic data** — tickets, agents, and outcomes are generated for the demo, not pulled from a live Zendesk instance.
        - **Not fully scrubbed** — field names, categories, and metric definitions may still look unfinished or inconsistent; treat everything as illustrative.
        - **Alpha scope** — the goal was a working Cursor-built path (generate → simulate → model → app), not a finished product analytics suite.
        - **Vibe metrics** — CSAT, SLA flags, and priority scores are demo signals meant to feel operationally useful, not certified KPIs.
        """
    )

    section("Source code")
    st.markdown(
        f"""
        <p class="tab-lead" style="margin-bottom:0.5rem">
          Pipeline scripts, dbt models, and this Streamlit app live in the public repo.
        </p>
        <a class="about-link" href="{REPO_URL}" target="_blank" rel="noopener noreferrer">
          View on GitHub → mpackard24/SUPPORT-ANALYTICS-PROJECT
        </a>
        """,
        unsafe_allow_html=True,
    )
    st.caption(REPO_URL)


# ---------------------------------------------------------------------------
# App entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Support Analytics",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme(max_width=1400)
    page_header(
        "User Operations · Demo",
        "Support Analytics",
        "Synthetic Zendesk data · FIFO capacity simulation · dbt + DuckDB models",
    )

    view = st.radio(
        "App section",
        options=["About", "Dashboard"],
        horizontal=True,
        label_visibility="collapsed",
        key="app_section",
    )

    if view == "About":
        render_about_landing()
        st.sidebar.markdown("**About**")
        st.sidebar.caption("Alpha demo · synthetic pipeline · Cursor prototype")
        st.sidebar.markdown(f"[GitHub repository]({REPO_URL})")
        return

    data_version = processed_data_version()
    tickets = load_tickets(data_version)
    priority_mart = load_priority_mart(data_version)

    if tickets.empty:
        st.error(
            "Processed data not found. From the project root run:\n\n"
            "`python scripts/run_pipeline.py --generate`\n\n"
            "Then reload this app (or use **Rerun** in Streamlit)."
        )
        st.stop()

    start_date, end_date, groups, priorities, enterprise_only = render_sidebar(tickets)

    if not groups or not priorities:
        st.warning("Select at least one support group and one priority.")
        st.stop()

    # Base filter (date / group / priority) — Enterprise tab applies its own segment
    filtered = apply_filters(
        tickets, start_date, end_date, groups, priorities, enterprise_only
    )
    filtered_no_ent_toggle = apply_filters(
        tickets, start_date, end_date, groups, priorities, enterprise_only=False
    )

    filter_bar(
        ticket_count=len(filtered),
        start_date=start_date,
        end_date=end_date,
        groups=groups,
        priorities=priorities,
        enterprise_only=enterprise_only,
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Support Operations",
            "SLA Trends",
            "Issue Prioritization",
            "Capacity & Team",
            "Enterprise View",
        ]
    )

    with tab1:
        tab_operations(filtered)
    with tab2:
        tab_sla_trends(filtered)
    with tab3:
        tab_prioritization(filtered, priority_mart)
    with tab4:
        tab_capacity(filtered)
    with tab5:
        tab_enterprise(filtered_no_ent_toggle)


if __name__ == "__main__":
    main()
