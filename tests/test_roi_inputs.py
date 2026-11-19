"""ROI inputs: ownership rule, annualisation, gross/net, vendor codes only."""
import json
import os

import pandas as pd
import pytest

from scripts import roi_inputs as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ship(rows):
    return pd.DataFrame(rows, columns=["shipment_no", "vendor_code", "route", "vehicle_type", "train", "all_cost_valid", "total_qty", "freight", "n_lrs"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROI_DIR", str(tmp_path))
    rows = []
    n = 0
    for v, rate in (("V001", 100.0), ("V002", 80.0)):          # training: V001 costs 100/MT, V002 80/MT on the same lane, 12 LRs each
        for _ in range(12):
            n += 1
            rows.append([n, v, "R1", "X", True, True, 10.0, 10.0 * rate, 1])
    ship = _ship(rows)
    test = []
    for i in range(12):                                         # test: V001 pays 1500 for a shipment the model prices at 1000; V002 pays its fair 800
        test.append([1000 + i, "V001", "R1", "X", 10.0, 1500.0, 1000.0, "P1"])
        test.append([2000 + i, "V002", "R1", "X", 10.0, 800.0, 800.0, "P1"])
    u = pd.DataFrame(test, columns=["shipment_no", "vendor_code", "route", "vehicle_type", "qty", "actual", "fair", "origin_plant"])
    lanes = R.lane_rates(ship)
    u = u.merge(lanes[["route", "vehicle_type", "vendor_code", "rate", "cheapest"]], on=["route", "vehicle_type", "vendor_code"], how="left")
    u["b_full"] = ((u["rate"] - u["cheapest"]).clip(lower=0) * u["qty"]).fillna(0.0)
    return {"ship": ship}, u


def test_lane_rates_need_two_vendors_with_ten_lrs():
    ship = _ship([[i, "V001", "R", "X", True, True, 1.0, 100.0, 1] for i in range(12)] + [[100 + i, "V002", "R", "X", True, True, 1.0, 90.0, 1] for i in range(5)])
    assert R.lane_rates(ship).empty  # V002 has only 5 LRs: the lane has one qualifying vendor


def test_levers_take_the_larger_not_the_sum(world):
    d, u = world
    out, _, csv = R.s1(d, u, set())
    cell = csv[csv["vendor_code"] == "V001"].iloc[0]
    assert cell["lever_a"] == pytest.approx(12 * 500.0)                        # (1500 - 1000) x 12
    assert cell["lever_b_35"] == pytest.approx(0.35 * 12 * 10 * 20.0)          # (100 - 80) per MT x 10 MT x 12 x 35%
    assert cell["best_35"] == pytest.approx(max(cell["lever_a"], cell["lever_b_35"]))
    assert out["s1_rs_gross"] == pytest.approx(cell["best_35"])                # V002's cell has no gap and no cheaper vendor


def test_s1_excludes_shipments_owned_by_s3(world):
    d, u = world
    s3_claims = {1000 + i for i in range(4)}                                    # S3 owns 4 of V001's 12 shipments
    out, claim1, _ = R.s1(d, u, s3_claims)
    assert not (claim1 & s3_claims)                                            # no shipment in two strategies
    assert out["s1_rs_net"] < out["s1_rs_gross"]
    assert out["s1_rs_net"] == pytest.approx(8 * 500.0)                         # lever A on the 8 remaining shipments
    assert out["s1_rs_net"] <= out["s1_rs_gross"]


def test_annual_factor_is_year_over_quarter_not_four():
    lr = pd.DataFrame({"lr_date": pd.to_datetime(["2025-06-01", "2025-09-01", "2026-01-15", "2026-02-15"]), "r2_freight": [100.0, 100.0, 50.0, 50.0],
                       "gta_invoice_value": [110.0] * 4, "margin": [10.0] * 4, "vendor_code": ["V001"] * 4, "route": ["R"] * 4, "vehicle_type": ["X"] * 4, "origin_plant": ["P"] * 4})
    ship = pd.DataFrame({"valid_cost": [True, True]})
    ctx = R.context({"lr": lr, "ship": ship})
    assert ctx["annual_factor"] == pytest.approx(300.0 / 100.0)               # seasonal: Q1 is lighter than a quarter of the year
    assert ctx["annual_factor_x4"] == 4.0
    assert ctx["annual_factor"] != ctx["annual_factor_x4"]


@pytest.mark.skipif(not os.path.exists(R.OUT_JSON), reason="run scripts/roi_inputs.py first")
class TestSavedOutput:
    @pytest.fixture(scope="class")
    def j(self):
        return json.load(open(R.OUT_JSON, encoding="utf8"))

    def test_net_not_above_gross_and_no_overlap(self, j):
        o = j["ownership"]
        assert o["overlap_shipments"] == 0
        for k in ("S1", "S2", "S3"):
            assert o["net_rs"][k] <= o["gross_rs"][k] + 1e-6
        assert o["net_sum_rs"] <= o["gross_sum_rs"] + 1e-6

    def test_annualisation_uses_the_factor(self, j):
        f = j["context"]["annual_factor"]
        for k, v in j["ownership"]["annualised_net_rs"].items():
            assert v == pytest.approx(j["ownership"]["net_rs"][k] * f)
        assert 3.0 < f < 5.0

    def test_vendor_names_never_appear(self, j):
        import backend.vendor_codes as vc
        blob = json.dumps(j).casefold()
        assert not [n for n in vc.names() if n.casefold() in blob]
