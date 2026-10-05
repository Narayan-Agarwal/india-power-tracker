import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (  # noqa: E402
    METRIC_LABELS,
    latest_month,
    national_series,
    page_config,
    render_footer,
)

page_config("National overview")
st.title("🇮🇳 National overview")
st.caption(
    "Series come from the file's own 'India Total' rows, which equal the sum "
    "of states/UTs (verified in the pipeline's quality gate)."
)

lm = latest_month()
if lm is None:
    st.warning("No data loaded yet — run the pipeline first.")
    st.stop()

nat = national_series()
base = alt.Chart(nat).encode(x="year_month:T")

c1, c2 = st.columns(2)
c1.altair_chart(
    base.mark_line(color="#d62728").encode(
        y=alt.Y("total_generation_gwh:Q", title="Generation (GWh)"),
        tooltip=["year_month:T", "total_generation_gwh"],
    ).interactive().properties(title="Monthly generation", height=300, width='container'),
)
c2.altair_chart(
    base.mark_line(color="#2ca02c").encode(
        y=alt.Y("renewables_share:Q", title="Renewables share (%)"),
        tooltip=["year_month:T", "renewables_share"],
    ).interactive().properties(title="Renewables share of generation", height=300, width='container'),
)

c3, c4 = st.columns(2)
c3.altair_chart(
    base.mark_area(color="#8c564b").encode(
        y=alt.Y("emissions_ktco2:Q", title="Emissions (ktCO2)"),
        tooltip=["year_month:T", "emissions_ktco2"],
    ).interactive().properties(title="Power-sector CO2 emissions", height=300, width='container'),
)
c4.altair_chart(
    base.mark_line(color="#7f7f7f").encode(
        y=alt.Y("carbon_intensity_gco2kwh:Q", title="gCO2/kWh"),
        tooltip=["year_month:T", "carbon_intensity_gco2kwh"],
    ).interactive().properties(title="Carbon intensity", height=300, width='container'),
)

st.subheader("Monthly detail (last 12 months)")
st.dataframe(
    nat.tail(12).assign(
        **{
            "Total generation (GWh)": lambda d: d.total_generation_gwh.round(0),
            "Renewables share (%)": lambda d: d.renewables_share.round(1),
            "Emissions (ktCO2)": lambda d: d.emissions_ktco2.round(0),
            "Carbon intensity (gCO2/kWh)": lambda d: d.carbon_intensity_gco2kwh.round(0),
        }
    )[["year_month", "Total generation (GWh)", "Renewables share (%)",
       "Emissions (ktCO2)", "Carbon intensity (gCO2/kWh)"]]
    .set_index("year_month"),
)
st.caption(f"Metrics available: {', '.join(METRIC_LABELS.values())}. Latest month: {lm}.")
render_footer()
