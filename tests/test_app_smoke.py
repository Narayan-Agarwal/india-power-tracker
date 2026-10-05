"""Streamlit smoke tests: every page must run without exceptions."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

PAGES = [
    "streamlit_app.py",
    "pages/1_National_Overview.py",
    "pages/2_State_Explorer.py",
    "pages/3_Compare_States.py",
    "pages/4_Energy_Mix_Over_Time.py",
    "pages/5_Data_Freshness_and_Pipeline_Health.py",
]


@pytest.mark.parametrize("page", PAGES)
def test_page_runs_without_exception(local_pipeline, page):
    at = AppTest.from_file(APP_DIR / page, default_timeout=120)
    at.run()
    assert not at.exception, f"{page} raised: {at.exception}"


def test_main_page_shows_data(local_pipeline):
    at = AppTest.from_file(APP_DIR / "streamlit_app.py", default_timeout=120)
    at.run()
    assert not at.exception
    # KPI metrics render once data is present
    assert len(at.metric) >= 1
    assert "India Power Tracker" in at.title[0].value


def test_state_explorer_renders_state_data(local_pipeline):
    at = AppTest.from_file(APP_DIR / "pages/2_State_Explorer.py", default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.selectbox) >= 1
    assert at.selectbox[0].value  # a state is selected by default


def test_compare_states_renders_chart(local_pipeline):
    at = AppTest.from_file(APP_DIR / "pages/3_Compare_States.py", default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.multiselect) == 1


def test_freshness_page_lists_run_log(local_pipeline):
    at = AppTest.from_file(APP_DIR / "pages/5_Data_Freshness_and_Pipeline_Health.py",
                           default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 1


def test_footer_credit_present(local_pipeline):
    at = AppTest.from_file(APP_DIR / "pages/1_National_Overview.py", default_timeout=120)
    at.run()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "Ember" in captions
    assert "CC BY 4.0" in captions
    assert "derived analysis" in captions
