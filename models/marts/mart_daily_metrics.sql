{{
  config(
    materialized="incremental",
    unique_key=["period_grain", "period_start"],
    incremental_strategy="delete+insert",
    on_schema_change="sync_all_columns",
    tags=["marts", "ops"],
  )
}}

{#
  Operational time-series metrics at daily and weekly grains.
  Incremental: rebuild the trailing lookback window so late-arriving tickets
  and status changes are reflected without a full refresh.
#}

{% set lookback_days = var("daily_metrics_lookback_days", 14) %}

with tickets as (
    select *
    from {{ ref("stg_tickets") }}
    {% if is_incremental() %}
    where created_date >= (
        select coalesce(
            max(period_start) - interval {{ lookback_days }} day,
            cast('1900-01-01' as date)
        )
        from {{ this }}
        where period_grain = 'day'
    )
    {% endif %}
),

daily as (
    select
        'day' as period_grain,
        created_date as period_start,
        count(*) as ticket_volume,
        count(*) filter (where is_solved) as solved_volume,
        round(
            count(*) filter (where is_solved)::double / nullif(count(*), 0),
            4
        ) as solve_rate,
        round(avg(first_response_time_hours), 2) as avg_first_response_hours,
        round(
            avg(resolution_time_hours) filter (where is_solved),
            2
        ) as avg_resolution_hours,
        round(
            avg(satisfaction_rating) filter (where has_csat),
            2
        ) as avg_csat,
        count(*) filter (where has_csat) as csat_responses,
        round(
            count(*) filter (where has_csat)::double / nullif(count(*), 0),
            4
        ) as csat_response_rate,
        count(*) filter (where is_enterprise) as enterprise_volume,
        count(*) filter (where is_high_urgency) as high_urgency_volume,
        count(*) filter (where is_open) as open_volume
    from tickets
    group by created_date
),

weekly as (
    select
        'week' as period_grain,
        created_week as period_start,
        count(*) as ticket_volume,
        count(*) filter (where is_solved) as solved_volume,
        round(
            count(*) filter (where is_solved)::double / nullif(count(*), 0),
            4
        ) as solve_rate,
        round(avg(first_response_time_hours), 2) as avg_first_response_hours,
        round(
            avg(resolution_time_hours) filter (where is_solved),
            2
        ) as avg_resolution_hours,
        round(
            avg(satisfaction_rating) filter (where has_csat),
            2
        ) as avg_csat,
        count(*) filter (where has_csat) as csat_responses,
        round(
            count(*) filter (where has_csat)::double / nullif(count(*), 0),
            4
        ) as csat_response_rate,
        count(*) filter (where is_enterprise) as enterprise_volume,
        count(*) filter (where is_high_urgency) as high_urgency_volume,
        count(*) filter (where is_open) as open_volume
    from tickets
    group by created_week
)

select * from daily
union all
select * from weekly
