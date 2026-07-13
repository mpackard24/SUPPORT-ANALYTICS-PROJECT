"""
Shared paths and constants for the Support Analytics Streamlit app.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_GENERATED = PROJECT_ROOT / "data" / "generated"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_REFERENCE = PROJECT_ROOT / "data" / "reference"

TICKETS_SIMULATED_CSV = DATA_PROCESSED / "tickets_simulated.csv"
STG_TICKETS_PARQUET = DATA_PROCESSED / "stg_tickets.parquet"
INT_TICKET_METRICS_PARQUET = DATA_PROCESSED / "int_ticket_metrics.parquet"
MART_PRIORITY_SCORING_PARQUET = DATA_PROCESSED / "mart_priority_scoring.parquet"

PRIORITY_WEIGHTS = {
    "frequency": 0.25,
    "enterprise_impact": 0.30,
    "support_cost": 0.25,
    "sentiment_impact": 0.20,
}


def processed_data_version() -> str:
    """Cache-busting token for Streamlit (mtimes of key artifacts)."""
    paths = [
        INT_TICKET_METRICS_PARQUET,
        STG_TICKETS_PARQUET,
        MART_PRIORITY_SCORING_PARQUET,
        TICKETS_SIMULATED_CSV,
    ]
    parts = [str(p.stat().st_mtime_ns) for p in paths if p.exists()]
    return "|".join(parts) if parts else "empty"
