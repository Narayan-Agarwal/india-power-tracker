"""India Power Tracker — home page.

Electricity generation, capacity and emissions for Indian states and UTs,
monthly, from Ember's open dataset (CC BY 4.0).
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import FOOTER, latest_month, national_series, page_config  # noqa: E402

page_config("India Power Tracker")

st.title("⚡ India Power Tracker")
st.markdown(
    "Monthly electricity **generation**, **capacity**, **emissions** and "
    "**carbon intensity** for India's states and union territories."
)

st.markdown(
    """
    **Pages in this app**

    | Page | What it shows |
    |---|---|
    | **National overview** | India-wide generation, renewables share, emissions and intensity |
    | **State explorer** | Deep dive into one state or UT |
    | **Compare states** | Side-by-side trends for 2-6 states |
    | **Energy mix over time** | Stacked generation by fuel, national or per state |
    | **Data freshness & pipeline health** | run_log, row counts, rejects, quality checks |
    """
)

lm = latest_month()
if lm is None:
    st.warning("No data loaded yet — run `python run.py backfill --target local` first.")
    st.stop()

st.subheader(f"Latest month: {lm}")
try:
    nat = national_series()
    row = nat.iloc[-1]
    prev = nat.iloc[-13] if len(nat) > 13 else nat.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Total generation",
        f"{row.total_generation_gwh:,.0f} GWh",
        f"{row.total_generation_gwh - prev.total_generation_gwh:+,.0f} vs {prev.year_month}",
    )
    c2.metric(
        "Renewables share",
        f"{row.renewables_share:.1f}%",
        f"{row.renewables_share - prev.renewables_share:+.1f} pp",
    )
    c3.metric(
        "Emissions",
        f"{row.emissions_ktco2:,.0f} ktCO2",
        f"{row.emissions_ktco2 - prev.emissions_ktco2:+,.0f} ktCO2",
    )
    c4.metric(
        "Carbon intensity",
        f"{row.carbon_intensity_gco2kwh:,.0f} gCO2/kWh",
        f"{row.carbon_intensity_gco2kwh - prev.carbon_intensity_gco2kwh:+,.0f} gCO2/kWh",
    )
except Exception as exc:  # pragma: no cover
    st.error(f"Could not read marts: {exc}")

st.caption(FOOTER)
