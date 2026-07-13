"""
Shared Hex Capacity visual theme for Streamlit apps.

Inspired by the Figma Hex Capacity Model design system:
dark navy surfaces, white type, purple / pink / mint accents.
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG = "#070B14"
BG_ELEVATED = "#0E1422"
SURFACE = "#141B2D"
SURFACE_2 = "#1A2338"
BORDER = "#243049"
BORDER_SOFT = "#1C263C"

TEXT = "#F4F7FB"
TEXT_MUTED = "#9AA8C1"
TEXT_DIM = "#6B7A96"

PURPLE = "#A78BFA"
PURPLE_DEEP = "#7C3AED"
PURPLE_SOFT = "#C4B5FD"
PINK = "#F472B6"
PINK_DEEP = "#DB2777"
PINK_SOFT = "#F9A8D4"
GREEN = "#34D399"
GREEN_DEEP = "#059669"
GREEN_SOFT = "#6EE7B7"
CYAN = "#38BDF8"
AMBER = "#FBBF24"

# Semantic roles (three-pillar accents from the design)
ACCENT_VOLUME = PURPLE      # Ticket volume / inbound / staffed
ACCENT_DEFLECTION = PINK    # Needed / deflection / pressure
ACCENT_SPECIALIST = GREEN   # Coverage / capacity / surplus

TEAM_COLORS = {
    "Tier 1": PURPLE,
    "Tier 2": PINK,
    "Enterprise": GREEN,
    "Technical Quality": CYAN,
}

FONT_UI = "DM Sans, Segoe UI, sans-serif"
FONT_DISPLAY = "Space Grotesk, DM Sans, Segoe UI, sans-serif"

PLOTLY_LAYOUT: dict[str, Any] = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor=SURFACE,
    font=dict(family=FONT_UI, size=13, color=TEXT),
    title=dict(
        font=dict(family=FONT_DISPLAY, size=16, color=TEXT),
        # Keep title above the horizontal legend
        y=0.98,
        yanchor="bottom",
        pad=dict(b=28),
    ),
    margin=dict(l=48, r=24, t=96, b=40),
    colorway=[PURPLE, PINK, GREEN, CYAN, AMBER, PURPLE_SOFT],
    xaxis=dict(
        gridcolor=BORDER_SOFT,
        zerolinecolor=BORDER,
        linecolor=BORDER,
        tickfont=dict(color=TEXT_MUTED),
        title_font=dict(color=TEXT_MUTED),
    ),
    yaxis=dict(
        gridcolor=BORDER_SOFT,
        zerolinecolor=BORDER,
        linecolor=BORDER,
        tickfont=dict(color=TEXT_MUTED),
        title_font=dict(color=TEXT_MUTED),
    ),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_MUTED),
        orientation="h",
        yanchor="bottom",
        y=1.02,
    ),
)


def colorscale(accent: str) -> list[list]:
    """Dark-base sequential scale that stays readable with light cell labels."""
    accents = {
        "purple": (PURPLE_SOFT, PURPLE, PURPLE_DEEP),
        "pink": (PINK_SOFT, PINK, PINK_DEEP),
        "green": (GREEN_SOFT, GREEN, GREEN_DEEP),
        "cyan": ("#7DD3FC", CYAN, "#0284C7"),
        "amber": ("#FDE68A", AMBER, "#D97706"),
    }
    soft, mid, deep = accents.get(accent, accents["purple"])
    return [
        [0.0, SURFACE_2],
        [0.12, BORDER],
        [0.4, mid],
        [0.72, soft],
        [1.0, deep],
    ]


def diverging_scale() -> list[list]:
    """Pink (deficit) ↔ navy mid ↔ green (surplus)."""
    return [
        [0.0, PINK_DEEP],
        [0.3, PINK],
        [0.5, SURFACE_2],
        [0.7, GREEN],
        [1.0, GREEN_DEEP],
    ]


def apply_theme(*, max_width: int = 1400) -> None:
    """Inject shared Hex dark theme CSS into the current Streamlit page."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Space+Grotesk:wght@500;600;700&display=swap');

        html, body, [class*="css"], .stApp {{
          font-family: {FONT_UI};
          color: {TEXT};
        }}
        .stApp {{
          background: radial-gradient(1200px 600px at 10% -10%, #1a1040 0%, transparent 55%),
                      radial-gradient(900px 500px at 100% 0%, #0d2a28 0%, transparent 50%),
                      {BG};
          color: {TEXT};
        }}
        .block-container {{
          padding-top: 1.35rem;
          padding-bottom: 2.5rem;
          max-width: {max_width}px;
        }}
        h1, h2, h3, .hex-title {{
          font-family: {FONT_DISPLAY} !important;
          color: {TEXT} !important;
          letter-spacing: -0.02em;
        }}
        p, label, .stMarkdown, .stCaption, span {{
          color: {TEXT_MUTED};
        }}
        [data-testid="stSidebar"] {{
          background: {BG_ELEVATED};
          border-right: 1px solid {BORDER};
        }}
        [data-testid="stSidebar"] * {{
          color: {TEXT_MUTED};
        }}
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {{
          color: {TEXT} !important;
        }}
        div[data-testid="stMetric"] {{
          background: {SURFACE};
          border: 1px solid {BORDER};
          border-top: 3px solid {PURPLE};
          border-radius: 14px;
          padding: 12px 14px;
          box-shadow: 0 10px 30px rgba(0,0,0,0.25);
        }}
        div[data-testid="stMetric"] label {{
          color: {TEXT_MUTED} !important;
          font-size: 0.78rem !important;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
          color: {TEXT} !important;
          font-family: {FONT_DISPLAY} !important;
          font-weight: 700 !important;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
          color: {GREEN} !important;
        }}
        .stTabs [data-baseweb="tab-list"] {{
          gap: 8px;
          border-bottom: 1px solid {BORDER};
        }}
        .stTabs [data-baseweb="tab"] {{
          border-radius: 10px 10px 0 0;
          padding: 10px 16px;
          background: {SURFACE};
          color: {TEXT_MUTED} !important;
          font-weight: 600;
          border: 1px solid {BORDER};
          border-bottom: none;
        }}
        .stTabs [aria-selected="true"] {{
          background: {SURFACE_2} !important;
          color: {TEXT} !important;
          border-top: 3px solid {PURPLE} !important;
        }}
        div[data-testid="stDataFrame"],
        div[data-testid="stDataFrame"] > div {{
          background: {SURFACE};
          border: 1px solid {BORDER};
          border-radius: 12px;
        }}
        .stButton > button {{
          background: {SURFACE};
          color: {TEXT};
          border: 1px solid {BORDER};
          border-radius: 10px;
          font-weight: 600;
        }}
        .stButton > button:hover {{
          border-color: {PURPLE};
          color: {TEXT};
          background: {SURFACE_2};
        }}
        .stSelectbox div[data-baseweb="select"] > div,
        .stMultiSelect div[data-baseweb="select"] > div,
        .stTextInput input,
        .stNumberInput input,
        .stDateInput input {{
          background: {SURFACE} !important;
          color: {TEXT} !important;
          border-color: {BORDER} !important;
        }}
        hr {{
          border-color: {BORDER} !important;
        }}
        .hex-eyebrow {{
          font-size: 0.72rem;
          letter-spacing: 0.14em;
          text-transform: uppercase;
          color: {PURPLE_SOFT};
          font-weight: 600;
          margin: 0 0 0.35rem 0;
        }}
        .hex-title {{
          margin: 0 0 0.35rem 0;
          font-size: 2rem;
          font-weight: 700;
          color: {TEXT};
        }}
        .hex-subtitle {{
          margin: 0;
          color: {TEXT_MUTED};
          font-size: 1rem;
          max-width: 52rem;
          line-height: 1.45;
        }}
        .hex-card {{
          background: {SURFACE};
          border: 1px solid {BORDER};
          border-radius: 16px;
          padding: 1rem 1.15rem 1.1rem;
          margin: 0.75rem 0 1rem;
          box-shadow: 0 14px 40px rgba(0,0,0,0.28);
        }}
        .hex-card.purple {{ border-top: 3px solid {PURPLE}; }}
        .hex-card.pink {{ border-top: 3px solid {PINK}; }}
        .hex-card.green {{ border-top: 3px solid {GREEN}; }}
        .hex-card h3, .hex-card h4 {{
          color: {TEXT} !important;
          margin: 0.15rem 0 0.4rem 0;
          font-family: {FONT_DISPLAY} !important;
        }}
        .hex-card p {{
          color: {TEXT_MUTED};
          margin: 0;
          font-size: 0.92rem;
        }}
        .demo-section {{
          margin: 1.35rem 0 0.35rem 0;
          padding-top: 0.85rem;
          border-top: 1px solid {BORDER};
        }}
        .demo-section.first {{
          border-top: none;
          padding-top: 0.25rem;
          margin-top: 0.75rem;
        }}
        .demo-section h3 {{
          font-family: {FONT_DISPLAY} !important;
          color: {TEXT} !important;
          font-size: 1.2rem !important;
          font-weight: 600 !important;
          letter-spacing: -0.01em;
          margin: 0 0 0.2rem 0 !important;
        }}
        .filter-bar {{
          display: flex;
          flex-wrap: wrap;
          gap: 0.45rem;
          align-items: center;
          margin: 0.25rem 0 1rem 0;
          padding: 0.65rem 0.85rem;
          background: {SURFACE};
          border: 1px solid {BORDER};
          border-radius: 12px;
        }}
        .filter-bar .pill {{
          display: inline-flex;
          align-items: center;
          gap: 0.35rem;
          padding: 0.2rem 0.65rem;
          border-radius: 999px;
          background: {SURFACE_2};
          border: 1px solid {BORDER};
          color: {TEXT_MUTED};
          font-size: 0.78rem;
          font-weight: 500;
        }}
        .filter-bar .pill strong {{
          color: {TEXT};
          font-weight: 600;
        }}
        .filter-bar .count {{
          margin-left: auto;
          color: {PURPLE_SOFT};
          font-weight: 600;
          font-size: 0.85rem;
        }}
        .tab-lead {{
          color: {TEXT_MUTED};
          font-size: 0.95rem;
          line-height: 1.45;
          margin: 0.15rem 0 0.85rem 0;
          max-width: 48rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(
    eyebrow: str,
    title: str,
    subtitle: str | None = None,
) -> None:
    """Page header: accent eyebrow, display title, muted subtitle."""
    sub = f'<p class="hex-subtitle">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"""
        <div style="margin-bottom:0.85rem">
          <div class="hex-eyebrow">{eyebrow}</div>
          <h1 class="hex-title">{title}</h1>
          {sub}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, caption: str | None = None, *, first: bool = False) -> None:
    """Consistent in-tab section title so chart headers are never the only label."""
    cls = "demo-section first" if first else "demo-section"
    st.markdown(
        f'<div class="{cls}"><h3>{title}</h3></div>',
        unsafe_allow_html=True,
    )
    if caption:
        st.caption(caption)


def tab_lead(text: str) -> None:
    """One-line narrative under the tab (avoids duplicating the tab label as an H2)."""
    st.markdown(f'<p class="tab-lead">{text}</p>', unsafe_allow_html=True)


def filter_bar(
    *,
    ticket_count: int,
    start_date,
    end_date,
    groups: list[str],
    priorities: list[str],
    enterprise_only: bool,
) -> None:
    """Compact demo-friendly summary of the active sidebar filters."""
    group_label = "All teams" if len(groups) >= 4 else ", ".join(groups) or "—"
    pri_label = "All priorities" if len(priorities) >= 4 else ", ".join(p.title() for p in priorities) or "—"
    ent_pill = (
        '<span class="pill"><strong>Segment</strong> Enterprise only</span>'
        if enterprise_only
        else ""
    )
    st.markdown(
        f"""
        <div class="filter-bar">
          <span class="pill"><strong>Range</strong> {start_date} → {end_date}</span>
          <span class="pill"><strong>Teams</strong> {group_label}</span>
          <span class="pill"><strong>Priority</strong> {pri_label}</span>
          {ent_pill}
          <span class="count">{ticket_count:,} tickets</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_fig(fig: go.Figure, height: int = 380, **layout_overrides: Any) -> go.Figure:
    """Apply shared dark Plotly chrome to a figure."""
    layout = {**PLOTLY_LAYOUT, "height": height, **layout_overrides}
    # Nested defaults — merge carefully so callers can override
    xaxis = {**PLOTLY_LAYOUT.get("xaxis", {}), **layout_overrides.get("xaxis", {})}  # type: ignore[arg-type]
    yaxis = {**PLOTLY_LAYOUT.get("yaxis", {}), **layout_overrides.get("yaxis", {})}  # type: ignore[arg-type]
    title: dict[str, Any] = {**(PLOTLY_LAYOUT.get("title") or {})}  # type: ignore[arg-type]
    title_override = layout_overrides.get("title")
    if isinstance(title_override, dict):
        title = {**title, **title_override}
    elif isinstance(title_override, str) and title_override:
        title["text"] = title_override
    elif fig.layout.title and fig.layout.title.text:
        title["text"] = fig.layout.title.text
    layout["xaxis"] = xaxis
    layout["yaxis"] = yaxis
    layout["title"] = title
    fig.update_layout(**{k: v for k, v in layout.items() if k not in ("xaxis", "yaxis")})
    fig.update_xaxes(**xaxis)
    fig.update_yaxes(**yaxis)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_UI, size=13, color=TEXT),
    )
    return fig


def heatmap_layout(
    *,
    title: str,
    tz_label: str,
    height: int = 720,
) -> dict[str, Any]:
    return dict(
        title=dict(text=title, font=dict(family=FONT_DISPLAY, size=16, color=TEXT)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=SURFACE,
        font=dict(family=FONT_UI, size=13, color=TEXT),
        margin=dict(l=72, r=24, t=72, b=56),
        height=height,
        coloraxis_colorbar=dict(
            title_font=dict(color=TEXT_MUTED),
            tickfont=dict(color=TEXT_MUTED),
        ),
    )
