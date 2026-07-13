#!/usr/bin/env python3
"""
Zendesk-like Synthetic Ticket Data Generator for Analytics

This script generates realistic multi-year synthetic support ticket data for analytics and machine learning use cases.
It simulates Zendesk-style tickets, with realistic team routing, seasonal and annual volume trends, customer and agent details,
resolution metrics, and useful columns for downstream analysis.

Key features:
- Seasonality and year-over-year volume growth
- Realistic agent/assignee with language skills and start dates
- Language-matching for non-English tickets
- Configurable proportions of Enterprise/Pro/Starter customer tiers
- Analytics columns: is_enterprise, customer_tier, structured tags, reopened flag, etc.

USAGE:
    python scripts/generate_tickets.py

AGENT ROSTER (edit separately — not in this file):
    data/reference/agent_roster.csv          # default source of truth
    data/raw/agent_roster.csv                # Fivetran / Sheets sync override

OUTPUT:
    data/generated/zendesk_dummy_tickets.csv
    data/generated/zendesk_dummy_agents.csv  (roster snapshot for models)

Incoming ticket timestamps are UTC, biased to each support team's regional work hours.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import random
from faker import Faker
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from agent_roster import load_agent_roster, resolve_roster_path, roster_to_export_df

# =============================================================================
# CONFIGURATION - Update these parameters to control ticket generation
# =============================================================================

SEED: int = 42

# Date range of simulated tickets (UTC).
# END_DATE=None → generate through the current UTC time on each run.
START_DATE: datetime = datetime(2023, 1, 1, tzinfo=timezone.utc)
END_DATE: datetime | None = None


def effective_end_date() -> datetime:
    """Inclusive generation horizon — defaults to now (UTC)."""
    if END_DATE is None:
        return datetime.now(timezone.utc)
    return min(END_DATE, datetime.now(timezone.utc))

# Baseline average (before growth & seasonality)
BASE_DAILY_TICKETS: int = 200

# Output files (written under data/generated/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "generated"
OUTPUT_TICKETS = OUTPUT_DIR / "zendesk_dummy_tickets.csv"
OUTPUT_AGENTS = OUTPUT_DIR / "zendesk_dummy_agents.csv"

# Loaded at runtime from data/reference/agent_roster.csv (see agent_roster.py)
AGENTS: List[Dict[str, Any]] = []
GROUPS: List[str] = []

# Proportion of enterprise tickets (as %): recommended between 18-22%
ENTERPRISE_PROB: float = 0.20

# Customer tier mix (approximate, sum should be ~1.0)
CUSTOMER_TIER_WEIGHTS: Dict[str, float] = {
    "Starter": 0.38,
    "Pro": 0.42,
    "Enterprise": 0.20,
}

# Annual growth rate (~33% per year compounds nicely)
ANNUAL_GROWTH_RATE: float = 0.40

# Optional safety ceiling (None = auto from base + growth span + peak seasonality)
MAX_DAILY_TICKETS: int | None = None

# Monthly seasonality multipliers (tune as needed)
MONTH_FACTORS: Dict[int, float] = {
    1: 0.82,   # Post-holiday lull
    2: 0.88,
    3: 1.02,
    4: 0.98,
    5: 0.95,
    6: 0.85,   # Summer slowdown begins
    7: 0.80,   # Lowest month
    8: 0.88,
    9: 1.05,
    10: 1.15,  # Back to school + Q4 ramp
    11: 1.22,  # Pre-holiday spike
    12: 1.28,  # Highest month (holiday support surge)
}

# =============================================================================
# INCOMING TICKET TIMING — region-aware workweek bias, timestamps stored as UTC
# =============================================================================
# Same pattern as the original Tier 1 / EMEA example, applied per region so
# US and APAC teams receive volume during their local business hours.

REGION_UTC_CORE: Dict[str, Tuple[List[int], List[float]]] = {
    # ~08:00–18:00 local → UTC windows
    "EMEA": (list(range(7, 18)), [2, 4, 7, 9, 10, 10, 9, 8, 7, 6, 4]),
    "US": (list(range(13, 23)), [3, 5, 8, 10, 10, 9, 8, 7, 5, 3]),
    "APAC": (list(range(0, 10)), [4, 6, 8, 10, 10, 9, 7, 5, 3, 2]),
}

REGION_UTC_SHOULDER: Dict[str, List[int]] = {
    "EMEA": [5, 6, 18, 19, 20],
    "US": [11, 12, 23, 0, 1],
    "APAC": [22, 23, 10, 11, 12],
}

# Weekend UTC: thin off-hours trickle (global users / async follow-ups)
WEEKEND_UTC_HOURS = [0, 1, 2, 22, 23]

# Daily volume: weekday vs weekend (UTC calendar day)
WEEKDAY_VOLUME_FACTOR = 1.0
WEEKEND_VOLUME_FACTOR = 0.18

# Filled by init_agents() from agent_schedules.csv regions per support group
TEAM_REGION_WEIGHTS: Dict[str, Dict[str, float]] = {}

# =============================================================================
# TICKET CATEGORIES, PRIORITIES, LANGUAGES, CHANNELS
# =============================================================================

CATEGORIES: List[str] = [
    "Billing Inquiry", "Technical Issue", "Account Access Problem",
    "Bug Report", "Feature Request", "How-to Question",
    "Cancellation Request", "Integration Support", "Performance Issue",
    "Security & Access Concern"
]
CATEGORY_WEIGHTS: List[float] = [0.17, 0.23, 0.11, 0.15, 0.08, 0.10, 0.05, 0.06, 0.03, 0.02]

PRIORITIES: List[str] = ["low", "normal", "high", "urgent"]
PRIORITY_WEIGHTS: List[float] = [0.20, 0.50, 0.20, 0.10]

LANGUAGES: List[str] = ["en", "es", "fr", "de", "pt", "it", "nl"]
LANGUAGE_WEIGHTS: List[float] = [0.75, 0.08, 0.05, 0.04, 0.03, 0.03, 0.02]

CHANNELS: List[str] = ["email"] * 7 + ["web", "chat"]

# =============================================================================
# TEAM ROUTING (volume mix only — assignment/completion from simulation)
# =============================================================================

SUPPORT_GROUP_WEIGHTS: Dict[str, float] = {
    "Tier 1": 0.75,
    "Tier 2": 0.08,
    "Enterprise": 0.12,
    "Technical Quality": 0.05,
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def init_agents() -> None:
    """Load roster + schedule regions for logging and region-aware intake timing."""
    global AGENTS, GROUPS, TEAM_REGION_WEIGHTS
    AGENTS = load_agent_roster(active_only=True)
    GROUPS = sorted({a["group"] for a in AGENTS})

    schedules_path = PROJECT_ROOT / "data" / "reference" / "agent_schedules.csv"
    raw_schedules = PROJECT_ROOT / "data" / "raw" / "agent_schedules.csv"
    path = raw_schedules if raw_schedules.exists() else schedules_path
    sched = pd.read_csv(path, dtype={"agent_id": "Int64"})
    sched.columns = [c.strip().lower() for c in sched.columns]
    region_by_id = {
        int(r.agent_id): str(r.region).strip().upper()
        for r in sched.itertuples(index=False)
    }

    weights: Dict[str, Dict[str, float]] = {}
    for agent in AGENTS:
        group = agent["group"]
        region = region_by_id.get(int(agent["id"]), str(agent.get("region", "EMEA")).upper())
        bucket = weights.setdefault(group, {})
        bucket[region] = bucket.get(region, 0.0) + 1.0
    for group, regions in weights.items():
        total = sum(regions.values()) or 1.0
        TEAM_REGION_WEIGHTS[group] = {r: n / total for r, n in regions.items()}
    # Fallback for any configured team missing from active roster
    for group in SUPPORT_GROUP_WEIGHTS:
        TEAM_REGION_WEIGHTS.setdefault(group, {"EMEA": 1.0})


def _sample_region_for_group(support_group: str) -> str:
    mix = TEAM_REGION_WEIGHTS.get(support_group) or {"EMEA": 1.0}
    regions = list(mix.keys())
    weights = [mix[r] for r in regions]
    return random.choices(regions, weights=weights, k=1)[0]


def sample_created_at_utc(day: datetime, support_group: str | None = None) -> datetime:
    """
    Sample ticket created_at in UTC, biased to the support team's regional hours.

    Mirrors the original Tier 1 / EMEA weekday pattern for every region:
    ~86% core business hours, shoulder, then residual off-hours.
    """
    weekday = day.weekday()  # Mon=0 … Sun=6 (UTC)
    region = _sample_region_for_group(support_group or "Tier 1")
    core_hours, core_weights = REGION_UTC_CORE.get(region, REGION_UTC_CORE["EMEA"])
    shoulder = REGION_UTC_SHOULDER.get(region, REGION_UTC_SHOULDER["EMEA"])

    if weekday >= 5:
        hour = random.choice(WEEKEND_UTC_HOURS)
    elif random.random() < 0.86:
        hour = random.choices(core_hours, weights=core_weights)[0]
    elif random.random() < 0.75:
        hour = random.choice(shoulder)
    else:
        # Residual global off-hours
        hour = random.choice([h for h in range(24) if h not in core_hours])

    created = day.replace(
        hour=hour,
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        tzinfo=timezone.utc,
    )
    return created


def sample_support_group() -> str:
    """Sample support team from global volume mix."""
    groups = list(SUPPORT_GROUP_WEIGHTS.keys())
    weights = [SUPPORT_GROUP_WEIGHTS[g] for g in groups]
    return random.choices(groups, weights=weights, k=1)[0]


def generate_subject_and_description(
    category: str, language: str, requester_name: str
) -> Tuple[str, str]:
    """
    Generate realistic subject + email body.
    """
    product = random.choice(["Pro Plan", "Enterprise", "Starter", "API v2", "Dashboard", "Integrations Hub"])
    month = random.choice(["January", "Q2", "last month"])
    amount = f"${random.randint(29, 899)}"
    error_code = random.choice(["ERR-4012", "AUTH-503", "SYNC-7721", "API-429"])
    feature = random.choice(["export", "webhook", "SSO", "bulk import", "reporting", "automation"])

    subjects = {
        "Billing Inquiry": [f"Question about my {month} invoice ({amount})",
                            f"Unexpected charge on {product} subscription"],
        "Technical Issue": [f"{product} not loading / {error_code}",
                            f"Error trying to {feature} in {product}"],
        "Account Access Problem": [f"Can't log into my {product} account",
                                   f"SSO / password reset issues"],
        "Bug Report": [f"Bug: {feature} broken after update",
                       f"{error_code} when using {feature}"],
        "Feature Request": [f"Feature request: {feature} capability",
                            f"Suggestion to improve {product} {feature}"],
        "How-to Question": [f"How do I {feature} in {product}?",
                            f"Best way to configure {feature}?"],
        "Cancellation Request": [f"Request to cancel {product} subscription",
                                 f"Please help downgrade/cancel my account"],
        "Integration Support": [f"Issue connecting {product} to {random.choice(['Salesforce','HubSpot','Jira'])}",
                                f"{feature} integration not syncing"],
        "Performance Issue": [f"{product} is very slow",
                              f"Reports taking forever to load"],
        "Security & Access Concern": [f"Suspicious login activity on {product}",
                                      f"Need to review access permissions"],
    }

    subject = random.choice(subjects.get(category, [f"Issue with {product}"]))

    body = f"""Hi Support Team,

