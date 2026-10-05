"""Parsing edge cases: synthetic CSV fragments through pipeline.parse_csv."""
from __future__ import annotations

import textwrap

import pytest

from pipeline.parse import EXPECTED_COLUMNS, parse_csv


def make_csv(rows: list[str]) -> str:
    header = ",".join(EXPECTED_COLUMNS)
    return textwrap.dedent("\n".join([header, *rows]) + "\n")


def parse_rows(rows: list[str]):
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(make_csv(rows))
        path = Path(f.name)
    return parse_csv(path, "testhash")


GOOD = (
    'India,IND,Test State,TS,state,2024-01-01,Electricity generation,Total,'
    'Total Generation,GWh,123.5,,'
)


def test_good_row_is_parsed_and_typed():
    r = parse_rows([GOOD])
    assert len(r.raw_rows) == 1
    assert len(r.staging_rows) == 1
    assert r.rejects == []
    s = r.staging_rows[0]
    assert s["metric"] == "generation_gwh"
    assert s["value"] == 123.5
    assert s["month_date"].isoformat() == "2024-01-01"
    assert s["month_date"].day == 1


def test_empty_value_becomes_null_not_reject():
    r = parse_rows([GOOD.replace(",123.5,,", ",,,")])
    assert len(r.staging_rows) == 1
    assert r.staging_rows[0]["value"] is None
    assert r.rejects == []


def test_bad_date_is_rejected():
    r = parse_rows([GOOD.replace("2024-01-01", "01/02/2024")])
    assert r.staging_rows == []
    assert len(r.rejects) == 1 and r.rejects[0]["reason"] == "unparseable_date"


def test_not_first_of_month_is_rejected():
    r = parse_rows([GOOD.replace("2024-01-01", "2024-01-15")])
    assert len(r.rejects) == 1 and r.rejects[0]["reason"] == "date_not_first_of_month"


def test_impossible_date_is_rejected():
    r = parse_rows([GOOD.replace("2024-01-01", "2024-13-01")])
    assert len(r.rejects) == 1 and r.rejects[0]["reason"] == "unparseable_date"


def test_unparseable_value_is_rejected():
    r = parse_rows([GOOD.replace("123.5", "n/a")])
    assert r.staging_rows == []
    assert len(r.rejects) == 1 and r.rejects[0]["reason"] == "unparseable_value"


def test_unknown_metric_mapping_is_rejected():
    r = parse_rows([
        'India,IND,Test State,TS,state,2024-01-01,Wind speed,Total,Wind,m/s,5.0,,'
    ])
    assert r.staging_rows == []
    assert len(r.rejects) == 1 and r.rejects[0]["reason"] == "unknown_metric"


def test_whitespace_is_stripped_everywhere():
    r = parse_rows([
        ' India ,IND, Test State ,TS, state ,2024-01-01, Electricity generation ,'
        ' Total , Total Generation , GWh , 123.5 ,, '
    ])
    assert len(r.staging_rows) == 1
    s = r.staging_rows[0]
    assert s["reporting_state"] == "Test State"
    assert s["variable"] == "Total Generation"
    assert s["value"] == 123.5


def test_negative_value_is_parsed_not_rejected():
    """Negatives are a quality-gate failure, not a parse failure: the row is
    typed so the gate can see and report it."""
    r = parse_rows([GOOD.replace("123.5", "-5")])
    assert len(r.staging_rows) == 1
    assert r.staging_rows[0]["value"] == -5.0


def test_all_six_metric_mappings_present():
    from pipeline.parse import METRIC_MAP

    assert len(METRIC_MAP) == 6
    assert METRIC_MAP[("Power sector emissions", "ktCO2")] == "emissions_ktco2"
    assert METRIC_MAP[("Power sector emissions", "gCO2/kWh")] == "carbon_intensity_gco2kwh"


def test_wrong_schema_fails_loudly():
    import tempfile
    from pathlib import Path

    from pipeline.parse import parse_csv as pc

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write("Country,State,Value\nIndia,Bihar,1\n")
        path = Path(f.name)
    with pytest.raises(ValueError, match="Unexpected CSV schema"):
        pc(path, "testhash")


def test_reject_context_carries_raw_fields():
    r = parse_rows([GOOD.replace("2024-01-01", "bad-date")])
    rej = r.rejects[0]
    assert rej["state"] == "Test State"
    assert rej["variable"] == "Total Generation"
    assert rej["reason"] == "unparseable_date"
