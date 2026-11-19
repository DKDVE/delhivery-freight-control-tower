"""Download the public Delhivery trip dataset to data/raw/delhivery_data.csv.

Source: https://github.com/iamjaspreetsingh/DelhiveryCaseStudy (third party, no licence file; not redistributed here).
"""
import hashlib
import io
import os
import urllib.request
import zipfile

URL = "https://raw.githubusercontent.com/iamjaspreetsingh/DelhiveryCaseStudy/main/delhivery_data.csv.zip"
SHA256 = "203535609bb2e2fe60030705b18bb5a2685f137e81bcd3b16579b75891c40422"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw", "delhivery_data.csv")


def main() -> None:
    if os.path.exists(OUT):
        print("already present:", OUT)
        return
    with urllib.request.urlopen(URL, timeout=120) as r:
        archive = zipfile.ZipFile(io.BytesIO(r.read()))
    name = next(n for n in archive.namelist() if n.endswith(".csv"))
    data = archive.read(name)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("Downloaded file does not match the checksum used for the reported results.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(data)
    print("saved", OUT)


if __name__ == "__main__":
    main()