I'm having an issue with {product}. {random.choice([
    "It started a few days ago.",
    "This is blocking my team from completing work.",
    "We've tried basic troubleshooting already."
])}

{fake.paragraph(nb_sentences=random.randint(2, 3))}

Could you please take a look?

Thanks,
{requester_name}
"""
    body = body.strip()

    if language != "en" and random.random() < 0.10:
        body = f"[Originally in {language.upper()}] {body}"

    return subject, body


def generate_structured_tags(
    category: str,
    priority: str,
    language: str,
    support_group: str,
    is_enterprise: bool,
    customer_tier: str,
) -> str:
    tags = [
        f"category={category.lower().replace(' ', '_')}",
        f"priority={priority}",
        f"lang={language}",
        f"support_group={support_group.lower().replace(' ', '_')}",
        f"customer_tier={customer_tier.lower()}",
        f"is_enterprise={str(is_enterprise).lower()}",
    ]
    return "|".join(tags)


def generate_ticket(
    i: int,
    created_at: datetime,
    *,
    support_group: str | None = None,
) -> Dict[str, Any]:
    """Generate intake-only ticket (no assignment or completion fields)."""
    is_enterprise = random.random() < ENTERPRISE_PROB
    customer_tier = random.choices(
        ["Starter", "Pro", "Enterprise"],
        weights=[
            CUSTOMER_TIER_WEIGHTS["Starter"],
            CUSTOMER_TIER_WEIGHTS["Pro"],
            CUSTOMER_TIER_WEIGHTS["Enterprise"],
        ],
        k=1,
    )[0]
    if customer_tier == "Enterprise":
        is_enterprise = True
    elif is_enterprise:
        customer_tier = "Enterprise"

    category: str = random.choices(CATEGORIES, weights=CATEGORY_WEIGHTS, k=1)[0]
    priority: str = random.choices(PRIORITIES, weights=PRIORITY_WEIGHTS, k=1)[0]
    language: str = random.choices(LANGUAGES, weights=LANGUAGE_WEIGHTS, k=1)[0]
    support_group = support_group or sample_support_group()

    requester_name: str = fake.name()
    requester_email: str = fake.email().replace("@example.com", "@mail.com")
    subject, description = generate_subject_and_description(category, language, requester_name)

    tags = generate_structured_tags(
        category=category,
        priority=priority,
        language=language,
        support_group=support_group,
        is_enterprise=is_enterprise,
        customer_tier=customer_tier,
    )

    return {
        "ticket_id": 1122330000 + i,
        "created_at": created_at,
        "priority": priority,
        "channel": random.choice(CHANNELS),
        "subject": subject,
        "description": description[:2600],
        "support_group": support_group,
        "requester_id": abs(hash(requester_email)) % 900000000 + 100000000,
        "requester_name": requester_name,
        "requester_email": requester_email,
        "language": language,
        "category": category,
        "tags": tags,
        "is_enterprise": is_enterprise,
        "customer_tier": customer_tier,
    }


def get_daily_multiplier(current_date: datetime) -> float:
    """Year-over-year growth (continuous) × monthly seasonality."""
    elapsed_days = (current_date - START_DATE).total_seconds() / 86400.0
    years_elapsed = max(0.0, elapsed_days / 365.25)
    growth = (1 + ANNUAL_GROWTH_RATE) ** years_elapsed
    month_mult = MONTH_FACTORS.get(current_date.month, 1.0)
    return growth * month_mult


def max_daily_ticket_cap() -> int:
    """Upper bound for a single day — scales with base volume and growth settings."""
    if MAX_DAILY_TICKETS is not None:
        return MAX_DAILY_TICKETS
    span_years = (effective_end_date() - START_DATE).total_seconds() / (86400.0 * 365.25)
    peak_mult = (1 + ANNUAL_GROWTH_RATE) ** span_years * max(MONTH_FACTORS.values())
    return int(BASE_DAILY_TICKETS * peak_mult * 1.5)

# =============================================================================
# MAIN SCRIPT (DATA GENERATION & OUTPUT)
# =============================================================================

def main() -> None:
    roster_path = resolve_roster_path()
    init_agents()
    active_n = len(AGENTS)

    print("Generating enhanced Zendesk dummy ticket dataset for analytics (Cursor Exercise)...")
    print(f"Agent roster : {roster_path} ({active_n} active agents)")
    print(f"Teams        : {', '.join(GROUPS)}")
    end_bound = effective_end_date()
    print(f"Period       : {START_DATE.date()} → {end_bound.date()} (UTC)")
    print("Ticket flow  : region-aware workweek bias (EMEA/US/APAC by team), UTC timestamps")
    print(f"Base volume  : {BASE_DAILY_TICKETS}/weekday (× growth + seasonality; cap {max_daily_ticket_cap():,}/day)\n")

    random.seed(SEED)
    np.random.seed(SEED)
    global fake
    fake = Faker()
    fake.seed_instance(SEED)

    tickets: List[Dict[str, Any]] = []
    current = START_DATE
    counter = 0
    daily_cap = max_daily_ticket_cap()

    # Generate tickets by UTC calendar day through today
    while current.date() <= end_bound.date():
        is_weekend = current.weekday() >= 5
        multiplier = get_daily_multiplier(current)
        day_factor = WEEKEND_VOLUME_FACTOR if is_weekend else WEEKDAY_VOLUME_FACTOR
        daily_mean = BASE_DAILY_TICKETS * multiplier * day_factor
        # Partial volume on the final (current) day
        if current.date() == end_bound.date():
            day_start = end_bound.replace(hour=0, minute=0, second=0, microsecond=0)
            day_progress = (end_bound - day_start).total_seconds() / 86400.0
            daily_mean *= max(0.05, min(1.0, day_progress))
        daily_n = max(2 if is_weekend else 4, int(np.random.normal(daily_mean, daily_mean * 0.30)))
        daily_n = min(daily_n, daily_cap)

        for _ in range(daily_n):
            support_group = sample_support_group()
            created = sample_created_at_utc(current, support_group)
            # Never emit tickets in the future relative to the generation horizon
            if created > end_bound:
                created = end_bound - timedelta(seconds=random.randint(0, 3599))
            ticket = generate_ticket(counter, created, support_group=support_group)
            tickets.append(ticket)
            counter += 1

        current += timedelta(days=1)

    # Build and format DataFrames
    df = pd.DataFrame(tickets)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    if df["created_at"].dt.tz is not None:
        df["created_at"] = df["created_at"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.sort_values("created_at").reset_index(drop=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
    df.to_csv(OUTPUT_TICKETS, index=False, date_format="%Y-%m-%dT%H:%M:%SZ", encoding="utf-8")

    all_agents = load_agent_roster(active_only=False)
    roster_to_export_df(all_agents).to_csv(OUTPUT_AGENTS, index=False, encoding="utf-8")

    # Hourly staffing schedule (LA timezone) from roster → first ticket hour through now
    sys.path.insert(0, str(_SCRIPTS_DIR))
    from scheduling.hourly_schedule import build_schedule_from_roster, save_hourly_staffing_schedule

    first_ticket = pd.Timestamp(df["created_at"].min(), tz="UTC").to_pydatetime()
    staffing = build_schedule_from_roster(all_agents, first_ticket_utc=first_ticket)
    sched_paths = save_hourly_staffing_schedule(staffing, PROCESSED_DIR)

    # Summary for rapid analytics confidence checks
    print("="*70)
    print("GENERATION COMPLETE")
    print("="*70)
    print(f"Total tickets generated: {len(df):,}")
    print(f"Date range: {df['created_at'].min().date()} → {df['created_at'].max().date()}")
    print(f"\nTickets by Support Group (% target in parentheses):")
    counts = df["support_group"].value_counts()
    for grp in SUPPORT_GROUP_WEIGHTS:
        n = counts.get(grp, 0)
        pct = n / len(df) * 100
        target = SUPPORT_GROUP_WEIGHTS[grp] * 100
        print(f"  {grp:<20} {n:>7,}  ({pct:5.1f}%  target {target:.0f}%)")
    print(f"\nLanguage distribution (%):\n{(df['language'].value_counts(normalize=True)*100).round(1).to_string()}%")
    print(f"\nPriority distribution:\n{df['priority'].value_counts().to_string()}")
    print(f"\nCustomer Tier distribution:\n{df['customer_tier'].value_counts().to_string()}")
    print(f"\nEnterprise ticket ratio: {df['is_enterprise'].mean():.2%}")

    print(f"\nAverage weekday volume by year:")
    weekdays = df[df["created_at"].dt.weekday < 5].copy()
    weekdays["year"] = weekdays["created_at"].dt.year
    wd_daily = weekdays.groupby([weekdays["created_at"].dt.date]).size().reset_index(name="n")
    wd_daily["year"] = pd.to_datetime(wd_daily["created_at"]).dt.year
    print(wd_daily.groupby("year")["n"].mean().round(1).to_string())

    print(f"\nFiles saved:")
    print(f"  → {OUTPUT_TICKETS}")
    print(f"  → {OUTPUT_AGENTS}")
    print(f"  → {sched_paths[0]}")
    print(f"  → {sched_paths[1]}")
    print("\nIntake-only tickets. Assignment/completion fields are produced by run_simulation.py.")

if __name__ == "__main__":
    main()
