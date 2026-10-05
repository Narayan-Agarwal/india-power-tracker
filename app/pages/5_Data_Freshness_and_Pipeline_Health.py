import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import LICENCE, SOURCE_NAME, SOURCE_URL, latest_month, page_config, pipeline_health, render_footer  # noqa: E402

page_config("Data freshness and pipeline health")
st.title("🩺 Data freshness & pipeline health")

lm = latest_month()
if lm is not None:
    st.metric("Newest month in the data", lm)

h = pipeline_health()

st.subheader("Last successful run")
if h["last_ok"].empty:
    st.warning("No successful pipeline run recorded yet.")
else:
    row = h["last_ok"].iloc[0]
    st.write(f"**{row.command}** finished at {row.finished_at}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw rows in", f"{row.raw_rows_in:,.0f}" if row.raw_rows_in is not None else "—")
    c2.metric("Fact rows out", f"{row.fact_rows_out:,.0f}" if row.fact_rows_out is not None else "—")
    c3.metric("Rejects", f"{row.rejects:,.0f}" if row.rejects is not None else "—")
    c4.metric("File hash", (row.file_sha256 or "")[:12] or "—")

st.subheader("Layer counts (from the last successful run)")
if h["last_ok"].empty:
    st.caption("No run recorded yet.")
else:
    row = h["last_ok"].iloc[0]
    st.dataframe(
        pd.DataFrame(
            {
                "Layer": ["raw.ember_india_monthly", "staging.power_monthly",
                          "marts.fact_power_monthly", "pipeline.rejects"],
                "Rows": [row.raw_rows_in, row.staging_rows_out,
                         row.fact_rows_out, row.rejects],
            }
        ).set_index("Layer"),
        width='stretch',
    )
    st.caption(
        "Counts come from pipeline.run_log — the app never queries raw or "
        "staging tables directly."
    )

st.subheader("Quality checks from the last successful run")
if h["checks"].empty:
    st.caption("No check details recorded yet.")
else:
    details = h["checks"].iloc[0].details
    # psycopg returns jsonb already parsed; older drivers return a string.
    details = json.loads(details) if isinstance(details, str) else details
    check_rows = [
        {
            "Check": c["name"],
            "Severity": c["severity"],
            "Result": "PASS" if c["passed"] else "FAIL",
            "Detail": c["detail"],
        }
        for c in details
    ]
    st.dataframe(pd.DataFrame(check_rows).set_index("Check"), width='stretch')

st.subheader("Recent runs (latest 30)")
st.dataframe(
    h["runs"].assign(
        started_at=lambda d: pd.to_datetime(d.started_at).dt.strftime("%Y-%m-%d %H:%M UTC")
    ).set_index("started_at"),
    width='stretch',
)

st.subheader("Known data caveats")
st.markdown(
    f"""
    - The newest month in the file can lag the calendar by several months:
      Ember publishes this dataset periodically and its cadence is outside
      this project's control. The freshness check therefore guards against
      the data getting *older* between runs, and reports the lag openly.
    - **Jammu and Kashmir / Ladakh** were reported as one entity until
      June 2021 (inclusive) per Ember's methodology; Ladakh appears
      separately from July 2021. This is documented in
      `marts.dim_state_reporting`.
    - **Others** is a residual bucket Ember stopped reporting after
      August 2022.
    - Some state-months have blank capacity or share values in the source
      file; they are stored as NULL, never zero-filled or interpolated.
    - Emissions are Ember's estimates using IPCC emission factors applied to
      generation — a modelled series, not measured emissions.
    """
)

st.subheader("Source and licence")
st.markdown(
    f"""
    All electricity data comes from [{SOURCE_NAME}]({SOURCE_URL}) and is
    released under the **{LICENCE}** licence. This application is a **derived
    analysis**, not an official statistic.
    """
)
render_footer()
