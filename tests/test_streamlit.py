import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_MODE", "1")


def _app():
    return AppTest.from_file("../app/streamlit_app.py", default_timeout=120).run()


def test_booking_page_demo_end_to_end():
    at = _app()
    assert not at.exception and "DEMO" in at.sidebar.markdown[0].value
    at.button[0].click().run()
    assert not at.exception
    assert any("Recommended action" in s.value for s in at.subheader)
    assert any(b.label == "Approve" for b in at.button)
    next(b for b in at.button if b.label == "Approve").click().run()
    assert any("Nothing was sent" in s.value for s in at.success)
    from app import guardrails as G
    assert [r["type"] for r in G.read_audit()] == ["run", "decision"]


@pytest.mark.parametrize("page", ["Trip alert", "Ask the data", "Prompt library", "Audit log"])
def test_other_pages_render(page):
    at = _app()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception
