# Specialist schedules (paired with agent_roster.csv by agent_id)

Edit **`agent_schedules.csv`** to set each specialist's:

| Column | Example | Description |
|---|---|---|
| `agent_id` | `101` | Must match `agent_roster.csv` |
| `region` | `EMEA` | US, EMEA, or APAC |
| `timezone` | `Europe/Dublin` | IANA timezone for shift localization |
| `shift_pattern` | `M-F` | Five-day pattern (see below) |
| `shift_start_local` | `09:00` | Start of 9-hour shift in local time |

### Shift patterns

| Pattern | Working days (local) |
|---|---|
| `M-F` | Monday – Friday |
| `M-TH` | Monday – Thursday |
| `T-SAT` | Tuesday – Saturday |
| `T-FRI` | Tuesday – Friday |
| `SUN-TH` | Sunday – Thursday |
| `W-SUN` | Wednesday – Sunday |

Shift duration (9 hours) and productive hours per tier live in **`capacity_config.yaml`**.

Override path: `data/raw/agent_schedules.csv` or `AGENT_SCHEDULES_PATH`.
