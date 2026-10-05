import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import energy_mix, page_config, render_footer, state_list

page_config("Energy mix over time")
st.title("🔋 Energy mix over time")

scope = st.radio("Scope", ["National", "Single state"], horizontal=True)
state_name = None
if scope == "Single state":
    states = state_list()
    default = "Tamil Nadu" if "Tamil Nadu" in states else states[0]
    state_name = st.selectbox("State or UT", states, index=states.index(default))

mix = energy_mix(state_name)
if mix.empty:
    st.warning("No data loaded yet — run the pipeline first.")
    st.stop()

mix = mix.set_index("year_month").fillna(0.0)
order = [
    "coal", "gas", "other_fossil", "nuclear",
    "hydro", "solar", "wind", "bioenergy", "other_renewables",
]
mix = mix[[c for c in order if c in mix.columns]]
pretty = {
    "coal": "Coal", "gas": "Gas", "other_fossil": "Other fossil",
    "nuclear": "Nuclear", "hydro": "Hydro", "solar": "Solar",
    "wind": "Wind", "bioenergy": "Bioenergy", "other_renewables": "Other renewables",
}

st.subheader("Absolute generation by fuel (GWh)")
st.area_chart(mix.rename(columns=pretty), height=360)

st.subheader("Share of monthly generation (%)")
share = mix.div(mix.sum(axis=1), axis=0) * 100
st.area_chart(share.rename(columns=pretty), height=360)

st.caption(
    "Fuel-level rows from the fact table, aggregated in SQL. States whose "
    "reporting changed (see Data freshness page) keep their publisher label."
)
render_footer()
