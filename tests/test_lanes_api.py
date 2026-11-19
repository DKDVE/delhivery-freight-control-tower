"""API tests: /api/lanes/points, /api/lanes/search, /api/vendors/compare, and the two new Ask-the-data queries.
Every endpoint gets a vendor-name-leak regression test."""
import pytest
from fastapi.testclient import TestClient

from app import lanes as L
from backend import vendor_codes as vc

NAMES = vc.names()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DEMO_MODE", "1")
    mp.setenv("AUDIT_LOG_DIR", str(tmp_path_factory.mktemp("lanes_api")))
    from backend.main import app
    with TestClient(app) as c:
        yield c
    mp.undo()


def no_names(text: str):
    low = text.casefold()
    leaked = [n for n in NAMES if n.casefold() in low]
    assert not leaked, f"vendor name(s) leaked: {leaked[:3]}"


def test_points_lists_origins_with_reachable_destinations(client):
    r = client.get("/api/lanes/points")
    assert r.status_code == 200
    j = r.json()
    h = next(o for o in j["origins"] if o["name"] == "HAZIRA")
    assert any(d["name"] == "KHADOLI" for d in h["destinations"]) and j["n_lanes"] == sum(len(o["destinations"]) for o in j["origins"])
    assert "no on-time or damage" in j["footer"]
    no_names(r.text)


def test_search_hazira_to_khadoli(client):
    r = client.get("/api/lanes/search", params={"from": "hazira", "to": "KHADOLI", "vehicle_type": "SB04", "qty": 15, "months": 6})
    assert r.status_code == 200
    j = r.json()
    assert j["origin"] == "HAZIRA" and j["destination"] == "KHADOLI" and j["route"] == "HZKHA1" and j["has_direct"]
    g = j["groups"][0]
    assert g["vehicle_type"] == "SB04" and g["vendors"][0]["tag"] == "Cheapest" and g["vendors"][0]["vendor_code"].startswith("V")
    assert j["recommendation"]["booking_prefill"]["route"] == "HZKHA1" and "two_leg" in j and "footer" in j
    no_names(r.text)


def test_search_unknown_origin_or_destination_is_404_with_suggestions(client):
    r = client.get("/api/lanes/search", params={"from": "HAZIR", "to": "KHADOLI"})
    assert r.status_code == 404 and r.json()["kind"] == "origin" and r.json()["suggestions"] == ["HAZIRA"]
    r = client.get("/api/lanes/search", params={"from": "HAZIRA", "to": "KHADOL"})
    assert r.status_code == 404 and r.json()["kind"] == "destination" and r.json()["suggestions"] == ["KHADOLI"]
    no_names(r.text)


def test_search_unknown_place_with_no_close_match_gives_an_empty_suggestion_list(client):
    r = client.get("/api/lanes/search", params={"from": "QQQQQ", "to": "KHADOLI"})
    assert r.status_code == 404 and r.json()["suggestions"] == [] and "Did you mean" not in r.json()["detail"]


def test_search_validates_inputs(client):
    assert client.get("/api/lanes/search", params={"from": "HAZIRA"}).status_code == 422
    assert client.get("/api/lanes/search", params={"from": "HAZIRA", "to": "KHADOLI", "months": 13}).status_code == 422
    assert client.get("/api/lanes/search", params={"from": "HAZIRA", "to": "KHADOLI", "qty": -2}).status_code == 422


def test_search_known_places_without_a_direct_lane_is_200_with_a_note(client):
    o = L.lane_points()["origins"][-1]
    reach = {d["name"] for d in o["destinations"]}
    other = next(d for d in L._places()[1] if d not in reach)
    r = client.get("/api/lanes/search", params={"from": o["name"], "to": other})
    assert r.status_code == 200 and r.json()["has_direct"] is False and any("No direct lane" in n for n in r.json()["notes"])


def test_compare_two_vendors_that_share_lanes(client):
    a, b = L.best_pair()
    r = client.get("/api/vendors/compare", params={"codes": f"{a},{b.lower()}"})
    assert r.status_code == 200          # and not read as vendor code "compare" by /api/vendors/{code}
    j = r.json()
    assert j["codes"] == [a, b] and j["n_shared_lanes"] > 0 and {v["vendor_code"] for v in j["vendors"]} == {a, b}
    assert all(x["cheaper"] in (a, b) for x in j["lanes"])
    no_names(r.text)


def test_compare_validation_and_unknown_vendor(client):
    assert client.get("/api/vendors/compare", params={"codes": "V001"}).status_code == 422
    assert client.get("/api/vendors/compare", params={"codes": "V001,V999"}).status_code == 404
    assert client.get("/api/vendors/compare").status_code == 422
    assert client.get("/api/vendors/V001").status_code == 200   # the {code} route still works


def test_ask_the_data_answers_cheapest_vendor_from_hazira_to_khadoli_with_chart_and_table(client):
    r = client.post("/api/ask", json={"question": "cheapest vendor from Hazira to Khadoli"})
    assert r.status_code == 200
    from tests.test_api import sse
    ev = dict((e, d) for e, d in sse(r))
    assert ev["analysis"]["query_id"] == "lane_vendor_ranking" and ev["analysis"]["params"]["origin"] == "HAZIRA"
    assert ev["chart"]["type"] == "bar" and ev["result"]["row_count"] >= 2
    assert ev["result"]["rows"][0]["rs_per_mt"] <= ev["result"]["rows"][1]["rs_per_mt"]
    no_names(r.text)


def test_ask_head_to_head_and_catalogue_endpoint_have_no_names(client):
    a, b = L.best_pair()
    r = client.post("/api/ask", json={"question": f"compare {a} vs {b}"})
    assert r.status_code == 200 and "vendor_head_to_head" in r.text
    no_names(r.text)
    c = client.get("/api/ask/catalogue")
    assert {"lane_vendor_ranking", "vendor_head_to_head"} <= {x["id"] for x in c.json()}
    no_names(c.text)
