{#
  Business-hours elapsed between two timestamps.

  Policy:
  - Window: Mon–Fri 09:00–17:00 in var('sla_timezone') (default America/Los_Angeles)
  - Weekends and off-hours do not count
  - Returns NULL if either bound is NULL; 0 if end <= start
#}

{% macro business_hours_between(start_ts, end_ts) -%}
(
  CASE
    WHEN ({{ start_ts }}) IS NULL OR ({{ end_ts }}) IS NULL THEN CAST(NULL AS DOUBLE)
    WHEN CAST(({{ end_ts }}) AS TIMESTAMPTZ) <= CAST(({{ start_ts }}) AS TIMESTAMPTZ) THEN 0.0
    ELSE (
      SELECT COALESCE(SUM(slice_hours), 0.0)
      FROM (
        SELECT
          GREATEST(
            0.0,
            EPOCH(
              LEAST(
                end_local,
                CAST(day_date AS TIMESTAMP) + INTERVAL 17 HOUR
              )
              - GREATEST(
                start_local,
                CAST(day_date AS TIMESTAMP) + INTERVAL 9 HOUR
              )
            ) / 3600.0
          ) AS slice_hours
        FROM (
          SELECT
            timezone(
              '{{ var("sla_timezone") }}',
              CAST(({{ start_ts }}) AS TIMESTAMPTZ)
            ) AS start_local,
            timezone(
              '{{ var("sla_timezone") }}',
              CAST(({{ end_ts }}) AS TIMESTAMPTZ)
            ) AS end_local
        ) AS bounds,
        LATERAL (
          SELECT UNNEST(
            generate_series(
              CAST(bounds.start_local AS DATE),
              CAST(bounds.end_local AS DATE),
              INTERVAL 1 DAY
            )
          ) AS day_date
        ) AS days
        WHERE EXTRACT(isodow FROM CAST(day_date AS DATE)) BETWEEN 1 AND 5
      ) AS slices
    )
  END
)
{%- endmacro %}
