# Support Analytics Project

Synthetic Zendesk-style support analytics: ticket generation → FIFO simulation → **dbt + DuckDB** transforms → Streamlit dashboard.

Designed to be moved into its own git repo and published on Streamlit Community Cloud.

## Project layout

```
SUPPORT-ANALYTICS-PROJECT/
├── app.py                      # Streamlit dashboard (publish this)
├── theme.py                    # Shared Plotly / Streamlit theme
├── support_analytics/          # App helpers (paths, cache version)
├── dbt_project.yml             # dbt project
├── profiles.yml                # DuckDB profile (set DBT_PROFILES_DIR=.)
├── models/
│   ├── sources.yml             # Raw ticket + agent sources
│   ├── staging/                # Views + schema.yml tests
│   ├── intermediate/           # Incremental SLA metrics
│   └── marts/                  # Ops / SLA / priority tables
├── macros/                     # Business-hours SLA + Parquet export
├── scripts/                    # Generate tickets + FIFO simulation
└── data/
    ├── reference/              # Roster, schedules, capacity YAML
    ├── generated/              # Intake tickets + agents CSV
    └── processed/              # Simulation + dbt Parquet exports
```

## Quick start

```bash
cd SUPPORT-ANALYTICS-PROJECT
# Python 3.10+ required (Streamlit does not support 3.9.7)
python3.12 -m venv .venv   # or python3.11 / python3.10
source .venv/bin/activate
pip install -r requirements.txt

# One-shot: generate → simulate → dbt run (+ export Parquet for Streamlit)
export DBT_PROFILES_DIR="$(pwd)"
dbt deps
python scripts/run_pipeline.py --generate

# Dashboard
streamlit run app.py
```

### dbt-only (after simulation exists)

```bash
export DBT_PROFILES_DIR="$(pwd)"
dbt run                 # incremental where configured
dbt run --full-refresh  # after a full re-simulation
dbt test                # staging + mart tests
dbt docs generate && dbt docs serve
```

## dbt model layers

| Layer | Models | Materialization | Notes |
|-------|--------|-----------------|-------|
| **Staging** | `stg_agents`, `stg_tickets` | view | Clean types, flags, agent join |
| **Intermediate** | `int_ticket_metrics` | **incremental** (merge on `ticket_id`) | Business-hours SLA + support cost |
| **Marts** | `mart_daily_metrics` | **incremental** (delete+insert) | Daily/weekly ops KPIs |
| **Marts** | `mart_sla_performance`, `mart_priority_scoring` | table | Full refresh — need full population for rollups / min-max norms |

After each `dbt run`, `on-run-end` exports app-facing tables to `data/processed/*.parquet`.

### SLA policy (vars in `dbt_project.yml`)

- Timezone: `America/Los_Angeles`
- Window: Mon–Fri 09:00–17:00
- Target: 24 business hours (3 business days)
- Implemented in `macros/business_hours.sql`

### Staging tests

See `models/staging/schema.yml`:

- `unique` / `not_null` on keys
- `accepted_values` on priority, support group, status
- `relationships` from `stg_tickets.assignee_id` → `stg_agents.agent_id` (warn when assigned)
- `dbt_utils.accepted_range` on CSAT 1–5

## Publishing on Streamlit

1. Move this folder to its own git repository.
2. Either:
   - **Commit small Parquet samples** for a demo, or
   - Run the pipeline in CI and upload artifacts, or
   - Point the app at cloud storage later.
3. Set the Streamlit main file to `app.py`.
4. Add `requirements.txt` (already included). Large `data/processed` CSVs are gitignored by default — export / host Parquet separately for production demos.

## Regenerating data

| Change | Command |
|--------|---------|
| New ticket volume / date range | Edit `scripts/generate_tickets.py`, then `python scripts/run_pipeline.py --generate` |
| Roster / schedules / capacity | Edit `data/reference/*`, then `python scripts/run_pipeline.py --force-simulation` |
| Transform logic only | `dbt run` (or `--full-refresh` if incremental state is stale) |
