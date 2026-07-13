"""
End-to-end pipeline: generate tickets (optional) → simulate → dbt models.

Usage (from SUPPORT-ANALYTICS-PROJECT root):
    python scripts/run_pipeline.py --generate
    python scripts/run_pipeline.py --force-simulation
    python scripts/run_pipeline.py --skip-simulation   # dbt only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run support analytics pipeline")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Regenerate intake tickets (not needed for roster-only changes)",
    )
    parser.add_argument(
        "--skip-simulation",
        action="store_true",
        help="Skip simulation; dbt uses existing tickets_simulated.csv",
    )
    parser.add_argument(
        "--force-simulation",
        action="store_true",
        help="Re-run simulation even if outputs appear fresh",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Pass --full-refresh to dbt (required after a full re-simulation)",
    )
    args = parser.parse_args()

    py = sys.executable
    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(PROJECT_ROOT)

    if args.generate:
        print("Step 1/3: Generating intake tickets + hourly staffing schedule …")
        subprocess.check_call(
            [py, str(PROJECT_ROOT / "scripts" / "generate_tickets.py")],
            cwd=PROJECT_ROOT,
        )
    else:
        print("Step 1/3: Skipping ticket generation")

    if not args.skip_simulation:
        sim_cmd = [py, str(PROJECT_ROOT / "scripts" / "run_simulation.py")]
        if args.force_simulation or args.generate:
            sim_cmd.append("--force-rebuild-staffing")
        print("Step 2/3: Running FIFO simulation + backlog marts …")
        subprocess.check_call(sim_cmd, cwd=PROJECT_ROOT)
    else:
        print("Step 2/3: Skipping simulation")

    dbt_cmd = [py, "-m", "dbt", "run", "--project-dir", str(PROJECT_ROOT)]
    if args.full_refresh or args.generate or args.force_simulation:
        dbt_cmd.append("--full-refresh")
        print("Step 3/3: Building dbt models (full refresh) …")
    else:
        print("Step 3/3: Building dbt models (incremental where configured) …")
    subprocess.check_call(dbt_cmd, cwd=PROJECT_ROOT, env=env)

    print("Done. Parquet exports are in data/processed/ for Streamlit.")


if __name__ == "__main__":
    main()
