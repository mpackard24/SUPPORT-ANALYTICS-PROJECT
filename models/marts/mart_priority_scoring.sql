{{
  config(
    materialized="table",
    tags=["marts", "priority"],
  )
}}

{#
  Multi-signal category priority scores for product / ops attention.
  Full-refresh — min-max normalization requires the full category set.
  Grain: one row per category (ranked).
#}

{% set w_freq = var("priority_weight_frequency") %}
{% set w_ent = var("priority_weight_enterprise") %}
{% set w_cost = var("priority_weight_support_cost") %}
{% set w_sent = var("priority_weight_sentiment") %}

with category_raw as (
    select
        category,
        count(*) as ticket_count,
        count(*) filter (where is_enterprise) as enterprise_ticket_count,
        round(
            count(*) filter (where is_enterprise)::double / nullif(count(*), 0),
            4
        ) as enterprise_share,
        round(avg(support_cost_hours), 2) as avg_support_cost_hours,
        round(
            avg(resolution_time_hours) filter (where is_solved),
            2
        ) as avg_resolution_hours,
        round(avg(reopen_count), 3) as avg_reopen_count,
        round(
            avg(satisfaction_rating) filter (where has_csat),
            2
        ) as avg_csat,
        round(
            count(*) filter (where is_low_csat)::double
            / nullif(count(*) filter (where has_csat), 0),
            4
        ) as low_csat_rate,
        round(
            count(*) filter (where is_high_urgency)::double / nullif(count(*), 0),
            4
        ) as high_urgency_rate
    from {{ ref("int_ticket_metrics") }}
    group by category
),

totals as (
    select sum(ticket_count)::double as total_tickets
    from category_raw
),

signals as (
    select
        c.*,
        round(c.ticket_count / t.total_tickets, 4) as frequency,
        round(
            0.5 * coalesce(c.low_csat_rate, 0)
            + 0.5 * coalesce(c.high_urgency_rate, 0),
            4
        ) as sentiment_impact
    from category_raw c
    cross join totals t
),

bounds as (
    select
        min(frequency) as min_freq,
        max(frequency) as max_freq,
        min(enterprise_share) as min_ent,
        max(enterprise_share) as max_ent,
        min(avg_support_cost_hours) as min_cost,
        max(avg_support_cost_hours) as max_cost,
        min(sentiment_impact) as min_sent,
        max(sentiment_impact) as max_sent
    from signals
),

scored as (
    select
        s.category,
        s.ticket_count,
        s.enterprise_ticket_count,
        s.frequency,
        s.enterprise_share,
        s.avg_support_cost_hours,
        s.avg_resolution_hours,
        s.avg_reopen_count,
        s.avg_csat,
        s.low_csat_rate,
        s.high_urgency_rate,
        s.sentiment_impact,
        round(
            (s.frequency - b.min_freq) / nullif(b.max_freq - b.min_freq, 0),
            4
        ) as frequency_norm,
        round(
            (s.enterprise_share - b.min_ent) / nullif(b.max_ent - b.min_ent, 0),
            4
        ) as enterprise_impact_norm,
        round(
            (s.avg_support_cost_hours - b.min_cost)
            / nullif(b.max_cost - b.min_cost, 0),
            4
        ) as support_cost_norm,
        round(
            (s.sentiment_impact - b.min_sent)
            / nullif(b.max_sent - b.min_sent, 0),
            4
        ) as sentiment_impact_norm
    from signals s
    cross join bounds b
),

final as (
    select
        category,
        ticket_count,
        enterprise_ticket_count,
        frequency,
        enterprise_share,
        avg_support_cost_hours,
        avg_resolution_hours,
        avg_reopen_count,
        avg_csat,
        low_csat_rate,
        high_urgency_rate,
        sentiment_impact,
        frequency_norm,
        enterprise_impact_norm,
        support_cost_norm,
        sentiment_impact_norm,
        round(
            100 * (
                {{ w_freq }} * coalesce(frequency_norm, 0)
                + {{ w_ent }} * coalesce(enterprise_impact_norm, 0)
                + {{ w_cost }} * coalesce(support_cost_norm, 0)
                + {{ w_sent }} * coalesce(sentiment_impact_norm, 0)
            ),
            1
        ) as priority_score,
        round(100 * {{ w_freq }} * coalesce(frequency_norm, 0), 1) as score_from_frequency,
        round(100 * {{ w_ent }} * coalesce(enterprise_impact_norm, 0), 1) as score_from_enterprise,
        round(100 * {{ w_cost }} * coalesce(support_cost_norm, 0), 1) as score_from_cost,
        round(100 * {{ w_sent }} * coalesce(sentiment_impact_norm, 0), 1) as score_from_sentiment
    from scored
),

ranked as (
    select
        row_number() over (
            order by priority_score desc, ticket_count desc
        ) as priority_rank,
        *
    from final
)

select
    priority_rank,
    category,
    ticket_count,
    enterprise_ticket_count,
    frequency,
    enterprise_share,
    avg_support_cost_hours,
    avg_resolution_hours,
    avg_reopen_count,
    avg_csat,
    low_csat_rate,
    high_urgency_rate,
    sentiment_impact,
    frequency_norm,
    enterprise_impact_norm,
    support_cost_norm,
    sentiment_impact_norm,
    priority_score,
    score_from_frequency,
    score_from_enterprise,
    score_from_cost,
    score_from_sentiment,
    'Ranked #' || priority_rank::varchar
        || ' — volume share '
        || round(frequency * 100, 1)::varchar || '%'
        || ', enterprise share '
        || round(enterprise_share * 100, 1)::varchar || '%'
        || ', avg support cost '
        || avg_support_cost_hours::varchar || 'h'
        || ', low-CSAT rate '
        || round(coalesce(low_csat_rate, 0) * 100, 1)::varchar || '%'
        || ', high-urgency rate '
        || round(high_urgency_rate * 100, 1)::varchar || '%'
        || '. Top drivers: '
        || case
            when score_from_enterprise >= score_from_frequency
             and score_from_enterprise >= score_from_cost
             and score_from_enterprise >= score_from_sentiment
            then 'enterprise impact'
            when score_from_cost >= score_from_frequency
             and score_from_cost >= score_from_sentiment
            then 'support cost'
            when score_from_sentiment >= score_from_frequency
            then 'sentiment / urgency'
            else 'ticket volume'
        end
        || '.' as rationale
from ranked
order by priority_rank
