{#
  Prefer a simple single-schema DuckDB layout (main) so Streamlit exports
  and local exploration stay uncomplicated.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ target.schema }}
{%- endmacro %}
