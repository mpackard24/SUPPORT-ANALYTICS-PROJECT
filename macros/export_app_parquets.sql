{#
  Write Streamlit-facing tables to Parquet under var('export_dir').
  Called from on-run-end in dbt_project.yml.
#}

{% macro export_app_parquets() %}
  {% if execute %}
    {% set export_dir = var("export_dir", "data/processed") %}
    {% set tables = [
      "stg_tickets",
      "int_ticket_metrics",
      "mart_daily_metrics",
      "mart_sla_performance",
      "mart_priority_scoring",
    ] %}
    {% for table_name in tables %}
      {% set rel = adapter.get_relation(
          database=target.database,
          schema=target.schema,
          identifier=table_name
      ) %}
      {% if rel is not none %}
        {% set path = export_dir ~ "/" ~ table_name ~ ".parquet" %}
        {% do log("Exporting " ~ table_name ~ " → " ~ path, info=True) %}
        {% set sql %}
          COPY (SELECT * FROM {{ rel }}) TO '{{ path }}' (FORMAT PARQUET)
        {% endset %}
        {% do run_query(sql) %}
      {% else %}
        {% do log("Skip export (relation not found): " ~ table_name, info=True) %}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endmacro %}
