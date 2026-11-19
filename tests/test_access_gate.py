"""Shared-password access gate. A separate client fixture (not the shared one in
test_api.py) because APP_ACCESS_CODE must be set before the app module's middleware stack is built."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gated_client(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path_factory.mktemp("gate")))
    monkeypatch.setenv("APP_ACCESS_CODE", "let-me-in")
    import importlib

    from backend import access_gate
    importlib.reload(access_gate)  # picks up APP_ACCESS_CODE
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)
    importlib.reload(access_gate)
    importlib.reload(main)


def test_probes_stay_open_without_a_session(gated_client):
    assert gated_client.get("/api/health").status_code == 200
    assert gated_client.get("/api/ready").status_code == 200


def test_api_call_without_a_session_is_refused(gated_client):
    r = gated_client.get("/api/overview", follow_redirects=False)
    assert r.status_code == 401


# Every other /api/* route (health/ready excluded - probes must stay open). The gate middleware runs before
# routing/body-parsing, so it refuses these regardless of method, path params or a missing body.
_OTHER_API_ROUTES = [
    ("GET", "/api/meta/options"),
    ("GET", "/api/figures/V1_shap_bar.png"),
    ("GET", "/api/overview"),
    ("POST", "/api/booking/check"),
    ("GET", "/api/lanes/AFBAN1/alternatives"),
    ("GET", "/api/vendors"),
    ("GET", "/api/vendors/V001"),
    ("GET", "/api/trips"),
    ("GET", "/api/trips/some-trip-id"),
    ("GET", "/api/hubs"),
    ("POST", "/api/ask"),
    ("GET", "/api/prompts"),
    ("GET", "/api/prompts/01"),
    ("POST", "/api/drafts"),
    ("POST", "/api/decisions"),
    ("GET", "/api/audit/export.csv"),
    ("GET", "/api/audit"),
    ("GET", "/api/audit/some-run-id"),
    ("GET", "/api/models"),
    ("GET", "/api/some-unknown-endpoint"),  # the api_not_found catch-all
]


@pytest.mark.parametrize("method,path", _OTHER_API_ROUTES)
def test_every_other_api_route_is_refused_without_a_session(gated_client, method, path):
    r = gated_client.request(method, path, follow_redirects=False)
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}, expected 401"


@pytest.mark.parametrize("path", ["/", "/vendors/V012", "/trips/abc", "/some/deep/spa/route"])
def test_spa_routes_redirect_to_signin_without_a_session(gated_client, path):
    r = gated_client.get(path, follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/signin"


def test_page_without_a_session_redirects_to_signin(gated_client):
    r = gated_client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/signin"


def test_wrong_code_is_refused(gated_client):
    r = gated_client.post("/signin", data={"code": "wrong"}, follow_redirects=False)
    assert r.status_code == 401 and "fct_session" not in r.cookies


def test_right_code_sets_a_session_cookie_that_unlocks_the_api(gated_client):
    r = gated_client.post("/signin", data={"code": "let-me-in"}, follow_redirects=False)
    assert r.status_code == 303 and "fct_session" in r.cookies
    assert gated_client.get("/api/overview").status_code == 200


def test_signin_rate_limited_after_repeated_wrong_codes(gated_client):
    for _ in range(5):
        gated_client.post("/signin", data={"code": "wrong"})
    r = gated_client.post("/signin", data={"code": "wrong"})
    assert r.status_code == 429


def test_gate_disabled_when_no_access_code_is_set(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path_factory.mktemp("ungated")))
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)
    import importlib

    from backend import access_gate, main
    importlib.reload(access_gate)
    importlib.reload(main)
    with TestClient(main.app) as c:
        assert c.get("/api/overview").status_code == 200
