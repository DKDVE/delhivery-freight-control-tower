"""Fail if any real vendor name (from the private mapping) appears in a file that would be committed.

Usage: python scripts/check_vendor_name_leaks.py
Needs data/private/vendor_names.csv, which exists only on a machine that has the confidential workbook.
"""
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "data", "private", "vendor_names.csv")


def main() -> int:
    if not os.path.exists(PRIVATE):
        print("No private mapping on this machine; nothing to check.")
        return 0
    names = [n for n in pd.read_csv(PRIVATE)["vendor_name"].dropna() if len(n) >= 4]
    needles = {n: n.casefold().encode("utf8") for n in names}
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    hits = {}
    for f in filter(None, files):
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        raw = open(p, "rb").read()
        low = raw.lower()
        found = [n for n, b in needles.items() if b in low]
        if found:
            hits[f] = found[:3]
    for f, found in hits.items():
        print(f"LEAK {f}: {found}")
    print(f"scanned {len(files)} files, {len(hits)} with vendor names")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
