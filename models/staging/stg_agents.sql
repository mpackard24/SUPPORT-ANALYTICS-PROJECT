{{
  config(
    materialized="view",
    tags=["staging", "agents"],
  )
}}

{# Light clean of the agent roster snapshot. Grain: one row per agent. #}

with source as (
    select * from {{ source("zendesk_raw", "agents") }}
),

renamed as (
    select
        cast(id as integer) as agent_id,
        trim(name) as agent_name,
        trim("group") as agent_group,
        trim(languages) as agent_languages,
        try_cast(start_date as date) as agent_start_date,
        trim(manager) as agent_manager,
        coalesce(cast(active as boolean), true) as agent_is_active,
        trim(region) as agent_region
    from source
    where id is not null
)

select * from renamed
