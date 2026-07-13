{{
  config(
    materialized="table",
    tags=["marts", "sla"],
  )
}}

{#
  SLA attainment rollups across priority, support group, enterprise segment, and overall.
  Full-refresh table — aggregates need the full eligible population.
  Grain: one row per (slice_type, slice_value).
#}

{% set metrics %}
    count(*) filter (where is_fr_sla_eligible) as fr_eligible_tickets,
    count(*) filter (where met_first_response_sla) as fr_met_tickets,
    round(
        count(*) filter (where met_first_response_sla)::double
        / nullif(count(*) filter (where is_fr_sla_eligible), 0),
        4
    ) as pct_within_first_response_sla,
    round(
        avg(first_response_business_hours) filter (where is_fr_sla_eligible),
        2
    ) as avg_first_response_business_hours,
    round(
        avg(first_response_sla_hours) filter (where is_fr_sla_eligible),
        2
    ) as first_response_sla_target_hours,
    count(*) filter (where is_res_sla_eligible) as res_eligible_tickets,
    count(*) filter (where met_resolution_sla) as res_met_tickets,
    round(
        count(*) filter (where met_resolution_sla)::double
        / nullif(count(*) filter (where is_res_sla_eligible), 0),
        4
    ) as pct_within_resolution_sla,
    round(
        avg(resolution_business_hours) filter (where is_res_sla_eligible),
        2
    ) as avg_resolution_business_hours,
    round(
        avg(resolution_sla_hours) filter (where is_res_sla_eligible),
        2
    ) as resolution_sla_target_hours,
    count(*) as ticket_volume
{% endset %}

with by_priority as (
    select
        'priority' as slice_type,
        priority as slice_value,
        {{ metrics }}
    from {{ ref("int_ticket_metrics") }}
    group by priority
),

by_group as (
    select
        'support_group' as slice_type,
        support_group as slice_value,
        {{ metrics }}
    from {{ ref("int_ticket_metrics") }}
    group by support_group
),

by_enterprise as (
    select
        'enterprise_segment' as slice_type,
        case when is_enterprise then 'Enterprise' else 'Non-Enterprise' end as slice_value,
        {{ metrics }}
    from {{ ref("int_ticket_metrics") }}
    group by 2
),

overall as (
    select
        'overall' as slice_type,
        'All Tickets' as slice_value,
        {{ metrics }}
    from {{ ref("int_ticket_metrics") }}
)

select * from by_priority
union all
select * from by_group
union all
select * from by_enterprise
union all
select * from overall
