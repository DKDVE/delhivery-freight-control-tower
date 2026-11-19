"""Frontend rules that can be checked without a browser."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")
RUPEE = re.compile("₹|\\\\u20b9|&#8377;|&#x20b9;|&rupee;|Rs\\.? ?\\d", re.I)


def _files():
    for d, _, fs in os.walk(SRC):
        for f in fs:
            if f.endswith((".ts", ".tsx", ".css", ".html")):
                yield os.path.join(d, f)


def test_no_rupee_literals_outside_formatters():
    bad = []
    for p in _files():
        if os.path.basename(p) == "fmt.ts":
            continue
        for i, line in enumerate(open(p, encoding="utf8"), 1):
            if RUPEE.search(line):
                bad.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:80]}")
    assert not bad, "rupee literals must live in lib/fmt.ts only:\n" + "\n".join(bad)


def test_no_vendor_names_or_delhivery_logo_in_frontend():
    for p in _files():
        s = open(p, encoding="utf8").read().lower()
        assert "vendor_name" not in s, p
        assert "delhivery.com" not in s and "delhivery-logo" not in s, p


def test_required_banner_and_footer_text_present():
    shell = open(os.path.join(SRC, "components", "layout", "AppShell.tsx"), encoding="utf8").read()
    assert "Demo mode: explanations are rule-based; models and data are real" in shell
    assert "Student capstone prototype" in shell and "not an official Delhivery system" in shell
