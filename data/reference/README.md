# Agent roster (source of truth)

Edit **`agent_roster.csv`** to manage who is active on each support team and who they report to.

## Columns

| Column | Description |
|---|---|
| `agent_id` | Stable Zendesk-style ID (used on tickets) |
| `name` | Display name |
| `support_group` | Team: Tier 1, Tier 2, Enterprise, Technical Quality |
| `manager` | People-manager name (for capacity / org views) |
| `languages` | Comma-separated language codes (e.g. `en,fr`) |
| `start_date` | Agent start date (`YYYY-MM-DD`) |
| `end_date` | Optional offboard date — agent excluded from schedules after this day |
| `active` | `true` / `false` — inactive agents with no `end_date` are excluded from generation, staffing, and simulation |
| `region` | Home region (`US`, `EMEA`, `APAC`) — should match `agent_schedules.csv` |

## How the pipeline uses this file

1. **`scripts/generate_tickets.py`** loads the roster (see resolution order below), assigns tickets only to **active** agents, and writes a snapshot to `data/generated/zendesk_dummy_agents.csv` for the modeling layer.
2. **`models/staging/stg_tickets.py`** joins `manager`, `active`, and `region` onto tickets via assignee.

## Override order (most specific wins)

1. `data/raw/agent_roster.csv` — drop zone for Fivetran / manual sync from Google Sheets
2. `data/reference/agent_roster.csv` — default editable roster in-repo

Set `AGENT_ROSTER_PATH` to point anywhere else (absolute path).

## Google Sheets workflow (recommended)

**Immediate (no Fivetran):** Share the sheet → File → Download → CSV → save as `data/reference/agent_roster.csv` (or `data/raw/agent_roster.csv`).

**Production-style:** Keep the sheet as the UI; Fivetran (or a scheduled export) lands CSV into `data/raw/agent_roster.csv`. Re-run `python scripts/generate_tickets.py` and `python -m models.run_models`.

Sheet column headers should match this CSV exactly so no transform is needed.

## Specialist schedules & capacity simulation

| File | Purpose |
|---|---|
| `agent_schedules.csv` | Region, timezone, shift pattern, shift start — see `SCHEDULES.md` |
| `capacity_config.yaml` | Productive hours/day and P50 handle times by tier (adjust without code) |
| `productivity_ramp.yaml` | Onboarding ramp days by tier (T1 45d, T2 60d, Enterprise 70d, TQ 90d) |

```bash
python scripts/generate_tickets.py      # incoming tickets (UTC)
python scripts/run_simulation.py        # FIFO assignment + hourly capacity marts
python -m models.run_models             # SLA / priority marts (uses simulated tickets)
python scripts/run_pipeline.py --generate   # all of the above in order
streamlit run dashboard/capacity_app.py # utilization / backlog dashboard
```

Simulation outputs land in `data/processed/`:
- `hourly_staffing_schedule.parquet` — HC + hourly ticket capacity by tier (LA time), built after ticket generation
- `tickets_simulated.csv` — completed ticket dataset from FIFO simulation (through current hour)
- `backlog_hourly.parquet` — hourly backlog, inbound, solved, capacity gap by tier
- `simulation_events.csv` — assignment events
- `mart_hourly_capacity.parquet` — utilization marts
