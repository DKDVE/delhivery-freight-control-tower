"""The app must start in demo mode from a checkout that has no confidential file and no name mapping."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_PREFIXES = ("data/raw/", "data/private/", "frontend/src/", "frontend/e2e/", "frontend/node_modules/", "app/logs/", "notebooks/", "docs/screenshots/")
SKIP_FILES = ("data/processed/vendor_codes.csv",)

PROBE = r'''
import json
from fastapi.testclient import TestClient
from backend.main import app

booking = {"route": "AFBAN1", "vehicle_type": "OB22", "vendor": "V002", "qty": 3, "month": 3}
out = {}
with TestClient(app) as c:
    out["overview"] = c.get("/api/overview").status_code
    r = c.post("/api/booking/check", json=booking)
    out["booking"] = r.status_code
    out["booking_has_risk"] = "loss_risk" in r.json()
    out["lanes"] = c.get("/api/lanes/search", params={"from": "hazira", "to": "KHADOLI", "vehicle_type": "SB04", "qty": 15, "months": 6}).status_code
    r = c.get("/api/vendors", params={"min_lrs": 10})
    out["vendors"] = r.status_code
    out["first_vendor"] = r.json()["items"][0]["vendor_code"]
print("RESULT" + json.dumps(out))
'''


def _files():
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in listed if f and not f.startswith(SKIP_PREFIXES) and f not in SKIP_FILES and os.path.isfile(os.path.join(ROOT, f))]


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, ".git")), reason="needs a git checkout to list the shipped files")
def test_demo_mode_starts_without_raw_data_or_name_mapping(tmp_path):
    for f in _files():
        dst = tmp_path / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(ROOT, f), dst)
    assert not (tmp_path / "data" / "raw").exists() and not (tmp_path / "data" / "processed" / "vendor_codes.csv").exists()
    env = {**os.environ, "DEMO_MODE": "1", "PYTHONPATH": str(tmp_path), "AUDIT_LOG_DIR": str(tmp_path / "audit"), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=600)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, r.stderr[-2000:]
    out = json.loads(line[len("RESULT"):])
    assert out["overview"] == out["booking"] == out["lanes"] == out["vendors"] == 200 and out["booking_has_risk"]
    assert out["first_vendor"] == "V001"
