{{
  config(
    materialized="incremental",
    unique_key="ticket_id",
    incremental_strategy="merge",
    on_schema_change="sync_all_columns",
    tags=["intermediate", "sla"],
  )
}}

{#
  Ticket-level SLA and cost metrics.
  Grain: one row per ticket (same as stg_tickets).

  Incremental: merge new/changed tickets by created_at watermark.
  Re-run with --full-refresh after a full re-simulation.
#}

with base as (
    select
        ticket_id,
        created_at,
        created_date,
        created_week,
        created_month,
        status,
        priority,
        support_group,
        category,
        is_solved,
        is_enterprise,
        is_high_urgency,
        is_low_csat,
        has_csat,
        satisfaction_rating,
        first_response_time_hours,
        resolution_time_hours,
        reopen_count,
        solved_at,
        -- Infer first-response timestamp from calendar duration
        case
            when first_response_time_hours is not null
            then created_at + (first_response_time_hours * interval 1 hour)
            else null
        end as first_response_at
    from {{ ref("stg_tickets") }}
    {% if is_incremental() %}
    where created_at > (
        select coalesce(max(created_at), cast('1900-01-01' as timestamp))
        from {{ this }}
    )
    {% endif %}
),

with_bh as (
    select
        ticket_id,
        created_at,
        created_date,
        created_week,
        created_month,
        status,
        priority,
        support_group,
        category,
        is_solved,
        is_enterprise,
        is_high_urgency,
        is_low_csat,
        has_csat,
        satisfaction_rating,
        first_response_time_hours,
        resolution_time_hours,
        reopen_count,
        {{ business_hours_between("created_at", "first_response_at") }}
            as first_response_business_hours,
        {{ business_hours_between("created_at", "solved_at") }}
            as resolution_business_hours,
        cast({{ var("sla_business_hours_target") }} as double) as first_response_sla_hours,
        cast({{ var("sla_business_hours_target") }} as double) as resolution_sla_hours,
        first_response_time_hours is not null as is_fr_sla_eligible,
        coalesce(is_solved, false) and solved_at is not null as is_res_sla_eligible
    from base
)

select
    ticket_id,
    created_at,
    created_date,
    created_week,
    created_month,
    status,
    priority,
    support_group,
    category,
    is_solved,
    is_enterprise,
    is_high_urgency,
    is_low_csat,
    has_csat,
    satisfaction_rating,
    first_response_time_hours,
    resolution_time_hours,
    first_response_business_hours,
    resolution_business_hours,
    reopen_count,
    first_response_sla_hours,
    resolution_sla_hours,
    is_fr_sla_eligible,
    is_res_sla_eligible,
    case
        when is_fr_sla_eligible
        then first_response_business_hours <= {{ var("sla_business_hours_target") }}
        else null
    end as met_first_response_sla,
    case
        when is_res_sla_eligible
        then resolution_business_hours <= {{ var("sla_business_hours_target") }}
        else null
    end as met_resolution_sla,
    coalesce(resolution_time_hours, 0)
        + coalesce(reopen_count, 0) * {{ var("reopen_cost_hours") }}
        as support_cost_hours
from with_bh
