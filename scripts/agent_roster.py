"""
Load and validate the support agent roster.

Source of truth: data/reference/agent_roster.csv
Override: data/raw/agent_roster.csv (Fivetran / Sheets sync)
Env override: AGENT_ROSTER_PATH
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = PROJECT_ROOT / "data" / "reference" / "agent_roster.csv"
FIVETRAN_DROP = PROJECT_ROOT / "data" / "raw" / "agent_roster.csv"

REQUIRED_COLUMNS = {
    "agent_id",
    "name",
    "support_group",
    "manager",
    "languages",
    "start_date",
    "active",
}


def resolve_roster_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        path = Path(explicit)
    elif env_path := os.environ.get("AGENT_ROSTER_PATH"):
        path = Path(env_path)
    elif FIVETRAN_DROP.exists():
        path = FIVETRAN_DROP
    else:
        path = DEFAULT_ROSTER

    if not path.exists():
        raise FileNotFoundError(
            f"Agent roster not found at {path}. "
            f"Create {DEFAULT_ROSTER} or set AGENT_ROSTER_PATH."
        )
    return path


def _parse_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "active"}


def _parse_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", ""}:
        return None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.strftime("%Y-%m-%d")


def load_agent_roster(
    path: Path | None = None,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Load roster as list of dicts.

    Keys: id, name, group, manager, languages, start_date, end_date, active, region

    For simulation / schedules use active_only=False so offboarded agents
    remain in history until end_date.
    """
    roster_path = resolve_roster_path(path)
    df = pd.read_csv(roster_path, dtype={"agent_id": "Int64"})
    df.columns = [c.strip().lower() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Roster {roster_path} missing columns: {sorted(missing)}")

    if "region" not in df.columns:
        df["region"] = "EMEA"
    if "end_date" not in df.columns:
        df["end_date"] = None

    df["active"] = df["active"].map(_parse_active)
    df["languages"] = df["languages"].astype(str).str.replace("|", ",", regex=False)
    df["start_date"] = df["start_date"].apply(_parse_date)
    df["end_date"] = df["end_date"].apply(_parse_date)

    if active_only:
        df = df[df["active"]]

    if df.empty:
        raise ValueError(f"No agents available in roster (active_only={active_only}).")

    agents: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        agents.append(
            {
                "id": int(row.agent_id),
                "name": str(row.name).strip(),
                "group": str(row.support_group).strip(),
                "manager": str(row.manager).strip(),
                "languages": str(row.languages).strip(),
                "start_date": row.start_date,
                "end_date": row.end_date,
                "active": bool(row.active),
                "region": str(getattr(row, "region", "EMEA")).strip(),
            }
        )
    return agents


def roster_to_export_df(agents: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": a["id"],
                "name": a["name"],
                "group": a["group"],
                "manager": a["manager"],
                "languages": a["languages"],
                "start_date": a["start_date"],
                "end_date": a.get("end_date"),
                "active": a["active"],
                "region": a["region"],
            }
            for a in agents
        ]
    )


def is_agent_employed(agent: dict[str, Any], on_day: date) -> bool:
    start = pd.to_datetime(agent["start_date"]).date()
    if on_day < start:
        return False
    end_raw = agent.get("end_date")
    if end_raw:
        end = pd.to_datetime(end_raw).date()
        if on_day > end:
            return False
    return True
