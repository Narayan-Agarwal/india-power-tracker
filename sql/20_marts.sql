-- Build all marts from staging. Executed inside one transaction after the
-- marts tables have been truncated; deterministic and idempotent.
-- Requires: staging.power_monthly, staging.state_mapping populated, and
-- marts.dim_source seeded by the runner (values come from pipeline/parse.py).

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------
INSERT INTO marts.dim_state
    (canonical_state, display_name, state_code, state_type, first_month, last_month)
SELECT m.canonical_state,
       max(m.display_name),
       max(s.state_code),
       max(s.state_type),
       min(s.month_date),
       max(s.month_date)
FROM staging.power_monthly s
JOIN staging.state_mapping m
  ON m.reporting_state = s.reporting_state
 AND s.month_date >= m.valid_from
 AND (m.valid_to IS NULL OR s.month_date <= m.valid_to)
GROUP BY m.canonical_state;

INSERT INTO marts.dim_state_reporting (state_key, reporting_state, valid_from, valid_to, note)
SELECT ds.state_key, m.reporting_state, m.valid_from, m.valid_to, m.note
FROM staging.state_mapping m
JOIN marts.dim_state ds ON ds.canonical_state = m.canonical_state;

INSERT INTO marts.dim_date (date_key, month_date, year, month, quarter, year_month)
SELECT DISTINCT
       (EXTRACT(YEAR FROM s.month_date) * 100 + EXTRACT(MONTH FROM s.month_date))::int,
       s.month_date,
       EXTRACT(YEAR FROM s.month_date)::int,
       EXTRACT(MONTH FROM s.month_date)::int,
       EXTRACT(QUARTER FROM s.month_date)::int,
       to_char(s.month_date, 'YYYY-MM')
FROM staging.power_monthly s;

-- ---------------------------------------------------------------------------
-- Fact
-- ---------------------------------------------------------------------------
INSERT INTO marts.fact_power_monthly (state_key, date_key, source_key, metric, value)
SELECT ds.state_key, dd.date_key, dsrc.source_key, s.metric, s.value
FROM staging.power_monthly s
JOIN staging.state_mapping m
  ON m.reporting_state = s.reporting_state
 AND s.month_date >= m.valid_from
 AND (m.valid_to IS NULL OR s.month_date <= m.valid_to)
JOIN marts.dim_state ds ON ds.canonical_state = m.canonical_state
JOIN marts.dim_date dd ON dd.month_date = s.month_date
JOIN marts.dim_source dsrc
  ON dsrc.source_name = s.variable AND dsrc.subcategory = s.subcategory;

-- ---------------------------------------------------------------------------
-- Derived: renewables share (computed from fuel rows; publisher row kept as
-- a cross-check column, see D8)
-- ---------------------------------------------------------------------------
INSERT INTO marts.renewables_share_monthly
    (state_key, date_key, renewable_gwh, total_gwh, renewables_share, publisher_share)
WITH fuel AS (
    SELECT f.state_key, f.date_key, sum(f.value) AS renewable_gwh
    FROM marts.fact_power_monthly f
    JOIN marts.dim_source s ON s.source_key = f.source_key
    WHERE f.metric = 'generation_gwh'
      AND s.subcategory = 'Fuel'
      AND s.is_renewable
    GROUP BY 1, 2
),
tot AS (
    SELECT f.state_key, f.date_key, f.value AS total_gwh
    FROM marts.fact_power_monthly f
    JOIN marts.dim_source s ON s.source_key = f.source_key
    WHERE f.metric = 'generation_gwh'
      AND s.is_total
),
pub AS (
    SELECT f.state_key, f.date_key, f.value AS share_pct
    FROM marts.fact_power_monthly f
    JOIN marts.dim_source s ON s.source_key = f.source_key
    WHERE f.metric = 'generation_share_pct'
      AND s.source_name = 'Renewables'
      AND s.subcategory = 'Aggregate fuel'
)
SELECT fu.state_key, fu.date_key, fu.renewable_gwh, t.total_gwh,
       CASE WHEN t.total_gwh > 0 THEN fu.renewable_gwh / t.total_gwh END,
       p.share_pct / 100.0
FROM fuel fu
JOIN tot t ON t.state_key = fu.state_key AND t.date_key = fu.date_key
LEFT JOIN pub p ON p.state_key = fu.state_key AND p.date_key = fu.date_key;

-- ---------------------------------------------------------------------------
-- Derived: year-over-year change for every fact series (self-join 12 months
-- apart in YYYYMM key space)
-- ---------------------------------------------------------------------------
INSERT INTO marts.yoy_change_monthly
    (state_key, date_key, source_key, metric, value, value_year_ago, yoy_abs, yoy_pct)
SELECT cur.state_key, cur.date_key, cur.source_key, cur.metric,
       cur.value, prev.value,
       cur.value - prev.value,
       CASE WHEN prev.value <> 0
            THEN (cur.value - prev.value) / abs(prev.value) * 100 END
