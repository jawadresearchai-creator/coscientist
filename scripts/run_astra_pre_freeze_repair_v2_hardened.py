"""Hardened launcher for Astra pre-freeze V2 repair.

The shared SEC acquisition headers allow gzip/deflate. Daily master indexes are
small text files, so this launcher deliberately requests identity encoding and
validates the master-index header plus row volume before letting the repair
builder consume the response. This prevents compressed or HTML error payloads
from silently appearing as a zero-filing day.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import repair_astra_pre_freeze_v2 as base
from coscientist.sec_corpus import USER_AGENT


def fetch_sec_master_identity(date: str) -> str:
    compact = date.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.{compact}.idx"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/plain,*/*;q=0.1",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        content_type = str(resp.headers.get("Content-Type", ""))
    text = raw.decode("latin-1", errors="replace")
    # SEC currently renders the fifth column as "File Name". Validate the
    # stable four-column prefix so a harmless spacing change in that label does
    # not turn a valid daily index into a false zero-filing day.
    expected_header_prefix = "CIK|Company Name|Form Type|Date Filed|"
    pipe_rows = sum(1 for line in text.splitlines() if line.count("|") == 4)
    if expected_header_prefix not in text or pipe_rows < 100:
        preview = text[:240].replace("\n", " ").replace("\r", " ")
        raise RuntimeError(
            f"SEC daily master validation failed for {date}: bytes={len(raw)}, "
            f"content_type={content_type!r}, pipe_rows={pipe_rows}, preview={preview!r}"
        )
    return text


base.fetch_sec_master = fetch_sec_master_identity

if __name__ == "__main__":
    raise SystemExit(base.main())
