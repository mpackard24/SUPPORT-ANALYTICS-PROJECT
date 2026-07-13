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
    CYAN,
    TEAM_COLORS,
    AMBER,
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
def render_sidebar(df: pd.DataFrame) -> tuple:
    st.sidebar.title("Filters")
    st.sidebar.caption("Applied across every tab")

    min_date = df["created_date"].min().date()
    max_date = df["created_date"].max().date()

    date_range = st.sidebar.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = min_date, max_date

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


def render_kpi_row(kpis: dict) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Tickets", f"{kpis['total']:,}")
    c2.metric("% Solved", fmt_pct(kpis["pct_solved"]))
    c3.metric("Avg First Response", fmt_hours(kpis["avg_frt"]))
    c4.metric("Avg Resolution", fmt_hours(kpis["avg_res"]))
    c5.metric("Avg CSAT", f"{kpis['avg_csat']:.2f}" if kpis["avg_csat"] else "—")


def tab_operations(df: pd.DataFrame) -> None:
    """Tab 1 — Support Operations Overview."""
    tab_lead(
        "Start here: headline health, inbound volume, and where tickets land by team."
    )

    kpis = kpi_block(df)
    section("Headline KPIs", "Filtered period · calendar hours unless noted", first=True)
    render_kpi_row(kpis)

    sla1, sla2 = st.columns(2)
    sla1.metric(
        "First Response SLA",
        fmt_pct(kpis["pct_fr_sla"]),
        help="Within 3 business days (Mon–Fri 9–5 PT)",
    )
    sla2.metric(
        "Resolution SLA",
        fmt_pct(kpis["pct_res_sla"]),
        help="Within 3 business days (Mon–Fri 9–5 PT)",
    )

    if df.empty:
        st.info("No tickets match the current filters.")
        return

    section(
        "Ticket volume",
        "Toggle the grain — title and rolling average update with the selection.",
    )
    volume_grain = st.radio(
        "Volume grain",
        options=list(VOLUME_GRAIN_CONFIG.keys()),
        index=0,
        horizontal=True,
        key="ops_volume_grain",
        label_visibility="collapsed",
        help="Aggregate ticket volume and the rolling average to the selected period.",
    )
    vol_cfg = VOLUME_GRAIN_CONFIG[volume_grain]
    volume = volume_ops_series(df, volume_grain)
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
    st.caption("For SLA attainment over the same grains, open the **SLA Trends** tab.")

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
        fig_grp = px.bar(
            by_group,
            x="support_group",
            y="tickets",
            color="support_group",
            color_discrete_map=GROUP_COLORS,
            title="Ticket volume by support group",
            labels={"tickets": "Tickets", "support_group": "Group"},
        )
        fig_grp.update_layout(showlegend=False)
        st.plotly_chart(style_fig(fig_grp, 340), width="stretch")

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

    section("Trend controls", "Grain applies to every chart on this tab.", first=True)
    grain = st.radio(
        "Trend grain",
        options=list(VOLUME_GRAIN_CONFIG.keys()),
        index=1,  # Weekly default
        horizontal=True,
        key="sla_trend_period",
        help="Aggregate SLA trends to the selected period. Weekly is a good default for long ranges.",
    )

    series = sla_period_series(df, grain=grain)
    if series.empty:
        st.info("No SLA-eligible tickets in this period.")
        return

    kpis = kpi_block(df)
    fr_elig = int(df["is_fr_sla_eligible"].fillna(False).astype(bool).sum())
    res_elig = int(df["is_res_sla_eligible"].fillna(False).astype(bool).sum())
    fr_met = int(df["met_first_response_sla"].fillna(False).astype(bool).sum())
    res_met = int(df["met_resolution_sla"].fillna(False).astype(bool).sum())
    avg_fr_bh = float(df["first_response_business_hours"].mean()) if "first_response_business_hours" in df else None
    avg_res_bh = float(df["resolution_business_hours"].mean()) if "resolution_business_hours" in df else None

    section("SLA snapshot", "Business-hours metrics for the filtered ticket set.")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("First response SLA", fmt_pct(kpis["pct_fr_sla"]))
    m2.metric("Resolution SLA", fmt_pct(kpis["pct_res_sla"]))
    m3.metric("Avg FR (business h)", fmt_hours(avg_fr_bh))
    m4.metric("Avg resolution (business h)", fmt_hours(avg_res_bh))
    m5.metric("FR breaches", f"{fr_elig - fr_met:,}")
    m6.metric("Resolution breaches", f"{res_elig - res_met:,}")

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
        y=1.0,
        line_dash="dot",
        line_color="#94A3B8",
        annotation_text="100% target",
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

    section("Enterprise KPIs", first=True)
    kpis = kpi_block(ent)
    render_kpi_row(kpis)

    e1, e2 = st.columns(2)
    e1.metric("First Response SLA", fmt_pct(kpis["pct_fr_sla"]))
    e2.metric("Resolution SLA", fmt_pct(kpis["pct_res_sla"]))

    section("Volume & quality trends", "Daily grain for the Enterprise segment.")
    daily = daily_ops_series(ent)
    if daily.empty:
        st.info("Not enough Enterprise history to plot trends.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=daily["created_date"],
            y=daily["ticket_volume"],
            name="Volume",
            fill="tozeroy",
            line=dict(color="#059669", width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=daily["created_date"],
            y=daily["rolling_7d"],
            name="7-day avg",
            line=dict(color="#064E3B", width=2, dash="dash"),
        )
    )
    fig.update_layout(
        title="Enterprise ticket volume",
        yaxis_title="Tickets",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(style_fig(fig, 360), width="stretch")

    left, right = st.columns(2)
    with left:
        fig_csat = go.Figure()
        fig_csat.add_trace(
            go.Scatter(
                x=daily["created_date"],
                y=daily["avg_csat"],
                mode="lines",
                line=dict(color="#2563EB", width=2),
                name="Avg CSAT",
            )
        )
        fig_csat.update_layout(title="Enterprise CSAT trend", yaxis=dict(range=[1, 5]))
        st.plotly_chart(style_fig(fig_csat, 320), width="stretch")

    with right:
        fig_sla = go.Figure()
        fig_sla.add_trace(
            go.Scatter(
                x=daily["created_date"],
                y=daily["pct_fr_sla"],
                name="FR SLA",
                line=dict(color="#059669"),
            )
        )
        fig_sla.add_trace(
            go.Scatter(
                x=daily["created_date"],
                y=daily["pct_res_sla"],
                name="Resolution SLA",
                line=dict(color="#D97706"),
            )
        )
        fig_sla.update_layout(
            title="Enterprise SLA attainment",
            yaxis_tickformat=".0%",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
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
