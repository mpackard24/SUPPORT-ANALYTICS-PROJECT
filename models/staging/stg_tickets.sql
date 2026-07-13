{{
  config(
    materialized="view",
    tags=["staging", "tickets"],
  )
}}

{#
  Cleaned ticket grain with lifecycle flags, calendar grains, and agent enrichment.
  Grain: one row per ticket.
#}

with raw_tickets as (
    select * from {{ source("zendesk_raw", "tickets_simulated") }}
),

tickets as (
    select
        cast(ticket_id as bigint) as ticket_id,
        try_cast(created_at as timestamp) as created_at,
        try_cast(updated_at as timestamp) as updated_at,
        try_cast(solved_at as timestamp) as solved_at,
        lower(trim(status)) as status,
        lower(trim(priority)) as priority,
        lower(trim(channel)) as channel,
        trim(subject) as subject,
        description,
        cast(assignee_id as integer) as assignee_id,
        trim(assignee_name) as assignee_name,
        trim(support_group) as support_group,
        -- Simulated export does not include assignee_languages; language match uses agent roster
        cast('' as varchar) as assignee_languages,
        cast(requester_id as bigint) as requester_id,
        trim(requester_name) as requester_name,
        lower(trim(requester_email)) as requester_email,
        lower(trim(language)) as language,
        trim(category) as category,
        tags,
        coalesce(try_cast(is_enterprise as boolean), false) as is_enterprise_flag,
        trim(coalesce(customer_tier, '')) as customer_tier,
        try_cast(satisfaction_rating as double) as satisfaction_rating,
        try_cast(resolution_time_hours as double) as resolution_time_hours,
        try_cast(first_response_time_hours as double) as first_response_time_hours,
        coalesce(cast(reopen_count as integer), 0) as reopen_count,
        cast('simulation' as varchar) as tickets_source
    from raw_tickets
    where ticket_id is not null
      and created_at is not null
),

enriched as (
    select
        t.ticket_id,
        t.created_at,
        t.updated_at,
        t.solved_at,
        t.status,
        t.priority,
        t.channel,
        t.subject,
        t.description,
        t.assignee_id,
        t.assignee_name,
        t.support_group,
        t.assignee_languages,
        t.requester_id,
        t.requester_name,
        t.requester_email,
        t.language,
        t.category,
        t.tags,
        t.is_enterprise_flag,
        t.customer_tier,
        t.satisfaction_rating,
        t.resolution_time_hours,
        t.first_response_time_hours,
        t.reopen_count,
        t.tickets_source,

        -- Lifecycle flags
        t.status in ('solved', 'closed') as is_solved,
        t.status in ('open', 'pending') as is_open,
        coalesce(t.is_enterprise_flag, t.support_group = 'Enterprise') as is_enterprise,
        t.priority in ('high', 'urgent') as is_high_urgency,
        t.satisfaction_rating is not null as has_csat,
        t.satisfaction_rating <= 2 as is_low_csat,

        -- Calendar grains
        cast(t.created_at as date) as created_date,
        date_trunc('week', t.created_at)::date as created_week,
        date_trunc('month', t.created_at)::date as created_month,
        extract(year from t.created_at)::integer as created_year,
        extract(month from t.created_at)::integer as created_month_num,
        extract(dow from t.created_at)::integer as created_dow,
        extract(dow from t.created_at) in (0, 6) as is_weekend,

        -- Agent enrichment
        a.agent_start_date,
        a.agent_group,
        a.agent_manager,
        a.agent_is_active,
        a.agent_region,
        case
            when a.agent_start_date is not null
            then date_diff('day', a.agent_start_date, cast(t.created_at as date))
            else null
        end as agent_tenure_days,
        case
            when t.language is null
              or coalesce(a.agent_languages, t.assignee_languages) is null
            then null
            when list_contains(
                string_split(coalesce(a.agent_languages, t.assignee_languages), ','),
                t.language
            ) then true
            else false
        end as is_language_match

    from tickets t
    left join {{ ref("stg_agents") }} a
        on t.assignee_id = a.agent_id
)

select * from enriched
