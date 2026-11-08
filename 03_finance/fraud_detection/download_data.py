"""Fetch the credit card fraud dataset into data/ (gitignored). Run once: py download_data.py

The raw file is 150 MB, so it is never committed. This script downloads it from the
OpenML mirror (no account or login, so nothing about the person running it is sent),
checks the MD5 that OpenML publishes for it, and only then moves it into place. A file
that fails the check is deleted, never used.

License: the publishers' listing (Kaggle, mlg-ulb/creditcardfraud) puts the database under
the Open Database License (ODbL) v1.0 and its contents under the Database Contents License
(DbCL) v1.0; the OpenML mirror only labels it "Public", so the publishers' terms are the
ones followed here. Both allow public use. The charts and tables this project
publishes are "Produced Works" under the ODbL, which is why each carries the notice in
config.DATA_NOTICE. Sharing the data itself, or a database derived from it, would bring
the ODbL share-alike terms into play; this repository does neither.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

from config import DATA_MD5, DATA_PATH, DATA_URL

CHUNK = 1 << 20
TIMEOUT_SECONDS = 60


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str = DATA_URL, target: Path = DATA_PATH) -> Path:
    if target.exists() and md5_of(target) == DATA_MD5:
        print(f"Already present and verified: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {url}\n  -> {target}")
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response, \
            partial.open("wb") as out:
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        for block in iter(lambda: response.read(CHUNK), b""):
            out.write(block)
            done += len(block)
            if total:
                print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    actual = md5_of(partial)
    if actual != DATA_MD5:
        partial.unlink()
        raise RuntimeError(f"MD5 mismatch: expected {DATA_MD5}, got {actual}. File deleted.")
    partial.replace(target)
    print(f"Verified MD5 {actual}")
    return target


if __name__ == "__main__":
    try:
        download()
    except (OSError, RuntimeError) as error:
        print(f"Download failed: {error}", file=sys.stderr)
        sys.exit(1)