FROM marts.fact_power_monthly cur
JOIN marts.fact_power_monthly prev
  ON prev.state_key = cur.state_key
 AND prev.source_key = cur.source_key
 AND prev.metric = cur.metric
 AND prev.date_key = cur.date_key - 100
WHERE cur.value IS NOT NULL
  AND prev.value IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Derived: per-month state rankings (national entity excluded).
-- rank_total_generation: 1 = largest. rank_renewables_share: 1 = highest.
-- rank_carbon_intensity: 1 = lowest (cleanest). Ties broken by state_key.
-- ---------------------------------------------------------------------------
INSERT INTO marts.state_rankings_monthly
    (date_key, state_key, total_generation_gwh, renewables_share,
     carbon_intensity_gco2kwh, rank_total_generation, rank_renewables_share,
     rank_carbon_intensity)
WITH base AS (
    SELECT tg.date_key,
           tg.state_key,
           tg.value AS total_generation_gwh,
           rs.renewables_share,
           ci.value AS carbon_intensity_gco2kwh
    FROM marts.fact_power_monthly tg
    JOIN marts.dim_source stg ON stg.source_key = tg.source_key AND stg.is_total
    JOIN marts.dim_state ds
      ON ds.state_key = tg.state_key AND ds.canonical_state <> 'India Total'
    LEFT JOIN marts.renewables_share_monthly rs
      ON rs.state_key = tg.state_key AND rs.date_key = tg.date_key
    LEFT JOIN marts.fact_power_monthly ci
      ON ci.state_key = tg.state_key AND ci.date_key = tg.date_key
     AND ci.metric = 'carbon_intensity_gco2kwh'
    WHERE tg.metric = 'generation_gwh'
)
SELECT date_key, state_key, total_generation_gwh, renewables_share,
       carbon_intensity_gco2kwh,
       RANK() OVER (PARTITION BY date_key ORDER BY total_generation_gwh DESC NULLS LAST, state_key),
       RANK() OVER (PARTITION BY date_key ORDER BY renewables_share DESC NULLS LAST, state_key),
       RANK() OVER (PARTITION BY date_key ORDER BY carbon_intensity_gco2kwh ASC NULLS LAST, state_key)
FROM base;

-- ---------------------------------------------------------------------------
-- Derived: carbon intensity trend per entity (states + national), with YoY
-- ---------------------------------------------------------------------------
INSERT INTO marts.carbon_intensity_trend (state_key, date_key, carbon_intensity, yoy_abs, yoy_pct)
SELECT f.state_key, f.date_key, f.value, y.yoy_abs, y.yoy_pct
FROM marts.fact_power_monthly f
LEFT JOIN marts.yoy_change_monthly y
  ON y.state_key = f.state_key AND y.date_key = f.date_key
 AND y.source_key = f.source_key AND y.metric = f.metric
WHERE f.metric = 'carbon_intensity_gco2kwh';

-- ---------------------------------------------------------------------------
-- Derived: national monthly series from the file's own "India Total" rows.
-- Pure LEFT JOINs: a month missing one series still appears with the others.
-- ---------------------------------------------------------------------------
INSERT INTO marts.national_monthly
    (date_key, total_generation_gwh, emissions_ktco2, renewables_share,
     carbon_intensity_gco2kwh)
WITH nat AS (
    SELECT f.state_key, f.date_key
    FROM marts.fact_power_monthly f
    JOIN marts.dim_state ds
      ON ds.state_key = f.state_key AND ds.canonical_state = 'India Total'
    GROUP BY 1, 2
)
SELECT n.date_key,
       tg.value,
       te.value,
       pub.value,
       ci.value
FROM nat n
LEFT JOIN marts.fact_power_monthly tg
  ON tg.state_key = n.state_key AND tg.date_key = n.date_key
 AND tg.metric = 'generation_gwh'
 AND tg.source_key = (SELECT source_key FROM marts.dim_source WHERE source_name = 'Total Generation')
LEFT JOIN marts.fact_power_monthly te
  ON te.state_key = n.state_key AND te.date_key = n.date_key
 AND te.metric = 'emissions_ktco2'
 AND te.source_key = (SELECT source_key FROM marts.dim_source WHERE source_name = 'Total emissions')
LEFT JOIN marts.fact_power_monthly pub
  ON pub.state_key = n.state_key AND pub.date_key = n.date_key
 AND pub.metric = 'generation_share_pct'
 AND pub.source_key = (SELECT source_key FROM marts.dim_source
                       WHERE source_name = 'Renewables' AND subcategory = 'Aggregate fuel')
LEFT JOIN marts.fact_power_monthly ci
  ON ci.state_key = n.state_key AND ci.date_key = n.date_key
 AND ci.metric = 'carbon_intensity_gco2kwh'
 AND ci.source_key = (SELECT source_key FROM marts.dim_source WHERE source_name = 'CO2 intensity');
