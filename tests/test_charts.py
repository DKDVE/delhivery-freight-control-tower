"""Unit tests for app.charts.choose_chart: one case per row of the chart decision table."""
from app.charts import choose_chart, ALLOWED_TYPES, valid_types_for_shape


def D(name, unit=None):
    return {"name": name, "label": name, "role": "dimension", "unit": unit}


def M(name, unit=None):
    return {"name": name, "label": name, "role": "measure", "unit": unit}


def Tm(name):
    return {"name": name, "label": name, "role": "time", "unit": None}


def test_one_row_one_measure_is_kpi_tile():
    spec = choose_chart([M("loss_rs", "Rs")], row_count=1)
    assert spec["type"] == "kpi_tile"
    assert spec["y"] == ["loss_rs"]


def test_one_row_with_a_dimension_present_is_still_kpi_tile():
    """A single filtered-down row (e.g. one zone) is still clearer as a KPI tile than a lone one-bar chart."""
    spec = choose_chart([D("zone"), M("margin_pct", "%")], row_count=1)
    assert spec["type"] == "kpi_tile"


def test_time_column_with_measures_is_line():
    spec = choose_chart([Tm("month"), M("margin_pct", "%")], row_count=12)
    assert spec["type"] == "line"
    assert spec["x"] == "month"
    assert spec["y"] == ["margin_pct"]


def test_time_column_with_up_to_three_measures_is_still_line():
    spec = choose_chart([Tm("month"), M("a"), M("b"), M("c")], row_count=12)
    assert spec["type"] == "line"


def test_one_dimension_one_measure_is_bar():
    spec = choose_chart([D("vendor_name"), M("loss_rs", "Rs")], row_count=10)
    assert spec["type"] == "bar"
    assert spec["x"] == "vendor_name"


def test_one_dimension_one_percent_measure_is_stacked100_not_pie():
    spec = choose_chart([D("vehicle_type"), M("loss_share_pct", "%")], row_count=5)
    assert spec["type"] == "stacked100"
    assert spec["type"] != "pie"
    assert "pie" not in ALLOWED_TYPES


def test_one_dimension_two_measures_different_units_is_bar_dot():
    spec = choose_chart([D("route"), M("excess_rs", "Rs"), M("vendors", "count")], row_count=8)
    assert spec["type"] == "bar_dot"
    assert spec["y"] == ["excess_rs", "vendors"]


def test_two_measures_no_dimension_many_rows_is_scatter():
    spec = choose_chart([M("min_rs_per_mt", "Rs"), M("max_rs_per_mt", "Rs")], row_count=20)
    assert spec["type"] == "scatter"


def test_more_than_25_rows_falls_back_to_table():
    spec = choose_chart([D("vendor_name"), M("loss_rs", "Rs")], row_count=40)
    assert spec["type"] == "table"


def test_nothing_fits_falls_back_to_table():
    spec = choose_chart([D("a"), D("b"), M("x"), M("y"), M("z")], row_count=5)
    assert spec["type"] == "table"


def test_title_hint_is_used_when_given():
    spec = choose_chart([M("x")], row_count=1, title_hint="Custom title")
    assert spec["title"] == "Custom title"


def test_valid_types_never_include_pie():
    for cols, n in [([M("x")], 1), ([Tm("m"), M("x")], 10), ([D("a"), M("x")], 5), ([D("a"), M("x", "%")], 5),
                    ([D("a"), M("x"), M("y", "count")], 5), ([M("x"), M("y")], 20), ([D("a"), D("b"), M("x")], 30)]:
        for t in valid_types_for_shape(cols, n):
            assert t in ALLOWED_TYPES
            assert t != "pie"


def test_valid_types_always_includes_the_chosen_type():
    cols, n = [D("vendor_name"), M("loss_rs", "Rs")], 10
    chosen = choose_chart(cols, n)
    assert chosen["type"] in valid_types_for_shape(cols, n)
