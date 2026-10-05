import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (  # noqa: E402
    METRIC_LABELS,
    compare_metric,
    latest_month,
    page_config,
    rankings_latest,
    render_footer,
    state_list,
)

page_config("Compare states")
st.title("⚖️ Compare states")

lm = latest_month()
if lm is None:
    st.warning("No data loaded yet — run the pipeline first.")
    st.stop()

states = state_list()
metric_label = st.selectbox(
    "Metric",
    [
        "Total generation (GWh)",
        "Renewables share (%)",
        "Carbon intensity (gCO2/kWh)",
        "Emissions (ktCO2)",
        "Installed capacity (MW)",
    ],
    index=0,
)
metric = {
    "Total generation (GWh)": "generation_gwh",
    "Renewables share (%)": "generation_share_pct",
    "Carbon intensity (gCO2/kWh)": "carbon_intensity_gco2kwh",
    "Emissions (ktCO2)": "emissions_ktco2",
    "Installed capacity (MW)": "capacity_mw",
}[metric_label]

defaults = [s for s in ["Maharashtra", "Tamil Nadu", "Gujarat", "Karnataka"] if s in states]
picked = st.multiselect(
    "States to compare (2-6)", states, default=defaults[:4], max_selections=6
)

if len(picked) >= 1:
    df = compare_metric(tuple(picked), metric)
    if metric in ("generation_gwh", "emissions_ktco2", "capacity_mw"):
        # use the publisher's Total row rather than per-fuel rows
        df = df[df.state.notna()]
    # For total-series metrics keep only the total/aggregate rows so each
    # state contributes exactly one line per month.
    df = df.drop_duplicates(subset=["state", "year_month"], keep="first")
    wide = df.pivot_table(index="year_month", columns="state", values="value")
    st.line_chart(wide, height=380)
else:
    st.info("Pick at least one state.")

st.subheader(f"Latest-month rankings ({lm})")
st.dataframe(
    rankings_latest().set_index("state").style.format(
        {
            "total_generation_gwh": "{:,.0f}",
            "renewables_share": "{:.1%}",
            "carbon_intensity_gco2kwh": "{:,.0f}",
        }
    ),
    width='stretch',
)
st.caption(
    "Rankings: 1 = largest generation, 1 = highest renewables share, "
    "1 = lowest carbon intensity. 'India Total' is excluded from rankings."
)
render_footer()
