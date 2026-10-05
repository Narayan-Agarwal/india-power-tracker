import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (  # noqa: E402
    latest_month,
    page_config,
    render_footer,
    state_capacity_mix,
    state_generation_by_fuel,
    state_key_of,
    state_list,
    state_metric_series,
)

page_config("State explorer")
st.title("🔍 State explorer")

lm = latest_month()
if lm is None:
    st.warning("No data loaded yet — run the pipeline first.")
    st.stop()

states = state_list()
default = "Maharashtra" if "Maharashtra" in states else states[0]
name = st.selectbox("State or UT", states, index=states.index(default))
k = state_key_of(name)
if k is None:
    st.error("State not found in dim_state.")
    st.stop()

gen = state_generation_by_fuel(k)
wide = gen.pivot_table(index="year_month", columns="fuel", values="gwh", aggfunc="sum")

left, right = st.columns(2)
with left:
    st.subheader("Generation by fuel")
    if not wide.empty:
        st.line_chart(wide, height=320)
    st.caption("Monthly GWh by fuel, from fuel-level fact rows.")

with right:
    st.subheader(f"Installed capacity — latest month ({lm})")
    cap = state_capacity_mix(k)
    cap = cap[cap.mw > 0]
    if cap.empty:
        st.info("No capacity reported for this state.")
    else:
        st.bar_chart(cap.set_index("fuel"), height=320)
        st.caption(f"Total: {cap.mw.sum():,.0f} MW")

st.subheader("Totals over time")
tot_gen = state_metric_series(k, "generation_gwh")
tot_em = state_metric_series(k, "emissions_ktco2")
cap_tot = state_metric_series(k, "capacity_mw")
intensity = state_metric_series(k, "carbon_intensity_gco2kwh")


def _total(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    t = df[df.source_name.isin(["Total Generation", "Total emissions"])]
    src = t if not t.empty else df
    return src.set_index("year_month").value


c1, c2 = st.columns(2)
s1 = _total(tot_gen)
c1.line_chart(s1, height=260) if not s1.empty else c1.info("No generation data")
s2 = _total(intensity)
c2.line_chart(s2, height=260) if not s2.empty else c2.info("No carbon intensity data")

st.subheader(f"Latest month numbers ({lm})")
latest_rows = []
for df, label, unit in [
    (tot_gen, "Total generation", "GWh"),
    (cap_tot, "Installed capacity", "MW"),
    (tot_em, "Emissions", "ktCO2"),
    (intensity, "Carbon intensity", "gCO2/kWh"),
]:
    if not df.empty:
        last = df[df.year_month == lm]
        if not last.empty:
            latest_rows.append(
                {"Metric": label, f"Value ({unit})": last.value.iloc[0]}
            )
st.dataframe(pd.DataFrame(latest_rows).set_index("Metric"), width='stretch')
render_footer()
